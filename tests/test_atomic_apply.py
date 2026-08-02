from dataclasses import replace

import pytest

from second_brain_protocol.atomic_apply import (
    ApplyRequest,
    AtomicApplyGate,
    BoundedPublicationPolicy,
    CutoverAuthorization,
    InMemoryApplyJournal,
    InMemoryCutoverAuthorizationStore,
    JournaledApplyCoordinator,
)
from second_brain_protocol.daily_weekly_runner import (
    CheckpointAdvance,
    DailyWeeklyRunner,
    EvidenceManifest,
    EvidenceManifestEntry,
    InMemoryRunArtifactStore,
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
from second_brain_protocol.publication_policy import (
    StagedCanonicalFile,
)


EVIDENCE_HASH = "a" * 64


class Extractor:
    def __init__(self, *, failed: bool = False) -> None:
        self.failed = failed

    def extract(self, _request) -> ExtractionResult:
        failures = (
            ExtractionFailure(
                code="model-error",
                error_type="RuntimeError",
                evidence_refs=("ev-episode-atomic",),
            ),
        ) if self.failed else ()
        return ExtractionResult(
            status="partial" if failures else "accepted",
            candidates=(
                ExtractionCandidate(
                    candidate_type="memory_mutation",
                    operation="create",
                    kind="preference",
                    subject="Atomic publication",
                    claim="Canonical writes and checkpoints commit through one gate.",
                    scope="global",
                    project_id=None,
                    target_memory_id=None,
                    evidence_refs=("ev-episode-atomic",),
                    confidence=0.95,
                    explicit=True,
                ),
            ),
            rejections=(),
            coverage=ExtractionCoverage(1, 1, 0, len(failures)),
            usage=ExtractionUsage(10, 5, 15, 1),
            failures=failures,
            episodes=(
                EpisodeReceipt(
                    episode_id="ev-episode-atomic",
                    source_evidence_ids=("ev-session-atomic",),
                    analysis_lane="profile_only",
                    project_id=None,
                    started_at="2026-08-01T10:00:00Z",
                    ended_at="2026-08-01T10:01:00Z",
                    message_count=1,
                    content_chars=50,
                ),
            ),
        )


class Prepared:
    changed_paths = ("Journal/Daily/2026-08-01.md", "Memory/Preferences.md")

    def __init__(self, events: list[str], staging_id: str) -> None:
        self.events = events
        self.staging_id = staging_id
        self.committed = False
        self.committed_valid = True
        self.precondition_valid = True
        existing = """<!-- sb:generated preferences:start -->
old
<!-- sb:generated preferences:end -->

manual
"""
        self.staged_files = (
            StagedCanonicalFile(
                path="Journal/Daily/2026-08-01.md",
                before_text=None,
                after_text="""<!-- sb:generated daily-summary:start -->
summary
<!-- sb:generated daily-summary:end -->
""",
            ),
            StagedCanonicalFile(
                path="Memory/Preferences.md",
                before_text=existing,
                after_text=existing.replace("\nold\n", "\nnew\n"),
            ),
        )

    def commit(self, _manifest) -> None:
        if not self.committed:
            if not self.precondition_valid:
                raise RuntimeError("canonical preimage changed")
            self.events.append("files-commit")
            self.committed = True

    def rollback(self) -> None:
        self.events.append("staging-rollback")

    def verify_committed(self, manifest) -> bool:
        self.events.append("files-verify")
        return self.committed and self.committed_valid and bool(manifest.files)


class Publisher:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.prepared_by_id: dict[str, Prepared] = {}

    def prepare(self, _artifact, *, idempotency_key: str):
        self.events.append("prepare")
        staging_id = f"staging-{idempotency_key}"
        prepared = self.prepared_by_id.get(staging_id)
        if prepared is None:
            prepared = Prepared(self.events, staging_id)
            self.prepared_by_id[staging_id] = prepared
        return prepared

    def open(self, staging_id: str):
        return self.prepared_by_id[staging_id]


class StateCommitter:
    def __init__(self, events: list[str], *, fail_once: bool = False) -> None:
        self.events = events
        self.fail_once = fail_once
        self.receipts = {}
        self.manifests = []

    def get_receipt(self, idempotency_key: str):
        return self.receipts.get(idempotency_key)

    def preflight(self, _artifact, *, manifest) -> None:
        assert manifest.evidence.entries

    def commit(
        self,
        _artifact,
        *,
        manifest,
        idempotency_key: str,
        receipt,
    ) -> None:
        assert manifest.evidence == EvidenceManifest(
            entries=(
                EvidenceManifestEntry(
                    evidence_id="ev-session-atomic",
                    source_evidence_ids=("ev-session-atomic",),
                    kind="session_episode",
                    content_hash=EVIDENCE_HASH,
                    source_content_hashes=(("ev-session-atomic", EVIDENCE_HASH),),
                ),
            ),
            checkpoints=(
                CheckpointAdvance(
                    source_key="codex:atomic-session",
                    cursor='{"kind":"jsonl","offset":50}',
                    fingerprint="atomic-source-v1",
                    evidence_ids=("ev-session-atomic",),
                ),
            ),
        )
        self.manifests.append(manifest)
        assert manifest.publication.changed_paths == Prepared.changed_paths
        self.events.append("state-commit")
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("state transaction failed")
        self.receipts[idempotency_key] = receipt


class FailCompletionOnceJournal(InMemoryApplyJournal):
    def __init__(self) -> None:
        super().__init__()
        self.fail_once = True

    def mark_completed(self, idempotency_key: str) -> None:
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("simulated crash after state commit")
        super().mark_completed(idempotency_key)


def setup_gate(
    *,
    extraction_fails: bool = False,
    state_fails_once: bool = False,
    journal=None,
):
    artifacts = InMemoryRunArtifactStore()
    receipt = DailyWeeklyRunner(
        extractor=Extractor(failed=extraction_fails), artifact_store=artifacts
    ).run(
        RunRequest(
            run_kind="daily",
            period="2026-08-01",
            model_contract="test-model-v1",
            evidence_manifest=EvidenceManifest(
                entries=(
                    EvidenceManifestEntry(
                        evidence_id="ev-session-atomic",
                        source_evidence_ids=("ev-session-atomic",),
                        kind="session_episode",
                        content_hash=EVIDENCE_HASH,
                        source_content_hashes=(
                            ("ev-session-atomic", EVIDENCE_HASH),
                        ),
                    ),
                ),
                checkpoints=(
                    CheckpointAdvance(
                        source_key="codex:atomic-session",
                        cursor='{"kind":"jsonl","offset":50}',
                        fingerprint="atomic-source-v1",
                        evidence_ids=("ev-session-atomic",),
                    ),
                ),
            ),
            evidence=(
                {
                    "id": "ev-session-atomic",
                    "source_type": "session-episode",
                    "kind": "session_episode",
                    "content_hash": EVIDENCE_HASH,
                    "source_content_hashes": {
                        "ev-session-atomic": EVIDENCE_HASH
                    },
                    "payload": {"analysis_lane": "profile_only", "project_ids": []},
                },
            ),
        )
    )
    events: list[str] = []
    publisher = Publisher(events)
    state = StateCommitter(events, fail_once=state_fails_once)
    coordinator = JournaledApplyCoordinator(
        publisher=publisher,
        state_committer=state,
        journal=journal or InMemoryApplyJournal(),
        publication_policy=BoundedPublicationPolicy(
            owned_sections=("daily-summary", "preferences")
        ),
    )
    artifact = artifacts.get(receipt.fingerprint)
    assert artifact is not None
    authorization = CutoverAuthorization(
        authorization_id="cutover-review-1",
        contract_version="daily-weekly-cutover-v2",
        approved=True,
        run_fingerprint=receipt.fingerprint,
        run_kind=receipt.run_kind,
        period=receipt.period,
        artifact_fingerprint=receipt.artifact_fingerprint,
        behavior_contract=artifact.behavior_contract,
    )
    authorizations = InMemoryCutoverAuthorizationStore((authorization,))
    gate = AtomicApplyGate(
        artifact_store=artifacts,
        coordinator=coordinator,
        authorization_store=authorizations,
    )
    return gate, receipt, authorization, events, state, publisher, authorizations


def test_atomic_apply_commits_files_then_transactional_state_once() -> None:
    gate, receipt, authorization, events, _state, _publisher, _authorizations = setup_gate()

    first = gate.apply(ApplyRequest(receipt, authorization.authorization_id))
    second = gate.apply(ApplyRequest(receipt, authorization.authorization_id))

    assert events == ["prepare", "files-commit", "files-verify", "state-commit"]
    assert first == second
    assert first.published is True
    assert first.checkpoint_advanced is True
    assert first.changed_paths == Prepared.changed_paths


def test_new_authorization_for_same_artifact_cannot_reapply_it() -> None:
    gate, receipt, authorization, events, _state, _publisher, authorizations = setup_gate()

    first = gate.apply(ApplyRequest(receipt, authorization.authorization_id))
    second_authorization = replace(
        authorization, authorization_id="cutover-review-2"
    )
    authorizations.add(second_authorization)
    second = gate.apply(
        ApplyRequest(receipt, second_authorization.authorization_id)
    )

    assert first == second
    assert first.authorization_id == "cutover-review-1"
    assert events == ["prepare", "files-commit", "files-verify", "state-commit"]


def test_atomic_apply_requires_explicit_period_bound_authorization() -> None:
    gate, receipt, authorization, events, _state, _publisher, authorizations = setup_gate()

    denied = replace(authorization, authorization_id="denied", approved=False)
    wrong_period = replace(
        authorization,
        authorization_id="wrong-period",
        period="2026-08-02",
    )
    authorizations.add(denied)
    authorizations.add(wrong_period)

    with pytest.raises(RuntimeError, match="not approved"):
        gate.apply(ApplyRequest(receipt, denied.authorization_id))
    with pytest.raises(RuntimeError, match="binding mismatch"):
        gate.apply(ApplyRequest(receipt, wrong_period.authorization_id))

    assert events == []


def test_state_failure_keeps_committed_files_and_retry_resumes_from_journal() -> None:
    gate, receipt, authorization, events, _state, _publisher, _authorizations = setup_gate(state_fails_once=True)

    with pytest.raises(RuntimeError, match="state transaction failed"):
        gate.apply(ApplyRequest(receipt, authorization.authorization_id))

    applied = gate.apply(ApplyRequest(receipt, authorization.authorization_id))

    assert events == [
        "prepare",
        "files-commit",
        "files-verify",
        "state-commit",
        "files-verify",
        "state-commit",
    ]
    assert applied.published is True


def test_retry_revalidates_committed_files_before_advancing_state() -> None:
    gate, receipt, authorization, events, state, publisher, _authorizations = setup_gate(
        state_fails_once=True
    )

    with pytest.raises(RuntimeError, match="state transaction failed"):
        gate.apply(ApplyRequest(receipt, authorization.authorization_id))
    prepared = next(iter(publisher.prepared_by_id.values()))
    prepared.committed_valid = False

    with pytest.raises(RuntimeError, match="durable manifest"):
        gate.apply(ApplyRequest(receipt, authorization.authorization_id))

    assert len(state.manifests) == 1
    assert events[-1] == "files-verify"


def test_atomic_apply_rejects_owner_edit_between_prepare_and_commit() -> None:
    gate, receipt, authorization, events, _state, publisher, _authorizations = setup_gate()
    original_prepare = publisher.prepare

    def drifted_prepare(artifact, *, idempotency_key: str):
        prepared = original_prepare(artifact, idempotency_key=idempotency_key)
        prepared.precondition_valid = False
        return prepared

    publisher.prepare = drifted_prepare

    with pytest.raises(RuntimeError, match="preimage changed"):
        gate.apply(ApplyRequest(receipt, authorization.authorization_id))

    assert events == ["prepare"]


def test_retry_recovers_when_process_dies_after_transactional_state_commit() -> None:
    journal = FailCompletionOnceJournal()
    gate, receipt, authorization, events, _state, _publisher, _authorizations = setup_gate(journal=journal)

    with pytest.raises(RuntimeError, match="simulated crash"):
        gate.apply(ApplyRequest(receipt, authorization.authorization_id))

    applied = gate.apply(ApplyRequest(receipt, authorization.authorization_id))

    assert events == ["prepare", "files-commit", "files-verify", "state-commit"]
    assert applied.published is True


def test_atomic_apply_recomputes_eligibility_from_stored_artifact() -> None:
    gate, receipt, authorization, events, _state, _publisher, _authorizations = setup_gate(extraction_fails=True)
    forged = replace(receipt, checkpoint_eligible=True, status="accepted")

    with pytest.raises(RuntimeError, match="stored artifact is not checkpoint eligible"):
        gate.apply(ApplyRequest(forged, authorization.authorization_id))

    assert events == []


def test_atomic_apply_rejects_artifact_hash_mismatch_before_prepare() -> None:
    gate, receipt, authorization, events, _state, _publisher, authorizations = setup_gate()

    wrong = replace(
        authorization,
        authorization_id="wrong-artifact",
        artifact_fingerprint="wrong",
    )
    authorizations.add(wrong)

    with pytest.raises(RuntimeError, match="artifact fingerprint mismatch"):
        gate.apply(ApplyRequest(receipt, wrong.authorization_id))

    assert events == []


def test_atomic_apply_rejects_obsolete_extraction_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate, receipt, authorization, events, _state, _publisher, _authorizations = setup_gate()
    monkeypatch.setattr(
        "second_brain_protocol.atomic_apply.extraction_contract_fingerprint",
        lambda _run_kind: "newer-contract",
    )

    with pytest.raises(RuntimeError, match="obsolete extraction contract"):
        gate.apply(ApplyRequest(receipt, authorization.authorization_id))

    assert events == []


def test_cutover_authorization_store_is_authoritative_and_one_use() -> None:
    gate, receipt, authorization, events, _state, _publisher, authorizations = setup_gate()

    with pytest.raises(RuntimeError, match="not approved"):
        gate.apply(ApplyRequest(receipt, "caller-invented-authorization"))

    authorizations.bind_once(authorization.authorization_id, operation_key="one")
    with pytest.raises(RuntimeError, match="already consumed"):
        authorizations.bind_once(authorization.authorization_id, operation_key="two")
    assert events == []


def test_atomic_apply_rejects_noncanonical_write_target() -> None:
    gate, receipt, authorization, events, _state, publisher, _authorizations = setup_gate()
    operation_key = next(iter(publisher.prepared_by_id), None)
    assert operation_key is None

    original_prepare = publisher.prepare

    def unsafe_prepare(artifact, *, idempotency_key: str):
        prepared = original_prepare(artifact, idempotency_key=idempotency_key)
        prepared.staged_files = (
            StagedCanonicalFile(
                path="Protocol/OperatingContract.md",
                before_text="<!-- sb:generated contract:start -->\nold\n<!-- sb:generated contract:end -->",
                after_text="<!-- sb:generated contract:start -->\nnew\n<!-- sb:generated contract:end -->",
            ),
        )
        return prepared

    publisher.prepare = unsafe_prepare

    with pytest.raises(RuntimeError, match="not writable canon"):
        gate.apply(ApplyRequest(receipt, authorization.authorization_id))

    assert events == ["prepare", "staging-rollback"]
