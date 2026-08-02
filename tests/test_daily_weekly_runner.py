import json
from dataclasses import asdict, replace
import pytest

import second_brain_protocol.daily_weekly_runner as runner_module
from second_brain_protocol.daily_weekly_runner import (
    CheckpointAdvance,
    DailyWeeklyRunner,
    EvidenceManifest,
    EvidenceManifestEntry,
    InMemoryRunArtifactStore,
    RunRequest,
)
from second_brain_protocol.extraction_harness import (
    CandidateRejection,
    ExtractionCandidate,
    ExtractionCoverage,
    ExtractionFailure,
    EpisodeReceipt,
    ExtractionRequest,
    ExtractionResult,
    ExtractionUsage,
)
from second_brain_protocol.novelty import SQLiteEstablishedMemoryFilter
from second_brain_protocol.state import StateStore


class ScriptedExtractor:
    def __init__(self, result: ExtractionResult) -> None:
        self.result = result
        self.requests: list[ExtractionRequest] = []

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        self.requests.append(request)
        return self.result


class SequenceExtractor:
    def __init__(self, results: tuple[ExtractionResult, ...]) -> None:
        self.results = list(results)
        self.requests: list[ExtractionRequest] = []

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        self.requests.append(request)
        return self.results.pop(0)


EVIDENCE_HASH = "a" * 64
SOURCE_HASH = "b" * 64


def manifest_entry(
    evidence_id: str,
    source_evidence_ids: tuple[str, ...],
    *,
    kind: str = "session_episode",
    content_hash: str = EVIDENCE_HASH,
) -> EvidenceManifestEntry:
    return EvidenceManifestEntry(
        evidence_id=evidence_id,
        source_evidence_ids=source_evidence_ids,
        kind=kind,
        content_hash=content_hash,
        source_content_hashes=tuple(
            (
                source_id,
                content_hash if source_id == evidence_id else SOURCE_HASH,
            )
            for source_id in source_evidence_ids
        ),
    )


def extraction_result(*, failed: bool = False) -> ExtractionResult:
    candidate = ExtractionCandidate(
        candidate_type="memory_mutation",
        operation="create",
        kind="preference",
        subject="Concise receipts",
        claim="PRIVATE CLAIM MUST NOT APPEAR IN RUN RECEIPT",
        scope="global",
        project_id=None,
        target_memory_id=None,
        evidence_refs=("ev-runner-input",),
        confidence=0.95,
        explicit=True,
    )
    failures = (
        (
            ExtractionFailure(
                code="model-error",
                error_type="RuntimeError",
                evidence_refs=("ev-runner-input",),
            ),
        )
        if failed
        else ()
    )
    return ExtractionResult(
        status="partial",
        candidates=(candidate,),
        rejections=(
            CandidateRejection(
                index=1,
                code="invalid-candidate-schema",
                candidate_type="memory_mutation",
                evidence_refs=("ev-runner-input",),
            ),
        ),
        coverage=ExtractionCoverage(
            source_evidence_count=1,
            episode_count=2,
            omitted_messages=0,
            failed_episodes=len(failures),
        ),
        usage=ExtractionUsage(
            input_tokens=400,
            output_tokens=80,
            total_tokens=480,
            model_calls=2,
        ),
        failures=failures,
        episodes=(
            EpisodeReceipt(
                episode_id="ev-runner-input",
                source_evidence_ids=("ev-source-session",),
                analysis_lane="profile_only",
                project_id=None,
                started_at="2026-08-01T10:00:00Z",
                ended_at="2026-08-01T10:01:00Z",
                message_count=1,
                content_chars=120,
            ),
            EpisodeReceipt(
                episode_id="ev-runner-input-two",
                source_evidence_ids=("ev-source-session",),
                analysis_lane="profile_only",
                project_id=None,
                started_at="2026-08-01T10:02:00Z",
                ended_at="2026-08-01T10:03:00Z",
                message_count=1,
                content_chars=80,
            ),
        ),
    )


