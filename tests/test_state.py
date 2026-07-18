from pathlib import Path
import json

import pytest

from second_brain_protocol.state import StateStore


def test_evidence_deduplication_and_publish_checkpoint(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    kwargs = dict(source_type="codex", source_ref="codex:s:1:h", kind="visible_message", payload={"text": "same"})
    evidence_id, added = store.add_evidence(**kwargs)
    assert added
    assert store.add_evidence(**kwargs) == (evidence_id, False)
    store.attach_checkpoint_candidate(evidence_id, source_key="file:a", cursor="1", fingerprint="abc")
    assert store.checkpoint("file:a") is None
    store.set_collection_receipt("file:a", "abc")
    assert store.collection_receipt("file:a")["fingerprint"] == "abc"
    assert store.checkpoint("file:a") is None
    store.mark_evidence([evidence_id], "processed")
    store.publish_checkpoint_candidates([evidence_id])
    assert store.checkpoint("file:a")["fingerprint"] == "abc"


def test_completed_bootstrap_refuses_reopen(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    for state in ("collecting", "synthesis_ready", "awaiting_review", "completed"):
        store.set_bootstrap_state(state)
    with pytest.raises(RuntimeError):
        store.set_bootstrap_state("collecting")


def test_batch_review_decision_requires_all_items_to_still_be_pending(tmp_path: Path) -> None:
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
    assert {store.observation(observation_id)["status"] for observation_id in ids} == {"rejected"}
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
        payload={"text": "modify this"},
        project_id="project-dify",
    )
    store.reassign_evidence_project(evidence_id, None)
    assert store.evidence_by_ids([evidence_id])[0]["project_id"] is None


def test_question_project_override_records_explicit_owner_correction(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.upsert_project(
        {"id": "project-portfolio", "name": "Portfolio", "classification": "first-party"}
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
            {"version": 1, "kind": "jsonl", "offset": offset, "prefix_sha256": str(offset)}
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
