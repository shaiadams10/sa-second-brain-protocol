"""Web chat exports: the JSON files ChatGPT, Claude.ai, and Gemini (Google Takeout) let you download.

Only the owner's messages are kept; these exports are read by `sbrain corpus`, not by the weekly
run. Every `*.json` file under the given folder is tried, whatever its name:

- ChatGPT `conversations.json`: conversations with a `mapping` tree; user nodes hold `content.parts`.
- Claude.ai `conversations.json`: conversations with `chat_messages`; the owner is `sender: human`.
- Gemini, Google Takeout `My Activity.json`: entries whose `title` starts with "Prompted ".
- Anything shaped like `{"messages": [{"role": "user", "content": ...}]}`, alone or in a list.

Zip files (the way ChatGPT and Claude.ai hand exports over) are read in place, every JSON inside.
Files in none of these shapes are reported, not guessed at.

The assistants' replies are read too, never as the owner's words: a coding-agent message that
contains a long stretch of one was pasted from that chat (see corpus.strip_web_replies).
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections.abc import Iterator
from pathlib import Path

from brain.model import ExchangeBuilder, Session
from brain.text import parse_time, strip_injected

OWNER_ROLES = ("user", "human")


def export_files(root: Path) -> Iterator[Path]:
    yield from sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in (".json", ".zip"))


def _load(path: Path) -> Iterator[tuple[str, object]]:
    """(name, parsed JSON) for a JSON file, or for every JSON file inside a zip."""
    if path.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(path) as z:
                for name in sorted(n for n in z.namelist() if n.lower().endswith(".json")):
                    try:
                        yield f"{path.name}/{name}", json.load(io.TextIOWrapper(z.open(name), encoding="utf-8-sig", errors="replace"))
                    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                        continue
        except (zipfile.BadZipFile, OSError):
            return
        return
    try:
        yield path.name, json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return


def _text_of(content) -> str:
    """Plain text from the content shapes the exports use: a string, a list of parts, or a block."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(t for t in (_text_of(c) for c in content) if t)
    if isinstance(content, dict):
        if content.get("type") not in (None, "text", "input_text"):
            return ""
        return content.get("text") or _text_of(content.get("parts") or "")
    return ""


def _session(tool: str, conv_id: str, path: Path, messages: list[tuple]) -> Session:
    session = Session(tool=tool, id=conv_id, source_file=str(path))
    builder = ExchangeBuilder()
    for at, text in sorted((m for m in messages if m[0]), key=lambda m: m[0]):
        text = strip_injected(text)
        if text:
            builder.user(at, text)
    session.exchanges = builder.finish()
    return session


def _chatgpt(conv: dict, path: Path) -> Session:
    messages = []
    for node in (conv.get("mapping") or {}).values():
        message = (node or {}).get("message") or {}
        if (message.get("author") or {}).get("role") != "user":
            continue
        content = message.get("content") or {}
        if content.get("content_type") not in ("text", "multimodal_text"):
            continue  # custom instructions and other context the app adds
        if (message.get("metadata") or {}).get("is_visually_hidden_from_conversation"):
            continue
        text = _text_of([p for p in content.get("parts") or [] if isinstance(p, str)])
        messages.append((parse_time(message.get("create_time")), text))
    conv_id = conv.get("conversation_id") or conv.get("id") or _hash(conv.get("title"), path)
    return _session("chatgpt", conv_id, path, messages)


def _claude_ai(conv: dict, path: Path) -> Session:
    messages = []
    for message in conv.get("chat_messages") or []:
        if message.get("sender") != "human":
            continue
        text = message.get("text") or _text_of(message.get("content") or [])
        messages.append((parse_time(message.get("created_at")), text))
    return _session("claude-ai", conv.get("uuid") or _hash(conv.get("name"), path), path, messages)


def _generic(conv: dict, path: Path, index: int) -> Session:
    messages = []
    for message in conv.get("messages") or []:
        role = message.get("role") or message.get("author") or message.get("sender")
        if isinstance(role, dict):
            role = role.get("role")
        if role not in OWNER_ROLES:
            continue
        at = parse_time(message.get("create_time") or message.get("created_at") or message.get("timestamp")
                        or conv.get("create_time") or conv.get("created_at"))
        messages.append((at, _text_of(message.get("content") or message.get("text") or "")))
    conv_id = str(conv.get("id") or conv.get("uuid") or _hash(f"{index}", path))
    return _session("web", conv_id, path, messages)


def _gemini(entries: list[dict], path: Path) -> list[Session]:
    """Takeout keeps prompts as activity entries, one per prompt; each day becomes one session."""
    by_day: dict[str, list[tuple]] = {}
    for entry in entries:
        title = entry.get("title") or ""
        if not title.startswith("Prompted "):
            continue
        at = parse_time(entry.get("time"))
        if at:
            by_day.setdefault(at.date().isoformat(), []).append((at, title.removeprefix("Prompted ")))
    return [_session("gemini", f"gemini-{day}", path, msgs) for day, msgs in sorted(by_day.items())]


def _hash(value, path: Path) -> str:
    return hashlib.sha1(f"{path}|{value}".encode("utf-8", "replace")).hexdigest()[:16]


def _parse_data(data, path: Path) -> tuple[list[Session], str]:
    items = data if isinstance(data, list) else [data]
    items = [i for i in items if isinstance(i, dict)]
    if not items:
        return [], ""
    if any("mapping" in i for i in items):
        return [_chatgpt(i, path) for i in items if "mapping" in i], "chatgpt"
    if any("chat_messages" in i for i in items):
        return [_claude_ai(i, path) for i in items if "chat_messages" in i], "claude-ai"
    if any(str(i.get("title", "")).startswith("Prompted ") for i in items):
        return _gemini(items, path), "gemini"
    if any(isinstance(i.get("messages"), list) for i in items):
        return [_generic(i, path, n) for n, i in enumerate(items) if isinstance(i.get("messages"), list)], "web"
    return [], ""


def parse_file(path: Path) -> tuple[list[Session], str]:
    """Sessions from one export file (or every JSON in a zip), and the format read ("" if none)."""
    sessions, kinds = [], []
    for _name, data in _load(path):
        found, kind = _parse_data(data, path)
        if kind:
            sessions += found
            kinds.append(kind)
    return sessions, ",".join(sorted(set(kinds)))


def assistant_texts(path: Path) -> Iterator[str]:
    """Every reply an assistant wrote in an export (ChatGPT, Claude.ai, generic)."""
    for _name, data in _load(path):
        items = data if isinstance(data, list) else [data]
        for conv in (i for i in items if isinstance(i, dict)):
            for node in (conv.get("mapping") or {}).values():
                message = (node or {}).get("message") or {}
                if (message.get("author") or {}).get("role") == "assistant":
                    content = message.get("content") or {}
                    yield _text_of([p for p in content.get("parts") or [] if isinstance(p, str)])
            for message in conv.get("chat_messages") or []:
                if message.get("sender") == "assistant":
                    yield message.get("text") or _text_of(message.get("content") or [])
            for message in conv.get("messages") or [] if isinstance(conv.get("messages"), list) else []:
                role = message.get("role") or message.get("author") or message.get("sender")
                if isinstance(role, dict):
                    role = role.get("role")
                if role in ("assistant", "model", "bot"):
                    yield _text_of(message.get("content") or message.get("text") or "")
