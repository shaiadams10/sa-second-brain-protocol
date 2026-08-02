from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Callable, Mapping

from .apply_persistence import (
    SQLiteApplyJournal,
    SQLiteApplyStateCommitter,
    SQLiteCutoverAuthorizationStore,
    SQLiteRunArtifactStore,
)
from .atomic_apply import (
    CUTOVER_CONTRACT_VERSION,
    ApplyRequest,
    AtomicApplyGate,
    BoundedPublicationPolicy,
    CutoverAuthorization,
    JournaledApplyCoordinator,
)
from .daily_weekly_runner import (
    CheckpointAdvance,
    DailyWeeklyRunner,
    EvidenceManifest,
    EvidenceManifestEntry,
    RunRequest,
)
from .extraction_harness import (
    ExtractionHarness,
    ExtractionLimits,
    ExtractionModel,
    ExtractionRequest,
    canonical_extraction_replay_payload,
)
from .extraction_model import BudgetedExtractionModel
from .governed_publication import DurableJournalPublicationPlanner
from .memory_mutations import MemoryMutationPlanner
from .memory_registry import (
    CanonicalMemoryPublicationVerifier,
    SQLiteMemoryRegistry,
    UnavailableMemoryPublicationVerifier,
)
from .novelty import SQLiteEstablishedMemoryFilter
from .state import StateStore


GOVERNED_ENGINE = "governed-extraction-v3"
GOVERNED_BEHAVIOR_CONTRACT = "daily-weekly-governed-v3"


