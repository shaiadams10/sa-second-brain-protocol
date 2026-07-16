from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from .state import canonical_hash


GROUP_SPECS: dict[str, dict[str, str]] = {
    "questions-profile-privacy": {
        "title": "Profile, identity, and disclosure questions",
        "filename": "ProfileAndPrivacyQuestions.md",
        "section": "questions",
        "mode": "answer",
        "description": "Questions about identity, career wording, military privacy, and what may be shared publicly.",
        "answer_template": "Answer with: private only / safe for career use / safe to share publicly, followed by any correction.",
    },
    "questions-attribution": {
        "title": "Project ownership and attribution questions",
        "filename": "ProjectAttributionQuestions.md",
        "section": "questions",
        "mode": "answer",
        "description": "Questions that prevent the brain from crediting third-party, forked, or collaborative work incorrectly.",
        "answer_template": "Answer with: first-party / modified fork / third-party reference / collaboration, then name the parts you directed or implemented.",
    },
    "questions-project-state": {
        "title": "Project status and timeline questions",
        "filename": "ProjectStatusQuestions.md",
        "section": "questions",
        "mode": "answer",
        "description": "Questions about what is working, deployed, paused, archived, or still experimental.",
        "answer_template": "Answer with: working locally / deployed / prototype / paused / archived / unknown, plus an approximate date if useful.",
    },
    "questions-technical": {
        "title": "Technical detail questions",
        "filename": "TechnicalQuestions.md",
        "section": "questions",
        "mode": "answer",
        "description": "Lower-priority technical ambiguities that may be answered gradually as the related work resumes.",
        "answer_template": "Answer with the correction you know, or say defer / unknown. A deferred question simply remains pending.",
    },
    "public-profile": {
        "title": "Public profile facts",
        "filename": "PublicProfileFacts.md",
        "section": "public",
        "mode": "review",
        "description": "Identity, education, employment, and military claims that could later support resumes, LinkedIn, or biographies.",
        "answer_template": "Approve only when both the fact and its public wording are accurate.",
    },
    "public-positioning": {
        "title": "Public positioning and work style",
        "filename": "PublicPositioning.md",
        "section": "public",
        "mode": "review",
        "description": "Career direction, preferences, voice, and work-style claims intended for public-facing use.",
        "answer_template": "Approve only when every statement sounds accurate and appropriate for public use.",
    },
    "public-projects": {
        "title": "Public project and technical claims",
        "filename": "PublicProjectClaims.md",
        "section": "public",
        "mode": "review",
        "description": "Project, architecture, implementation, and lesson claims that may be useful in a portfolio or interview.",
        "answer_template": "Check authorship, successful implementation, confidentiality, and wording before approval.",
    },
    "private-profile": {
        "title": "Private experience and capability observations",
        "filename": "PrivateExperienceAndCapabilities.md",
        "section": "private",
        "mode": "review",
        "description": "Experience and capability observations that remain private unless separately approved for public use.",
        "answer_template": "Approve accurate private memory; reject incorrect attribution; leave uncertain items pending.",
    },
    "private-projects": {
        "title": "Private project knowledge",
        "filename": "PrivateProjectKnowledge.md",
        "section": "private",
        "mode": "review",
        "description": "Decisions, lessons, and project state useful inside the private brain.",
        "answer_template": "Approve accurate durable knowledge; leave temporary or uncertain details pending.",
    },
    "private-patterns": {
        "title": "Private preferences and recurring patterns",
        "filename": "PrivatePatterns.md",
        "section": "private",
        "mode": "review",
        "description": "Inferred preferences, voice traits, and work patterns that have not met automatic-promotion thresholds.",
        "answer_template": "Approve only patterns that feel consistently true; reject overgeneralizations.",
    },
    "private-goals-facts": {
        "title": "Private goals and contextual facts",
        "filename": "PrivateGoalsAndFacts.md",
        "section": "private",
        "mode": "review",
        "description": "Private goals and contextual facts that may be useful but are not public career claims.",
        "answer_template": "Approve durable context; leave short-lived or low-value items pending.",
    },
}


