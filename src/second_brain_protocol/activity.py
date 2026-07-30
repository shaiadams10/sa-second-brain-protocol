from __future__ import annotations

from collections import Counter
from datetime import datetime
import re
from typing import Any

from .markdown import slugify
from .security import sanitize_text


MAX_CHANGED_PATHS = 100
MAX_COMMITS = 25
CHANGE_LABELS = {
    "added": "project added",
    "classification_changed": "classification changed",
    "commits_added": "commits added",
    "files_changed": "files changed",
    "head_changed": "Git head changed",
    "lifecycle_changed": "lifecycle changed",
    "moved": "project moved",
    "project_brain_changed": "project guidance changed",
    "reactivated": "project reactivated",
    "removed": "project removed",
    "renamed": "project renamed",
    "stack_changed": "technology stack changed",
    "working_tree_changed": "working tree changed",
}


def _manifest(project: dict[str, Any] | None) -> dict[str, tuple[int, int]]:
    if not project:
        return {}
    result: dict[str, tuple[int, int]] = {}
    for path, value in (project.get("manifest") or {}).items():
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            result[str(path)] = (int(value[0]), int(value[1]))
    return result


def _manifest_value_changed(before: tuple[int, int], after: tuple[int, int]) -> bool:
    # ``(0, 0)`` is the scanner's stable Gitlink/directory sentinel.  Ignore the
    # one-time transition from the legacy, platform-dependent directory stat.
    if after == (0, 0):
        return False
    return before != after


def _new_commits(
    previous: dict[str, Any] | None, current: dict[str, Any]
) -> list[dict[str, Any]]:
    current_rows = list((current.get("git_history") or {}).get("recent_commits") or [])
    if not current_rows:
        return []
    previous_head = str((previous or {}).get("head_commit") or "")
    previous_ids = {
        str(item.get("commit") or "")
        for item in ((previous or {}).get("git_history") or {}).get("recent_commits", [])
    }
    result = []
    for item in current_rows:
        commit = str(item.get("commit") or "")
        if previous_head and previous_head.startswith(commit):
            break
        if commit and commit in previous_ids:
            continue
        result.append(
            {
                "commit": commit,
                "timestamp": item.get("timestamp"),
                "subject": item.get("subject"),
                "email_confirmed_as_user": bool(item.get("email_confirmed_as_user")),
            }
        )
        if len(result) >= MAX_COMMITS:
            break
    return result