class GovernedDailyWeeklyPipeline:
    """Run extraction, atomic journal publication, and checkpoint commit."""

    def __init__(
        self,
        *,
        vault: Path,
        staging_root: Path,
        store: StateStore,
        model_factory: Callable[[str], ExtractionModel],
        model_contract: str,
        max_model_calls: int,
        limits: ExtractionLimits | None = None,
        max_packet_chars: int = 180_000,
    ) -> None:
        if max_model_calls <= 0:
            raise ValueError("Governed model-call budget must be positive")
        self._vault = vault
        self._staging_root = staging_root
        self._store = store
        self._model_factory = model_factory
        self._model_contract = model_contract
        self._max_model_calls = max_model_calls
        self._limits = limits or ExtractionLimits()
        self._max_packet_chars = max_packet_chars

    def run(self, *, run_kind: str, period: str) -> dict:
        if run_kind not in {"daily", "weekly"}:
            raise ValueError("Governed pipeline supports Daily and Weekly")
        _reconcile_daily_summaries(self._store)
        _reconcile_review_observations(self._store)
        available = _available_evidence(self._store, run_kind=run_kind, period=period)
        selected = _select_evidence(
            available,
            run_kind=run_kind,
            limits=self._limits,
            max_model_calls=self._max_model_calls,
            max_packet_chars=self._max_packet_chars,
        )
        if not selected and run_kind == "daily":
            return {
                "status": "empty",
                "engine": GOVERNED_ENGINE,
                "evidence_count": 0,
                "model_called": False,
            }

        evidence, manifest = _manifest(self._store, selected)
        run_id = self._store.start_run(run_kind, self._model_contract, "governed")
        artifacts = SQLiteRunArtifactStore(self._store)
        memory_registry = SQLiteMemoryRegistry(
            self._store,
            publication_verifier=CanonicalMemoryPublicationVerifier(self._vault),
        )
        model = BudgetedExtractionModel(
            delegate=self._model_factory(run_id),
            max_model_calls=self._max_model_calls,
        )
        runner = DailyWeeklyRunner(
            extractor=ExtractionHarness(model=model),
            artifact_store=artifacts,
            memory_planner=MemoryMutationPlanner(catalog=memory_registry),
            candidate_filter=SQLiteEstablishedMemoryFilter(
                self._store,
                vault=self._vault,
            ),
        )
        request = RunRequest(
            run_kind=run_kind,
            period=period,
            model_contract=self._model_contract,
            evidence_manifest=manifest,
            evidence=evidence,
            mode="authoritative",
            behavior_contract=GOVERNED_BEHAVIOR_CONTRACT,
            limits=self._limits,
        )
        try:
            receipt = runner.run(request)
            if not receipt.checkpoint_eligible:
                raise RuntimeError("Governed extraction was not checkpoint eligible")
            self._store.finish_run(
                run_id,
                "awaiting_publication",
                evidence_count=len(selected),
                usage=asdict(receipt.usage),
            )
            artifact = artifacts.get(receipt.fingerprint)
            if artifact is None:
                raise RuntimeError("Governed run artifact was not persisted")
            authorization = _authorization(artifact)
            authorizations = SQLiteCutoverAuthorizationStore(self._store)
            authorizations.issue(authorization)
            applied = AtomicApplyGate(
                artifact_store=artifacts,
                authorization_store=authorizations,
                coordinator=JournaledApplyCoordinator(
                    publisher=DurableJournalPublicationPlanner(
                        vault=self._vault,
                        staging_root=self._staging_root,
                    ),
                    journal=SQLiteApplyJournal(self._store),
                    state_committer=SQLiteApplyStateCommitter(
                        self._store,
                        memory_registry=memory_registry,
                    ),
                    publication_policy=BoundedPublicationPolicy(
                        owned_sections=(run_kind,)
                    ),
                ),
            ).apply(ApplyRequest(receipt, authorization.authorization_id))
            _mirror_review_observations(self._store, artifact)
            if run_kind == "daily":
                _record_daily_summary(self._store, artifact)
            self._store.replace_summary_runs(run_kind, period, [run_id])
            self._store.replace_summary_evidence(
                run_kind,
                period,
                [str(item["id"]) for item in selected],
            )
            self._store.complete_validated_run(run_id)
        except Exception as error:
            self._store.finish_run(
                run_id,
                "failed",
                evidence_count=len(selected),
                error=f"{type(error).__name__}: {error}",
            )
            raise
        return {
            "status": "completed",
            "engine": GOVERNED_ENGINE,
            "run_id": run_id,
            "run_fingerprint": receipt.fingerprint,
            "artifact_fingerprint": receipt.artifact_fingerprint,
            "evidence_count": len(selected),
            "remaining_evidence": len(available) - len(selected),
            "candidate_count": receipt.candidate_count,
            "procedure_candidate_count": receipt.procedure_candidate_count,
            "suppressed_established_count": dict(receipt.rejection_counts).get(
                "stale-established-memory", 0
            ),
            "model_called": bool(receipt.usage.model_calls),
            "model_calls": receipt.usage.model_calls,
            "changed_paths": list(applied.changed_paths),
            "synthesis_path": str(self._vault / applied.changed_paths[0]),
            "checkpoint_advanced": applied.checkpoint_advanced,
            "published": applied.published,
        }


def _available_evidence(
    store: StateStore,
    *,
    run_kind: str,
    period: str,
) -> list[dict]:
    new = store.evidence(status="new")
    if run_kind == "daily":
        return [item for item in new if item.get("kind") != "daily_run_summary"]
    return [
        item
        for item in new
        if item.get("kind") == "daily_run_summary"
        and _iso_week(str((item.get("payload") or {}).get("period") or "")) == period
    ]


def _select_evidence(
    evidence: list[dict],
    *,
    run_kind: str,
    limits: ExtractionLimits,
    max_model_calls: int,
    max_packet_chars: int,
) -> list[dict]:
    priority = {
        "explicit_owner_statement": 0,
        "explicit_project_answer": 0,
        "session_digest": 1,
        "project_delta": 2,
        "daily_run_summary": 2,
        "code_graph_summary": 3,
        "cross_project_graph_summary": 3,
        "project_inventory": 4,
    }
    ordered = sorted(
        evidence,
        key=lambda item: (
            priority.get(str(item.get("kind") or ""), 3),
            str(item.get("occurred_at") or item.get("created_at") or ""),
            str(item.get("id") or ""),
        ),
    )
    selected: list[dict] = []
    for item in ordered:
        trial = [*selected, item]
        request = ExtractionRequest(
            run_kind=run_kind,
            evidence=tuple(trial),
            limits=limits,
        )
        packets = canonical_extraction_replay_payload(request)
        encoded_chars = len(json.dumps(packets, ensure_ascii=False, sort_keys=True))
        if len(packets) <= max_model_calls and encoded_chars <= max_packet_chars:
            selected = trial
    return selected


