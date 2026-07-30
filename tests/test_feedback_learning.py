from pathlib import Path

from second_brain_protocol.feedback_learning import (
    knowledge_feedback_profile,
    should_suppress_candidate,
)
from second_brain_protocol.knowledge import like_knowledge
from second_brain_protocol.state import StateStore


def _observation(store: StateStore, subject: str, claim: str) -> str:
    evidence_id, _ = store.add_evidence(
        source_type="interview",
        source_ref=f"test:{subject}",
        kind="answer",
        payload={"text": claim},
    )
    return store.add_observation(
        {
            "kind": "preference",
            "subject": subject,
            "claim": claim,
            "evidence_refs": [evidence_id],
            "confidence": 0.9,
            "status": "promoted",
        }
    )


def test_feedback_profile_learns_repeated_removals_without_reasons(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    removed_ids = [
        _observation(
            store,
            f"Network preview {index}",
            "The preview server should be available over the local network.",
        )
        for index in range(2)
    ]
    for observation_id in removed_ids:
        with store.transaction() as connection:
            connection.execute(
                "INSERT INTO knowledge_feedback(observation_id,decision,updated_at) VALUES(?,?,datetime('now'))",
                (observation_id, "disliked"),
            )
    confirmed_id = _observation(
        store,
        "Visual quality",
        "the user prefers premium, realistic interfaces and rejects generic AI design.",
    )
    like_knowledge(store, confirmed_id)

    profile = knowledge_feedback_profile(store)

    assert profile["reviewed_cards"] == 3
    assert profile["avoid"][0]["feature"] == "content:one-off-task-instruction"
    assert should_suppress_candidate(
        {
            "kind": "preference",
            "subject": "Preview server",
            "claim": "The preview server should be exposed to the local network.",
        },
        profile,
    )
