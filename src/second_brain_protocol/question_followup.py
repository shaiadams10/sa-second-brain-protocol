from __future__ import annotations

import re
from typing import Any


HUMAN_ONLY_TERMS = (
    "approved for public",
    "public-facing",
    "public disclosure",
    "public biography",
    "public attribution",
    "private or public",
    "confidential",
    "privacy",
    "military",
    "employment",
    "education",
    "career",
    "professional title",
    "personal authorship",
    "personally authored",
    "personally coded",
    "ownership",
    "author relationship",
    "authorship",
    "attributable to you",
    "credited as",
    "first-party",
    "third-party",
    "a fork",
    "identity",
    "account is yours",
    "account shai",
    "role for each",
    "preferred",
    "intended outcome",
    "intended current",
)

OBJECTIVE_FOLLOWUP_TERMS = (
    "status",
    "deployed",
    "working",
    "implemented",
    "completed",
    "validated",
    "verified",
    "branch",
    "merged",
    "date",
    "timeline",
    "milestone",
    "count",
    "size",
    "duration",
    "fix",
    "resolved",
    "current canonical",
    "final authoritative",
    "repository url",
    "install command",
    "session",
)

AGENT_RESOLVABLE_REVIEW_TERMS = (
    "canonical project id",
    "canonical repository name",
    "repository names",
    "folder project boundaries",
    "source directory",
    "src project",
    "what user request initiated",
    "system-prompt size",
    "system prompt size",
    "exact count",
    "exact duration",
    "session timeline",
)

BROAD_REVIEW_QUESTION_TERMS = (
    "all projects",
    "each confirmed",
    "each inventoried",
    "each major project",
    "for each",
    "for each major project",
    "for every project",
    "multiple projects",
    "which repositories are confirmed first-party",
)

PROFILE_QUESTION_TERMS = (
    "biography",
    "career",
    "education",
    "employment",
    "identity",
    "military",
    "personal profile",
    "preferred name",
    "privacy",
    "professional title",
)

ATTRIBUTION_DECISION_TERMS = (
    "authorship",
    "contribution",
    "credited",
    "first-party",
    "fork",
    "ownership",
    "personally authored",
    "personally built",
    "personally coded",
    "personally directed",
    "third-party",
)

LIFECYCLE_DECISION_TERMS = (
    "active date",
    "archived",
    "current state",
    "current status",
    "deployed",
    "deployment state",
    "end date",
    "last active",
    "paused",
    "project status",
    "start date",
    "timeline",
    "working locally",
)

DISCLOSURE_DECISION_TERMS = (
    "approved for public",
    "confidential",
    "disclosure",
    "private or public",
    "public attribution",
    "public biography",
    "public-facing",
    "safe to share",
)


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _mentions_project(text: str, name: str) -> bool:
    normalized = name.strip().casefold()
    if not normalized:
        return False
    return bool(
        re.search(
            rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])",
            text,
        )
    )


def should_create_review_question(
    item: dict[str, Any],
    *,
    project_ids: set[str] | None = None,
    project_names: dict[str, str] | None = None,
) -> bool:
    """Admit only one-decision, one-destination owner questions."""

    question = str(item.get("question") or "").strip()
    text = " ".join(
        str(value)
        for value in (item.get("subject"), item.get("description"), question)
        if value
    ).casefold()
    if not question:
        return False
    if any(term in text for term in AGENT_RESOLVABLE_REVIEW_TERMS):
        return False
    if any(term in text for term in BROAD_REVIEW_QUESTION_TERMS):
        return False
    project_ids = set(project_ids or set())
    project_names = project_names or {}
    if len(project_ids) > 1:
        return False

    decision_domains = sum(
        (
            _contains_any(text, ATTRIBUTION_DECISION_TERMS),
            _contains_any(text, LIFECYCLE_DECISION_TERMS),
            _contains_any(text, DISCLOSURE_DECISION_TERMS),
        )
    )
    if decision_domains > 1:
        return False

    if not project_ids:
        return _contains_any(text, PROFILE_QUESTION_TERMS)

    # A project question must resolve from its evidence to exactly one real
    # project and name that project. This blocks packets from turning several
    # unrelated repositories into a single owner question.
    project_id = next(iter(project_ids))
    project_name = project_names.get(project_id, "")
    if not project_name or not _mentions_project(text, project_name):
        return False
    mentioned_projects = {
        candidate_id
        for candidate_id, candidate_name in project_names.items()
        if len(candidate_name.strip()) >= 4
        and _mentions_project(text, candidate_name)
    }
    if mentioned_projects - {project_id}:
        return False
    return decision_domains == 1


def is_auto_resolvable_question(item: dict[str, Any]) -> bool:
    """Return true only for objective questions future machine evidence can settle."""

    if item.get("kind") != "clarification" or item.get("status") != "pending":
        return False
    payload = item.get("payload") or {}
    question = str(payload.get("question") or item.get("claim") or "").strip()
    text = " ".join((str(item.get("subject") or ""), question)).casefold()
    if not question or question.casefold().startswith("should "):
        return False
    if any(term in text for term in HUMAN_ONLY_TERMS):
        return False
    return any(term in text for term in OBJECTIVE_FOLLOWUP_TERMS)


def pending_question_context(
    observations: list[dict[str, Any]], *, limit: int = 24
) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for item in observations:
        if not is_auto_resolvable_question(item):
            continue
        payload = item.get("payload") or {}
        result.append(
            {
                "id": str(item["id"]),
                "subject": str(item.get("subject") or "Objective project follow-up")[:300],
                "question": str(payload.get("question") or item.get("claim") or "")[:2000],
            }
        )
        if len(result) >= limit:
            break
    return result
