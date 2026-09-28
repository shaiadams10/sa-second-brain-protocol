"""Codex: ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl

Kept: the owner's messages, the assistant's final answer per turn, the working folder.
Dropped: reasoning, tool calls and their output, token counts, injected context.
Session files can be hundreds of MB of tool output, so lines are filtered by
substring before they are decoded.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from brain.model import ExchangeBuilder, Session
from brain.text import is_injected, parse_time, strip_injected

TOOL_MARKERS = ('"type":"function_call"', '"type":"custom_tool_call"', '"type":"local_shell_call"',
                '"type":"web_search_call"')


def session_files(root: Path, since: datetime) -> Iterator[Path]:
    cutoff = since.timestamp()
    for path in root.rglob("rollout-*.jsonl"):
        if path.stat().st_mtime >= cutoff:
            yield path


def _user_text(content: list[dict]) -> str:
    parts = []
    for item in content:
        if item.get("type") != "input_text":
            continue
        text = item.get("text", "")
        if is_injected(text):
            continue
        if "<send_user_message_question_reply>" in text:
            text = _question_reply(text)
        text = strip_injected(text).removeprefix("## My request:").strip()
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _question_reply(text: str) -> str:
    """Codex's ask-the-user tool: keep the question and the owner's answer."""
    body = text.split("<send_user_message_question_reply>", 1)[1]
    body = body.split("</send_user_message_question_reply>", 1)[0].strip()
    try:
        items = json.loads(body)
    except json.JSONDecodeError:
        return body
    lines = []
    for item in items if isinstance(items, list) else [items]:
        question = item.get("question", "")
        answer = item.get("answer") or item.get("answers") or item.get("response") or ""
        if isinstance(answer, (list, dict)):
            answer = json.dumps(answer, ensure_ascii=False)
        lines.append(f"(answering: {question}) {answer}".strip())
    return "\n".join(lines)


def parse(path: Path) -> Session:
    session = Session(tool="codex", id=path.stem.removeprefix("rollout-"), source_file=str(path))
    builder = ExchangeBuilder()
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if any(marker in line for marker in TOOL_MARKERS) and '"type":"response_item"' in line:
                builder.tool()
                continue
            wanted = ('"type":"session_meta"' in line or '"type":"turn_context"' in line
                      or ('"type":"message"' in line and '"type":"response_item"' in line))
            if not wanted:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = record.get("payload") or {}
            kind = record.get("type")
            if kind == "session_meta":
                session.id = payload.get("id") or session.id
                session.cwd = payload.get("cwd") or session.cwd
            elif kind == "turn_context":
                session.cwd = session.cwd or payload.get("cwd")
            elif kind == "response_item" and payload.get("type") == "message":
                at = parse_time(record.get("timestamp"))
                role = payload.get("role")
                if role == "user" and at:
                    text = _user_text(payload.get("content") or [])
                    if text:
                        builder.user(at, text)
                elif role == "assistant":
                    text = "\n".join(c.get("text", "") for c in payload.get("content") or []
                                     if c.get("type") == "output_text")
                    builder.assistant(text, final=payload.get("phase") == "final_answer")
    session.exchanges = builder.finish()
    return session
