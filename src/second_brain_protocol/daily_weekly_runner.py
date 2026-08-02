from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Protocol

from .extraction_harness import (
    EpisodeReceipt,
    ExtractionCoverage,
    ExtractionCandidate,
    ExtractionLimits,
    ExtractionRequest,
    ExtractionResult,
    ExtractionUsage,
    canonical_extraction_replay_payload,
    extraction_contract_fingerprint,
)
from .memory_mutations import (
    InMemoryMemoryCatalog,
    MemoryMutationPlan,
    MemoryMutationPlanner,
)
from .security import assert_model_packet_safe, sanitize_external_text


DEFAULT_RUNNER_CONTRACT = "daily-weekly-governed-v3"
SHA256 = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True)
class CheckpointAdvance:
    source_key: str
    cursor: str | None
    fingerprint: str | None
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.source_key.strip() or not self.evidence_ids:
            raise ValueError("Checkpoint advances require a source and evidence")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("Checkpoint evidence IDs must be unique")


@dataclass(frozen=True)
class EvidenceManifestEntry:
    evidence_id: str
    source_evidence_ids: tuple[str, ...]
    kind: str
    content_hash: str
    source_content_hashes: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if (
            not self.evidence_id.strip()
            or not self.source_evidence_ids
            or not self.kind.strip()
            or not SHA256.fullmatch(self.content_hash)
        ):
            raise ValueError("Evidence manifest entries require stable IDs")
        if self.source_evidence_ids != tuple(sorted(set(self.source_evidence_ids))):
            raise ValueError("Source evidence IDs must be unique and sorted")
        if self.source_content_hashes != tuple(
            sorted(set(self.source_content_hashes))
        ) or tuple(value[0] for value in self.source_content_hashes) != (
            self.source_evidence_ids
        ):
            raise ValueError("Source evidence hashes must match source evidence IDs")
        if any(
            not evidence_id.strip() or not SHA256.fullmatch(content_hash)
            for evidence_id, content_hash in self.source_content_hashes
        ):
            raise ValueError("Evidence manifest content hashes must be SHA-256")
        hashes = dict(self.source_content_hashes)
        if self.evidence_id in hashes and hashes[self.evidence_id] != self.content_hash:
            raise ValueError("Direct and source evidence hashes disagree")


@dataclass(frozen=True)
class EvidenceManifest:
    entries: tuple[EvidenceManifestEntry, ...]
    checkpoints: tuple[CheckpointAdvance, ...]

    def __post_init__(self) -> None:
        evidence_ids = tuple(item.evidence_id for item in self.entries)
        source_keys = tuple(item.source_key for item in self.checkpoints)
        if evidence_ids != tuple(sorted(evidence_ids)) or len(evidence_ids) != len(
            set(evidence_ids)
        ):
            raise ValueError("Evidence manifest entries must be unique and sorted")
        if source_keys != tuple(sorted(source_keys)) or len(source_keys) != len(
            set(source_keys)
        ):
            raise ValueError("Checkpoint advances must be unique and sorted")
        supported_ids = {
            value
            for item in self.entries
            for value in (item.evidence_id, *item.source_evidence_ids)
        }
        if any(
            set(checkpoint.evidence_ids) - supported_ids
            for checkpoint in self.checkpoints
        ):
            raise ValueError("Checkpoint evidence must exist in the evidence manifest")
        session_roots = [
            item.source_evidence_ids
            for item in self.entries
            if item.kind in {"session_digest", "session_episode"}
        ]
        if len(session_roots) != len(set(session_roots)):
            raise ValueError("Session evidence roots must identify one manifest entry")
        content_hashes: dict[str, str] = {}
        for item in self.entries:
            for evidence_id, content_hash in (
                (item.evidence_id, item.content_hash),
                *item.source_content_hashes,
            ):
                existing = content_hashes.get(evidence_id)
                if existing is not None and existing != content_hash:
                    raise ValueError("Evidence manifest hashes conflict")
                content_hashes[evidence_id] = content_hash

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return tuple(item.evidence_id for item in self.entries)


