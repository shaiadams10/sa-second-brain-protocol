"""Antigravity app: ~/.gemini/antigravity/brain/<id>/.system_generated/logs/transcript.jsonl

Kept: the owner's requests, the model's text responses, tool-call counts.
The transcript records no working folder, so the paths the model's tools
touched are collected as hints and the project is inferred from them.

The Antigravity CLI keeps its own history in ~/.gemini/antigravity-cli, which
is never read, so the brain's own model runs cannot feed back into it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from brain.model import ExchangeBuilder, Session
from brain.text import parse_time, strip_injected

_REQUEST = re.compile(r"<USER_REQUEST>(.*?)</USER_REQUEST>", re.S)
_PATH = re.compile(r"(?i)\b([a-z]:[\\/]+[^\"'<>|?*\r\n]+)")


def session_files(root: Path, since: datetime) -> Iterator[Path]:
    cutoff = since.timestamp()
    for path in root.glob("*/.system_generated/logs/transcript.jsonl"):
        if path.stat().st_mtime >= cutoff:
            yield path


def _hint_paths(tool_calls: list[dict]) -> Iterator[str]:
    for call in tool_calls:
        for value in (call.get("args") or {}).values():
            if isinstance(value, str):
                value = value.strip('"').replace("\\\\", "\\")
                for match in _PATH.findall(value):
                    yield match.strip().rstrip("\\/.,;:)")


def parse(path: Path) -> Session:
    conversation_id = path.parents[2].name
    session = Session(tool="antigravity", id=conversation_id, source_file=str(path))
    builder = ExchangeBuilder()
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = record.get("type")
            if kind == "USER_INPUT" and record.get("source") == "USER_EXPLICIT":
                content = record.get("content") or ""
                match = _REQUEST.search(content)
                text = strip_injected(match.group(1) if match else content)
                at = parse_time(record.get("created_at"))
                if text and at:
                    builder.user(at, text)
            elif kind == "PLANNER_RESPONSE":
                tool_calls = record.get("tool_calls") or []
                if tool_calls:
                    builder.tool(len(tool_calls))
                    session.path_hints.update(_hint_paths(tool_calls))
                if record.get("content"):
                    builder.assistant(strip_injected(record["content"]))
    session.exchanges = builder.finish()
    return session
