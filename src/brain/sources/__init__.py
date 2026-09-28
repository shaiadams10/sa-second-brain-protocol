"""Conversation sources. Each module turns one tool's local history into Sessions."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from brain.model import Session
from brain.sources import antigravity, claude_code, codex

PARSERS = {
    "codex": codex,
    "claude_code": claude_code,
    "antigravity": antigravity,
}


def load_sources_for(sources: dict[str, Path], since: datetime, until: datetime) -> Iterator[Session]:
    """Yield every session with at least one exchange in [since, until)."""
    for name, root in sources.items():
        parser = PARSERS[name]
        if not root.exists():
            continue
        for path in parser.session_files(root, since):
            session = parser.parse(path)
            session.exchanges = [e for e in session.exchanges if since <= e.at < until]
            if session.exchanges:
                yield session