@dataclass(frozen=True)
class RunRequest:
    run_kind: str
    period: str
    model_contract: str
    evidence_manifest: EvidenceManifest
    evidence: tuple[Mapping[str, Any], ...]
    mode: str = "authoritative"
    behavior_contract: str = DEFAULT_RUNNER_CONTRACT
    limits: ExtractionLimits = field(default_factory=ExtractionLimits)

    def __post_init__(self) -> None:
        if self.run_kind not in {"daily", "weekly"}:
            raise ValueError("Run kind must be daily or weekly")
        if not self.period:
            raise ValueError("Run period must not be empty")
        if not self.model_contract.strip():
            raise ValueError("Run model contract must not be empty")
        if not self.behavior_contract.strip():
            raise ValueError("Runner behavior contract must not be empty")
        expected_manifest = tuple(
            sorted(
                (
                    EvidenceManifestEntry(
                        evidence_id=str(item.get("id") or ""),
                        source_evidence_ids=_source_evidence_ids(item),
                        kind=str(item.get("kind") or ""),
                        content_hash=str(item.get("content_hash") or ""),
                        source_content_hashes=_source_content_hashes(item),
                    )
                    for item in self.evidence
                ),
                key=lambda item: item.evidence_id,
            )
        )
        if self.evidence_manifest.entries != expected_manifest:
            raise ValueError("Evidence manifest does not match runner evidence")
        if self.mode != "authoritative":
            raise ValueError("Runner mode must be authoritative")


@dataclass(frozen=True)
class SessionRunSummary:
    summary_id: str
    source_evidence_ids: tuple[str, ...]
    analysis_lane: str
    project_id: str | None
    episode_count: int
    candidate_count: int
    rejection_count: int
    failure_count: int
    operation_counts: tuple[tuple[str, int], ...]
    kind_counts: tuple[tuple[str, int], ...]
    subjects: tuple[str, ...]
    summary: str
    procedure_candidate_count: int = 0


@dataclass(frozen=True)
class PeriodRunSummary:
    summary_id: str
    session_count: int
    episode_count: int
    candidate_count: int
    rejection_count: int
    failure_count: int
    operation_counts: tuple[tuple[str, int], ...]
    kind_counts: tuple[tuple[str, int], ...]
    summary: str
    procedure_candidate_count: int = 0


@dataclass(frozen=True)
class RunArtifact:
    run_kind: str
    period: str
    mode: str
    behavior_contract: str
    model_contract: str
    extraction_contract: str
    evidence_manifest: EvidenceManifest
    run_fingerprint: str
    artifact_fingerprint: str
    extraction: ExtractionResult
    memory_plan: MemoryMutationPlan
    session_summaries: tuple[SessionRunSummary, ...]
    period_summary: PeriodRunSummary


@dataclass(frozen=True)
class RunReceipt:
    status: str
    run_kind: str
    period: str
    mode: str
    fingerprint: str
    artifact_fingerprint: str
    extraction_status: str
    candidate_count: int
    rejection_counts: tuple[tuple[str, int], ...]
    failure_counts: tuple[tuple[str, int], ...]
    coverage: ExtractionCoverage
    usage: ExtractionUsage
    checkpoint_eligible: bool
    episode_accounting_complete: bool
    published: bool
    session_summaries: tuple[SessionRunSummary, ...]
    period_summary: PeriodRunSummary
    procedure_candidate_count: int = 0
    planned_memory_mutation_count: int = 0
    rejected_memory_mutation_count: int = 0


class Extractor(Protocol):
    def extract(self, request: ExtractionRequest) -> ExtractionResult: ...


class MemoryPlanner(Protocol):
    def plan(
        self, candidates: tuple[ExtractionCandidate, ...]
    ) -> MemoryMutationPlan: ...


class CandidateFilter(Protocol):
    def filter(
        self,
        result: ExtractionResult,
        request: ExtractionRequest,
    ) -> ExtractionResult: ...


