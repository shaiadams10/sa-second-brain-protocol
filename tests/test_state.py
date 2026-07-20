from pathlib import Path
import json

import pytest

from second_brain_protocol.state import StateStore


def test_evidence_deduplication_and_publish_checkpoint(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    kwargs = dict(
        source_type="codex",
        source_ref="codex:s:1:h",
        kind="visible_message",
        payload={"text": "same"},
    )
    evidence_id, added = store.add_evidence(**kwargs)
    assert added
    assert store.add_evidence(**kwargs) == (evidence_id, False)
    store.attach_checkpoint_candidate(
        evidence_id, source_key="file:a", cursor="1", fingerprint="abc"
    )
    assert store.checkpoint("file:a") is None
    store.set_collection_receipt("file:a", "abc")
    assert store.collection_receipt("file:a")["fingerprint"] == "abc"
    assert store.checkpoint("file:a") is None
    store.mark_evidence([evidence_id], "processed")
    store.publish_checkpoint_candidates([evidence_id])
    assert store.checkpoint("file:a")["fingerprint"] == "abc"


def test_pipeline_runs_keep_failure_stage_and_error(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    run_id = store.start_pipeline_run(
        "daily", trigger="scheduled", stage="collect_projects"
    )
    store.set_pipeline_stage(run_id, "collect_sessions")
    store.finish_pipeline_run(
        run_id, "failed", error="RuntimeError: transcript replay interrupted"
    )

    run = store.pipeline_runs(limit=1)[0]
    assert run["status"] == "failed"
    assert run["stage"] == "collect_sessions"
    assert run["trigger"] == "scheduled"
    assert run["error"] == "RuntimeError: transcript replay interrupted"


def test_evidence_batch_is_atomic_and_supersedes_mutable_source(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    old_id, _ = store.add_evidence(
        source_type="antigravity",
        source_ref="antigravity:session:message",
        kind="visible_message",
        payload={"text": "old"},
    )
    results = store.add_evidence_batch(
        [
            {
                "source_type": "antigravity",
                "source_ref": "antigravity:session:message",
                "kind": "visible_message",
                "payload": {"text": "new"},
                "project_id": None,
                "occurred_at": "2026-07-18T20:00:00Z",
                "cursor": "42",
            }
        ],
        source_key="antigravity-file-v3:test",
        fingerprint="current",
        supersede_mutable_source=True,
    )

    new_id, added = results[0]
    assert added is True
    rows = {row["id"]: row for row in store.evidence()}
    assert rows[old_id]["status"] == "superseded"
    assert rows[new_id]["status"] == "new"


def test_completed_bootstrap_refuses_reopen(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    for state in ("collecting", "synthesis_ready", "awaiting_review", "completed"):
        store.set_bootstrap_state(state)
    with pytest.raises(RuntimeError):
        store.set_bootstrap_state("collecting")


def test_batch_review_decision_requires_all_items_to_still_be_pending(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    evidence_id, _ = store.add_evidence(
        source_type="interview", source_ref="interview:one", kind="answer", payload={}
    )
    ids = [
        store.add_observation(
            {
                "kind": "lesson",
                "subject": subject,
                "claim": f"Claim {subject}",
                "evidence_refs": [evidence_id],
                "confidence": 0.8,
            }
        )
        for subject in ("one", "two")
    ]

    store.decide_observations(ids, "rejected", "Not durable")
    assert {store.observation(observation_id)["status"] for observation_id in ids} == {
        "rejected"
    }
    with pytest.raises(RuntimeError):
        store.decide_observations(ids, "approved")


def test_evidence_counts_are_not_limited_by_read_page(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    for index in range(12):
        store.add_evidence(
            source_type="codex",
            source_ref=f"codex:{index}",
            kind="visible_message",
            payload={"index": index},
        )
    assert len(store.evidence(status="new", limit=5)) == 5
    assert len(store.evidence(status="new")) == 12
    assert store.evidence_count(status="new") == 12


def test_evidence_project_attribution_can_be_corrected(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    evidence_id, _ = store.add_evidence(
        source_type="antigravity",
        source_ref="antigravity:one",
        kind="artifact",
        payload={"text": "modify this", "project_ids": ["project-dify"]},
        project_id="project-dify",
    )
    store.reassign_evidence_project(evidence_id, None)
    reassigned = store.evidence_by_ids([evidence_id])[0]
    assert reassigned["project_id"] is None
    assert reassigned["payload"]["project_ids"] == []


def test_question_project_override_records_explicit_owner_correction(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.upsert_project(
        {
            "id": "project-portfolio",
            "name": "Portfolio",
            "classification": "first-party",
        }
    )
    observation_id = store.add_observation(
        {
            "kind": "clarification",
            "subject": "Portfolio status",
            "claim": "What belongs in the portfolio?",
            "evidence_refs": [],
            "confidence": 0.8,
        }
    )

    store.set_observation_project_override(observation_id, ["project-portfolio"])

    payload = store.observation(observation_id)["payload"]
    assert payload["project_ids_override"] == ["project-portfolio"]
    assert payload["project_attribution_source"] == "explicit_owner_correction"
    with pytest.raises(KeyError):
        store.set_observation_project_override(observation_id, ["project-missing"])


def test_checkpoint_publisher_selects_latest_validated_cursor_per_source(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    ids = []
    for offset in (10, 20):
        evidence_id, _ = store.add_evidence(
            source_type="codex",
            source_ref=f"codex:session:{offset}",
            kind="visible_message",
            payload={"offset": offset},
        )
        cursor = json.dumps(
            {
                "version": 1,
                "kind": "jsonl",
                "offset": offset,
                "prefix_sha256": str(offset),
            }
        )
        store.attach_checkpoint_candidate(
            evidence_id,
            source_key="codex-file:one",
            cursor=cursor,
            fingerprint="current",
        )
        ids.append(evidence_id)
    store.publish_checkpoint_candidates(list(reversed(ids)))
    published = json.loads(store.checkpoint("codex-file:one")["cursor"])
    assert published["offset"] == 20


def test_project_path_and_session_indexes_round_trip(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.upsert_project(
        {
            "id": "project-one",
            "name": "One",
            "classification": "first-party",
        }
    )
    store.register_project_path(
        "project-one",
        "c:/projects/one",
        source="current_scan",
        current=True,
    )
    store.upsert_session_source(
        source_key="codex-file:one",
        surface="codex",
        session_id="session-one",
        fingerprint="fingerprint",
        workspace_hash="workspace-hash",
        project_id="project-one",
        status="matched",
        resolver="current_path",
        confidence=1.0,
        ingested=False,
    )
    store.mark_session_source_ingested("codex-file:one", fingerprint="fingerprint")
    store.replace_session_project_index(
        [
            {
                "surface": "codex",
                "session_id": "session-one",
                "project_id": "project-one",
                "status": "matched",
                "resolver": "current_path",
                "confidence": 1.0,
                "workspace_count": 1,
                "source_record_count": 2,
            }
        ]
    )

    assert store.project_path_aliases()[0]["is_current"] == 1
    assert store.session_source("codex-file:one")["ingested"] == 1
    assert store.resolved_session_source_project("codex", "session-one") == (
        "project-one"
    )
    assert store.session_project_index()[0]["project_id"] == "project-one"
    evidence_id, _ = store.add_evidence(
        source_type="session-digest",
        source_ref="session-digest:codex:one",
        kind="session_digest",
        payload={"session_id": "session-one"},
        project_id="project-one",
    )
    store.mark_project_session_evidence_analyzed(
        "project-one", [evidence_id], run_id="run-one"
    )
    assert store.analyzed_project_session_evidence("project-one") == {evidence_id}
    assert store.clear_project_session_analysis({"project-one"}) == 1
    assert store.analyzed_project_session_evidence("project-one") == set()


def test_missing_projects_are_reported_without_local_paths(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.upsert_project(
        {
            "id": "project-old",
            "name": "Old Project",
            "classification": "first-party",
            "local_path": str(tmp_path / "Projects" / "Old Project"),
        }
    )
    store.set_project_presence("project-old", present=True)
    store.set_project_presence("project-old", present=False)

    missing = store.missing_projects()

    assert missing[0]["project"] == "Old Project"
    assert missing[0]["missing_since"]
    assert "local_path" not in missing[0]
