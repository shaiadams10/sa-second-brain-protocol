from second_brain_protocol.question_followup import (
    is_auto_resolvable_question,
    pending_question_context,
)


def _question(subject: str, question: str) -> dict:
    return {
        "id": "obs-12345678",
        "kind": "clarification",
        "status": "pending",
        "subject": subject,
        "claim": question,
        "payload": {"question": question},
    }


def test_only_objective_project_followups_are_eligible() -> None:
    objective = _question(
        "Portfolio deployment status", "Was the portfolio deployed and verified?"
    )
    disclosure = _question(
        "Military public disclosure", "Which military details are approved for public use?"
    )
    authorship = _question(
        "Implementation authorship", "Which files were personally authored?"
    )

    assert is_auto_resolvable_question(objective)
    assert not is_auto_resolvable_question(disclosure)
    assert not is_auto_resolvable_question(authorship)
    assert pending_question_context([disclosure, objective, authorship]) == [
        {
            "id": "obs-12345678",
            "subject": "Portfolio deployment status",
            "question": "Was the portfolio deployed and verified?",
        }
    ]