class RunArtifactStore(Protocol):
    def put(self, artifact: RunArtifact) -> None: ...

    def get(self, run_fingerprint: str) -> RunArtifact | None: ...


class InMemoryRunArtifactStore:
    """Replay guard used by tests and short-lived governed runners."""

    def __init__(self) -> None:
        self._artifacts: dict[str, RunArtifact] = {}

    @property
    def artifacts(self) -> dict[str, RunArtifact]:
        return dict(self._artifacts)

    def put(self, artifact: RunArtifact) -> None:
        existing = self._artifacts.get(artifact.run_fingerprint)
        if (
            existing is not None
            and existing.artifact_fingerprint != artifact.artifact_fingerprint
            and artifact_checkpoint_eligible(existing)
        ):
            raise RuntimeError(
                "Replay divergence for identical Daily/Weekly input fingerprint"
            )
        self._artifacts[artifact.run_fingerprint] = artifact

    def get(self, run_fingerprint: str) -> RunArtifact | None:
        return self._artifacts.get(run_fingerprint)


class DailyWeeklyRunner:
    """Orchestrate extraction into a privacy-safe replayable run receipt."""

    def __init__(
        self,
        *,
        extractor: Extractor,
        artifact_store: RunArtifactStore | None = None,
        memory_planner: MemoryPlanner | None = None,
        candidate_filter: CandidateFilter | None = None,
    ) -> None:
        self._extractor = extractor
        self._artifact_store = artifact_store or InMemoryRunArtifactStore()
        self._memory_planner = memory_planner or MemoryMutationPlanner(
            catalog=InMemoryMemoryCatalog()
        )
        self._candidate_filter = candidate_filter

    def run(self, request: RunRequest) -> RunReceipt:
        extraction_contract = extraction_contract_fingerprint(request.run_kind)
        fingerprint = _run_fingerprint(
            request,
            extraction_contract=extraction_contract,
        )
        existing = self._artifact_store.get(fingerprint)
        if existing is not None:
            _validate_stored_artifact(
                existing,
                request=request,
                expected_extraction_contract=extraction_contract,
            )
            if artifact_checkpoint_eligible(existing):
                return _receipt_from_artifact(existing)

        extraction_request = ExtractionRequest(
            run_kind=request.run_kind,
            evidence=request.evidence,
            limits=request.limits,
        )
        extraction = self._extractor.extract(extraction_request)
        if self._candidate_filter is not None:
            extraction = self._candidate_filter.filter(
                extraction,
                extraction_request,
            )
        if extraction_contract_fingerprint(request.run_kind) != extraction_contract:
            raise RuntimeError("Extraction contract changed during run")
        memory_plan = self._memory_planner.plan(extraction.candidates)
        session_summaries = _session_summaries(extraction)
        period_summary = _period_summary(extraction, session_summaries)
        artifact_fingerprint = _artifact_fingerprint(
            run_kind=request.run_kind,
            period=request.period,
            mode=request.mode,
            behavior_contract=request.behavior_contract,
            model_contract=request.model_contract,
            extraction_contract=extraction_contract,
            evidence_manifest=request.evidence_manifest,
            run_fingerprint=fingerprint,
            extraction=extraction,
            memory_plan=memory_plan,
            session_summaries=session_summaries,
            period_summary=period_summary,
        )
        artifact = RunArtifact(
            run_kind=request.run_kind,
            period=request.period,
            mode=request.mode,
            behavior_contract=request.behavior_contract,
            model_contract=request.model_contract,
            extraction_contract=extraction_contract,
            evidence_manifest=request.evidence_manifest,
            run_fingerprint=fingerprint,
            artifact_fingerprint=artifact_fingerprint,
            extraction=extraction,
            memory_plan=memory_plan,
            session_summaries=session_summaries,
            period_summary=period_summary,
        )
        self._artifact_store.put(artifact)
        return _receipt_from_artifact(artifact)