def build_project_delta(
    previous: dict[str, Any] | None,
    current: dict[str, Any] | None,
    *,
    was_present: bool | None,
) -> dict[str, Any] | None:
    """Build a bounded, path-safe before/after project activity record."""
    if current is None:
        if previous is None or was_present is False:
            return None
        return {
            "project_id": previous["id"],
            "project_name": previous["name"],
            "classification": previous.get("classification"),
            "change_types": ["removed"],
            "previous_head": previous.get("head_commit"),
            "current_head": None,
            "details": {"source_no_longer_present": True},
        }

    change_types: list[str] = []
    details: dict[str, Any] = {}
    if previous is None:
        change_types.append("added")
    elif was_present is False:
        change_types.append("reactivated")

    if previous:
        if previous.get("name") != current.get("name"):
            change_types.append("renamed")
            details["previous_name"] = previous.get("name")
        if previous.get("local_path") != current.get("local_path"):
            change_types.append("moved")
            details["path_changed"] = True
        if previous.get("lifecycle") != current.get("lifecycle"):
            change_types.append("lifecycle_changed")
            details["lifecycle"] = {
                "before": previous.get("lifecycle"),
                "after": current.get("lifecycle"),
            }
        if previous.get("classification") != current.get("classification"):
            change_types.append("classification_changed")
            details["classification"] = {
                "before": previous.get("classification"),
                "after": current.get("classification"),
            }

    previous_manifest = _manifest(previous)
    current_manifest = _manifest(current)
    added_files = sorted(set(current_manifest) - set(previous_manifest))
    removed_files = sorted(set(previous_manifest) - set(current_manifest))
    modified_files = sorted(
        path
        for path in set(previous_manifest) & set(current_manifest)
        if _manifest_value_changed(previous_manifest[path], current_manifest[path])
    )
    if added_files or removed_files or modified_files:
        change_types.append("files_changed")
        details["files"] = {
            "added_count": len(added_files),
            "removed_count": len(removed_files),
            "modified_count": len(modified_files),
            "added": added_files[:MAX_CHANGED_PATHS],
            "removed": removed_files[:MAX_CHANGED_PATHS],
            "modified": modified_files[:MAX_CHANGED_PATHS],
        }

    before_stack = set((previous or {}).get("tech_stack") or [])
    after_stack = set(current.get("tech_stack") or [])
    if before_stack != after_stack:
        change_types.append("stack_changed")
        details["tech_stack"] = {
            "added": sorted(after_stack - before_stack),
            "removed": sorted(before_stack - after_stack),
        }

    before_brain = set((previous or {}).get("second_brain_files") or [])
    after_brain = set(current.get("second_brain_files") or [])
    if before_brain != after_brain:
        change_types.append("project_brain_changed")
        details["project_brain"] = {
            "added": sorted(after_brain - before_brain)[:MAX_CHANGED_PATHS],
            "removed": sorted(before_brain - after_brain)[:MAX_CHANGED_PATHS],
        }

    commits = _new_commits(previous, current)
    if commits:
        change_types.append("commits_added")
        details["new_commits"] = commits

    before_worktree = set(((previous or {}).get("git_history") or {}).get("working_tree") or [])
    after_worktree = set((current.get("git_history") or {}).get("working_tree") or [])
    if before_worktree != after_worktree:
        change_types.append("working_tree_changed")
        details["working_tree"] = {
            "new_entries": sorted(after_worktree - before_worktree)[:MAX_CHANGED_PATHS],
            "resolved_entries": sorted(before_worktree - after_worktree)[:MAX_CHANGED_PATHS],
        }

    if previous and previous.get("head_commit") != current.get("head_commit"):
        change_types.append("head_changed")

    change_types = list(dict.fromkeys(change_types))
    if not change_types:
        return None
    return {
        "project_id": current["id"],
        "project_name": current["name"],
        "classification": current.get("classification"),
        "change_types": change_types,
        "previous_head": (previous or {}).get("head_commit"),
        "current_head": current.get("head_commit"),
        "details": details,
    }


def _project_link(name: str) -> str:
    return f"[[Projects/{slugify(name)}|{name}]]"


def _request_text(payload: dict[str, Any]) -> str:
    messages = payload.get("user_messages") or []
    if not messages:
        return ""
    text = sanitize_text(str(messages[0].get("text") or ""), max_chars=600)
    match = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", text, re.DOTALL)
    if match:
        text = match.group(1)
    return re.sub(r"\s+", " ", text).strip()


def session_recap(payload: dict[str, Any]) -> str:
    """Create a safe deterministic fallback when the model omits a session recap."""

    request = _request_text(payload)
    assistant_text = " ".join(
        sanitize_text(str(item.get("text") or ""), max_chars=1000)
        for item in (payload.get("assistant_results") or [])[:3]
    ).casefold()
    if request.casefold() in {"test", "connectivity test", "connection test"} and any(
        phrase in assistant_text
        for phrase in (
            "successfully connected",
            "successfully loaded",
            "loaded the workspace",
            "loaded your workspace",
        )
    ):
        return "Connectivity test completed; the workspace loaded successfully."

    visible_users = len(payload.get("user_messages") or [])
    assistant_count = len(payload.get("assistant_results") or [])
    tool_count = sum(
        int(value) for value in (payload.get("tool_usage") or {}).values()
    )
    if not visible_users:
        return "Backfilled session metadata; no user-authored message was available for synthesis."
    parts = [
        f"{visible_users} user message{'s' if visible_users != 1 else ''}",
        f"{assistant_count} selected result{'s' if assistant_count != 1 else ''}",
    ]
    if tool_count:
        parts.append(f"{tool_count} tool action{'s' if tool_count != 1 else ''}")
    return "Session reviewed: " + ", ".join(parts) + "."


def _session_time(payload: dict[str, Any]) -> str:
    value = str(payload.get("started_at") or "")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return "time unavailable"
    return parsed.strftime("%H:%M")


