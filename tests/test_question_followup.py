from second_brain_protocol.question_followup import (
    is_auto_resolvable_question,
    pending_question_context,
    should_create_review_question,
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


def test_review_questions_require_one_named_project_and_one_decision() -> None:
    names = {
        "project-angel": "First Party Project With Existing Brain",
        "project-dify": "dify",
    }

    def review(subject: str, question: str) -> dict:
        return {
            "kind": "ambiguity",
            "subject": subject,
            "description": "Owner judgment is required.",
            "question": question,
        }

    precise = review(
        "First Party Project With Existing Brain authorship",
        "Is First Party Project With Existing Brain first-party work directed by the user?",
    )
    combined = review(
        "First Party Project With Existing Brain ownership and disclosure",
        "Is First Party Project With Existing Brain first-party and approved for public attribution?",
    )
    mixed_projects = review(
        "First Party Project With Existing Brain + dify ownership",
        "Are First Party Project With Existing Brain and dify first-party work?",
    )
    broad = review(
        "First Party Project With Existing Brain timelines",
        "What are the status and dates for each inventoried project?",
    )

    assert should_create_review_question(
        precise,
        project_ids={"project-angel"},
        project_names=names,
    )
    assert not should_create_review_question(
        combined,
        project_ids={"project-angel"},
        project_names=names,
    )
    assert not should_create_review_question(
        mixed_projects,
        project_ids={"project-angel"},
        project_names=names,
    )
    assert not should_create_review_question(
        mixed_projects,
        project_ids={"project-angel", "project-dify"},
        project_names=names,
    )
    assert not should_create_review_question(
        broad,
        project_ids={"project-angel"},
        project_names=names,
    )


def test_profile_question_is_the_only_valid_projectless_destination() -> None:
    profile = {
        "subject": "Preferred name",
        "description": "The profile has conflicting names.",
        "question": "Which preferred name should appear in the personal profile?",
    }
    generic = {
        "subject": "Missing information",
        "description": "Some evidence is incomplete.",
        "question": "What else should be added?",
    }

    assert should_create_review_question(profile)
    assert not should_create_review_question(generic)
