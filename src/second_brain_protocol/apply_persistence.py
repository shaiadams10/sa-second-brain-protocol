from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from typing import Any

from .atomic_apply import (
    ApplyJournalEntry,
    ApplyReceipt,
    ApplyStateManifest,
    CutoverAuthorization,
)
from .daily_weekly_runner import (
    CheckpointAdvance,
    EvidenceManifest,
    EvidenceManifestEntry,
    PeriodRunSummary,
    RunArtifact,
    SessionRunSummary,
    artifact_checkpoint_eligible,
)
from .extraction_harness import (
    CandidateRejection,
    EpisodeReceipt,
    ExtractionCandidate,
    ExtractionCoverage,
    ExtractionFailure,
    ExtractionResult,
    ExtractionUsage,
    ProcedureCandidate,
)
from .memory_mutations import (
    MemoryKey,
    MemoryMutationPlan,
    MemoryPlanRejection,
    PlannedMemoryMutation,
)
from .memory_registry import (
    SQLiteMemoryRegistry,
    UnavailableMemoryPublicationVerifier,
)
from .procedure_review import SQLiteProcedureReviewStore
from .publication_policy import PublicationFile, PublicationManifest
from .state import StateStore, canonical_hash, utc_now


APPLY_SCHEMA = """
CREATE TABLE IF NOT EXISTS governed_run_artifacts (
  run_fingerprint TEXT PRIMARY KEY,
  artifact_fingerprint TEXT NOT NULL,
  artifact_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS atomic_apply_journal (
  idempotency_key TEXT PRIMARY KEY,
  run_fingerprint TEXT NOT NULL,
  artifact_fingerprint TEXT NOT NULL,
  authorization_id TEXT NOT NULL,
  staging_id TEXT NOT NULL,
  publication_json TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('prepared','files_committed','completed')),
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cutover_authorizations (
  authorization_id TEXT PRIMARY KEY,
  authorization_json TEXT NOT NULL,
  operation_key TEXT,
  issued_at TEXT NOT NULL,
  bound_at TEXT
);

CREATE TABLE IF NOT EXISTS atomic_apply_receipts (
  idempotency_key TEXT PRIMARY KEY,
  receipt_json TEXT NOT NULL,
  committed_at TEXT NOT NULL
);
"""


def _initialize(store: StateStore) -> None:
    with store.connect() as connection:
        connection.executescript(APPLY_SCHEMA)
        retired = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='shadow_run_artifacts'"
        ).fetchone()
        if retired:
            rows = connection.execute(
                "SELECT * FROM shadow_run_artifacts"
            ).fetchall()
            for row in rows:
                try:
                    artifact = json.loads(str(row["artifact_json"]))
                except (TypeError, json.JSONDecodeError):
                    continue
                if artifact.get("mode") != "authoritative":
                    continue
                connection.execute(
                    "INSERT OR IGNORE INTO governed_run_artifacts VALUES(?,?,?,?)",
                    (
                        row["run_fingerprint"],
                        row["artifact_fingerprint"],
                        row["artifact_json"],
                        row["created_at"],
                    ),
                )
            connection.execute("DROP TABLE shadow_run_artifacts")