ATTRIBUTION_TERMS = (
    "ownership",
    "authorship",
    "attribution",
    "contribution",
    "first-party",
    "third-party",
    "fork",
    "project boundaries",
    "folder project",
    "relationship",
    "remote identity",
    "identifier collision",
    "unclassified repository",
)
PROJECT_STATE_TERMS = (
    "status",
    "timeline",
    "completion",
    "working",
    "deployed",
    "deployment",
    "release",
    "validation",
    "current state",
    "architecture",
    "scope",
    "duration",
    "verification",
    "launch method",
)
PROFILE_PRIVACY_TERMS = (
    "user identity",
    "historical identity",
    "military",
    "public claim",
    "public disclosure",
    "confidential",
    "privacy",
    "career",
    "capability claims",
)


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    folded = text.casefold()
    return any(term in folded for term in terms)


def _clarification_key(item: dict[str, Any]) -> str:
    text = " ".join(
        str(value)
        for value in (
            item.get("subject"),
            item.get("claim"),
            item.get("payload", {}).get("question"),
        )
        if value
    )
    if _contains_any(text, ATTRIBUTION_TERMS):
        return "questions-attribution"
    if _contains_any(text, PROFILE_PRIVACY_TERMS):
        return "questions-profile-privacy"
    if _contains_any(text, PROJECT_STATE_TERMS):
        return "questions-project-state"
    return "questions-technical"


def _group_key(item: dict[str, Any]) -> str:
    if item["kind"] == "clarification":
        return _clarification_key(item)
    public = bool(item.get("payload", {}).get("public_claim"))
    kind = item["kind"]
    if public:
        if kind in {"explicit_fact", "experience", "education", "military"}:
            return "public-profile"
        if kind in {"goal", "preference", "work_style", "voice_style", "personality"}:
            return "public-positioning"
        return "public-projects"
    if kind in {"experience", "education", "military"}:
        return "private-profile"
    if kind in {"project_fact", "decision", "lesson"}:
        return "private-projects"
    if kind in {"preference", "work_style", "voice_style", "personality"}:
        return "private-patterns"
    return "private-goals-facts"


def build_review_groups(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        buckets[_group_key(item)].append(item)

    groups: list[dict[str, Any]] = []
    for key, spec in GROUP_SPECS.items():
        members = sorted(buckets.get(key, []), key=lambda item: (item["subject"], item["id"]))
        if not members:
            continue
        snapshot = canonical_hash([item["id"] for item in members])[:10]
        groups.append(
            {
                "id": f"rvg-{key}-{snapshot}",
                "key": key,
                "title": spec["title"],
                "filename": spec["filename"],
                "section": spec["section"],
                "mode": spec["mode"],
                "description": spec["description"],
                "answer_template": spec["answer_template"],
                "count": len(members),
                "items": members,
            }
        )
    return groups


def review_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    groups = build_review_groups(items)
    return {
        "pending": len(items),
        "needs_answers": sum(group["count"] for group in groups if group["section"] == "questions"),
        "public_claims": sum(group["count"] for group in groups if group["section"] == "public"),
        "private_review": sum(group["count"] for group in groups if group["section"] == "private"),
        "groups": [
            {
                "id": group["id"],
                "key": group["key"],
                "title": group["title"],
                "count": group["count"],
                "mode": group["mode"],
                "section": group["section"],
                "preview": [item["claim"] for item in group["items"][:3]],
            }
            for group in groups
        ],
    }


def find_review_group(items: list[dict[str, Any]], group_id: str) -> dict[str, Any]:
    matches = [group for group in build_review_groups(items) if group["id"] == group_id]
    if not matches:
        raise KeyError(
            f"Review group not found or its membership changed: {group_id}. Regenerate the digest and use its current token."
        )
    return matches[0]
