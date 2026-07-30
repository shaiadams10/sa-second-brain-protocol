from __future__ import annotations

from collections import Counter
from typing import Any

from .state import StateStore


FEATURE_LABELS = {
    "content:career-fact": "verified career, education, and service facts",
    "content:consequential-decision": "consequential project decisions and durable lessons",
    "content:durable-design-preference": "durable cross-project design preferences",
    "content:exhaustive-analysis-generalization": "exhaustive source-analysis requests generalized into personality",
    "content:implementation-detail-generalization": "implementation mechanics generalized into personal knowledge",
    "content:one-off-task-instruction": "one-off task instructions promoted as durable knowledge",
    "content:project-status-snapshot": "temporary project-status snapshots",
    "content:temporary-creative-brief": "single deliverable briefs and transient creative specifications",
    "content:validated-project-knowledge": "attributable, validated project knowledge",
    "question:attribution": "ownership and contribution questions",
    "question:project-state": "project status and lifecycle questions",
    "question:profile-privacy": "career, identity, and privacy questions",
    "question:technical-followup": "technical lookup and implementation follow-up questions",
    "question:generic-missing-information": "generic missing-information questions without a consequential owner decision",
}

NEGATIVE_GUARD_FEATURES = {
    "content:exhaustive-analysis-generalization",
    "content:implementation-detail-generalization",
    "content:one-off-task-instruction",
    "content:project-status-snapshot",
    "content:temporary-creative-brief",
}

QUESTION_GUARD_FEATURES = {
    "question:attribution",
    "question:project-state",
    "question:profile-privacy",
    "question:technical-followup",
    "question:generic-missing-information",
}


def observation_feedback_features(observation: dict[str, Any]) -> set[str]:
    """Classify a reviewed card without asking the owner to explain the decision."""

    kind = str(observation.get("kind") or "")
    text = " ".join(
        str(value)
        for value in (observation.get("subject"), observation.get("claim"))
        if value
    ).casefold()
    features: set[str] = set()
    if kind in {"experience", "education", "military"}:
        features.add("content:career-fact")
    if kind in {"decision", "lesson"}:
        features.add("content:consequential-decision")
    if kind in {"project_fact", "decision", "lesson"} and any(
        term in text
        for term in (
            "deployed",
            "validated",
            "first-party",
            "third-party",
            "security boundary",
            "privacy boundary",
            "production build",
            "successfully",
        )
    ):
        features.add("content:validated-project-knowledge")
    if kind in {"preference", "work_style"} and any(
        term in text
        for term in (
            "premium",
            "realistic",
            "generic ai",
            "typography",
            "visual polish",
            "smooth motion",
            "design",
        )
    ):
        features.add("content:durable-design-preference")
    if any(
        term in text
        for term in (
            "exhaustive",
            "every relevant reference",
            "complete source review",
            "complete source-file",
            "full-file reading",
            "exact code patterns",
            "summaries of summaries",
        )
    ):
        features.add("content:exhaustive-analysis-generalization")
    if any(
        term in text
        for term in (
            "preview server",
            "local network",
            "svgl.app",
            "one worktree",
            "local branches",
            "ask which mode",
            "planned before implementation",
            "stopping local ai work",
        )
    ):
        features.add("content:one-off-task-instruction")
    if any(
        term in text
        for term in (
            "timeline scrubbing",
            "annotation pins",
            "persistent notes",
            "seek-safe",
            "paused-timeline",
            "explicit export stages",
            "master duration",
        )
    ):
        features.add("content:implementation-detail-generalization")
    if any(
        term in text
        for term in (
            "45-second",
            "aspect ratios",
            "cta",
            "promotional demo specification",
            "single deliverable",
        )
    ):
        features.add("content:temporary-creative-brief")
    if kind == "project_fact" and any(
        term in text
        for term in (
            "as of ",
            "passing tests",
            "current status",
            "remained incomplete",
            "production deployment",
            "reported healthy",
        )
    ):
        features.add("content:project-status-snapshot")
    return features


