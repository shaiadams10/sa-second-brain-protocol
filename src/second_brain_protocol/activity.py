from __future__ import annotations

from collections import Counter
from typing import Any


MAX_CHANGED_PATHS = 100
MAX_COMMITS = 25


def _manifest(project: dict[str, Any] | None) -> dict[str, tuple[int, int]]:
    if not project:
        return {}
    result: dict[str, tuple[int, int]] = {}
    for path, value in (project.get("manifest") or {}).items():
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            result[str(path)] = (int(value[0]), int(value[1]))
    return result


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
        if previous_manifest[path] != current_manifest[path]
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


def activity_markdown(
    evidence: list[dict[str, Any]], *, pattern_stats: dict[str, int] | None = None
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
    lines = ["### Deterministic activity ledger", ""]
    lines.append(
        f"- Project deltas: {len(deltas)}"
        + (f" across {', '.join(project_names[:12])}" if project_names else "")
    )
    if change_counts:
        lines.append(
            "- Change types: "
            + ", ".join(f"{name} ({count})" for name, count in sorted(change_counts.items()))
        )
    lines.append(
        f"- Agent sessions evaluated: {len(sessions)}"
        + (f" across {len(session_projects)} attributed projects" if session_projects else "")
    )
    stats = pattern_stats or {}
    lines.append(
        "- Recurring patterns: "
        f"{stats.get('tracking', 0)} tracking, "
        f"{stats.get('promoted', 0)} promoted, "
        f"{stats.get('pending', 0)} awaiting review"
    )
    return "\n".join(lines)
