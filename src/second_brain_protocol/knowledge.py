from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .config import RuntimePaths
from .markdown import GeneratedSectionError, replace_generated_section
from .state import StateStore, utc_now


CANONICAL_ROOTS = {"Identity", "Experience", "Projects", "Skills", "Memory", "Goals"}
OBSERVATION_ID = re.compile(r"^obs-[a-f0-9]{24}$")
START_MARKER = re.compile(r"^<!-- sb:generated ([a-z0-9-]+):start -->$")
END_MARKER = re.compile(r"^<!-- sb:generated ([a-z0-9-]+):end -->$")


def _validate_observation_id(observation_id: str) -> str:
    if not OBSERVATION_ID.fullmatch(observation_id):
        raise ValueError("Invalid observation ID")
    return observation_id


def _atomic_write(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".sbtmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _remove_from_generated_section(
    text: str, observation_id: str
) -> tuple[str, list[dict[str, str]]]:
    token = re.compile(rf"\^{re.escape(observation_id)}(?![a-zA-Z0-9-])")
    current_section: str | None = None
    output: list[str] = []
    removed: list[dict[str, str]] = []

    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        start = START_MARKER.fullmatch(stripped)
        end = END_MARKER.fullmatch(stripped)
        if start:
            if current_section is not None:
                raise GeneratedSectionError("Nested generated sections are not allowed")
            current_section = start.group(1)
            output.append(line)
            continue
        if end:
            if current_section != end.group(1):
                raise GeneratedSectionError("Malformed generated section markers")
            current_section = None
            output.append(line)
            continue
        if current_section is not None and token.search(line):
            removed.append({"section": current_section, "line": line.rstrip("\r\n")})
            continue
        output.append(line)

    if current_section is not None:
        raise GeneratedSectionError("Unclosed generated section marker")
    return "".join(output), removed


def _canonical_changes(
    vault: Path, observation_id: str
) -> tuple[dict[Path, str], list[dict[str, str]]]:
    originals: dict[Path, str] = {}
    updates: dict[Path, str] = {}
    occurrences: list[dict[str, str]] = []
    token = f"^{observation_id}"
    for root_name in sorted(CANONICAL_ROOTS):
        root = vault / root_name
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.md")):
            current = path.read_text(encoding="utf-8", errors="strict")
            if token not in current:
                continue
            updated, removed = _remove_from_generated_section(current, observation_id)
            if not removed:
                continue
            originals[path] = current
            updates[path] = updated
            for item in removed:
                occurrences.append(
                    {
                        "path": path.relative_to(vault).as_posix(),
                        "section": item["section"],
                        "line": item["line"],
                    }
                )
    if not occurrences:
        raise RuntimeError("No generated canonical occurrence was found for this knowledge item")
    try:
        for path, updated in updates.items():
            _atomic_write(path, updated)
    except Exception:
        _restore_files(originals)
        raise
    return originals, occurrences


def _restore_occurrences(
    vault: Path, occurrences: list[dict[str, str]]
) -> dict[Path, str]:
    originals: dict[Path, str] = {}
    updates: dict[Path, str] = {}
    grouped: dict[tuple[str, str], list[str]] = {}
    for occurrence in occurrences:
        relative = Path(str(occurrence["path"]))
        if not relative.parts or relative.parts[0] not in CANONICAL_ROOTS:
            raise RuntimeError("Knowledge undo referenced a non-canonical path")
        grouped.setdefault((relative.as_posix(), str(occurrence["section"])), []).append(
            str(occurrence["line"])
        )

    for (relative_text, section), lines in grouped.items():
        relative = Path(relative_text)
        path = (vault / relative).resolve()
        if not path.is_relative_to(vault.resolve()) or not path.is_file():
            raise RuntimeError("Knowledge undo referenced an unavailable canonical note")
        original = path.read_text(encoding="utf-8", errors="strict")
        current = updates.get(path, original)
        start = f"<!-- sb:generated {section}:start -->"
        end = f"<!-- sb:generated {section}:end -->"
        if current.count(start) != 1 or current.count(end) != 1:
            raise GeneratedSectionError(f"Malformed generated markers in {relative_text}")
        body = current.split(start, 1)[1].split(end, 1)[0].strip()
        body_lines = [] if not body else body.splitlines()
        for line in lines:
            if line not in body_lines:
                body_lines.append(line)
        updated = replace_generated_section(current, section, "\n".join(body_lines))
        originals.setdefault(path, original)
        updates[path] = updated
    try:
        for path, updated in updates.items():
            _atomic_write(path, updated)
    except Exception:
        _restore_files(originals)
        raise
    return originals


def _restore_files(originals: dict[Path, str]) -> None:
    for path, text in originals.items():
        _atomic_write(path, text)


def _previous_feedback(connection: sqlite3.Connection, observation_id: str) -> str | None:
    row = connection.execute(
        "SELECT decision FROM knowledge_feedback WHERE observation_id=?", (observation_id,)
    ).fetchone()
    return str(row["decision"]) if row else None


def _upsert_feedback(
    connection: sqlite3.Connection, observation_id: str, decision: str
) -> None:
    connection.execute(
        """INSERT INTO knowledge_feedback(observation_id,decision,updated_at) VALUES(?,?,?)
        ON CONFLICT(observation_id) DO UPDATE SET decision=excluded.decision,updated_at=excluded.updated_at""",
        (observation_id, decision, utc_now()),
    )


