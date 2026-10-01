"""Text cleanup shared by the parsers: strip injected context, mask secrets, trim length."""

from __future__ import annotations

import re
from datetime import datetime

# Blocks that agent apps inject into the user's turn. They are context, not what the owner said.
INJECTED_TAGS = (
    "environment_context", "user_instructions", "app-context", "skills_instructions",
    "recommended_plugins", "in-app-browser-context", "turn_aborted", "collaboration_mode",
    "apps_instructions", "image_resize_notice", "model_switch", "multi_agent_role",
    "multi_agent_mode", "system-reminder", "ADDITIONAL_METADATA", "USER_SETTINGS_CHANGE",
    "local-command-caveat", "local-command-stdout", "local-command-stderr", "command-message",
    "agent-embed", "image",
)
_TAG_BLOCK = re.compile(
    r"<(%s)\b[^>]*>.*?</\1>" % "|".join(re.escape(t) for t in INJECTED_TAGS), re.S
)
_TAG_SINGLE = re.compile(r"</?(%s)\b[^>]*/?>" % "|".join(re.escape(t) for t in INJECTED_TAGS))

# Whole items that are injected context when a message starts with them.
INJECTED_PREFIXES = (
    "# AGENTS.md instructions",
    "# Files mentioned by the user",
    "Caveat: The messages below were generated",
    "Base directory for this skill:",
    "[Image: source:",
    # Handoff briefs one agent writes for another: pasted by the owner, but not in their words.
    "# Handoff",
)

_SECRETS = [
    re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),  # JWT
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{20,}"),
    re.compile(
        r"(?i)\b((?:api[_-]?key|secret|token|password|passwd|license[_-]?key)\s*[:=]\s*)"
        r"[\"']?[^\s\"']{8,}"
    ),
]


# An attached terminal or file: a marker line followed by its contents quoted with ">".
# It is output, not what the owner said, so only a short placeholder is kept.
_ATTACHED = re.compile(r"<!-- attach: ([^|>]+?)\s*(?:\|[^>]*)?-->[ \t]*\n?(?:[ \t]*>.*(?:\n|$))*")
# Pasted text may be the owner's own draft or someone else's output: kept, but marked.
_PASTED_OPEN = re.compile(r"<pasted_content\b[^>]*>")
_PASTED_CLOSE = re.compile(r"</pasted_content\b[^>]*>")


def strip_injected(text: str) -> str:
    text = _TAG_BLOCK.sub("", text)
    text = _TAG_SINGLE.sub("", text)
    text = _ATTACHED.sub(lambda m: f"[attached {m.group(1).strip()}]\n", text)
    text = _PASTED_OPEN.sub("[pasted text]", text)
    text = _PASTED_CLOSE.sub("[end of pasted text]", text)
    return text.strip()


def is_injected(text: str) -> bool:
    return text.lstrip().startswith(INJECTED_PREFIXES)


def redact(text: str) -> str:
    for pattern in _SECRETS:
        if pattern.groups:
            text = pattern.sub(lambda m: m.group(1) + " [REDACTED]", text)
        else:
            text = pattern.sub("[REDACTED]", text)
    return text


def clip(text: str, limit: int) -> str:
    """Keep the start and end of long text; the middle of a paste is rarely the point."""
    text = text.strip()
    if len(text) <= limit:
        return text
    head = int(limit * 0.7)
    tail = limit - head
    return f"{text[:head].rstrip()}\n[… {len(text) - limit} characters cut …]\n{text[-tail:].lstrip()}"


def parse_time(value: str | int | float | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000 if value > 1e11 else value).astimezone()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None