def activity_markdown(
    evidence: list[dict[str, Any]],
    *,
    pattern_stats: dict[str, int] | None = None,
    project_names_by_id: dict[str, str] | None = None,
    session_summaries: list[dict[str, Any]] | None = None,
    period: str | None = None,
    learning: str | None = None,
    stewardship: str | None = None,
) -> str:
    deltas = [item for item in evidence if item.get("kind") == "project_delta"]
    sessions = [item for item in evidence if item.get("kind") == "session_digest"]
    change_counts = Counter(
        change
        for item in deltas
        for change in item.get("payload", {}).get("change_types", [])
    )
    project_names = sorted(
        {
            str(item.get("payload", {}).get("project_name"))
            for item in deltas
            if item.get("payload", {}).get("project_name")
        }
    )
    session_projects = sorted(
        {
            str(project_id)
            for item in sessions
            for project_id in item.get("payload", {}).get("project_ids", [])
        }
    )
    attributed_sessions = [
        item for item in sessions if item.get("payload", {}).get("project_ids")
    ]
    source_counts = Counter(
        str(item.get("payload", {}).get("source") or "unknown").casefold()
        for item in sessions
    )
    names_by_id = project_names_by_id or {}
    recaps = {
        str(item.get("evidence_ref")): sanitize_text(
            str(item.get("summary") or ""), max_chars=1200
        ).strip()
        for item in (session_summaries or [])
        if item.get("evidence_ref") and item.get("summary")
    }
    lines: list[str] = []
    if deltas:
        lines.extend(["### Project changes", ""])
        for item in sorted(
            deltas,
            key=lambda row: str(row.get("payload", {}).get("project_name") or ""),
        ):
            payload = item.get("payload") or {}
            name = str(payload.get("project_name") or "Unknown project")
            labels = [
                CHANGE_LABELS.get(str(change), str(change).replace("_", " "))
                for change in payload.get("change_types") or []
            ]
            lines.append(f"- {_project_link(name)} — {', '.join(labels)}.")

    if sessions:
        lines.extend(([""] if lines else []) + ["### Sessions reviewed", ""])
        for item in sorted(
            sessions,
            key=lambda row: str(
                (row.get("payload") or {}).get("started_at")
                or row.get("occurred_at")
                or row.get("created_at")
                or ""
            ),
        ):
            payload = item.get("payload") or {}
            project_ids = [str(value) for value in payload.get("project_ids") or []]
            name = next(
                (names_by_id[value] for value in project_ids if value in names_by_id),
                "Unattributed session",
            )
            link = _project_link(name) if name != "Unattributed session" else name
            source = str(payload.get("source") or "agent").title()
            recap = recaps.get(str(item.get("id"))) or session_recap(payload)
            occurred = str(
                payload.get("started_at")
                or item.get("occurred_at")
                or item.get("created_at")
                or ""
            )
            occurred_date = occurred[:10]
            backfill = (
                f" · backfill from {occurred_date}"
                if period and occurred_date and occurred_date != period
                else ""
            )
            lines.append(
                f"- {link} · {source} · {_session_time(payload)}{backfill} — {recap}"
            )

    if learning:
        lines.extend(([""] if lines else []) + [learning.strip()])

    if stewardship:
        lines.extend(([""] if lines else []) + [stewardship.strip()])

    lines.extend(([""] if lines else []) + ["### Coverage details", ""])
    lines.append(
        f"- Projects with detected changes: {len(deltas)}"
        + (f" - {', '.join(project_names[:12])}" if project_names else "")
    )
    if change_counts:
        lines.append(
            "- Change signals: "
            + ", ".join(f"{name} ({count})" for name, count in sorted(change_counts.items()))
        )
    source_detail = ", ".join(
        f"{name.title()} {count}"
        for name, count in sorted(source_counts.items())
        if count
    )
    unattributed = len(sessions) - len(attributed_sessions)
    lines.append(
        f"- Agent sessions reviewed: {len(sessions)} total - "
        f"{len(attributed_sessions)} linked to {len(session_projects)} projects; "
        f"{unattributed} analyzed profile-only"
        + (f"; {source_detail}" if source_detail else "")
    )
    stats = pattern_stats or {}
    lines.append(
        "- Knowledge signals: "
        f"{stats.get('tracking', 0)} tracking, "
        f"{stats.get('promoted', 0)} promoted, "
        f"{stats.get('pending', 0)} awaiting review"
    )
    return "\n".join(lines)