def request() -> RunRequest:
    return RunRequest(
        run_kind="daily",
        period="2026-08-01",
        model_contract="test-model-v1",
        evidence_manifest=EvidenceManifest(
            entries=(manifest_entry("ev-runner-input", ("ev-source-session",)),),
            checkpoints=(
                CheckpointAdvance(
                    source_key="codex:session-one",
                    cursor='{"kind":"jsonl","offset":100}',
                    fingerprint="source-snapshot-v1",
                    evidence_ids=("ev-runner-input",),
                ),
            ),
        ),
        evidence=(
            {
                "id": "ev-runner-input",
                "source_type": "session-episode",
                "kind": "session_episode",
                "project_id": None,
                "content_hash": EVIDENCE_HASH,
                "source_content_hashes": {"ev-source-session": SOURCE_HASH},
                "payload": {
                    "analysis_lane": "profile_only",
                    "project_ids": [],
                    "source_evidence_ids": ["ev-source-session"],
                },
            },
        ),
    )


def test_authoritative_runner_returns_deterministic_nonpublishing_receipt() -> None:
    extractor = ScriptedExtractor(extraction_result())
    artifacts = InMemoryRunArtifactStore()
    runner = DailyWeeklyRunner(extractor=extractor, artifact_store=artifacts)

    first = runner.run(request())
    second = runner.run(request())

    assert first.fingerprint == second.fingerprint
    assert first.status == "partial"
    assert first.mode == "authoritative"
    assert first.published is False
    assert first.checkpoint_eligible is True
    assert first.candidate_count == 1
    assert first.rejection_counts == (("invalid-candidate-schema", 1),)
    assert first.failure_counts == ()
    assert first.coverage.episode_count == 2
    assert first.usage.total_tokens == 480
    assert len(first.session_summaries) == 1
    assert first.session_summaries[0].episode_count == 2
    assert first.session_summaries[0].candidate_count == 1
    assert first.session_summaries[0].subjects == ("Concise receipts",)
    assert first.period_summary.session_count == 1
    assert first.period_summary.episode_count == 2
    assert first.period_summary.candidate_count == 1
    assert first.artifact_fingerprint == second.artifact_fingerprint
    assert len(extractor.requests) == 1
    assert len(artifacts.artifacts) == 1


def test_runner_receipt_never_contains_candidate_claim_text() -> None:
    receipt = DailyWeeklyRunner(extractor=ScriptedExtractor(extraction_result())).run(
        request()
    )

    encoded = json.dumps(asdict(receipt), sort_keys=True)
    assert "PRIVATE CLAIM" not in encoded


def test_failed_episode_blocks_checkpoint_eligibility() -> None:
    receipt = DailyWeeklyRunner(
        extractor=ScriptedExtractor(extraction_result(failed=True))
    ).run(request())

    assert receipt.status == "blocked"
    assert receipt.checkpoint_eligible is False
    assert receipt.published is False
    assert receipt.failure_counts == (("model-error", 1),)


def test_failed_artifact_retries_until_same_input_becomes_checkpoint_eligible() -> None:
    extractor = SequenceExtractor((extraction_result(failed=True), extraction_result()))
    artifacts = InMemoryRunArtifactStore()
    runner = DailyWeeklyRunner(extractor=extractor, artifact_store=artifacts)

    first = runner.run(request())
    second = runner.run(request())
    replay = runner.run(request())

    assert first.status == "blocked"
    assert second.checkpoint_eligible is True
    assert replay == second
    assert len(extractor.requests) == 2
    stored = artifacts.get(first.fingerprint)
    assert stored is not None
    assert stored.artifact_fingerprint == second.artifact_fingerprint


def test_missing_episode_receipts_block_checkpoint_eligibility() -> None:
    result = replace(extraction_result(), episodes=())
    receipt = DailyWeeklyRunner(extractor=ScriptedExtractor(result)).run(request())

    assert receipt.episode_accounting_complete is False
    assert receipt.checkpoint_eligible is False
    assert receipt.status == "blocked"