def review_question_feedback_features(item: dict[str, Any]) -> set[str]:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    text = " ".join(
        str(value)
        for value in (
            item.get("subject"),
            item.get("description"),
            item.get("question"),
            item.get("claim"),
            payload.get("question"),
            payload.get("review_reason"),
        )
        if value
    ).casefold()
    features: set[str] = set()
    if any(
        term in text
        for term in (
            "owner",
            "ownership",
            "author",
            "attribution",
            "contribution",
            "first-party",
            "third-party",
            "fork",
        )
    ):
        features.add("question:attribution")
    if any(
        term in text
        for term in (
            "status",
            "deployed",
            "working",
            "prototype",
            "paused",
            "archived",
            "milestone",
            "timeline",
            "validation",
        )
    ):
        features.add("question:project-state")
    if any(
        term in text
        for term in (
            "career",
            "public",
            "private",
            "privacy",
            "identity",
            "employment",
            "education",
            "military",
        )
    ):
        features.add("question:profile-privacy")
    if any(
        term in text
        for term in (
            "latest session",
            "last message",
            "exact count",
            "lookup",
            "install command",
            "repository url",
            "technical",
        )
    ):
        features.add("question:technical-followup")
    if not features and any(
        term in text for term in ("missing information", "missing_information", "unclear", "unknown")
    ):
        features.add("question:generic-missing-information")
    return features


def knowledge_feedback_profile(store: StateStore) -> dict[str, Any]:
    """Aggregate confirmations and removals into bounded synthesis guidance."""

    counts: dict[str, Counter[str]] = {}
    reviewed = 0
    reviewed_questions = 0
    for observation_id, feedback in store.knowledge_feedback().items():
        observation = store.observation(observation_id)
        if observation is None:
            continue
        decision = str(feedback.get("decision") or "")
        if decision not in {"liked", "disliked"}:
            continue
        reviewed += 1
        for feature in observation_feedback_features(observation):
            counts.setdefault(feature, Counter())[decision] += 1

    for observation_id, feedback in store.review_feedback().items():
        observation = store.observation(observation_id)
        if observation is None:
            continue
        decision = str(feedback.get("decision") or "")
        if decision not in {"answered", "dismissed"}:
            continue
        reviewed_questions += 1
        normalized = "liked" if decision == "answered" else "disliked"
        for feature in review_question_feedback_features(observation):
            counts.setdefault(feature, Counter())[normalized] += 1

    avoid = []
    prefer = []
    for feature, decisions in sorted(counts.items()):
        confirmed = int(decisions["liked"])
        removed = int(decisions["disliked"])
        total = confirmed + removed
        if total < 2:
            continue
        if removed >= 2 and removed / total >= 0.67:
            avoid.append(
                {
                    "signal": FEATURE_LABELS[feature],
                    "feature": feature,
                    "removed": removed,
                    "confirmed": confirmed,
                    "confidence": round(removed / total, 2),
                }
            )
        elif confirmed >= 2 and confirmed / total >= 0.67:
            prefer.append(
                {
                    "signal": FEATURE_LABELS[feature],
                    "feature": feature,
                    "confirmed": confirmed,
                    "removed": removed,
                    "confidence": round(confirmed / total, 2),
                }
            )
    return {
        "version": 1,
        "reviewed_cards": reviewed,
        "reviewed_questions": reviewed_questions,
        "avoid": sorted(avoid, key=lambda item: (-item["confidence"], -item["removed"]))[:8],
        "prefer": sorted(prefer, key=lambda item: (-item["confidence"], -item["confirmed"]))[:8],
    }


def should_suppress_candidate(
    observation: dict[str, Any], feedback_profile: dict[str, Any]
) -> bool:
    avoided = {
        str(item.get("feature"))
        for item in feedback_profile.get("avoid", [])
        if float(item.get("confidence") or 0) >= 0.75
        and str(item.get("feature")) in NEGATIVE_GUARD_FEATURES
    }
    return bool(observation_feedback_features(observation) & avoided)


def should_suppress_review_question(
    item: dict[str, Any], feedback_profile: dict[str, Any]
) -> bool:
    avoided = {
        str(entry.get("feature"))
        for entry in feedback_profile.get("avoid", [])
        if float(entry.get("confidence") or 0) >= 0.75
        and str(entry.get("feature")) in QUESTION_GUARD_FEATURES
    }
    return bool(review_question_feedback_features(item) & avoided)