def _manifest(
    store: StateStore,
    selected: list[dict],
) -> tuple[tuple[Mapping[str, object], ...], EvidenceManifest]:
    roots_by_evidence: dict[str, tuple[str, ...]] = {}
    all_ids: set[str] = set()
    for item in selected:
        evidence_id = str(item["id"])
        payload = item.get("payload") or {}
        values = (
            payload.get("source_evidence_ids") if isinstance(payload, dict) else None
        )
        roots = (
            tuple(sorted({str(value) for value in values if value}))
            if isinstance(values, list)
            else ()
        )
        roots_by_evidence[evidence_id] = roots or (evidence_id,)
        all_ids.update(roots_by_evidence[evidence_id])
    rows = store.evidence_by_ids(sorted(all_ids))
    hashes = {str(item["id"]): str(item["content_hash"]) for item in rows}
    evidence: list[Mapping[str, object]] = []
    entries: list[EvidenceManifestEntry] = []
    for item in selected:
        evidence_id = str(item["id"])
        roots = roots_by_evidence[evidence_id]
        missing = set(roots) - set(hashes)
        if missing:
            raise RuntimeError("Governed evidence ancestry is incomplete")
        source_hashes = tuple((value, hashes[value]) for value in roots)
        transported = {**item, "source_content_hashes": dict(source_hashes)}
        evidence.append(transported)
        entries.append(
            EvidenceManifestEntry(
                evidence_id=evidence_id,
                source_evidence_ids=roots,
                kind=str(item.get("kind") or ""),
                content_hash=str(item.get("content_hash") or ""),
                source_content_hashes=source_hashes,
            )
        )
    checkpoints = _checkpoint_manifest(store, all_ids)
    return (
        tuple(evidence),
        EvidenceManifest(
            entries=tuple(sorted(entries, key=lambda item: item.evidence_id)),
            checkpoints=checkpoints,
        ),
    )


def _checkpoint_manifest(
    store: StateStore,
    evidence_ids: set[str],
) -> tuple[CheckpointAdvance, ...]:
    if not evidence_ids:
        return ()
    placeholders = ",".join("?" for _ in evidence_ids)
    with store.connect() as connection:
        rows = connection.execute(
            f"""SELECT evidence_id,source_key,cursor,fingerprint
            FROM evidence_checkpoints WHERE evidence_id IN ({placeholders})""",
            sorted(evidence_ids),
        ).fetchall()
    selected: dict[str, dict] = {}
    for row in rows:
        item = dict(row)
        source_key = str(item["source_key"])
        current = selected.get(source_key)
        if current is None or _cursor_key(item.get("cursor")) > _cursor_key(
            current.get("cursor")
        ):
            selected[source_key] = item
    return tuple(
        CheckpointAdvance(
            source_key=source_key,
            cursor=item.get("cursor"),
            fingerprint=item.get("fingerprint"),
            evidence_ids=(str(item["evidence_id"]),),
        )
        for source_key, item in sorted(selected.items())
    )


def _cursor_key(cursor: str | None) -> tuple[int, int]:
    if not cursor:
        return (0, -1)
    try:
        value = json.loads(cursor)
    except (json.JSONDecodeError, TypeError):
        return (0, -1)
    if not isinstance(value, dict):
        return (0, -1)
    if value.get("kind") == "jsonl":
        return (1, int(value.get("offset", -1)))
    if value.get("kind") == "sqlite-steps":
        return (2, int(value.get("row_idx", -1)))
    return (0, -1)