def test_manifest_source_count_mismatch_blocks_checkpoint_eligibility() -> None:
    result = replace(
        extraction_result(),
        coverage=ExtractionCoverage(
            source_evidence_count=0,
            episode_count=0,
            omitted_messages=0,
            failed_episodes=0,
        ),
        episodes=(),
    )

    receipt = DailyWeeklyRunner(extractor=ScriptedExtractor(result)).run(request())

    assert receipt.checkpoint_eligible is False
    assert receipt.status == "blocked"


def test_episode_provenance_mismatch_blocks_checkpoint_eligibility() -> None:
    mismatched = replace(
        extraction_result(),
        episodes=(
            replace(
                extraction_result().episodes[0],
                source_evidence_ids=("ev-unrelated-session",),
            ),
            replace(
                extraction_result().episodes[1],
                source_evidence_ids=("ev-unrelated-session",),
            ),
        ),
    )

    receipt = DailyWeeklyRunner(extractor=ScriptedExtractor(mismatched)).run(request())

    assert receipt.checkpoint_eligible is False
    assert receipt.status == "blocked"


def test_behavior_contract_change_forces_a_new_shadow_extraction() -> None:
    extractor = ScriptedExtractor(extraction_result())
    artifacts = InMemoryRunArtifactStore()
    runner = DailyWeeklyRunner(extractor=extractor, artifact_store=artifacts)

    first = runner.run(request())
    second = runner.run(replace(request(), behavior_contract="daily-weekly-governed-v4"))

    assert first.fingerprint != second.fingerprint
    assert len(extractor.requests) == 2
    assert len(artifacts.artifacts) == 2


def test_run_fingerprint_binds_authoritative_content_hash_and_actual_payload() -> None:
    extractor = ScriptedExtractor(extraction_result())
    artifacts = InMemoryRunArtifactStore()
    runner = DailyWeeklyRunner(extractor=extractor, artifact_store=artifacts)
    original = request()
    changed_hash = "c" * 64
    changed_hash_only = replace(
        original,
        evidence=(dict(original.evidence[0], content_hash=changed_hash),),
        evidence_manifest=EvidenceManifest(
            entries=(
                manifest_entry(
                    "ev-runner-input",
                    ("ev-source-session",),
                    content_hash=changed_hash,
                ),
            ),
            checkpoints=original.evidence_manifest.checkpoints,
        ),
    )
    changed_payload = replace(
        original,
        evidence=(
            dict(
                original.evidence[0],
                content_hash=EVIDENCE_HASH,
                payload={
                    "analysis_lane": "profile_only",
                    "project_ids": [],
                    "record_counts": {"messages": 2},
                    "source_evidence_ids": ["ev-source-session"],
                },
            ),
        ),
    )

    first = runner.run(original)
    same = runner.run(changed_hash_only)
    changed = runner.run(changed_payload)

    assert first.fingerprint != same.fingerprint
    assert first.fingerprint != changed.fingerprint
    assert len(extractor.requests) == 3


def test_artifact_binds_period_run_kind_and_behavior_contract() -> None:
    artifacts = InMemoryRunArtifactStore()
    receipt = DailyWeeklyRunner(
        extractor=ScriptedExtractor(extraction_result()), artifact_store=artifacts
    ).run(request())

    artifact = artifacts.get(receipt.fingerprint)

    assert artifact is not None
    assert artifact.run_kind == "daily"
    assert artifact.period == "2026-08-01"
    assert artifact.behavior_contract == request().behavior_contract
    assert artifact.model_contract == request().model_contract
    assert artifact.extraction_contract
    assert artifact.evidence_manifest == request().evidence_manifest


def test_shadow_artifact_contains_deterministic_review_gated_memory_plan() -> None:
    artifacts = InMemoryRunArtifactStore()
    receipt = DailyWeeklyRunner(
        extractor=ScriptedExtractor(extraction_result()), artifact_store=artifacts
    ).run(request())

    artifact = artifacts.get(receipt.fingerprint)

    assert artifact is not None
    assert len(artifact.memory_plan.items) == 1
    assert artifact.memory_plan.items[0].disposition == "review"
    assert artifact.memory_plan.items[0].evidence_refs == ("ev-runner-input",)
    assert receipt.planned_memory_mutation_count == 1