class SQLiteRunArtifactStore:
    def __init__(self, store: StateStore) -> None:
        self._store = store
        _initialize(store)

    def put(self, artifact: RunArtifact) -> None:
        encoded = _encode(_artifact_to_dict(artifact))
        with self._store.transaction() as connection:
            existing = connection.execute(
                "SELECT artifact_fingerprint,artifact_json FROM governed_run_artifacts WHERE run_fingerprint=?",
                (artifact.run_fingerprint,),
            ).fetchone()
            if existing is not None:
                if (
                    (
                        existing["artifact_fingerprint"]
                        != artifact.artifact_fingerprint
                        or existing["artifact_json"] != encoded
                    )
                    and artifact_checkpoint_eligible(
                        _artifact_from_dict(json.loads(existing["artifact_json"]))
                    )
                ):
                    raise RuntimeError("Replay divergence in durable artifact store")
                if (
                    existing["artifact_fingerprint"] == artifact.artifact_fingerprint
                    and existing["artifact_json"] == encoded
                ):
                    return
                connection.execute(
                    """UPDATE governed_run_artifacts
                    SET artifact_fingerprint=?,artifact_json=?,created_at=?
                    WHERE run_fingerprint=?""",
                    (
                        artifact.artifact_fingerprint,
                        encoded,
                        utc_now(),
                        artifact.run_fingerprint,
                    ),
                )
                return
            connection.execute(
                "INSERT INTO governed_run_artifacts VALUES(?,?,?,?)",
                (
                    artifact.run_fingerprint,
                    artifact.artifact_fingerprint,
                    encoded,
                    utc_now(),
                ),
            )

    def get(self, run_fingerprint: str) -> RunArtifact | None:
        with self._store.connect() as connection:
            row = connection.execute(
                "SELECT artifact_json FROM governed_run_artifacts WHERE run_fingerprint=?",
                (run_fingerprint,),
            ).fetchone()
        return _artifact_from_dict(json.loads(row["artifact_json"])) if row else None


