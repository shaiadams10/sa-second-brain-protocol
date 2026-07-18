from __future__ import annotations

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
    "for each major project",
    "for every project",
    "which repositories are confirmed first-party",
)


def should_create_review_question(item: dict[str, Any]) -> bool:
    """Reject low-value or machine-resolvable questions before they enter review."""

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
    return True


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
