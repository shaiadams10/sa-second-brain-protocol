"""Claude Code: ~/.claude/projects/<encoded-folder>/<session>.jsonl

Kept: the owner's messages (including slash commands and interruptions), the last
assistant text before the owner spoke again, the working folder.
Dropped: thinking, tool calls and results, subagent sidechains, injected context.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from brain.model import ExchangeBuilder, Session
from brain.text import is_injected, parse_time, strip_injected

_COMMAND = re.compile(r"<command-name>(.*?)</command-name>", re.S)
_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)


def session_files(root: Path, since: datetime) -> Iterator[Path]:
    cutoff = since.timestamp()
    for path in root.glob("*/*.jsonl"):
        if path.stat().st_mtime >= cutoff:
            yield path


def _user_text(content) -> str | None:
    """Return what the owner typed, or None when the entry is not a real user turn."""
    if isinstance(content, list):
        kinds = {c.get("type") for c in content}
        if "tool_result" in kinds:
            return None
        content = "\n".join(c.get("text", "") for c in content if c.get("type") == "text")
    if not isinstance(content, str):
        return None
    if content.startswith("[Request interrupted by user"):
        return "(interrupted the assistant)"
    command = _COMMAND.search(content)
    if command:
        args = _ARGS.search(content)
        return f"{command.group(1).strip()} {args.group(1).strip() if args else ''}".strip()
    if is_injected(content):
        return None
    return strip_injected(content) or None


def parse(path: Path) -> Session:
    session = Session(tool="claude-code", id=path.stem, source_file=str(path))
    builder = ExchangeBuilder()
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if '"type":"user"' not in line and '"type":"assistant"' not in line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("isSidechain") or record.get("isMeta"):
                continue
            session.cwd = session.cwd or record.get("cwd")
            message = record.get("message") or {}
            content = message.get("content")
            if record.get("type") == "user":
                at = parse_time(record.get("timestamp"))
                text = _user_text(content)
                if text and at:
                    builder.user(at, text)
            elif record.get("type") == "assistant" and isinstance(content, list):
                for block in content:
                    if block.get("type") == "text":
                        builder.assistant(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        builder.tool()
    session.exchanges = builder.finish()
    return session
