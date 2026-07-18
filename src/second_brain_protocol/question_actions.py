from __future__ import annotations

from pathlib import Path
from typing import Any

from .publisher import write_bootstrap_review_artifacts, write_review_artifacts
from .review import build_review_groups
from .security import sanitize_text
from .state import StateStore


def attribute_question(
    vault: Path,
    store: StateStore,
    observation_id: str,
    project_id: str,
) -> dict[str, Any]:
    """Apply an explicit owner correction to a pending question's destination project."""

    item = store.observation(observation_id)
    if item is None:
        raise KeyError(observation_id)
    if item.get("kind") != "clarification" or item.get("status") != "pending":
        raise ValueError("Only pending clarification questions can be attributed")
    store.set_observation_project_override(observation_id, [project_id])
    if store.bootstrap_state().get("state") == "awaiting_review":
        write_bootstrap_review_artifacts(vault, store)
    else:
        write_review_artifacts(vault, store)
    return {
        "id": observation_id,
        "status": "pending",
        "project_ids": [project_id],
        "attribution": "explicit_owner_correction",
    }


def answer_question(
    vault: Path,
    store: StateStore,
    observation_id: str,
    answer: str,
) -> dict[str, Any]:
    """Record one explicit owner answer and close its pending clarification."""

    clean_answer = sanitize_text(answer, max_chars=2000).strip()
    if not clean_answer:
        raise ValueError("An answer is required")

    item = store.observation(observation_id)
    if item is None:
        raise KeyError(observation_id)
    if item.get("kind") != "clarification" or item.get("status") != "pending":
        raise ValueError("Only pending clarification questions can be answered")

    payload = item.get("payload") or {}
    nested_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
    question = sanitize_text(
        str(payload.get("question") or nested_payload.get("question") or item.get("claim") or ""),
        max_chars=2000,
    ).strip()
    groups = build_review_groups([item])
    group_key = str(groups[0]["key"]) if groups else "questions-technical"
    scope = "profile" if group_key == "questions-profile-privacy" else "project"
    project_override = payload.get("project_ids_override")
    project_ids: set[str] = (
        {str(value) for value in project_override if value}
        if isinstance(project_override, list) and project_override
        else set()
    )
    if not project_ids:
        for row in store.evidence_by_ids(list(item.get("evidence_refs") or [])):
            row_payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            if row.get("project_id"):
                project_ids.add(str(row["project_id"]))
            project_ids.update(str(value) for value in row_payload.get("project_ids", []) if value)
    project_id = next(iter(project_ids)) if scope == "project" and len(project_ids) == 1 else None
    store.add_evidence(
        source_type="interview",
        source_ref=f"review-resolution:{observation_id}",
        kind="explicit_profile_answer" if scope == "profile" else "explicit_project_answer",
        project_id=project_id,
        payload={
            "question_id": observation_id,
            "question": question,
            "answer": clean_answer,
            "explicit": True,
            "scope": scope,
            "destination": "professional_profile" if scope == "profile" else "project_knowledge",
            "project_ids": sorted(project_ids) if scope == "project" else [],
        },
    )
    store.decide_observation(observation_id, "resolved", clean_answer)

    if store.bootstrap_state().get("state") == "awaiting_review":
        write_bootstrap_review_artifacts(vault, store)
    else:
        write_review_artifacts(vault, store)
    return {
        "id": observation_id,
        "status": "resolved",
        "answer_saved": True,
    }