def _authorization(artifact) -> CutoverAuthorization:
    authorization_id = (
        "cutover-"
        + hashlib.sha256(
            (
                artifact.run_fingerprint
                + artifact.artifact_fingerprint
                + CUTOVER_CONTRACT_VERSION
            ).encode("utf-8")
        ).hexdigest()[:32]
    )
    return CutoverAuthorization(
        authorization_id=authorization_id,
        contract_version=CUTOVER_CONTRACT_VERSION,
        approved=True,
        run_fingerprint=artifact.run_fingerprint,
        run_kind=artifact.run_kind,
        period=artifact.period,
        artifact_fingerprint=artifact.artifact_fingerprint,
        behavior_contract=artifact.behavior_contract,
    )


def _mirror_review_observations(store: StateStore, artifact) -> None:
    for item in artifact.memory_plan.items:
        _mirror_review_item(store, item)


def _mirror_review_item(store: StateStore, item) -> None:
    store.add_observation(
        {
            "kind": item.memory_key.kind,
            "subject": item.subject,
            "claim": item.claim,
            "evidence_refs": list(item.evidence_refs),
            "confidence": item.confidence,
            "source_count": len(item.evidence_refs),
            "project_count": 1 if item.memory_key.project_id else 0,
            "sensitivity": "normal",
            "promotion_tier": "review",
            "status": "pending",
            "explicit": item.explicit,
            "scope": item.memory_key.scope,
            "project_id": item.memory_key.project_id,
            "project_ids": (
                [item.memory_key.project_id] if item.memory_key.project_id else []
            ),
            "memory_mutation_id": item.mutation_id,
            "knowledge_layer": item.layer,
            "review_reason": "new or materially changed governed memory proposal",
            "governed_extraction": True,
        }
    )


def _record_daily_summary(store: StateStore, artifact) -> None:
    store.add_evidence(
        source_type="daily-run",
        source_ref=f"daily-run:{artifact.run_fingerprint}",
        kind="daily_run_summary",
        payload={
            "period": artifact.period,
            "run_fingerprint": artifact.run_fingerprint,
            "summary": artifact.period_summary.summary,
            "candidates": [
                {
                    "kind": item.kind,
                    "subject": item.subject,
                    "claim": item.claim,
                    "scope": item.scope,
                    "project_id": item.project_id,
                }
                for item in artifact.extraction.candidates
            ],
            "session_summaries": [
                {
                    "analysis_lane": item.analysis_lane,
                    "project_id": item.project_id,
                    "candidate_count": item.candidate_count,
                    "procedure_candidate_count": item.procedure_candidate_count,
                    "subjects": list(item.subjects),
                    "summary": item.summary,
                }
                for item in artifact.session_summaries
                if item.candidate_count or item.procedure_candidate_count
            ],
        },
        occurred_at=f"{artifact.period}T23:59:59Z",
    )


def _reconcile_daily_summaries(store: StateStore) -> None:
    artifacts = SQLiteRunArtifactStore(store)
    with store.connect() as connection:
        rows = connection.execute(
            "SELECT receipt_json FROM atomic_apply_receipts"
        ).fetchall()
    for row in rows:
        try:
            receipt = json.loads(str(row["receipt_json"]))
        except (json.JSONDecodeError, TypeError):
            continue
        if not receipt.get("published") or not receipt.get("checkpoint_advanced"):
            continue
        fingerprint = str(receipt.get("run_fingerprint") or "")
        if not fingerprint:
            continue
        artifact = artifacts.get(fingerprint)
        if artifact is None or artifact.run_kind != "daily":
            continue
        source_ref = f"daily-run:{artifact.run_fingerprint}"
        if (
            store.latest_evidence_by_source(
                source_type="daily-run",
                source_ref=source_ref,
            )
            is None
        ):
            _record_daily_summary(store, artifact)


def _reconcile_review_observations(store: StateStore) -> None:
    registry = SQLiteMemoryRegistry(
        store,
        publication_verifier=UnavailableMemoryPublicationVerifier(),
    )
    for proposal in registry.proposals("pending"):
        _mirror_review_item(store, proposal.item)


def _iso_week(value: str) -> str | None:
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    week = parsed.isocalendar()
    return f"{week.year}-W{week.week:02d}"