def test_runner_rejects_extraction_contract_change_during_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contracts = iter(("contract-before", "contract-after"))
    monkeypatch.setattr(
        runner_module,
        "extraction_contract_fingerprint",
        lambda _run_kind: next(contracts),
    )
    extractor = ScriptedExtractor(extraction_result())
    artifacts = InMemoryRunArtifactStore()

    with pytest.raises(RuntimeError, match="changed during run"):
        DailyWeeklyRunner(
            extractor=extractor,
            artifact_store=artifacts,
        ).run(request())

    assert len(extractor.requests) == 1
    assert artifacts.artifacts == {}


def test_run_fingerprint_binds_messages_beyond_first_model_packet() -> None:
    messages = [
        {"occurred_at": f"2026-08-01T10:{index:02d}:00Z", "text": f"message {index}"}
        for index in range(13)
    ]
    original = replace(
        request(),
        evidence_manifest=EvidenceManifest(
            entries=(
                manifest_entry(
                    "ev-long-session-fingerprint",
                    ("ev-long-session-fingerprint",),
                    kind="session_digest",
                ),
            ),
            checkpoints=(
                CheckpointAdvance(
                    source_key="codex:long-session",
                    cursor='{"kind":"jsonl","offset":200}',
                    fingerprint="long-session-v1",
                    evidence_ids=("ev-long-session-fingerprint",),
                ),
            ),
        ),
        evidence=(
            {
                "id": "ev-long-session-fingerprint",
                "source_type": "session-digest",
                "kind": "session_digest",
                "project_id": None,
                "content_hash": EVIDENCE_HASH,
                "source_content_hashes": {"ev-long-session-fingerprint": EVIDENCE_HASH},
                "payload": {
                    "analysis_lane": "profile_only",
                    "project_ids": [],
                    "user_messages": messages,
                    "assistant_results": [],
                },
            },
        ),
    )
    changed_messages = [dict(item) for item in messages]
    changed_messages[-1]["text"] = "changed final message"
    changed = replace(
        original,
        evidence=(
            dict(
                original.evidence[0],
                payload=dict(
                    original.evidence[0]["payload"],
                    user_messages=changed_messages,
                ),
            ),
        ),
    )
    extractor = ScriptedExtractor(extraction_result())
    runner = DailyWeeklyRunner(
        extractor=extractor,
        artifact_store=InMemoryRunArtifactStore(),
    )

    first = runner.run(original)
    second = runner.run(changed)

    assert first.fingerprint != second.fingerprint
    assert len(extractor.requests) == 2


def test_run_request_requires_exact_evidence_manifest() -> None:
    with pytest.raises(ValueError, match="Evidence manifest"):
        replace(
            request(),
            evidence_manifest=EvidenceManifest(
                entries=(manifest_entry("ev-different", ("ev-different",)),),
                checkpoints=(),
            ),
        )


def test_run_fingerprint_binds_prebuilt_episode_provenance() -> None:
    original = request()
    with_provenance = replace(
        original,
        evidence_manifest=EvidenceManifest(
            entries=(manifest_entry("ev-runner-input", ("ev-original-source",)),),
            checkpoints=(
                CheckpointAdvance(
                    source_key="codex:session-one",
                    cursor='{"kind":"jsonl","offset":100}',
                    fingerprint="source-snapshot-v1",
                    evidence_ids=("ev-original-source",),
                ),
            ),
        ),
        evidence=(
            dict(
                original.evidence[0],
                payload=dict(
                    original.evidence[0]["payload"],
                    source_evidence_ids=["ev-original-source"],
                ),
                source_content_hashes={"ev-original-source": SOURCE_HASH},
            ),
        ),
    )
    changed = replace(
        with_provenance,
        evidence_manifest=EvidenceManifest(
            entries=(manifest_entry("ev-runner-input", ("ev-changed-source",)),),
            checkpoints=(
                CheckpointAdvance(
                    source_key="codex:session-one",
                    cursor='{"kind":"jsonl","offset":100}',
                    fingerprint="source-snapshot-v1",
                    evidence_ids=("ev-changed-source",),
                ),
            ),
        ),
        evidence=(
            dict(
                with_provenance.evidence[0],
                payload=dict(
                    with_provenance.evidence[0]["payload"],
                    source_evidence_ids=["ev-changed-source"],
                ),
                source_content_hashes={"ev-changed-source": SOURCE_HASH},
            ),
        ),
    )
    extractor = ScriptedExtractor(extraction_result())
    runner = DailyWeeklyRunner(
        extractor=extractor,
        artifact_store=InMemoryRunArtifactStore(),
    )

    first = runner.run(with_provenance)
    second = runner.run(changed)

    assert first.fingerprint != second.fingerprint
    assert len(extractor.requests) == 2


