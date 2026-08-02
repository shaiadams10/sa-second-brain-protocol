import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from second_brain_protocol.apply_persistence import (
    SQLiteApplyJournal,
    SQLiteApplyStateCommitter,
    SQLiteCutoverAuthorizationStore,
    SQLiteRunArtifactStore,
)
from second_brain_protocol.atomic_apply import (
    ApplyReceipt,
    ApplyRequest,
    ApplyStateManifest,
    AtomicApplyGate,
    BoundedPublicationPolicy,
    CutoverAuthorization,
    JournaledApplyCoordinator,
)
from second_brain_protocol.evidence_compaction import compact_session_evidence
from second_brain_protocol.daily_weekly_runner import (
    CheckpointAdvance,
    DailyWeeklyRunner,
    EvidenceManifest,
    EvidenceManifestEntry,
    RunRequest,
)
from second_brain_protocol.extraction_harness import (
    EpisodeReceipt,
    ExtractionCandidate,
    ExtractionCoverage,
    ExtractionFailure,
    ExtractionResult,
    ExtractionUsage,
)
from second_brain_protocol.governed_publication import (
    DurableJournalPublicationPlanner,
)
from second_brain_protocol.publication_policy import (
    PublicationManifest,
    StagedCanonicalFile,
)
from second_brain_protocol.state import StateStore, canonical_hash, utc_now


PERSIST_HASH = "a" * 64


