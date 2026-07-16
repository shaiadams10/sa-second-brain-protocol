from second_brain_protocol.review import build_review_groups, find_review_group, review_summary


def _item(observation_id: str, *, kind: str = "lesson", public: bool = False) -> dict:
    return {
        "id": observation_id,
        "kind": kind,
        "subject": observation_id,
        "claim": f"Claim for {observation_id}",
        "evidence_refs": ["ev-one"],
        "confidence": 0.8,
        "source_count": 1,
        "project_count": 0,
        "sensitivity": "normal",
        "promotion_tier": "review",
        "status": "pending",
        "payload": {"public_claim": public, "review_reason": "manual review tier"},
    }


def test_group_token_changes_when_membership_changes() -> None:
    first = build_review_groups([_item("obs-one")])[0]
    second = build_review_groups([_item("obs-one"), _item("obs-two")])[0]

    assert first["key"] == second["key"]
    assert first["id"] != second["id"]
    try:
        find_review_group([_item("obs-one"), _item("obs-two")], first["id"])
    except KeyError as error:
        assert "membership changed" in str(error)
    else:
        raise AssertionError("A stale group token must not resolve")


def test_summary_separates_questions_public_claims_and_private_review() -> None:
    question = _item("obs-question", kind="clarification")
    question["subject"] = "Project ownership"
    question["payload"]["question"] = "Who owns it?"
    summary = review_summary(
        [question, _item("obs-public", kind="experience", public=True), _item("obs-private")]
    )

    assert summary["pending"] == 3
    assert summary["needs_answers"] == 1
    assert summary["public_claims"] == 1
    assert summary["private_review"] == 1
    assert {group["mode"] for group in summary["groups"]} == {"answer", "review"}
