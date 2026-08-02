from pathlib import Path

import pytest

from second_brain_protocol.extraction_harness import ProcedureCandidate
from second_brain_protocol.procedure_review import SQLiteProcedureReviewStore
from second_brain_protocol.state import StateStore, utc_now


def candidate() -> ProcedureCandidate:
    return ProcedureCandidate(
        procedure_id="procedure-" + "a" * 24,
        name="Verify atomic publication",
        purpose="Prove files and state reach one recoverable terminal outcome.",
        scope="project",
        project_id="project-protocol",
        prerequisites=("A prepared publication manifest",),
        steps=("Validate evidence hashes.", "Commit through the durable journal."),
        failure_branches=("Resume an interrupted state commit from the journal.",),
        tests=("Crash after the file commit and retry.",),
        evidence_refs=("ev-procedure-review",),
        owner_directed=True,
        validated_outcome=True,
    )


def store(tmp_path: Path) -> SQLiteProcedureReviewStore:
    state = StateStore(tmp_path / "state.sqlite")
    with state.connect() as connection:
        connection.execute(
            """INSERT INTO evidence(
            id,source_type,source_ref,project_id,kind,occurred_at,content_hash,
            payload_json,status,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                "ev-procedure-review",
                "session-episode",
                "session:test",
                "project-protocol",
                "session_episode",
                "2026-08-01T10:00:00Z",
                "a" * 64,
                "{}",
                "new",
                utc_now(),
            ),
        )
    return SQLiteProcedureReviewStore(state)


def test_procedure_stays_review_only_and_approval_returns_authoring_handoff(
    tmp_path: Path,
) -> None:
    reviews = store(tmp_path)
    procedure = candidate()

    reviews.stage((procedure,), run_fingerprint="run-procedure")

    assert reviews.proposals()[0].status == "pending"
    assert not (tmp_path / ".agents" / "skills").exists()

    handoff = reviews.approve(procedure.procedure_id)

    assert handoff.procedure_id == procedure.procedure_id
    assert handoff.workflow == "skill-creator-review-required"
    assert handoff.candidate == procedure
    assert reviews.proposals()[0].status == "approved"
    assert not (tmp_path / ".agents" / "skills").exists()


def test_procedure_stage_requires_durable_evidence(tmp_path: Path) -> None:
    reviews = store(tmp_path)
    unsupported = ProcedureCandidate(
        **{
            **candidate().__dict__,
            "procedure_id": "procedure-" + "b" * 24,
            "evidence_refs": ("ev-missing",),
        }
    )

    with pytest.raises(RuntimeError, match="evidence"):
        reviews.stage((unsupported,), run_fingerprint="run-unsupported")

    assert reviews.proposals() == ()