def artifact_checkpoint_eligible(artifact: RunArtifact) -> bool:
    extraction = artifact.extraction
    return bool(
        not extraction.failures
        and extraction.coverage.source_evidence_count
        == len(artifact.evidence_manifest.entries)
        and extraction.coverage.failed_episodes == 0
        and extraction.coverage.omitted_messages == 0
        and extraction.coverage.episode_count == len(extraction.episodes)
        and _episode_provenance_complete(artifact)
    )


def recompute_artifact_fingerprint(artifact: RunArtifact) -> str:
    return _artifact_fingerprint(
        run_kind=artifact.run_kind,
        period=artifact.period,
        mode=artifact.mode,
        behavior_contract=artifact.behavior_contract,
        model_contract=artifact.model_contract,
        extraction_contract=artifact.extraction_contract,
        evidence_manifest=artifact.evidence_manifest,
        run_fingerprint=artifact.run_fingerprint,
        extraction=artifact.extraction,
        memory_plan=artifact.memory_plan,
        session_summaries=artifact.session_summaries,
        period_summary=artifact.period_summary,
    )


def _receipt_from_artifact(artifact: RunArtifact) -> RunReceipt:
    extraction = artifact.extraction
    rejection_counts = Counter(rejection.code for rejection in extraction.rejections)
    failure_counts = Counter(failure.code for failure in extraction.failures)
    episode_accounting_complete = extraction.coverage.episode_count == len(
        extraction.episodes
    )
    checkpoint_eligible = artifact_checkpoint_eligible(artifact)
    return RunReceipt(
        status="blocked" if not checkpoint_eligible else extraction.status,
        run_kind=artifact.run_kind,
        period=artifact.period,
        mode=artifact.mode,
        fingerprint=artifact.run_fingerprint,
        artifact_fingerprint=artifact.artifact_fingerprint,
        extraction_status=extraction.status,
        candidate_count=len(extraction.candidates),
        rejection_counts=tuple(sorted(rejection_counts.items())),
        failure_counts=tuple(sorted(failure_counts.items())),
        coverage=extraction.coverage,
        usage=extraction.usage,
        checkpoint_eligible=checkpoint_eligible,
        episode_accounting_complete=episode_accounting_complete,
        published=False,
        session_summaries=artifact.session_summaries,
        period_summary=artifact.period_summary,
        procedure_candidate_count=len(extraction.procedures),
        planned_memory_mutation_count=len(artifact.memory_plan.items),
        rejected_memory_mutation_count=len(artifact.memory_plan.rejections),
    )


def _validate_stored_artifact(
    artifact: RunArtifact,
    *,
    request: RunRequest,
    expected_extraction_contract: str,
) -> None:
    if (
        artifact.run_kind != request.run_kind
        or artifact.period != request.period
        or artifact.mode != request.mode
        or artifact.behavior_contract != request.behavior_contract
        or artifact.model_contract != request.model_contract
        or artifact.extraction_contract != expected_extraction_contract
        or artifact.evidence_manifest != request.evidence_manifest
        or artifact.artifact_fingerprint != recompute_artifact_fingerprint(artifact)
    ):
        raise RuntimeError("Stored Daily/Weekly replay artifact failed validation")


