"""The normalized shape every conversation source is parsed into."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Exchange:
    """One round: what the owner said, and the assistant's final reply to it."""

    at: datetime
    user: str
    reply: str = ""
    tool_calls: int = 0
    # The assistant's first text in the round: where it usually says back what it understood.
    opening: str = ""


@dataclass
class Session:
    tool: str  # "codex" | "claude-code" | "antigravity"
    id: str
    source_file: str
    cwd: str | None = None
    # Paths the assistant touched, used when a source records no working folder.
    path_hints: Counter = field(default_factory=Counter)
    exchanges: list[Exchange] = field(default_factory=list)
    # Started by another agent (a subagent thread or a scripted run): its user turns are
    # that agent's instructions, not the owner's words.
    by_agent: bool = False


class ExchangeBuilder:
    """Groups a stream of user / assistant / tool events into exchanges.

    The reply kept for an exchange is the assistant's final answer when the
    source marks one, otherwise the last assistant text before the owner spoke again.
    The opening is its first text in the round.
    """

    def __init__(self) -> None:
        self.exchanges: list[Exchange] = []
        self._current: Exchange | None = None
        self._final: str | None = None
        self._last: str | None = None

    def user(self, at: datetime, text: str) -> None:
        self._close()
        self._current = Exchange(at=at, user=text)

    def assistant(self, text: str, final: bool = False) -> None:
        if self._current is None or not text.strip():
            return
        self._last = text
        if not self._current.opening:
            self._current.opening = text
        if final:
            self._final = text

    def tool(self, count: int = 1) -> None:
        if self._current is not None:
            self._current.tool_calls += count

    def finish(self) -> list[Exchange]:
        self._close()
        return self.exchanges

    def _close(self) -> None:
        if self._current is not None:
            self._current.reply = self._final or self._last or ""
            self.exchanges.append(self._current)
        self._current, self._final, self._last = None, None, None