def test_authoritative_runner_suppresses_established_memory_before_summaries(
    tmp_path,
) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.add_observation(
        {
            "kind": "preference",
            "subject": "Concise receipts",
            "claim": "PRIVATE CLAIM MUST NOT APPEAR IN RUN RECEIPT",
            "evidence_refs": [],
            "confidence": 1.0,
            "status": "promoted",
        }
    )
    novel = replace(
        extraction_result().candidates[0],
        subject="Evidence-changing updates",
        claim="Only resurface an established insight after material new evidence.",
    )
    result = replace(
        extraction_result(),
        candidates=(extraction_result().candidates[0], novel),
    )
    authoritative = request()

    receipt = DailyWeeklyRunner(
        extractor=ScriptedExtractor(result),
        artifact_store=InMemoryRunArtifactStore(),
        candidate_filter=SQLiteEstablishedMemoryFilter(store),
    ).run(authoritative)

    assert receipt.mode == "authoritative"
    assert receipt.candidate_count == 1
    assert receipt.rejection_counts == (
        ("invalid-candidate-schema", 1),
        ("stale-established-memory", 1),
    )
    assert receipt.session_summaries[0].subjects == ("Evidence-changing updates",)


def test_runner_rejects_retired_shadow_mode() -> None:
    with pytest.raises(ValueError, match="authoritative"):
        replace(request(), mode="shadow")


def test_rejected_project_memory_is_a_local_tombstone_scoped_to_its_project(
    tmp_path,
) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.add_observation(
        {
            "kind": "decision",
            "subject": "Daily engine",
            "claim": "Use governed extraction for Daily.",
            "evidence_refs": [],
            "confidence": 1.0,
            "status": "rejected",
            "project_id": "project-vault",
            "project_ids": ["project-vault"],
        }
    )
    candidate = replace(
        extraction_result().candidates[0],
        kind="decision",
        subject="Daily engine",
        claim="Use governed extraction for Daily.",
        scope="project",
        project_id="project-vault",
    )
    other_project = replace(candidate, project_id="project-public-protocol")
    base = extraction_result()
    result = replace(base, candidates=(candidate, other_project))

    filtered = SQLiteEstablishedMemoryFilter(store).filter(result, request())

    assert filtered.candidates == (other_project,)
    assert [item.code for item in filtered.rejections][-1] == (
        "rejected-memory-tombstone"
    )


def test_canonical_project_note_dedup_is_local_and_project_scoped(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    vault = tmp_path / "vault"
    note = vault / "Projects" / "managed-vault.md"
    note.parent.mkdir(parents=True)
    note.write_text(
        "# Managed vault\n\nUse governed extraction for Daily.\n",
        encoding="utf-8",
    )
    store.upsert_project(
        {
            "id": "project-vault",
            "name": "Managed Vault",
            "classification": "first-party",
            "lifecycle": "active",
            "tracked_file_count": 1,
        }
    )
    candidate = replace(
        extraction_result().candidates[0],
        kind="decision",
        subject="Daily engine",
        claim="Use governed extraction for Daily.",
        scope="project",
        project_id="project-vault",
    )
    other_project = replace(candidate, project_id="project-public-protocol")
    result = replace(extraction_result(), candidates=(candidate, other_project))

    filtered = SQLiteEstablishedMemoryFilter(store, vault=vault).filter(
        result, request()
    )

    assert filtered.candidates == (other_project,)
    assert filtered.rejections[-1].code == "stale-established-memory"
