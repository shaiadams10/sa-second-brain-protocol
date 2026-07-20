from __future__ import annotations

import json
import sqlite3
import hashlib
import re
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlparse

from .security import sanitize_text


CURSOR_VERSION = 1
ANTIGRAVITY_VISIBLE_STEP_ROLES = {14: "user", 15: "assistant", 132: "assistant"}
ANTIGRAVITY_TOOL_STEPS = {
    5: "code_action",
    7: "grep_search",
    8: "view_file",
    9: "list_directory",
    21: "run_command",
    25: "find",
    31: "read_url_content",
    33: "search_web",
    85: "browser_subagent",
    91: "generate_image",
    127: "invoke_subagent",
}
ANTIGRAVITY_PROJECT_PATH_STEPS = {5, 7, 8, 9, 21, 25}
WINDOWS_ABSOLUTE_PATH = re.compile(r"(?:file:///?[A-Za-z]:|[A-Za-z]:)[\\/][^\s\"'<>|]+")
ANTIGRAVITY_SESSION_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def antigravity_session_id(path: Path) -> str:
    """Return the trajectory UUID without mistaking a transcript filename for it."""

    for part in reversed(path.parts[:-1]):
        if ANTIGRAVITY_SESSION_ID.fullmatch(part):
            return part
    return (
        path.parent.name
        if path.parent.name not in {"logs", ".system_generated"}
        else path.stem
    )