def test_artifact_store_removes_retired_shadow_table_and_rows(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    with store.connect() as connection:
        connection.execute(
            """CREATE TABLE shadow_run_artifacts(
            run_fingerprint TEXT PRIMARY KEY,
            artifact_fingerprint TEXT NOT NULL,
            artifact_json TEXT NOT NULL,
            created_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            "INSERT INTO shadow_run_artifacts VALUES(?,?,?,?)",
            ("retired", "retired", '{"mode":"shadow"}', utc_now()),
        )

    SQLiteRunArtifactStore(store)

    with store.connect() as connection:
        tables = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        retained = connection.execute(
            "SELECT COUNT(*) AS count FROM governed_run_artifacts"
        ).fetchone()["count"]
    assert "shadow_run_artifacts" not in tables
    assert "governed_run_artifacts" in tables
    assert retained == 0


class Extractor:
    def __init__(
        self,
        evidence_id: str = "ev-persist-session",
        source_evidence_ids: tuple[str, ...] = ("ev-persist-session",),
        episode_payload: dict | None = None,
        emit_candidate: bool = True,
    ) -> None:
        self.evidence_id = evidence_id
        self.source_evidence_ids = source_evidence_ids
        self.episode_payload = episode_payload
        self.emit_candidate = emit_candidate

    def extract(self, _request) -> ExtractionResult:
        return ExtractionResult(
            status="accepted",
            candidates=(
                ExtractionCandidate(
                    candidate_type="memory_mutation",
                    operation="create",
                    kind="preference",
                    subject="Durable recovery",
                    claim="Interrupted apply resumes from persisted state.",
                    scope="global",
                    project_id=None,
                    target_memory_id=None,
                    evidence_refs=(self.evidence_id,),
                    confidence=0.96,
                    explicit=True,
                ),
            )
            if self.emit_candidate
            else (),
            rejections=(),
            coverage=ExtractionCoverage(1, 1, 0, 0),
            usage=ExtractionUsage(10, 5, 15, 1),
            failures=(),
            episodes=(
                EpisodeReceipt(
                    episode_id=self.evidence_id,
                    source_evidence_ids=self.source_evidence_ids,
                    analysis_lane="profile_only",
                    project_id=None,
                    started_at="2026-08-01T10:00:00Z",
                    ended_at="2026-08-01T10:01:00Z",
                    message_count=1,
                    content_chars=40,
                    content_hash=(
                        canonical_hash(self.episode_payload)
                        if self.episode_payload is not None
                        else None
                    ),
                    payload_json=(
                        json.dumps(
                            self.episode_payload,
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        if self.episode_payload is not None
                        else None
                    ),
                ),
            ),
        )


class FailedThenSuccessfulExtractor:
    def __init__(self) -> None:
        self.calls = 0

    def extract(self, request) -> ExtractionResult:
        self.calls += 1
        result = Extractor().extract(request)
        if self.calls > 1:
            return result
        failure = ExtractionFailure(
            code="model-error",
            error_type="RuntimeError",
            evidence_refs=("ev-persist-session",),
        )
        return replace(
            result,
            status="partial",
            failures=(failure,),
            coverage=replace(result.coverage, failed_episodes=1),
        )


class DiskPrepared:
    def __init__(self, root: Path, staging_id: str) -> None:
        self.root = root
        self.staging_id = staging_id
        data = json.loads(
            (root / "staging" / staging_id / "files.json").read_text(encoding="utf-8")
        )
        self.staged_files = tuple(StagedCanonicalFile(**item) for item in data)

    def commit(self, manifest: PublicationManifest) -> None:
        staged_by_path = {item.path: item for item in self.staged_files}
        pending_writes = []
        for file in manifest.files:
            item = staged_by_path[file.path]
            target = self.root / "canonical" / item.path
            current_hash = None
            if target.is_file():
                import hashlib

                current_hash = hashlib.sha256(target.read_bytes()).hexdigest()
            if current_hash == file.content_hash:
                continue
            if current_hash != file.before_hash:
                raise RuntimeError("canonical preimage changed")
            pending_writes.append((target, item.after_text))
        for target, after_text in pending_writes:
            target.parent.mkdir(parents=True, exist_ok=True)
            pending = target.with_suffix(target.suffix + ".next")
            with pending.open("w", encoding="utf-8", newline="") as handle:
                handle.write(after_text)
            pending.replace(target)

    def rollback(self) -> None:
        return None

    def verify_committed(self, manifest: PublicationManifest) -> bool:
        for file in manifest.files:
            target = self.root / "canonical" / file.path
            if not target.is_file():
                return False
            import hashlib

            if hashlib.sha256(target.read_bytes()).hexdigest() != file.content_hash:
                return False
        return True


class DiskPublisher:
    def __init__(self, root: Path) -> None:
        self.root = root

    def prepare(self, _artifact, *, idempotency_key: str) -> DiskPrepared:
        staging_id = f"staging-{idempotency_key}"
        folder = self.root / "staging" / staging_id
        folder.mkdir(parents=True, exist_ok=True)
        manifest_path = folder / "files.json"
        if not manifest_path.exists():
            canonical = self.root / "canonical" / "Identity" / "Preferences.md"
            before = canonical.read_text(encoding="utf-8")
            files = (
                StagedCanonicalFile(
                    path="Identity/Preferences.md",
                    before_text=before,
                    after_text=before.replace("\nold\n", "\nnew\n"),
                ),
            )
            manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "path": item.path,
                            "before_text": item.before_text,
                            "after_text": item.after_text,
                        }
                        for item in files
                    ],
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
        return DiskPrepared(self.root, staging_id)

    def open(self, staging_id: str) -> DiskPrepared:
        return DiskPrepared(self.root, staging_id)


class CrashBeforeCommitPublisher(DiskPublisher):
    def open(self, staging_id: str) -> DiskPrepared:
        prepared = super().open(staging_id)

        def crash(_manifest) -> None:
            raise RuntimeError("simulated death before file commit")

        prepared.commit = crash
        return prepared


class FailBeforeStateCommit:
    def __init__(self, delegate: SQLiteApplyStateCommitter) -> None:
        self.delegate = delegate

    def get_receipt(self, idempotency_key: str):
        return self.delegate.get_receipt(idempotency_key)

    def preflight(self, artifact, *, manifest) -> None:
        self.delegate.preflight(artifact, manifest=manifest)

    def commit(self, *_args, **_kwargs) -> None:
        raise RuntimeError("simulated process death before state commit")


def _seed_evidence(store: StateStore) -> None:
    with store.connect() as connection:
        connection.execute(
            """INSERT INTO evidence(
            id,source_type,source_ref,project_id,kind,occurred_at,content_hash,
            payload_json,status,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                "ev-persist-session",
                "session-episode",
                "session:test",
                None,
                "session_episode",
                "2026-08-01T10:00:00Z",
                PERSIST_HASH,
                "{}",
                "new",
                utc_now(),
            ),
        )
        connection.execute(
            """INSERT INTO evidence_checkpoints(
            evidence_id,source_key,cursor,fingerprint
            ) VALUES(?,?,?,?)""",
            (
                "ev-persist-session",
                "codex:persist-session",
                '{"kind":"jsonl","offset":500}',
                "persist-source-v1",
            ),
        )


def _request() -> RunRequest:
    return RunRequest(
        run_kind="daily",
        period="2026-08-01",
        model_contract="test-model-v1",
        evidence_manifest=EvidenceManifest(
            entries=(
                EvidenceManifestEntry(
                    evidence_id="ev-persist-session",
                    source_evidence_ids=("ev-persist-session",),
                    kind="session_episode",
                    content_hash=PERSIST_HASH,
                    source_content_hashes=(("ev-persist-session", PERSIST_HASH),),
                ),
            ),
            checkpoints=(
                CheckpointAdvance(
                    source_key="codex:persist-session",
                    cursor='{"kind":"jsonl","offset":500}',
                    fingerprint="persist-source-v1",
                    evidence_ids=("ev-persist-session",),
                ),
            ),
        ),
        evidence=(
            {
                "id": "ev-persist-session",
                "source_type": "session-episode",
                "kind": "session_episode",
                "project_id": None,
                "content_hash": PERSIST_HASH,
                "source_content_hashes": {"ev-persist-session": PERSIST_HASH},
                "payload": {"analysis_lane": "profile_only", "project_ids": []},
            },
        ),
    )


def test_interrupted_apply_recovers_after_reopening_all_durable_adapters(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "state.sqlite"
    first_store = StateStore(state_path)
    _seed_evidence(first_store)
    artifact_store = SQLiteRunArtifactStore(first_store)
    receipt = DailyWeeklyRunner(
        extractor=Extractor(), artifact_store=artifact_store
    ).run(_request())
    artifact = artifact_store.get(receipt.fingerprint)
    assert artifact is not None
    authorization = CutoverAuthorization(
        authorization_id="durable-cutover-1",
        contract_version="daily-weekly-cutover-v2",
        approved=True,
        run_fingerprint=receipt.fingerprint,
        run_kind=receipt.run_kind,
        period=receipt.period,
        artifact_fingerprint=receipt.artifact_fingerprint,
        behavior_contract=artifact.behavior_contract,
    )
    authorizations = SQLiteCutoverAuthorizationStore(first_store)
    authorizations.issue(authorization)
    canonical = tmp_path / "publication" / "canonical" / "Identity" / "Preferences.md"
    canonical.parent.mkdir(parents=True)
    with canonical.open("w", encoding="utf-8", newline="") as handle:
        handle.write("""<!-- sb:generated preferences:start -->
old
<!-- sb:generated preferences:end -->

manual
""")
    publisher = DiskPublisher(tmp_path / "publication")
    first_gate = AtomicApplyGate(
        artifact_store=artifact_store,
        authorization_store=authorizations,
        coordinator=JournaledApplyCoordinator(
            publisher=publisher,
            journal=SQLiteApplyJournal(first_store),
            state_committer=FailBeforeStateCommit(
                SQLiteApplyStateCommitter(first_store)
            ),
            publication_policy=BoundedPublicationPolicy(
                owned_sections=("preferences",)
            ),
        ),
    )

    with pytest.raises(RuntimeError, match="process death"):
        first_gate.apply(ApplyRequest(receipt, authorization.authorization_id))

    reopened_store = StateStore(state_path)
    reopened_artifacts = SQLiteRunArtifactStore(reopened_store)
    assert reopened_artifacts.get(receipt.fingerprint) == artifact
    reopened_journal = SQLiteApplyJournal(reopened_store)
    entry = reopened_journal.get(next(iter(_journal_keys(reopened_store))))
    assert entry is not None and entry.state == "files_committed"
    second_gate = AtomicApplyGate(
        artifact_store=reopened_artifacts,
        authorization_store=SQLiteCutoverAuthorizationStore(reopened_store),
        coordinator=JournaledApplyCoordinator(
            publisher=DiskPublisher(tmp_path / "publication"),
            journal=reopened_journal,
            state_committer=SQLiteApplyStateCommitter(reopened_store),
            publication_policy=BoundedPublicationPolicy(
                owned_sections=("preferences",)
            ),
        ),
    )

    applied = second_gate.apply(ApplyRequest(receipt, authorization.authorization_id))
    replay = second_gate.apply(ApplyRequest(receipt, authorization.authorization_id))

    assert applied == replay
    assert "\nnew\n" in canonical.read_text(encoding="utf-8")
    assert reopened_store.checkpoint("codex:persist-session")["cursor"] == (
        '{"kind":"jsonl","offset":500}'
    )
    assert [item["path"] for item in reopened_store.search_refresh_batch()] == [
        "Identity/Preferences.md"
    ]
    completed = reopened_journal.get(applied.idempotency_key)
    assert completed is not None and completed.state == "completed"
    with reopened_store.connect() as connection:
        status = connection.execute(
            "SELECT status FROM evidence WHERE id='ev-persist-session'"
        ).fetchone()["status"]
    assert status == "processed"


def test_durable_artifact_store_retries_and_replaces_only_blocked_attempt(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    _seed_evidence(store)
    artifacts = SQLiteRunArtifactStore(store)
    extractor = FailedThenSuccessfulExtractor()
    runner = DailyWeeklyRunner(extractor=extractor, artifact_store=artifacts)

    first = runner.run(_request())
    second = runner.run(_request())
    replay = runner.run(_request())

    assert first.status == "blocked"
    assert second.checkpoint_eligible is True
    assert replay == second
    assert extractor.calls == 2
    stored = artifacts.get(second.fingerprint)
    assert stored is not None
    assert stored.artifact_fingerprint == second.artifact_fingerprint


def test_sqlite_authorization_binding_is_concurrency_safe(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    authorizations = SQLiteCutoverAuthorizationStore(store)
    authorization = CutoverAuthorization(
        authorization_id="concurrent-auth",
        contract_version="daily-weekly-cutover-v2",
        approved=True,
        run_fingerprint="run-one",
        run_kind="daily",
        period="2026-08-01",
        artifact_fingerprint="artifact-one",
        behavior_contract="behavior-one",
    )
    authorizations.issue(authorization)

    def bind(operation_key: str) -> str:
        try:
            SQLiteCutoverAuthorizationStore(
                StateStore(tmp_path / "state.sqlite")
            ).bind_once("concurrent-auth", operation_key=operation_key)
            return "bound"
        except RuntimeError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = sorted(executor.map(bind, ("operation-one", "operation-two")))

    assert outcomes == ["bound", "rejected"]


def test_state_commit_accepts_sources_compacted_by_session_compaction(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    source_ids: list[str] = []
    for index, role, text in (
        (0, "user", "Remember this durable preference."),
        (1, "assistant", "Implemented and tests passed."),
    ):
        evidence_id, _ = store.add_evidence(
            source_type="codex",
            source_ref=f"codex:apply-session:{index}",
            kind="visible_message",
            payload={
                "session_id": "apply-session",
                "role": role,
                "text": text,
            },
            occurred_at=f"2026-08-01T10:0{index}:00Z",
        )
        source_ids.append(evidence_id)
    assert compact_session_evidence(store) == {
        "session_digests_created": 1,
        "source_records_compacted": 2,
    }
    digest = next(
        item
        for item in store.evidence(status="new")
        if item["kind"] == "session_digest"
    )
    digest_id = str(digest["id"])
    source_ids = list(digest["payload"]["source_evidence_ids"])
    source_hashes = {
        str(item["id"]): str(item["content_hash"])
        for item in store.evidence_by_ids(source_ids)
    }
    store.mark_evidence([source_ids[1]], "processed")
    store.attach_checkpoint_candidate(
        source_ids[0],
        source_key="codex:apply-session",
        cursor='{"kind":"jsonl","offset":900}',
        fingerprint="apply-session-v1",
    )
    request = RunRequest(
        run_kind="daily",
        period="2026-08-01",
        model_contract="test-model-v1",
        evidence_manifest=EvidenceManifest(
            entries=(
                EvidenceManifestEntry(
                    evidence_id=digest_id,
                    source_evidence_ids=tuple(source_ids),
                    kind="session_digest",
                    content_hash=str(digest["content_hash"]),
                    source_content_hashes=tuple(sorted(source_hashes.items())),
                ),
            ),
            checkpoints=(
                CheckpointAdvance(
                    source_key="codex:apply-session",
                    cursor='{"kind":"jsonl","offset":900}',
                    fingerprint="apply-session-v1",
                    evidence_ids=(source_ids[0],),
                ),
            ),
        ),
        evidence=(dict(digest, source_content_hashes=source_hashes),),
    )
    artifact_store = SQLiteRunArtifactStore(store)
    run_receipt = DailyWeeklyRunner(
        extractor=Extractor(digest_id, tuple(source_ids)),
        artifact_store=artifact_store,
    ).run(request)
    artifact = artifact_store.get(run_receipt.fingerprint)
    assert artifact is not None
    publication = PublicationManifest(
        run_kind="daily",
        period="2026-08-01",
        files=(),
    )
    apply_receipt = ApplyReceipt(
        idempotency_key="compacted-session-apply",
        run_fingerprint=artifact.run_fingerprint,
        artifact_fingerprint=artifact.artifact_fingerprint,
        authorization_id="test-authorization",
        changed_paths=(),
        published=True,
        checkpoint_advanced=True,
    )

    SQLiteApplyStateCommitter(store).commit(
        artifact,
        manifest=ApplyStateManifest(
            run_kind="daily",
            period="2026-08-01",
            evidence=request.evidence_manifest,
            publication=publication,
        ),
        idempotency_key=apply_receipt.idempotency_key,
        receipt=apply_receipt,
    )

    statuses = {
        item["id"]: item["status"]
        for item in store.evidence()
        if item["id"] in {digest_id, *source_ids}
    }
    assert statuses == {
        digest_id: "processed",
        **{source_id: "processed" for source_id in source_ids},
    }
    assert store.checkpoint("codex:apply-session")["cursor"] == (
        '{"kind":"jsonl","offset":900}'
    )


def test_apply_preflight_persists_artifact_bound_episode_evidence(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    _seed_evidence(store)
    episode_id = "ev-episode-aaaaaaaaaaaaaaaaaaaaaaaa"
    episode_payload = {
        "analysis_lane": "profile_only",
        "project_ids": [],
        "source_evidence_ids": ["ev-persist-session"],
        "started_at": "2026-08-01T10:00:00Z",
        "ended_at": "2026-08-01T10:01:00Z",
        "user_messages": [
            {
                "occurred_at": "2026-08-01T10:00:00Z",
                "text": "A sanitized, bounded episode.",
            }
        ],
        "assistant_results": [],
    }
    artifacts = SQLiteRunArtifactStore(store)
    receipt = DailyWeeklyRunner(
        extractor=Extractor(
            episode_id,
            ("ev-persist-session",),
            episode_payload,
        ),
        artifact_store=artifacts,
    ).run(_request())
    artifact = artifacts.get(receipt.fingerprint)
    assert artifact is not None and receipt.checkpoint_eligible
    publication = PublicationManifest(
        run_kind="daily",
        period="2026-08-01",
        files=(),
    )

    SQLiteApplyStateCommitter(store).preflight(
        artifact,
        manifest=ApplyStateManifest(
            run_kind="daily",
            period="2026-08-01",
            evidence=artifact.evidence_manifest,
            publication=publication,
        ),
    )

    with store.connect() as connection:
        row = connection.execute(
            "SELECT content_hash,payload_json,status FROM evidence WHERE id=?",
            (episode_id,),
        ).fetchone()
        ancestry = connection.execute(
            """SELECT source_evidence_id FROM evidence_derivations
            WHERE derived_evidence_id=?""",
            (episode_id,),
        ).fetchall()
    assert row is not None
    assert row["content_hash"] == canonical_hash(episode_payload)
    assert json.loads(row["payload_json"]) == episode_payload
    assert row["status"] == "new"
    assert [item["source_evidence_id"] for item in ancestry] == ["ev-persist-session"]


def test_successful_apply_processes_persisted_episode_without_proposal(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    _seed_evidence(store)
    episode_id = "ev-episode-bbbbbbbbbbbbbbbbbbbbbbbb"
    episode_payload = {
        "analysis_lane": "profile_only",
        "project_ids": [],
        "source_evidence_ids": ["ev-persist-session"],
        "started_at": "2026-08-01T10:00:00Z",
        "ended_at": "2026-08-01T10:01:00Z",
        "user_messages": [],
        "assistant_results": [],
    }
    artifacts = SQLiteRunArtifactStore(store)
    run_receipt = DailyWeeklyRunner(
        extractor=Extractor(
            episode_id,
            ("ev-persist-session",),
            episode_payload,
            emit_candidate=False,
        ),
        artifact_store=artifacts,
    ).run(_request())
    artifact = artifacts.get(run_receipt.fingerprint)
    assert artifact is not None and run_receipt.checkpoint_eligible
    publication = PublicationManifest(
        run_kind="daily",
        period="2026-08-01",
        files=(),
    )
    manifest = ApplyStateManifest(
        run_kind="daily",
        period="2026-08-01",
        evidence=artifact.evidence_manifest,
        publication=publication,
    )
    committer = SQLiteApplyStateCommitter(store)
    committer.preflight(artifact, manifest=manifest)
    receipt = ApplyReceipt(
        idempotency_key="no-proposal-episode-apply",
        run_fingerprint=artifact.run_fingerprint,
        artifact_fingerprint=artifact.artifact_fingerprint,
        authorization_id="test-authorization",
        changed_paths=(),
        published=True,
        checkpoint_advanced=True,
    )

    committer.commit(
        artifact,
        manifest=manifest,
        idempotency_key=receipt.idempotency_key,
        receipt=receipt,
    )

    with store.connect() as connection:
        statuses = {
            str(row["id"]): str(row["status"])
            for row in connection.execute(
                "SELECT id,status FROM evidence WHERE id IN (?,?)",
                ("ev-persist-session", episode_id),
            ).fetchall()
        }
    assert statuses == {
        "ev-persist-session": "processed",
        episode_id: "processed",
    }


def test_state_commit_rejects_evidence_changed_after_extraction(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    _seed_evidence(store)
    artifact_store = SQLiteRunArtifactStore(store)
    run_receipt = DailyWeeklyRunner(
        extractor=Extractor(),
        artifact_store=artifact_store,
    ).run(_request())
    artifact = artifact_store.get(run_receipt.fingerprint)
    assert artifact is not None
    store.reassign_evidence_project("ev-persist-session", "project-changed")
    publication = PublicationManifest(
        run_kind="daily",
        period="2026-08-01",
        files=(),
    )
    receipt = ApplyReceipt(
        idempotency_key="changed-evidence-apply",
        run_fingerprint=artifact.run_fingerprint,
        artifact_fingerprint=artifact.artifact_fingerprint,
        authorization_id="test-authorization",
        changed_paths=(),
        published=True,
        checkpoint_advanced=True,
    )

    with pytest.raises(RuntimeError, match="evidence snapshot changed"):
        SQLiteApplyStateCommitter(store).commit(
            artifact,
            manifest=ApplyStateManifest(
                run_kind="daily",
                period="2026-08-01",
                evidence=artifact.evidence_manifest,
                publication=publication,
            ),
            idempotency_key=receipt.idempotency_key,
            receipt=receipt,
        )

    assert store.checkpoint("codex:persist-session") is None
    with store.connect() as connection:
        assert (
            connection.execute(
                "SELECT status FROM evidence WHERE id='ev-persist-session'"
            ).fetchone()["status"]
            == "new"
        )


def test_apply_preflight_rejects_changed_evidence_before_file_commit(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    _seed_evidence(store)
    artifacts = SQLiteRunArtifactStore(store)
    run_receipt = DailyWeeklyRunner(
        extractor=Extractor(),
        artifact_store=artifacts,
    ).run(_request())
    artifact = artifacts.get(run_receipt.fingerprint)
    assert artifact is not None
    authorization = CutoverAuthorization(
        authorization_id="changed-evidence-cutover",
        contract_version="daily-weekly-cutover-v2",
        approved=True,
        run_fingerprint=artifact.run_fingerprint,
        run_kind=artifact.run_kind,
        period=artifact.period,
        artifact_fingerprint=artifact.artifact_fingerprint,
        behavior_contract=artifact.behavior_contract,
    )
    authorizations = SQLiteCutoverAuthorizationStore(store)
    authorizations.issue(authorization)
    publication_root = tmp_path / "publication"
    canonical = publication_root / "canonical" / "Identity" / "Preferences.md"
    canonical.parent.mkdir(parents=True)
    original = """<!-- sb:generated preferences:start -->
old
<!-- sb:generated preferences:end -->

manual
"""
    with canonical.open("w", encoding="utf-8", newline="") as handle:
        handle.write(original)
    store.reassign_evidence_project("ev-persist-session", "project-changed")
    gate = AtomicApplyGate(
        artifact_store=artifacts,
        authorization_store=authorizations,
        coordinator=JournaledApplyCoordinator(
            publisher=DiskPublisher(publication_root),
            journal=SQLiteApplyJournal(store),
            state_committer=SQLiteApplyStateCommitter(store),
            publication_policy=BoundedPublicationPolicy(
                owned_sections=("preferences",)
            ),
        ),
    )

    with pytest.raises(RuntimeError, match="evidence snapshot changed"):
        gate.apply(ApplyRequest(run_receipt, authorization.authorization_id))

    assert canonical.read_text(encoding="utf-8") == original
    assert store.checkpoint("codex:persist-session") is None


def test_reopened_prepared_apply_preserves_intervening_owner_edit(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "state.sqlite"
    store = StateStore(state_path)
    _seed_evidence(store)
    artifact_store = SQLiteRunArtifactStore(store)
    receipt = DailyWeeklyRunner(
        extractor=Extractor(), artifact_store=artifact_store
    ).run(_request())
    artifact = artifact_store.get(receipt.fingerprint)
    assert artifact is not None
    authorization = CutoverAuthorization(
        authorization_id="preimage-cutover",
        contract_version="daily-weekly-cutover-v2",
        approved=True,
        run_fingerprint=receipt.fingerprint,
        run_kind=receipt.run_kind,
        period=receipt.period,
        artifact_fingerprint=receipt.artifact_fingerprint,
        behavior_contract=artifact.behavior_contract,
    )
    authorizations = SQLiteCutoverAuthorizationStore(store)
    authorizations.issue(authorization)
    publication_root = tmp_path / "publication"
    canonical = publication_root / "canonical" / "Identity" / "Preferences.md"
    canonical.parent.mkdir(parents=True)
    original = """<!-- sb:generated preferences:start -->
old
<!-- sb:generated preferences:end -->

manual
"""
    with canonical.open("w", encoding="utf-8", newline="") as handle:
        handle.write(original)
    first_gate = AtomicApplyGate(
        artifact_store=artifact_store,
        authorization_store=authorizations,
        coordinator=JournaledApplyCoordinator(
            publisher=CrashBeforeCommitPublisher(publication_root),
            journal=SQLiteApplyJournal(store),
            state_committer=SQLiteApplyStateCommitter(store),
            publication_policy=BoundedPublicationPolicy(
                owned_sections=("preferences",)
            ),
        ),
    )

    with pytest.raises(RuntimeError, match="death before file commit"):
        first_gate.apply(ApplyRequest(receipt, authorization.authorization_id))

    with canonical.open("w", encoding="utf-8", newline="") as handle:
        handle.write(original.replace("manual", "owner edit"))
    reopened = StateStore(state_path)
    second_gate = AtomicApplyGate(
        artifact_store=SQLiteRunArtifactStore(reopened),
        authorization_store=SQLiteCutoverAuthorizationStore(reopened),
        coordinator=JournaledApplyCoordinator(
            publisher=DiskPublisher(publication_root),
            journal=SQLiteApplyJournal(reopened),
            state_committer=SQLiteApplyStateCommitter(reopened),
            publication_policy=BoundedPublicationPolicy(
                owned_sections=("preferences",)
            ),
        ),
    )

    with pytest.raises(RuntimeError, match="canonical preimage changed"):
        second_gate.apply(ApplyRequest(receipt, authorization.authorization_id))

    assert "owner edit" in canonical.read_text(encoding="utf-8")
    assert reopened.checkpoint("codex:persist-session") is None
    with reopened.connect() as connection:
        status = connection.execute(
            "SELECT status FROM evidence WHERE id='ev-persist-session'"
        ).fetchone()["status"]
    assert status == "new"


def _journal_keys(store: StateStore) -> list[str]:
    with store.connect() as connection:
        return [
            str(row["idempotency_key"])
            for row in connection.execute(
                "SELECT idempotency_key FROM atomic_apply_journal"
            ).fetchall()
        ]


def test_production_journal_planner_replaces_only_the_authorized_daily_section(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    daily = vault / "Journal" / "Daily" / "2026-08-01.md"
    daily.parent.mkdir(parents=True)
    daily.write_text(
        """---
id: daily-2026-08-01
type: daily
created: 2026-08-01
---

# 2026-08-01

## Personal notes

Owner prose stays here.

## Automated summary

<!-- sb:generated daily:start -->
old repeated insight
<!-- sb:generated daily:end -->
""",
        encoding="utf-8",
    )
    store = StateStore(tmp_path / "state.sqlite")
    _seed_evidence(store)
    artifacts = SQLiteRunArtifactStore(store)
    run_receipt = DailyWeeklyRunner(
        extractor=Extractor(),
        artifact_store=artifacts,
    ).run(
        replace(
            _request(),
            mode="authoritative",
            behavior_contract="daily-weekly-governed-v3",
        )
    )
    artifact = artifacts.get(run_receipt.fingerprint)
    assert artifact is not None
    authorization = CutoverAuthorization(
        authorization_id="owner-cutover-daily",
        contract_version="daily-weekly-cutover-v2",
        approved=True,
        run_fingerprint=artifact.run_fingerprint,
        run_kind=artifact.run_kind,
        period=artifact.period,
        artifact_fingerprint=artifact.artifact_fingerprint,
        behavior_contract=artifact.behavior_contract,
    )
    authorizations = SQLiteCutoverAuthorizationStore(store)
    authorizations.issue(authorization)
    gate = AtomicApplyGate(
        artifact_store=artifacts,
        authorization_store=authorizations,
        coordinator=JournaledApplyCoordinator(
            publisher=DurableJournalPublicationPlanner(
                vault=vault,
                staging_root=tmp_path / "staging",
            ),
            journal=SQLiteApplyJournal(store),
            state_committer=SQLiteApplyStateCommitter(store),
            publication_policy=BoundedPublicationPolicy(owned_sections=("daily",)),
        ),
    )

    applied = gate.apply(ApplyRequest(run_receipt, authorization.authorization_id))
    replay = gate.apply(ApplyRequest(run_receipt, authorization.authorization_id))

    text = daily.read_text(encoding="utf-8")
    assert applied == replay
    assert "Owner prose stays here." in text
    assert "old repeated insight" not in text
    assert "Interrupted apply resumes from persisted state." in text
    assert "Pending review" in text
    assert "Session extraction" in text
    assert "Subjects: Durable recovery." in text
