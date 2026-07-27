from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from typing import Any

from .security import sanitize_text
from .state import StateStore


SESSION_SOURCE_TYPES = {"codex", "antigravity"}
SESSION_EVENT_KINDS = {"visible_message", "tool_metadata", "artifact"}
SUCCESS_PATTERN = re.compile(
    r"(?i)\b(?:implemented|completed|working|verified|tests? passed|successful|shipped|fixed|built|created|resolved|commit(?:ted)?)\b"
)


def _ordered(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda item: (
            str(item.get("occurred_at") or item["created_at"]),
            item["id"],
        ),
    )


def _bounded_text_rows(
    rows: list[dict[str, Any]], *, total_chars: int, per_item_chars: int
) -> tuple[list[dict[str, str]], int]:
    ordered = _ordered(rows)
    if len(ordered) > 24:
        ordered = ordered[:12] + ordered[-12:]
    selected: list[dict[str, str]] = []
    used = 0
    seen_text: set[str] = set()
    for row in ordered:
        text = sanitize_text(
            str(row["payload"].get("text") or ""), max_chars=per_item_chars
        ).strip()
        if not text:
            continue
        normalized = re.sub(r"\s+", " ", text).strip().casefold()
        if normalized in seen_text:
            continue
        seen_text.add(normalized)
        if used + len(text) > total_chars:
            remaining = total_chars - used
            if remaining >= 200:
                text = sanitize_text(text, max_chars=remaining)
            else:
                break
        selected.append(
            {
                "occurred_at": str(row.get("occurred_at") or row["created_at"]),
                "text": text,
            }
        )
        used += len(text)
    return selected, max(0, len(rows) - len(selected))


def _assistant_results(rows: list[dict[str, Any]]) -> tuple[list[dict[str, str]], int]:
    ordered = _ordered(rows)
    successful = [
        row
        for row in ordered
        if SUCCESS_PATTERN.search(str(row["payload"].get("text") or ""))
    ]
    candidates = successful[-12:] + ordered[-6:]
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in candidates:
        text_key = re.sub(
            r"\s+", " ", str(row["payload"].get("text") or "")
        ).strip().casefold()
        if text_key and text_key not in seen:
            deduped.append(row)
            seen.add(text_key)
    return _bounded_text_rows(deduped, total_chars=16000, per_item_chars=3000)


def _tool_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        payload = row["payload"]
        tool = payload.get("tool")
        if tool:
            counts[str(tool)] += 1
        tools = payload.get("tools")
        if isinstance(tools, list):
            for item in tools:
                counts[str(item)] += 1
        elif isinstance(tools, dict):
            for item, count in tools.items():
                counts[str(item)] += int(count) if isinstance(count, int) else 1
        elif tools:
            counts[str(tools)] += 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:50])


def compact_session_evidence(store: StateStore) -> dict[str, int]:
    attribution = {
        (str(row["surface"]), str(row["session_id"])): row
        for row in store.session_project_index()
    }
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in store.evidence(status="new"):
        payload = row.get("payload") or {}
        session_id = payload.get("session_id") if isinstance(payload, dict) else None
        if (
            row["source_type"] in SESSION_SOURCE_TYPES
            and row["kind"] in SESSION_EVENT_KINDS
            and session_id
        ):
            groups[(row["source_type"], str(session_id))].append(row)

    created = 0
    compacted = 0
    for (source_type, session_id), rows in sorted(groups.items()):
        ordered = _ordered(rows)
        messages = [row for row in ordered if row["kind"] == "visible_message"]
        user_rows = [
            row for row in messages if row["payload"].get("role") == "user"
        ]
        assistant_rows = [
            row for row in messages if row["payload"].get("role") == "assistant"
        ]
        artifact_rows = [row for row in ordered if row["kind"] == "artifact"]
        tool_rows = [row for row in ordered if row["kind"] == "tool_metadata"]
        user_messages, omitted_user = _bounded_text_rows(
            user_rows, total_chars=16000, per_item_chars=3000
        )
        assistant_results, omitted_assistant = _assistant_results(assistant_rows)
        artifacts, omitted_artifacts = _bounded_text_rows(
            artifact_rows, total_chars=16000, per_item_chars=4000
        )
        project_ids = sorted(
            {str(row["project_id"]) for row in rows if row.get("project_id")}
        )
        indexed = attribution.get((source_type, session_id), {})
        attribution_status = str(indexed.get("status") or "").strip() or (
            "matched" if len(project_ids) == 1 else "unmatched"
        )
        analysis_lane = (
            "full"
            if attribution_status == "matched" and len(project_ids) == 1
            else "profile_only"
        )
        source_ids = [row["id"] for row in ordered]
        payload = {
            "session_id": session_id,
            "source": source_type,
            "project_ids": project_ids,
            "analysis_lane": analysis_lane,
            "attribution_status": attribution_status,
            "started_at": str(
                ordered[0].get("occurred_at") or ordered[0]["created_at"]
            ),
            "ended_at": str(
                ordered[-1].get("occurred_at") or ordered[-1]["created_at"]
            ),
            "record_counts": dict(Counter(row["kind"] for row in rows)),
            "user_messages": user_messages,
            "assistant_results": assistant_results,
            "artifacts": artifacts,
            "tool_usage": _tool_counts(tool_rows),
            "omitted_counts": {
                "user_messages": omitted_user,
                "assistant_messages": omitted_assistant,
                "artifacts": omitted_artifacts,
            },
            "source_evidence_ids": source_ids,
        }
        digest = hashlib.sha256("\n".join(source_ids).encode()).hexdigest()[:24]
        _evidence_id, added = store.add_derived_evidence(
            source_type="session-digest",
            source_ref=f"session-digest:{source_type}:{session_id}:{digest}",
            kind="session_digest",
            payload=payload,
            source_evidence_ids=source_ids,
            project_id=project_ids[0] if len(project_ids) == 1 else None,
            occurred_at=str(
                ordered[0].get("occurred_at") or ordered[0]["created_at"]
            ),
        )
        created += int(added)
        compacted += len(source_ids)
    return {
        "session_digests_created": created,
        "source_records_compacted": compacted,
    }