def like_knowledge(store: StateStore, observation_id: str) -> dict[str, Any]:
    observation_id = _validate_observation_id(observation_id)
    with store.transaction() as connection:
        observation = connection.execute(
            "SELECT status FROM observations WHERE id=?", (observation_id,)
        ).fetchone()
        if observation is None:
            raise KeyError(observation_id)
        if observation["status"] != "promoted":
            raise RuntimeError("Only promoted canonical knowledge can be confirmed")
        previous = _previous_feedback(connection, observation_id)
        _upsert_feedback(connection, observation_id, "liked")
        connection.execute(
            """INSERT INTO knowledge_feedback_events(
            observation_id,action,previous_decision,occurrences_json,created_at
            ) VALUES(?,?,?,?,?)""",
            (observation_id, "like", previous, "[]", utc_now()),
        )
    return {"id": observation_id, "decision": "liked"}


def dislike_knowledge(
    paths: RuntimePaths,
    vault: Path,
    store: StateStore,
    observation_id: str,
) -> dict[str, Any]:
    _ = paths
    observation_id = _validate_observation_id(observation_id)
    observation = store.observation(observation_id)
    if observation is None:
        raise KeyError(observation_id)
    if observation["status"] != "promoted":
        raise RuntimeError("Only promoted canonical knowledge can be removed")

    originals: dict[Path, str] = {}
    try:
        originals, occurrences = _canonical_changes(vault, observation_id)
        with store.transaction() as connection:
            current = connection.execute(
                "SELECT status FROM observations WHERE id=?", (observation_id,)
            ).fetchone()
            if current is None or current["status"] != "promoted":
                raise RuntimeError("Knowledge changed before the removal completed")
            previous = _previous_feedback(connection, observation_id)
            reason = "Rejected from the Knowledge Deck by the vault owner"
            connection.execute(
                """UPDATE observations SET status='rejected',rejection_reason=?,updated_at=?
                WHERE id=?""",
                (reason, utc_now(), observation_id),
            )
            connection.execute(
                """UPDATE pattern_signals SET status='rejected',rejection_reason=?,last_seen=?
                WHERE observation_id=?""",
                (reason, utc_now(), observation_id),
            )
            _upsert_feedback(connection, observation_id, "disliked")
            connection.execute(
                """INSERT INTO knowledge_feedback_events(
                observation_id,action,previous_decision,occurrences_json,created_at
                ) VALUES(?,?,?,?,?)""",
                (
                    observation_id,
                    "dislike",
                    previous,
                    json.dumps(occurrences, ensure_ascii=False, sort_keys=True),
                    utc_now(),
                ),
            )
            refresh_paths = {item["path"] for item in occurrences}
            store.enqueue_search_refresh(connection, refresh_paths)
    except Exception:
        if originals:
            _restore_files(originals)
        raise
    return {
        "id": observation_id,
        "decision": "disliked",
        "removed_occurrences": len(occurrences),
        "search_refresh_queued": len(refresh_paths),
        "learning_updated": True,
    }


def undo_last_dislike(
    paths: RuntimePaths,
    vault: Path,
    store: StateStore,
) -> dict[str, Any]:
    _ = paths
    event = store.last_knowledge_dislike()
    if event is None:
        raise RuntimeError("There is no knowledge removal to undo")
    observation_id = _validate_observation_id(str(event["observation_id"]))
    occurrences = list(event["occurrences"])
    originals: dict[Path, str] = {}

    try:
        originals = _restore_occurrences(vault, occurrences)
        with store.transaction() as connection:
            current = connection.execute(
                "SELECT status FROM observations WHERE id=?", (observation_id,)
            ).fetchone()
            if current is None or current["status"] != "rejected":
                raise RuntimeError("The last removed knowledge item cannot be restored safely")
            connection.execute(
                """UPDATE observations SET status='promoted',rejection_reason=NULL,updated_at=?
                WHERE id=?""",
                (utc_now(), observation_id),
            )
            connection.execute(
                """UPDATE pattern_signals SET status='promoted',rejection_reason=NULL,last_seen=?
                WHERE observation_id=?""",
                (utc_now(), observation_id),
            )
            previous = event.get("previous_decision")
            if previous == "liked":
                _upsert_feedback(connection, observation_id, "liked")
            else:
                connection.execute(
                    "DELETE FROM knowledge_feedback WHERE observation_id=?", (observation_id,)
                )
            connection.execute(
                "UPDATE knowledge_feedback_events SET undone_at=? WHERE id=? AND undone_at IS NULL",
                (utc_now(), int(event["id"])),
            )
            connection.execute(
                """INSERT INTO knowledge_feedback_events(
                observation_id,action,previous_decision,occurrences_json,created_at
                ) VALUES(?,?,?,?,?)""",
                (observation_id, "undo", "disliked", "[]", utc_now()),
            )
            refresh_paths = {item["path"] for item in occurrences}
            store.enqueue_search_refresh(connection, refresh_paths)
    except Exception:
        if originals:
            _restore_files(originals)
        raise
    return {
        "id": observation_id,
        "decision": previous or "unreviewed",
        "restored": True,
        "search_refresh_queued": len(refresh_paths),
    }