def _run_fingerprint(
    request: RunRequest,
    *,
    extraction_contract: str,
) -> str:
    extraction_request = ExtractionRequest(
        run_kind=request.run_kind,
        evidence=request.evidence,
        limits=request.limits,
    )
    encoded = json.dumps(
        {
            "behavior_contract": request.behavior_contract,
            "model_contract": request.model_contract,
            "extraction_contract": extraction_contract,
            "run_kind": request.run_kind,
            "period": request.period,
            "mode": request.mode,
            "evidence_manifest": asdict(request.evidence_manifest),
            "limits": {
                "max_episode_messages": request.limits.max_episode_messages,
                "max_episode_chars": request.limits.max_episode_chars,
            },
            "model_packets": canonical_extraction_replay_payload(extraction_request),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def _source_evidence_ids(item: Mapping[str, Any]) -> tuple[str, ...]:
    evidence_id = str(item.get("id") or "")
    payload = item.get("payload")
    values = (
        payload.get("source_evidence_ids") if isinstance(payload, Mapping) else None
    )
    roots = (
        tuple(sorted({str(value) for value in values if value}))
        if isinstance(values, (list, tuple))
        else ()
    )
    return roots or (evidence_id,)


def _source_content_hashes(
    item: Mapping[str, Any],
) -> tuple[tuple[str, str], ...]:
    evidence_id = str(item.get("id") or "")
    content_hash = str(item.get("content_hash") or "")
    values = item.get("source_content_hashes")
    hashes: dict[str, str] = {}
    if isinstance(values, Mapping):
        hashes = {
            str(key): str(value) for key, value in values.items() if key and value
        }
    roots = _source_evidence_ids(item)
    if roots == (evidence_id,) and evidence_id and content_hash:
        hashes.setdefault(evidence_id, content_hash)
    return tuple(sorted(hashes.items()))


def _episode_provenance_complete(artifact: RunArtifact) -> bool:
    expected = {
        item.source_evidence_ids
        for item in artifact.evidence_manifest.entries
        if item.kind in {"session_digest", "session_episode"}
    }
    actual: set[tuple[str, ...]] = set()
    episode_ids: set[str] = set()
    for episode in artifact.extraction.episodes:
        roots = tuple(sorted(set(episode.source_evidence_ids)))
        if (
            not episode.episode_id.strip()
            or episode.episode_id in episode_ids
            or roots != episode.source_evidence_ids
            or roots not in expected
        ):
            return False
        episode_ids.add(episode.episode_id)
        actual.add(roots)
    return actual == expected


def _session_summaries(
    extraction: ExtractionResult,
) -> tuple[SessionRunSummary, ...]:
    episode_by_id = {episode.episode_id: episode for episode in extraction.episodes}
    roots: dict[str, list[EpisodeReceipt]] = {}
    for episode in extraction.episodes:
        for source_id in episode.source_evidence_ids:
            roots.setdefault(source_id, []).append(episode)

    def referenced_roots(evidence_refs: tuple[str, ...]) -> set[str]:
        result: set[str] = set()
        for evidence_ref in evidence_refs:
            episode = episode_by_id.get(evidence_ref)
            if episode:
                result.update(episode.source_evidence_ids)
        return result

    summaries: list[SessionRunSummary] = []
    for source_id, episodes in sorted(roots.items()):
        episode_ids = {episode.episode_id for episode in episodes}
        candidates = tuple(
            candidate
            for candidate in extraction.candidates
            if episode_ids.intersection(candidate.evidence_refs)
        )
        procedures = tuple(
            procedure
            for procedure in extraction.procedures
            if episode_ids.intersection(procedure.evidence_refs)
        )
        rejections = tuple(
            rejection
            for rejection in extraction.rejections
            if source_id in referenced_roots(rejection.evidence_refs)
        )
        failures = tuple(
            failure
            for failure in extraction.failures
            if source_id in referenced_roots(failure.evidence_refs)
        )
        lanes = {episode.analysis_lane for episode in episodes}
        projects = {episode.project_id for episode in episodes if episode.project_id}
        subjects = tuple(
            sorted(
                {
                    sanitize_external_text(candidate.subject, max_chars=160).strip()
                    for candidate in candidates
                    if candidate.subject.strip()
                }
            )
        )[:8]
        summary_id = (
            "session-summary-"
            + hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:24]
        )
        summary_text = _session_summary_text(
            episode_count=len(episodes),
            candidate_count=len(candidates),
            procedure_count=len(procedures),
            subjects=subjects,
            rejection_count=len(rejections),
            failure_count=len(failures),
        )
        summary = SessionRunSummary(
            summary_id=summary_id,
            source_evidence_ids=(source_id,),
            analysis_lane=next(iter(lanes)) if len(lanes) == 1 else "mixed",
            project_id=next(iter(projects)) if len(projects) == 1 else None,
            episode_count=len(episodes),
            candidate_count=len(candidates),
            rejection_count=len(rejections),
            failure_count=len(failures),
            operation_counts=tuple(
                sorted(Counter(item.operation for item in candidates).items())
            ),
            kind_counts=tuple(
                sorted(Counter(item.kind for item in candidates).items())
            ),
            subjects=subjects,
            summary=summary_text,
            procedure_candidate_count=len(procedures),
        )
        assert_model_packet_safe(asdict(summary))
        summaries.append(summary)
    return tuple(summaries)


def _session_summary_text(
    *,
    episode_count: int,
    candidate_count: int,
    procedure_count: int,
    subjects: tuple[str, ...],
    rejection_count: int,
    failure_count: int,
) -> str:
    detail = f" Subjects: {'; '.join(subjects)}." if subjects else ""
    return (
        f"Covered {episode_count} episode(s); accepted {candidate_count} durable "
        f"memory candidate(s), rejected {rejection_count}, and failed "
        f"{failure_count}; proposed {procedure_count} review-only procedure(s)."
        f"{detail}"
    )


def _period_summary(
    extraction: ExtractionResult,
    session_summaries: tuple[SessionRunSummary, ...],
) -> PeriodRunSummary:
    operation_counts = tuple(
        sorted(Counter(item.operation for item in extraction.candidates).items())
    )
    kind_counts = tuple(
        sorted(Counter(item.kind for item in extraction.candidates).items())
    )
    identity = {
        "sessions": [item.summary_id for item in session_summaries],
        "episodes": [item.episode_id for item in extraction.episodes],
        "candidates": len(extraction.candidates),
        "procedures": len(extraction.procedures),
        "rejections": len(extraction.rejections),
        "failures": len(extraction.failures),
    }
    summary_id = (
        "period-summary-"
        + hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:24]
    )
    summary = PeriodRunSummary(
        summary_id=summary_id,
        session_count=len(session_summaries),
        episode_count=len(extraction.episodes),
        candidate_count=len(extraction.candidates),
        rejection_count=len(extraction.rejections),
        failure_count=len(extraction.failures),
        operation_counts=operation_counts,
        kind_counts=kind_counts,
        summary=(
            f"Covered {len(session_summaries)} session(s) across "
            f"{len(extraction.episodes)} episode(s); accepted "
            f"{len(extraction.candidates)} durable memory candidate(s), rejected "
            f"{len(extraction.rejections)}, and failed {len(extraction.failures)}; "
            f"proposed {len(extraction.procedures)} review-only procedure(s)."
        ),
        procedure_candidate_count=len(extraction.procedures),
    )
    assert_model_packet_safe(asdict(summary))
    return summary