class SQLiteApplyJournal:
    def __init__(self, store: StateStore) -> None:
        self._store = store
        _initialize(store)

    def get(self, idempotency_key: str) -> ApplyJournalEntry | None:
        with self._store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM atomic_apply_journal WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
        return _journal_from_row(row) if row else None

    def put_prepared(self, entry: ApplyJournalEntry) -> None:
        if entry.state != "prepared":
            raise RuntimeError("New apply journal entries must be prepared")
        with self._store.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM atomic_apply_journal WHERE idempotency_key=?",
                (entry.idempotency_key,),
            ).fetchone()
            if existing is not None:
                if _journal_from_row(existing) != entry:
                    raise RuntimeError("Conflicting atomic-apply journal entry")
                return
            connection.execute(
                """INSERT INTO atomic_apply_journal(
                idempotency_key,run_fingerprint,artifact_fingerprint,authorization_id,
                staging_id,publication_json,state,updated_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    entry.idempotency_key,
                    entry.run_fingerprint,
                    entry.artifact_fingerprint,
                    entry.authorization_id,
                    entry.staging_id,
                    _encode(asdict(entry.publication)),
                    entry.state,
                    utc_now(),
                ),
            )

    def mark_files_committed(self, idempotency_key: str) -> None:
        self._transition(
            idempotency_key,
            expected="prepared",
            target="files_committed",
        )

    def mark_completed(self, idempotency_key: str) -> None:
        self._transition(
            idempotency_key,
            expected="files_committed",
            target="completed",
        )

    def _transition(self, idempotency_key: str, *, expected: str, target: str) -> None:
        with self._store.transaction() as connection:
            row = connection.execute(
                "SELECT state FROM atomic_apply_journal WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if row is None:
                raise RuntimeError("Atomic-apply journal entry is unavailable")
            if row["state"] == target or row["state"] == "completed":
                return
            if row["state"] != expected:
                raise RuntimeError("Invalid atomic-apply journal transition")
            connection.execute(
                "UPDATE atomic_apply_journal SET state=?,updated_at=? WHERE idempotency_key=?",
                (target, utc_now(), idempotency_key),
            )


class SQLiteCutoverAuthorizationStore:
    def __init__(self, store: StateStore) -> None:
        self._store = store
        _initialize(store)

    def issue(self, authorization: CutoverAuthorization) -> None:
        encoded = _encode(asdict(authorization))
        with self._store.transaction() as connection:
            existing = connection.execute(
                "SELECT authorization_json FROM cutover_authorizations WHERE authorization_id=?",
                (authorization.authorization_id,),
            ).fetchone()
            if existing is not None:
                if existing["authorization_json"] != encoded:
                    raise RuntimeError("Cutover authorization ID is already issued")
                return
            connection.execute(
                "INSERT INTO cutover_authorizations VALUES(?,?,NULL,?,NULL)",
                (authorization.authorization_id, encoded, utc_now()),
            )

    def resolve(self, authorization_id: str) -> CutoverAuthorization | None:
        with self._store.connect() as connection:
            row = connection.execute(
                "SELECT authorization_json FROM cutover_authorizations WHERE authorization_id=?",
                (authorization_id,),
            ).fetchone()
        return CutoverAuthorization(**json.loads(row["authorization_json"])) if row else None

    def bind_once(self, authorization_id: str, *, operation_key: str) -> None:
        with self._store.transaction() as connection:
            row = connection.execute(
                "SELECT operation_key FROM cutover_authorizations WHERE authorization_id=?",
                (authorization_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("Cutover authorization was not issued")
            if row["operation_key"] is not None and row["operation_key"] != operation_key:
                raise RuntimeError("Cutover authorization was already consumed")
            if row["operation_key"] is None:
                connection.execute(
                    "UPDATE cutover_authorizations SET operation_key=?,bound_at=? WHERE authorization_id=?",
                    (operation_key, utc_now(), authorization_id),
                )


class SQLiteApplyStateCommitter:
    """Commit checkpoint advances, changed-note queue, and receipt atomically."""

    def __init__(
        self,
        store: StateStore,
        *,
        memory_registry: SQLiteMemoryRegistry | None = None,
        procedure_reviews: SQLiteProcedureReviewStore | None = None,
    ) -> None:
        self._store = store
        _initialize(store)
        self._memory_registry = memory_registry or SQLiteMemoryRegistry(
            store,
            publication_verifier=UnavailableMemoryPublicationVerifier(),
        )
        self._procedure_reviews = procedure_reviews or SQLiteProcedureReviewStore(
            store
        )

    def get_receipt(self, idempotency_key: str) -> ApplyReceipt | None:
        with self._store.connect() as connection:
            row = connection.execute(
                "SELECT receipt_json FROM atomic_apply_receipts WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
        return _receipt_from_dict(json.loads(row["receipt_json"])) if row else None

    def preflight(
        self,
        artifact: RunArtifact,
        *,
        manifest: ApplyStateManifest,
    ) -> None:
        if (
            manifest.run_kind != artifact.run_kind
            or manifest.period != artifact.period
            or manifest.evidence != artifact.evidence_manifest
        ):
            raise RuntimeError("Transactional apply manifest binding mismatch")
        with self._store.transaction() as connection:
            _validate_manifest_evidence_snapshot(connection, manifest.evidence)
            _validate_checkpoint_evidence(connection, manifest.evidence.checkpoints)
            _persist_artifact_episode_evidence(connection, artifact)

    def commit(
        self,
        artifact: RunArtifact,
        *,
        manifest: ApplyStateManifest,
        idempotency_key: str,
        receipt: ApplyReceipt,
    ) -> None:
        _validate_state_intent(
            artifact,
            manifest=manifest,
            idempotency_key=idempotency_key,
            receipt=receipt,
        )
        with self._store.transaction() as connection:
            existing = connection.execute(
                "SELECT receipt_json FROM atomic_apply_receipts WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            encoded_receipt = _encode(asdict(receipt))
            if existing is not None:
                if existing["receipt_json"] != encoded_receipt:
                    raise RuntimeError("Conflicting transactional apply receipt")
                return
            required_evidence = {
                value
                for item in manifest.evidence.entries
                for value in (item.evidence_id, *item.source_evidence_ids)
            } | {
                value
                for checkpoint in manifest.evidence.checkpoints
                for value in checkpoint.evidence_ids
            } | {
                evidence_ref
                for item in artifact.memory_plan.items
                for evidence_ref in item.evidence_refs
            } | {
                evidence_ref
                for item in artifact.extraction.procedures
                for evidence_ref in item.evidence_refs
            } | {
                episode.episode_id
                for episode in artifact.extraction.episodes
                if episode.episode_id.startswith("ev-episode-")
            }
            selected_evidence = {
                item.evidence_id for item in manifest.evidence.entries
            }
            _validate_manifest_evidence_snapshot(connection, manifest.evidence)
            _validate_checkpoint_evidence(
                connection,
                manifest.evidence.checkpoints,
            )
            _validate_artifact_episode_evidence(connection, artifact)
            processed_evidence = _evidence_ancestor_closure(
                connection,
                required_evidence,
            )
            if processed_evidence:
                placeholders = ",".join("?" for _ in processed_evidence)
                rows = connection.execute(
                    f"SELECT id,status FROM evidence WHERE id IN ({placeholders})",
                    sorted(processed_evidence),
                ).fetchall()
                if {str(row["id"]) for row in rows} != processed_evidence:
                    raise RuntimeError("Apply evidence derivation closure is incomplete")
                status_by_id = {
                    str(row["id"]): str(row["status"]) for row in rows
                }
                if any(
                    status_by_id[evidence_id] != "new"
                    for evidence_id in selected_evidence
                ):
                    raise RuntimeError("Selected apply evidence was already processed")
                if any(
                    status_by_id[evidence_id]
                    not in {"new", "compacted", "processed"}
                    for evidence_id in processed_evidence - selected_evidence
                ):
                    raise RuntimeError(
                        "Apply evidence ancestry has an ineligible status"
                    )
            self._memory_registry.stage_in_transaction(
                connection,
                artifact.memory_plan,
                run_fingerprint=artifact.run_fingerprint,
            )
            self._procedure_reviews.stage_in_transaction(
                connection,
                artifact.extraction.procedures,
                run_fingerprint=artifact.run_fingerprint,
            )
            for checkpoint in manifest.evidence.checkpoints:
                _advance_checkpoint(connection, checkpoint)
            if processed_evidence:
                placeholders = ",".join("?" for _ in processed_evidence)
                connection.execute(
                    f"""UPDATE evidence SET status='processed'
                    WHERE id IN ({placeholders})
                    AND status IN ('new','compacted')""",
                    sorted(processed_evidence),
                )
            StateStore.enqueue_search_refresh(
                connection,
                set(manifest.publication.changed_paths),
            )
            connection.execute(
                "INSERT INTO atomic_apply_receipts VALUES(?,?,?)",
                (idempotency_key, encoded_receipt, utc_now()),
            )


def _persist_artifact_episode_evidence(
    connection: sqlite3.Connection,
    artifact: RunArtifact,
) -> None:
    required = _proposal_evidence_refs(artifact)
    episodes = {item.episode_id: item for item in artifact.extraction.episodes}
    missing = {
        value
        for value in required
        if value.startswith("ev-episode-") and value not in episodes
    }
    if missing:
        raise RuntimeError("Artifact proposal references missing episode evidence")
    for episode in artifact.extraction.episodes:
        if not episode.episode_id.startswith("ev-episode-"):
            continue
        if not episode.payload_json or not episode.content_hash:
            raise RuntimeError("Artifact episode evidence is not durable")
        try:
            payload = json.loads(episode.payload_json)
        except json.JSONDecodeError as error:
            raise RuntimeError("Artifact episode evidence is malformed") from error
        if not isinstance(payload, dict) or canonical_hash(payload) != episode.content_hash:
            raise RuntimeError("Artifact episode evidence hash changed")
        roots = tuple(sorted(set(episode.source_evidence_ids)))
        if roots != episode.source_evidence_ids or not roots:
            raise RuntimeError("Artifact episode evidence ancestry is invalid")
        placeholders = ",".join("?" for _ in roots)
        found = connection.execute(
            f"SELECT COUNT(*) AS count FROM evidence WHERE id IN ({placeholders})",
            roots,
        ).fetchone()
        if found is None or int(found["count"]) != len(roots):
            raise RuntimeError("Artifact episode references unknown source evidence")
        values = (
            episode.episode_id,
            "session-episode",
            episode.episode_id,
            episode.project_id,
            "session_episode",
            episode.started_at or None,
            episode.content_hash,
            episode.payload_json,
        )
        existing = connection.execute(
            """SELECT id,source_type,source_ref,project_id,kind,occurred_at,
            content_hash,payload_json FROM evidence WHERE id=?""",
            (episode.episode_id,),
        ).fetchone()
        if existing is not None:
            if tuple(existing[key] for key in (
                "id",
                "source_type",
                "source_ref",
                "project_id",
                "kind",
                "occurred_at",
                "content_hash",
                "payload_json",
            )) != values:
                raise RuntimeError("Artifact episode evidence identity changed")
        else:
            connection.execute(
                """INSERT INTO evidence(
                id,source_type,source_ref,project_id,kind,occurred_at,
                content_hash,payload_json,status,created_at
                ) VALUES(?,?,?,?,?,?,?,?,'new',?)""",
                (*values, utc_now()),
            )
        connection.executemany(
            """INSERT OR IGNORE INTO evidence_derivations(
            derived_evidence_id,source_evidence_id
            ) VALUES(?,?)""",
            [(episode.episode_id, root) for root in roots],
        )
    _validate_artifact_episode_evidence(connection, artifact)


def _validate_artifact_episode_evidence(
    connection: sqlite3.Connection,
    artifact: RunArtifact,
) -> None:
    required = _proposal_evidence_refs(artifact)
    episodes = {item.episode_id: item for item in artifact.extraction.episodes}
    for evidence_id in sorted(required):
        if not evidence_id.startswith("ev-episode-"):
            continue
        episode = episodes.get(evidence_id)
        if episode is None or not episode.payload_json or not episode.content_hash:
            raise RuntimeError("Artifact proposal episode evidence is incomplete")
        row = connection.execute(
            """SELECT id,source_type,source_ref,project_id,kind,occurred_at,
            content_hash,payload_json FROM evidence WHERE id=?""",
            (evidence_id,),
        ).fetchone()
        expected = (
            episode.episode_id,
            "session-episode",
            episode.episode_id,
            episode.project_id,
            "session_episode",
            episode.started_at or None,
            episode.content_hash,
            episode.payload_json,
        )
        if row is None or tuple(row[key] for key in (
            "id",
            "source_type",
            "source_ref",
            "project_id",
            "kind",
            "occurred_at",
            "content_hash",
            "payload_json",
        )) != expected:
            raise RuntimeError("Artifact proposal episode evidence was not persisted")
        ancestry = tuple(
            str(item["source_evidence_id"])
            for item in connection.execute(
                """SELECT source_evidence_id FROM evidence_derivations
                WHERE derived_evidence_id=? ORDER BY source_evidence_id""",
                (evidence_id,),
            ).fetchall()
        )
        if ancestry != episode.source_evidence_ids:
            raise RuntimeError("Artifact proposal episode ancestry changed")


def _proposal_evidence_refs(artifact: RunArtifact) -> set[str]:
    return {
        evidence_ref
        for item in artifact.memory_plan.items
        for evidence_ref in item.evidence_refs
    } | {
        evidence_ref
        for item in artifact.extraction.procedures
        for evidence_ref in item.evidence_refs
    }


def _validate_checkpoint_evidence(
    connection: sqlite3.Connection,
    checkpoints: tuple[CheckpointAdvance, ...],
) -> None:
    for checkpoint in checkpoints:
        for evidence_id in checkpoint.evidence_ids:
            row = connection.execute(
                """SELECT cursor,fingerprint FROM evidence_checkpoints
                WHERE evidence_id=? AND source_key=?""",
                (evidence_id, checkpoint.source_key),
            ).fetchone()
            if row is None or (
                row["cursor"],
                row["fingerprint"],
            ) != (checkpoint.cursor, checkpoint.fingerprint):
                raise RuntimeError(
                    "Apply checkpoint is not backed by authoritative evidence"
                )


def _validate_manifest_evidence_snapshot(
    connection: sqlite3.Connection,
    manifest: EvidenceManifest,
) -> None:
    expected_hashes = _manifest_evidence_hashes(manifest)
    if not expected_hashes:
        return
    placeholders = ",".join("?" for _ in expected_hashes)
    rows = connection.execute(
        f"SELECT id,content_hash FROM evidence WHERE id IN ({placeholders})",
        sorted(expected_hashes),
    ).fetchall()
    found = {str(row["id"]) for row in rows}
    if found != set(expected_hashes):
        raise RuntimeError("Apply manifest cites unavailable evidence")
    if any(
        str(row["content_hash"]) != expected_hashes[str(row["id"])]
        for row in rows
    ):
        raise RuntimeError("Apply evidence snapshot changed")


def _manifest_evidence_hashes(manifest: EvidenceManifest) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for item in manifest.entries:
        hashes[item.evidence_id] = item.content_hash
        hashes.update(dict(item.source_content_hashes))
    return hashes


def _evidence_ancestor_closure(
    connection: sqlite3.Connection,
    evidence_ids: set[str],
) -> set[str]:
    closure = set(evidence_ids)
    frontier = set(evidence_ids)
    while frontier:
        placeholders = ",".join("?" for _ in frontier)
        rows = connection.execute(
            f"""SELECT source_evidence_id FROM evidence_derivations
            WHERE derived_evidence_id IN ({placeholders})""",
            sorted(frontier),
        ).fetchall()
        parents = {str(row["source_evidence_id"]) for row in rows} - closure
        closure.update(parents)
        frontier = parents
    return closure


def _advance_checkpoint(
    connection: sqlite3.Connection,
    checkpoint: CheckpointAdvance,
) -> None:
    existing = connection.execute(
        "SELECT cursor,fingerprint FROM checkpoints WHERE source_key=?",
        (checkpoint.source_key,),
    ).fetchone()
    if existing is not None:
        current = _cursor_position(existing["cursor"])
        incoming = _cursor_position(checkpoint.cursor)
        if incoming < current or (
            incoming == current
            and (
                existing["cursor"],
                existing["fingerprint"],
            )
            != (checkpoint.cursor, checkpoint.fingerprint)
        ):
            raise RuntimeError("Apply checkpoint would conflict or move backward")
    connection.execute(
        """INSERT INTO checkpoints(source_key,cursor,fingerprint,updated_at)
        VALUES(?,?,?,?) ON CONFLICT(source_key) DO UPDATE SET
        cursor=excluded.cursor,fingerprint=excluded.fingerprint,updated_at=excluded.updated_at""",
        (
            checkpoint.source_key,
            checkpoint.cursor,
            checkpoint.fingerprint,
            utc_now(),
        ),
    )


def _cursor_position(cursor: str | None) -> tuple[int, int]:
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


def _validate_state_intent(
    artifact: RunArtifact,
    *,
    manifest: ApplyStateManifest,
    idempotency_key: str,
    receipt: ApplyReceipt,
) -> None:
    if (
        manifest.run_kind != artifact.run_kind
        or manifest.period != artifact.period
        or manifest.evidence != artifact.evidence_manifest
        or receipt.idempotency_key != idempotency_key
        or receipt.run_fingerprint != artifact.run_fingerprint
        or receipt.artifact_fingerprint != artifact.artifact_fingerprint
        or receipt.changed_paths != manifest.publication.changed_paths
    ):
        raise RuntimeError("Transactional apply manifest binding mismatch")


def _artifact_to_dict(artifact: RunArtifact) -> dict[str, Any]:
    return asdict(artifact)


def _artifact_from_dict(value: dict[str, Any]) -> RunArtifact:
    extraction = value["extraction"]
    return RunArtifact(
        run_kind=value["run_kind"],
        period=value["period"],
        mode=value["mode"],
        behavior_contract=value["behavior_contract"],
        model_contract=value["model_contract"],
        extraction_contract=value["extraction_contract"],
        evidence_manifest=_evidence_manifest_from_dict(value["evidence_manifest"]),
        run_fingerprint=value["run_fingerprint"],
        artifact_fingerprint=value["artifact_fingerprint"],
        extraction=ExtractionResult(
            status=extraction["status"],
            candidates=tuple(
                ExtractionCandidate(
                    **{
                        **item,
                        "evidence_refs": tuple(item["evidence_refs"]),
                    }
                )
                for item in extraction["candidates"]
            ),
            rejections=tuple(
                CandidateRejection(
                    **{
                        **item,
                        "evidence_refs": tuple(item["evidence_refs"]),
                    }
                )
                for item in extraction["rejections"]
            ),
            coverage=ExtractionCoverage(**extraction["coverage"]),
            usage=ExtractionUsage(**extraction["usage"]),
            failures=tuple(
                ExtractionFailure(
                    **{
                        **item,
                        "evidence_refs": tuple(item["evidence_refs"]),
                    }
                )
                for item in extraction["failures"]
            ),
            episodes=tuple(
                EpisodeReceipt(
                    **{
                        **item,
                        "source_evidence_ids": tuple(item["source_evidence_ids"]),
                    }
                )
                for item in extraction["episodes"]
            ),
            procedures=tuple(
                ProcedureCandidate(
                    **{
                        **item,
                        "prerequisites": tuple(item["prerequisites"]),
                        "steps": tuple(item["steps"]),
                        "failure_branches": tuple(item["failure_branches"]),
                        "tests": tuple(item["tests"]),
                        "evidence_refs": tuple(item["evidence_refs"]),
                    }
                )
                for item in extraction.get("procedures", [])
            ),
        ),
        memory_plan=_memory_plan_from_dict(value["memory_plan"]),
        session_summaries=tuple(
            _session_summary_from_dict(item) for item in value["session_summaries"]
        ),
        period_summary=_period_summary_from_dict(value["period_summary"]),
    )


def _memory_plan_from_dict(value: dict[str, Any]) -> MemoryMutationPlan:
    return MemoryMutationPlan(
        items=tuple(
            PlannedMemoryMutation(
                **{
                    **item,
                    "memory_key": MemoryKey(**item["memory_key"]),
                    "evidence_refs": tuple(item["evidence_refs"]),
                }
            )
            for item in value["items"]
        ),
        rejections=tuple(
            MemoryPlanRejection(**item) for item in value["rejections"]
        ),
    )


def _session_summary_from_dict(value: dict[str, Any]) -> SessionRunSummary:
    return SessionRunSummary(
        **{
            **value,
            "source_evidence_ids": tuple(value["source_evidence_ids"]),
            "operation_counts": tuple(tuple(item) for item in value["operation_counts"]),
            "kind_counts": tuple(tuple(item) for item in value["kind_counts"]),
            "subjects": tuple(value["subjects"]),
        }
    )


def _period_summary_from_dict(value: dict[str, Any]) -> PeriodRunSummary:
    return PeriodRunSummary(
        **{
            **value,
            "operation_counts": tuple(tuple(item) for item in value["operation_counts"]),
            "kind_counts": tuple(tuple(item) for item in value["kind_counts"]),
        }
    )


def _evidence_manifest_from_dict(value: dict[str, Any]) -> EvidenceManifest:
    return EvidenceManifest(
        entries=tuple(
            EvidenceManifestEntry(
                evidence_id=item["evidence_id"],
                source_evidence_ids=tuple(item["source_evidence_ids"]),
                kind=item["kind"],
                content_hash=item["content_hash"],
                source_content_hashes=tuple(
                    tuple(value) for value in item["source_content_hashes"]
                ),
            )
            for item in value["entries"]
        ),
        checkpoints=tuple(
            CheckpointAdvance(
                source_key=item["source_key"],
                cursor=item["cursor"],
                fingerprint=item["fingerprint"],
                evidence_ids=tuple(item["evidence_ids"]),
            )
            for item in value["checkpoints"]
        ),
    )


def _publication_from_dict(value: dict[str, Any]) -> PublicationManifest:
    return PublicationManifest(
        run_kind=value["run_kind"],
        period=value["period"],
        files=tuple(
            PublicationFile(
                **{
                    **item,
                    "changed_sections": tuple(item["changed_sections"]),
                }
            )
            for item in value["files"]
        ),
    )


def _journal_from_row(row: sqlite3.Row) -> ApplyJournalEntry:
    return ApplyJournalEntry(
        idempotency_key=row["idempotency_key"],
        run_fingerprint=row["run_fingerprint"],
        artifact_fingerprint=row["artifact_fingerprint"],
        authorization_id=row["authorization_id"],
        staging_id=row["staging_id"],
        publication=_publication_from_dict(json.loads(row["publication_json"])),
        state=row["state"],
    )


def _receipt_from_dict(value: dict[str, Any]) -> ApplyReceipt:
    return ApplyReceipt(
        **{
            **value,
            "changed_paths": tuple(value["changed_paths"]),
        }
    )


def _encode(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