def _cursor_json(kind: str, position_name: str, position: int, digest: str) -> str:
    return json.dumps(
        {
            "version": CURSOR_VERSION,
            "kind": kind,
            position_name: position,
            "prefix_sha256": digest,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def cursor_position(cursor: str | None) -> tuple[int, int]:
    """Return a deterministic sort key for a structured ingestion cursor."""
    if not cursor:
        return (0, -1)
    try:
        parsed = json.loads(cursor)
    except (json.JSONDecodeError, TypeError):
        return (0, -1)
    if not isinstance(parsed, dict):
        return (0, -1)
    if parsed.get("kind") == "jsonl":
        return (1, int(parsed.get("offset", -1)))
    if parsed.get("kind") == "sqlite-steps":
        return (2, int(parsed.get("row_idx", -1)))
    return (0, -1)


def _hash_file_prefix(path: Path, length: int) -> str | None:
    if length < 0:
        return None
    digest = hashlib.sha256()
    remaining = length
    try:
        with path.open("rb") as handle:
            while remaining:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    return None
                digest.update(chunk)
                remaining -= len(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def validated_jsonl_resume_offset(path: Path, cursor: str | None) -> int:
    """Resume only when the already-published byte prefix is unchanged."""
    if not cursor:
        return 0
    try:
        parsed = json.loads(cursor)
        if parsed.get("version") != CURSOR_VERSION or parsed.get("kind") != "jsonl":
            return 0
        offset = int(parsed["offset"])
        if offset < 0 or offset > path.stat().st_size:
            return 0
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
        return 0
    digest = _hash_file_prefix(path, offset)
    return offset if digest and digest == parsed.get("prefix_sha256") else 0


def _jsonl_prefix(path: Path, start_offset: int) -> tuple[hashlib._Hash, bytes]:
    digest = hashlib.sha256()
    context = bytearray()
    remaining = start_offset
    with path.open("rb") as handle:
        while remaining:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            digest.update(chunk)
            if len(context) < 1024 * 1024:
                context.extend(chunk[: 1024 * 1024 - len(context)])
            remaining -= len(chunk)
    return digest, bytes(context)


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                for key in ("text", "input_text", "output_text"):
                    value = item.get(key)
                    if isinstance(value, str):
                        parts.append(value)
                        break
        return "\n".join(parts)
    if isinstance(content, dict):
        return _content_text(content.get("content") or content.get("text") or "")
    return ""


def codex_session_metadata(
    path: Path, *, max_bytes: int = 1024 * 1024
) -> dict[str, str | None]:
    """Read only the bounded Codex session header needed for project routing."""

    session_id = path.stem
    cwd: str | None = None
    remote_url: str | None = None
    commit_hash: str | None = None
    consumed = 0
    try:
        with path.open("rb") as handle:
            for raw_line in handle:
                consumed += len(raw_line)
                if consumed > max_bytes:
                    break
                try:
                    item = json.loads(raw_line.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue
                if item.get("type") != "session_meta":
                    continue
                payload = item.get("payload", {})
                if isinstance(payload, dict):
                    session_id = str(payload.get("id") or session_id)
                    cwd = str(payload.get("cwd") or "").strip() or None
                    git = payload.get("git")
                    if isinstance(git, dict):
                        remote_url = (
                            str(git.get("repository_url") or "").strip() or None
                        )
                        commit_hash = str(git.get("commit_hash") or "").strip() or None
                break
    except OSError:
        pass
    return {
        "session_id": session_id,
        "cwd": cwd,
        "remote_url": remote_url,
        "commit_hash": commit_hash,
    }


def parse_codex_jsonl(
    path: Path, *, max_chars: int = 6000, start_offset: int = 0
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    session_id = path.stem
    cwd: str | None = None
    try:
        stat = path.stat()
        file_identity = hashlib.sha256(
            f"{path.resolve()}:{getattr(stat, 'st_ino', 0)}".encode()
        ).hexdigest()[:16]
        prefix_digest, context = _jsonl_prefix(path, start_offset)
        for raw_line in context.splitlines():
            try:
                context_item = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if context_item.get("type") != "session_meta":
                continue
            context_payload = context_item.get("payload", {})
            if isinstance(context_payload, dict):
                session_id = str(context_payload.get("id") or session_id)
                cwd = context_payload.get("cwd") or cwd
        handle = path.open("rb")
        handle.seek(start_offset)
    except OSError:
        return records
    byte_offset = start_offset
    with handle:
        for raw_line in handle:
            current_offset = byte_offset
            byte_offset += len(raw_line)
            prefix_digest.update(raw_line)
            line = raw_line.decode("utf-8", errors="replace")
            line_hash = hashlib.sha256(raw_line).hexdigest()[:16]
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            item_type = item.get("type")
            payload = item.get("payload", {})
            if item_type == "session_meta":
                session_id = str(payload.get("id") or session_id)
                cwd = payload.get("cwd") or cwd
                continue
            if item_type != "response_item" or not isinstance(payload, dict):
                continue
            payload_type = payload.get("type")
            if payload_type == "reasoning":
                continue
            if payload_type == "message":
                role = payload.get("role")
                if role not in {"user", "assistant"}:
                    continue
                text = _content_text(payload.get("content"))
                if not text.strip():
                    continue
                records.append(
                    {
                        "source": "codex",
                        "session_id": session_id,
                        "source_ref": f"codex:{file_identity}:{current_offset}:{line_hash}",
                        "cwd": cwd,
                        "role": role,
                        "kind": "visible_message",
                        "text": sanitize_text(text, max_chars=max_chars),
                        "timestamp": item.get("timestamp"),
                        "untrusted": True,
                        "_cursor": _cursor_json(
                            "jsonl", "offset", byte_offset, prefix_digest.hexdigest()
                        ),
                    }
                )
            elif payload_type in {"function_call", "custom_tool_call"}:
                name = payload.get("name") or payload.get("tool_name") or "tool"
                records.append(
                    {
                        "source": "codex",
                        "session_id": session_id,
                        "source_ref": f"codex:{file_identity}:{current_offset}:{line_hash}",
                        "cwd": cwd,
                        "role": "assistant",
                        "kind": "tool_metadata",
                        "tool": sanitize_text(str(name), max_chars=200),
                        "timestamp": item.get("timestamp"),
                        "untrusted": True,
                        "_cursor": _cursor_json(
                            "jsonl", "offset", byte_offset, prefix_digest.hexdigest()
                        ),
                    }
                )
    return records


def discover_codex_sessions(roots: Iterable[Path]) -> list[Path]:
    paths: list[Path] = []
    for root in roots:
        if root.exists():
            paths.extend(root.rglob("*.jsonl"))
    return sorted(set(paths), key=lambda path: path.as_posix().lower())


def parse_antigravity_transcript(
    path: Path,
    *,
    max_chars: int = 6000,
    start_offset: int = 0,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    session_id = antigravity_session_id(path)
    try:
        prefix_digest, _context = _jsonl_prefix(path, start_offset)
        handle = path.open("rb")
        handle.seek(start_offset)
    except OSError:
        return records
    byte_offset = start_offset
    with handle:
        for line_number, raw_line in enumerate(handle, 1):
            byte_offset += len(raw_line)
            prefix_digest.update(raw_line)
            line = raw_line.decode("utf-8", errors="replace")
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            source = str(item.get("source") or "").upper()
            role = str(
                item.get("role") or item.get("author") or item.get("speaker") or ""
            ).lower()
            event_type = str(item.get("type") or item.get("event_type") or "")
            if "reason" in event_type.lower() or role in {"system", "developer"}:
                continue
            if source == "SYSTEM":
                continue
            if source in {"USER", "USER_EXPLICIT"}:
                role = "user"
            elif source == "MODEL" and event_type.upper() in {
                "GENERIC",
                "PLANNER_RESPONSE",
                "FINAL_RESPONSE",
                "MODEL_OUTPUT",
                "ASSISTANT_MESSAGE",
            }:
                role = "assistant"
            cursor = _cursor_json(
                "jsonl", "offset", byte_offset, prefix_digest.hexdigest()
            )
            if role not in {"user", "assistant", "model"}:
                if event_type.lower() not in {
                    "user_input",
                    "assistant_message",
                    "model_message",
                }:
                    tool_calls = item.get("tool_calls")
                    if (
                        source == "MODEL"
                        and isinstance(tool_calls, list)
                        and tool_calls
                    ):
                        names = []
                        for call in tool_calls:
                            if isinstance(call, dict):
                                name = (
                                    call.get("name")
                                    or call.get("tool_name")
                                    or call.get("type")
                                )
                                if name:
                                    names.append(
                                        sanitize_text(str(name), max_chars=200)
                                    )
                        if names:
                            content_hash = hashlib.sha256(raw_line).hexdigest()[:16]
                            records.append(
                                {
                                    "source": "antigravity",
                                    "session_id": session_id,
                                    "source_ref": f"antigravity:{session_id}:{item.get('step_index', line_number)}:{content_hash}",
                                    "cwd": workspace,
                                    "role": "assistant",
                                    "kind": "tool_metadata",
                                    "tools": names,
                                    "timestamp": item.get("created_at")
                                    or item.get("timestamp"),
                                    "untrusted": True,
                                    "_cursor": cursor,
                                }
                            )
                    continue
                role = "user" if "user" in event_type.lower() else "assistant"
            if role == "model":
                role = "assistant"
            text = _content_text(
                item.get("content")
                or item.get("text")
                or item.get("message")
                or item.get("payload")
            )
            if not text.strip():
                tool_calls = item.get("tool_calls")
                if source == "MODEL" and isinstance(tool_calls, list) and tool_calls:
                    names = []
                    for call in tool_calls:
                        if isinstance(call, dict):
                            name = (
                                call.get("name")
                                or call.get("tool_name")
                                or call.get("type")
                            )
                            if name:
                                names.append(sanitize_text(str(name), max_chars=200))
                    if names:
                        content_hash = hashlib.sha256(raw_line).hexdigest()[:16]
                        records.append(
                            {
                                "source": "antigravity",
                                "session_id": session_id,
                                "source_ref": f"antigravity:{session_id}:{item.get('step_index', line_number)}:{content_hash}",
                                "cwd": workspace,
                                "role": "assistant",
                                "kind": "tool_metadata",
                                "tools": names,
                                "timestamp": item.get("created_at")
                                or item.get("timestamp"),
                                "untrusted": True,
                                "_cursor": cursor,
                            }
                        )
                continue
            event_id = (
                item.get("id")
                or item.get("event_id")
                or item.get("row_id")
                or line_number
            )
            content_hash = hashlib.sha256(raw_line).hexdigest()[:16]
            records.append(
                {
                    "source": "antigravity",
                    "session_id": session_id,
                    "source_ref": f"antigravity:{session_id}:{event_id}:{content_hash}",
                    "cwd": item.get("cwd") or item.get("workspace") or workspace,
                    "role": role,
                    "kind": "visible_message",
                    "text": sanitize_text(text, max_chars=max_chars),
                    "timestamp": item.get("timestamp") or item.get("created_at"),
                    "untrusted": True,
                    "_cursor": cursor,
                }
            )
    return records


def parse_antigravity_artifact(
    path: Path, *, max_chars: int = 12000
) -> dict[str, Any] | None:
    if path.name not in {"implementation_plan.md", "task.md", "walkthrough.md"}:
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    session_id = path.parent.name
    return {
        "source": "antigravity",
        "session_id": session_id,
        "source_ref": f"antigravity-artifact:{session_id}:{path.name}",
        "role": "assistant",
        "kind": "artifact",
        "artifact": path.name,
        "text": sanitize_text(text, max_chars=max_chars),
        "untrusted": True,
    }


def _read_varint(data: bytes, position: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while position < len(data) and shift < 70:
        byte = data[position]
        position += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, position
        shift += 7
    raise ValueError("Invalid protobuf varint")


def _protobuf_fields(data: bytes) -> list[tuple[int, int, int | bytes]]:
    fields: list[tuple[int, int, int | bytes]] = []
    position = 0
    while position < len(data):
        tag, position = _read_varint(data, position)
        field_number = tag >> 3
        wire_type = tag & 0x7
        if field_number < 1:
            raise ValueError("Invalid protobuf field number")
        if wire_type == 0:
            value, position = _read_varint(data, position)
        elif wire_type == 1:
            if position + 8 > len(data):
                raise ValueError("Truncated protobuf fixed64")
            value = data[position : position + 8]
            position += 8
        elif wire_type == 2:
            length, position = _read_varint(data, position)
            if length < 0 or position + length > len(data):
                raise ValueError("Truncated protobuf bytes")
            value = data[position : position + length]
            position += length
        elif wire_type == 5:
            if position + 4 > len(data):
                raise ValueError("Truncated protobuf fixed32")
            value = data[position : position + 4]
            position += 4
        else:
            raise ValueError(f"Unsupported protobuf wire type: {wire_type}")
        fields.append((field_number, wire_type, value))
    return fields


def _protobuf_strings(
    data: bytes, *, path: tuple[int, ...] = (), depth: int = 0
) -> list[tuple[tuple[int, ...], str]]:
    if depth > 8:
        return []
    try:
        fields = _protobuf_fields(data)
    except ValueError:
        return []
    strings: list[tuple[tuple[int, ...], str]] = []
    for field_number, wire_type, value in fields:
        if wire_type != 2 or not isinstance(value, bytes):
            continue
        field_path = (*path, field_number)
        try:
            decoded = value.decode("utf-8")
        except UnicodeDecodeError:
            decoded = ""
        if (
            decoded
            and sum(
                character.isprintable() or character in "\r\n\t"
                for character in decoded
            )
            / len(decoded)
            >= 0.98
        ):
            strings.append((field_path, decoded))
        strings.extend(_protobuf_strings(value, path=field_path, depth=depth + 1))
    return strings


def _first_string_at(
    strings: list[tuple[tuple[int, ...], str]], path: tuple[int, ...]
) -> str | None:
    for candidate_path, value in strings:
        if candidate_path == path and value.strip():
            return value
    return None


def _antigravity_visible_text(step_type: int, payload: bytes) -> str | None:
    """Decode only fields proven to appear in exported visible transcripts."""
    strings = _protobuf_strings(payload)
    if step_type == 14:
        # UserInput.user_request. Metadata and attachments are deliberately excluded.
        return _first_string_at(strings, (19, 2)) or _first_string_at(
            strings, (19, 3, 1)
        )
    if step_type == 15:
        # PlannerResponse.content is the assistant message shown in the chat.
        return _first_string_at(strings, (20, 1))
    if step_type == 132:
        # Generic visible status/result. Background-task variants use field 148.
        background_parts = [
            value
            for field_path, value in strings
            if field_path in {(148, 1), (148, 2), (148, 4)} and value.strip()
        ]
        if background_parts:
            return "\n".join(dict.fromkeys(background_parts))
        return _first_string_at(strings, (140, 2, 1))
    return None


def _sqlite_row_digest(
    digest: hashlib._Hash,
    *,
    row_idx: int,
    step_type: int,
    status: int,
    payload: bytes,
) -> None:
    digest.update(str(row_idx).encode("ascii"))
    digest.update(b"\0")
    digest.update(str(step_type).encode("ascii"))
    digest.update(b"\0")
    digest.update(str(status).encode("ascii"))
    digest.update(b"\0")
    digest.update(hashlib.sha256(payload).digest())


def validated_antigravity_database_resume_index(
    path: Path, cursor: str | None, *, immutable: bool = True
) -> int:
    """Return the next row only if every previously published step is unchanged."""
    if not cursor:
        return 0
    try:
        parsed = json.loads(cursor)
        if (
            parsed.get("version") != CURSOR_VERSION
            or parsed.get("kind") != "sqlite-steps"
        ):
            return 0
        published_idx = int(parsed["row_idx"])
        expected_digest = str(parsed["prefix_sha256"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return 0
    digest = hashlib.sha256()
    suffix = "&immutable=1" if immutable else ""
    uri = f"file:{path.as_posix()}?mode=ro{suffix}"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            rows = connection.execute(
                "SELECT idx,step_type,status,step_payload FROM steps WHERE idx<=? ORDER BY idx",
                (published_idx,),
            ).fetchall()
    except (sqlite3.Error, OSError):
        return 0
    if not rows or int(rows[-1][0]) != published_idx:
        return 0
    for row_idx, step_type, status, payload in rows:
        _sqlite_row_digest(
            digest,
            row_idx=int(row_idx),
            step_type=int(step_type),
            status=int(status),
            payload=bytes(payload or b""),
        )
    return published_idx + 1 if digest.hexdigest() == expected_digest else 0


def _workspace_from_database(connection: sqlite3.Connection) -> str | None:
    try:
        rows = connection.execute(
            "SELECT data FROM trajectory_metadata_blob"
        ).fetchall()
    except sqlite3.Error:
        return None
    for (payload,) in rows:
        for _field_path, value in _protobuf_strings(bytes(payload or b"")):
            if not value.lower().startswith("file://"):
                continue
            parsed = urlparse(value)
            candidate = unquote(parsed.path or "")
            if re.match(r"^/[A-Za-z]:/", candidate):
                candidate = candidate[1:]
            return candidate.replace("/", "\\")
    return None


def _workspace_from_file_uri(value: str) -> str | None:
    if not value.casefold().startswith("file://"):
        return None
    parsed = urlparse(value)
    candidate = unquote(parsed.path or "")
    if re.match(r"^/[A-Za-z]:/", candidate):
        candidate = candidate[1:]
    return candidate.replace("/", "\\") or None


def antigravity_database_tool_paths(
    path: Path, *, immutable: bool = True, max_paths: int = 200
) -> list[str]:
    """Extract only absolute paths used by project-facing Antigravity tools.

    This is an attribution-only metadata view. It never returns commands, tool
    output, reasoning, or other protobuf text. Callers must still require all
    resolvable paths to identify one project before assigning the session.
    """

    suffix = "&immutable=1" if immutable else ""
    uri = f"file:{path.as_posix()}?mode=ro{suffix}"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            placeholders = ",".join("?" for _ in ANTIGRAVITY_PROJECT_PATH_STEPS)
            rows = connection.execute(
                f"SELECT step_type,step_payload FROM steps "
                f"WHERE step_type IN ({placeholders}) ORDER BY idx",
                tuple(sorted(ANTIGRAVITY_PROJECT_PATH_STEPS)),
            ).fetchall()
    except (sqlite3.Error, OSError):
        return []

    paths: set[str] = set()
    for step_type, raw_payload in rows:
        if int(step_type) not in ANTIGRAVITY_PROJECT_PATH_STEPS:
            continue
        for _field_path, value in _protobuf_strings(bytes(raw_payload or b"")):
            for match in WINDOWS_ABSOLUTE_PATH.finditer(value):
                candidate = match.group(0).rstrip(".,;:)]}")
                if candidate.casefold().startswith("file://"):
                    candidate = _workspace_from_file_uri(candidate) or ""
                else:
                    candidate = candidate.replace("/", "\\")
                if candidate:
                    paths.add(candidate)
                if len(paths) >= max_paths:
                    return sorted(paths, key=str.casefold)
    return sorted(paths, key=str.casefold)


def antigravity_summary_session_metadata(
    path: Path, *, known_session_ids: set[str]
) -> dict[str, dict[str, str]]:
    """Read authoritative session workspace metadata from Antigravity's local hub.

    The hub file contains one top-level summary record per conversation. Field 1
    is the conversation UUID. Workspace URIs and Git remotes live in documented
    metadata branches inside that record. Conflicting values are withheld.
    """

    try:
        fields = _protobuf_fields(path.read_bytes())
    except (OSError, ValueError):
        return {}
    workspace_values: dict[str, set[str]] = {}
    remote_values: dict[str, set[str]] = {}
    for _field_number, wire_type, value in fields:
        if wire_type != 2 or not isinstance(value, bytes):
            continue
        strings = _protobuf_strings(value)
        session_id = _first_string_at(strings, (1,))
        if not session_id or session_id not in known_session_ids:
            continue
        workspaces = {
            workspace
            for _field_path, candidate in strings
            if (workspace := _workspace_from_file_uri(candidate))
        }
        if len({item.casefold() for item in workspaces}) == 1:
            workspace_values.setdefault(session_id, set()).update(workspaces)
        remotes = {
            candidate.strip()
            for field_path, candidate in strings
            if field_path in {(2, 9, 3, 2), (2, 17, 1, 3, 2)}
            and candidate.strip().casefold().startswith(("https://", "http://", "git@"))
        }
        if len({item.casefold() for item in remotes}) == 1:
            remote_values.setdefault(session_id, set()).update(remotes)

    result: dict[str, dict[str, str]] = {}
    for session_id in sorted(known_session_ids):
        workspaces = workspace_values.get(session_id, set())
        remotes = remote_values.get(session_id, set())
        metadata: dict[str, str] = {}
        if len({item.casefold() for item in workspaces}) == 1 and workspaces:
            metadata["workspace"] = sorted(workspaces, key=str.casefold)[0]
        if len({item.casefold() for item in remotes}) == 1 and remotes:
            metadata["remote_url"] = sorted(remotes, key=str.casefold)[0]
        if metadata:
            result[session_id] = metadata
    return result


def antigravity_database_workspace(path: Path, *, immutable: bool = True) -> str | None:
    """Read only the visible workspace URI from Antigravity trajectory metadata."""

    suffix = "&immutable=1" if immutable else ""
    uri = f"file:{path.as_posix()}?mode=ro{suffix}"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            return _workspace_from_database(connection)
    except (sqlite3.Error, OSError):
        return None


def parse_antigravity_database(
    path: Path,
    *,
    start_idx: int = 0,
    max_chars: int = 6000,
    immutable: bool = True,
) -> list[dict[str, Any]]:
    """Read visible DB-only Antigravity messages without decoding hidden payloads."""
    records: list[dict[str, Any]] = []
    session_id = path.stem
    digest = hashlib.sha256()
    try:
        fallback_timestamp = datetime.fromtimestamp(
            path.stat().st_mtime, UTC
        ).isoformat()
    except OSError:
        fallback_timestamp = None
    suffix = "&immutable=1" if immutable else ""
    uri = f"file:{path.as_posix()}?mode=ro{suffix}"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            workspace = _workspace_from_database(connection)
            rows = connection.execute(
                "SELECT idx,step_type,status,step_payload FROM steps ORDER BY idx"
            ).fetchall()
    except (sqlite3.Error, OSError):
        return records
    for row_idx, step_type, status, raw_payload in rows:
        row_idx = int(row_idx)
        step_type = int(step_type)
        status = int(status)
        payload = bytes(raw_payload or b"")
        _sqlite_row_digest(
            digest,
            row_idx=row_idx,
            step_type=step_type,
            status=status,
            payload=payload,
        )
        if row_idx < start_idx:
            continue
        cursor = _cursor_json("sqlite-steps", "row_idx", row_idx, digest.hexdigest())
        content_hash = hashlib.sha256(payload).hexdigest()[:16]
        if step_type in ANTIGRAVITY_VISIBLE_STEP_ROLES:
            text = _antigravity_visible_text(step_type, payload)
            if text and text.strip():
                records.append(
                    {
                        "source": "antigravity",
                        "session_id": session_id,
                        "source_ref": f"antigravity-db:{session_id}:{row_idx}:{content_hash}",
                        "cwd": workspace,
                        "role": ANTIGRAVITY_VISIBLE_STEP_ROLES[step_type],
                        "kind": "visible_message",
                        "text": sanitize_text(text, max_chars=max_chars),
                        "timestamp": fallback_timestamp,
                        "untrusted": True,
                        "database_fallback": True,
                        "_cursor": cursor,
                    }
                )
        elif step_type in ANTIGRAVITY_TOOL_STEPS:
            records.append(
                {
                    "source": "antigravity",
                    "session_id": session_id,
                    "source_ref": f"antigravity-db:{session_id}:{row_idx}:{content_hash}",
                    "cwd": workspace,
                    "role": "assistant",
                    "kind": "tool_metadata",
                    "tool": ANTIGRAVITY_TOOL_STEPS[step_type],
                    "timestamp": fallback_timestamp,
                    "untrusted": True,
                    "database_fallback": True,
                    "_cursor": cursor,
                }
            )
    return records


def inspect_antigravity_database(
    path: Path, *, immutable: bool = True
) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(path), "tables": []}
    suffix = "&immutable=1" if immutable else ""
    uri = f"file:{path.as_posix()}?mode=ro{suffix}"
    try:
        connection = sqlite3.connect(uri, uri=True)
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        for (table,) in rows:
            escaped = table.replace("'", "''")
            columns = [
                row[1]
                for row in connection.execute(
                    f"PRAGMA table_info('{escaped}')"
                ).fetchall()
            ]
            count = connection.execute(
                f'SELECT COUNT(*) FROM "{table.replace(chr(34), chr(34) * 2)}"'
            ).fetchone()[0]
            result["tables"].append(
                {"name": table, "columns": columns, "row_count": count}
            )
        connection.close()
    except (sqlite3.Error, OSError) as error:
        result["error"] = str(error)
    return result


def discover_antigravity_sources(
    brain_root: Path, conversations_root: Path
) -> dict[str, list[Path]]:
    transcript_candidates: list[Path] = []
    if brain_root.exists():
        transcript_candidates.extend(brain_root.rglob("transcript.jsonl"))
        transcript_candidates.extend(brain_root.rglob("transcript_full.jsonl"))
    # The full export and canonical transcript usually contain the same visible
    # turns. Keep one source per trajectory so a single chat cannot become two
    # evidence sessions. Fall back to transcript_full only when transcript.jsonl
    # is absent.
    transcripts_by_session: dict[str, Path] = {}
    for path in sorted(set(transcript_candidates)):
        session_id = antigravity_session_id(path)
        current = transcripts_by_session.get(session_id)
        if current is None or (
            current.name == "transcript_full.jsonl" and path.name == "transcript.jsonl"
        ):
            transcripts_by_session[session_id] = path
    transcripts = list(transcripts_by_session.values())
    artifacts: list[Path] = []
    if brain_root.exists():
        for name in ("implementation_plan.md", "task.md", "walkthrough.md"):
            artifacts.extend(brain_root.rglob(name))
    databases = (
        sorted(conversations_root.glob("*.db")) if conversations_root.exists() else []
    )
    return {
        "transcripts": sorted(transcripts),
        "artifacts": sorted(set(artifacts)),
        "databases": sorted(set(databases)),
    }