def _artifact_fingerprint(
    *,
    run_kind: str,
    period: str,
    mode: str,
    behavior_contract: str,
    model_contract: str,
    extraction_contract: str,
    evidence_manifest: EvidenceManifest,
    run_fingerprint: str,
    extraction: ExtractionResult,
    memory_plan: MemoryMutationPlan,
    session_summaries: tuple[SessionRunSummary, ...],
    period_summary: PeriodRunSummary,
) -> str:
    payload = {
        "run_kind": run_kind,
        "period": period,
        "mode": mode,
        "behavior_contract": behavior_contract,
        "model_contract": model_contract,
        "extraction_contract": extraction_contract,
        "evidence_manifest": asdict(evidence_manifest),
        "run_fingerprint": run_fingerprint,
        "extraction_status": extraction.status,
        "candidates": [asdict(item) for item in extraction.candidates],
        "procedures": [asdict(item) for item in extraction.procedures],
        "memory_plan": asdict(memory_plan),
        "rejections": [asdict(item) for item in extraction.rejections],
        "failures": [asdict(item) for item in extraction.failures],
        "coverage": asdict(extraction.coverage),
        "usage": asdict(extraction.usage),
        "episodes": [asdict(item) for item in extraction.episodes],
        "session_summaries": [asdict(item) for item in session_summaries],
        "period_summary": asdict(period_summary),
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
