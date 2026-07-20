from __future__ import annotations

from pathlib import Path
from typing import Any

from .publisher import write_bootstrap_review_artifacts, write_review_artifacts
from .review import build_review_groups
from .security import sanitize_text
from .state import StateStore
from .state import utc_now


QUESTION_DISMISSAL_REASON = "Dismissed as not relevant by the vault owner"


def _set_review_feedback(
    connection, observation_id: str, decision: str
) -> str | None:
    row = connection.execute(
        "SELECT decision FROM review_feedback WHERE observation_id=?",
        (observation_id,),
    ).fetchone()
    previous = str(row["decision"]) if row else None
    connection.execute(
        """INSERT INTO review_feedback(observation_id,decision,updated_at) VALUES(?,?,?)
        ON CONFLICT(observation_id) DO UPDATE SET
        decision=excluded.decision,updated_at=excluded.updated_at""",
        (observation_id, decision, utc_now()),
    )
    return previous


def dismiss_question(
    vault: Path, store: StateStore, observation_id: str
) -> dict[str, Any]:
    """Dismiss one irrelevant pending question and retain category feedback."""

    with store.transaction() as connection:
        row = connection.execute(
            "SELECT kind,status FROM observations WHERE id=?", (observation_id,)
        ).fetchone()
        if row is None:
            raise KeyError(observation_id)
        if row["kind"] != "clarification" or row["status"] != "pending":
            raise ValueError("Only pending clarification questions can be dismissed")
        previous = _set_review_feedback(connection, observation_id, "dismissed")
        connection.execute(
            """UPDATE observations SET status='rejected',rejection_reason=?,updated_at=?
            WHERE id=?""",
            (QUESTION_DISMISSAL_REASON, utc_now(), observation_id),
        )
        connection.execute(
            """INSERT INTO review_feedback_events(
            observation_id,action,previous_decision,previous_status,created_at
            ) VALUES(?,?,?,?,?)""",
            (observation_id, "dismiss", previous, "pending", utc_now()),
        )
    if store.bootstrap_state().get("state") == "awaiting_review":
        write_bootstrap_review_artifacts(vault, store)
    else:
        write_review_artifacts(vault, store)
    return {
        "id": observation_id,
        "status": "rejected",
        "dismissed": True,
        "learning_updated": True,
    }


def undo_last_question_dismissal(vault: Path, store: StateStore) -> dict[str, Any]:
    event = store.last_question_dismissal()
    if event is None:
        raise RuntimeError("There is no question dismissal to undo")
    observation_id = str(event["observation_id"])
    with store.transaction() as connection:
        row = connection.execute(
            "SELECT kind,status,rejection_reason FROM observations WHERE id=?",
            (observation_id,),
        ).fetchone()
        if (
            row is None
            or row["kind"] != "clarification"
            or row["status"] != "rejected"
            or row["rejection_reason"] != QUESTION_DISMISSAL_REASON
        ):
            raise RuntimeError("The last dismissed question cannot be restored safely")
        connection.execute(
            """UPDATE observations SET status='pending',rejection_reason=NULL,updated_at=?
            WHERE id=?""",
            (utc_now(), observation_id),
        )
        previous = event.get("previous_decision")
        if previous in {"answered", "dismissed"}:
            _set_review_feedback(connection, observation_id, str(previous))
        else:
            connection.execute(
                "DELETE FROM review_feedback WHERE observation_id=?", (observation_id,)
            )
        connection.execute(
            "UPDATE review_feedback_events SET undone_at=? WHERE id=? AND undone_at IS NULL",
            (utc_now(), int(event["id"])),
        )
        connection.execute(
            """INSERT INTO review_feedback_events(
            observation_id,action,previous_decision,previous_status,created_at
            ) VALUES(?,?,?,?,?)""",
            (observation_id, "undo", "dismissed", "rejected", utc_now()),
        )
    if store.bootstrap_state().get("state") == "awaiting_review":
        write_bootstrap_review_artifacts(vault, store)
    else:
        write_review_artifacts(vault, store)
    return {"id": observation_id, "status": "pending", "restored": True}


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
    with store.transaction() as connection:
        previous = _set_review_feedback(connection, observation_id, "answered")
        connection.execute(
            """INSERT INTO review_feedback_events(
            observation_id,action,previous_decision,previous_status,created_at
            ) VALUES(?,?,?,?,?)""",
            (observation_id, "answer", previous, "pending", utc_now()),
        )

    if store.bootstrap_state().get("state") == "awaiting_review":
        write_bootstrap_review_artifacts(vault, store)
    else:
        write_review_artifacts(vault, store)
    return {
        "id": observation_id,
        "status": "resolved",
        "answer_saved": True,
    }
