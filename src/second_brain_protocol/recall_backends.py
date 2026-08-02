from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from .basic_memory_integration import search_vector
from .canonical_paths import is_indexable_markdown
from .config import RuntimePaths
from .recall_harness import RecallCandidate
from .security import SENSITIVE_FILE_NAME


SearchFunction = Callable[..., list[dict[str, Any]]]
TOKEN = re.compile(r"[a-z0-9]+")


def stable_source_id(note_path: str) -> str:
    """Return a path-stable opaque identity shared by every recall channel."""

    normalized = _safe_relative_markdown(note_path)
    digest = hashlib.sha256(normalized.casefold().encode("utf-8")).hexdigest()[:24]
    return f"note-{digest}"


class CanonicalLexicalBackend:
    """Search canonical Markdown directly without relying on a derived index."""

    def __init__(self, vault: Path) -> None:
        self._vault = vault.resolve()

    def search(self, query: str, *, limit: int) -> tuple[RecallCandidate, ...]:
        if limit <= 0:
            return ()
        query_text = " ".join(query.casefold().split())
        query_tokens = tuple(dict.fromkeys(TOKEN.findall(query_text)))
        if not query_tokens:
            return ()
        candidates: list[RecallCandidate] = []
        for path in sorted(self._vault.rglob("*.md")):
            relative = path.relative_to(self._vault).as_posix()
            if not _is_indexable_relative_path(relative):
                continue
            if _path_uses_symlink(self._vault, Path(relative)):
                continue
            try:
                source = path.resolve(strict=True)
                resolved_relative = source.relative_to(self._vault).as_posix()
                if not is_indexable_markdown(resolved_relative):
                    continue
                if not source.is_file():
                    continue
                text = source.read_text(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                continue
            lowered = text.casefold()
            title = _markdown_title(path, text)
            title_and_path = f"{title} {relative}".casefold()
            matched = tuple(token for token in query_tokens if token in lowered)
            if not matched:
                continue
            coverage = len(matched) / len(query_tokens)
            occurrences = sum(min(lowered.count(token), 20) for token in matched)
            heading_matches = sum(token in title_and_path for token in matched)
            phrase_bonus = 6.0 if query_text in lowered else 0.0
            score = phrase_bonus + coverage * 4.0 + heading_matches * 1.5
            score += min(occurrences, 40) / 40
            candidates.append(
                RecallCandidate(
                    source_id=stable_source_id(relative),
                    note_path=relative,
                    title=title,
                    snippet=_bounded_snippet(text, lowered, matched),
                    score=score,
                    canonical=True,
                    relations=(),
                )
            )
        candidates.sort(key=lambda item: (-item.score, item.source_id))
        return tuple(candidates[:limit])


class BasicMemoryVectorBackend:
    """Adapt Basic Memory's local hybrid results to the bounded recall seam."""

    def __init__(
        self,
        *,
        paths: RuntimePaths,
        vault: Path,
        search_fn: SearchFunction = search_vector,
    ) -> None:
        self._paths = paths
        self._vault = vault.resolve()
        self._search = search_fn

    def search(self, query: str, *, limit: int) -> tuple[RecallCandidate, ...]:
        if limit <= 0:
            return ()
        rows = self._search(self._paths, query, limit=limit)
        candidates: list[RecallCandidate] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            candidate = self._candidate(row)
            if candidate is not None:
                candidates.append(candidate)
        candidates.sort(key=lambda item: (-item.score, item.source_id))
        return tuple(candidates[:limit])

    def _candidate(self, row: Mapping[str, Any]) -> RecallCandidate | None:
        raw_path = row.get("file_path") or row.get("path")
        if not isinstance(raw_path, str):
            return None
        try:
            relative = _safe_relative_markdown(raw_path)
        except ValueError:
            return None
        if not _is_indexable_relative_path(relative):
            return None
        if _path_uses_symlink(self._vault, Path(relative)):
            return None
        source = (self._vault / Path(relative)).resolve()
        try:
            resolved_relative = source.relative_to(self._vault).as_posix()
        except ValueError:
            return None
        if not is_indexable_markdown(resolved_relative):
            return None
        if not source.is_file():
            return None
        raw_score = row.get("score")
        if (
            not isinstance(raw_score, (int, float))
            or isinstance(raw_score, bool)
            or not math.isfinite(float(raw_score))
        ):
            return None
        title = row.get("title")
        if not isinstance(title, str) or not title.strip():
            title = source.stem
        snippet = next(
            (
                value
                for key in ("matched_chunk", "excerpt", "content")
                if isinstance((value := row.get(key)), str) and value.strip()
            ),
            None,
        )
        if snippet is None:
            return None
        relations = row.get("relations")
        safe_relations = (
            tuple(value for value in relations if isinstance(value, str))
            if isinstance(relations, list)
            else ()
        )
        return RecallCandidate(
            source_id=stable_source_id(relative),
            note_path=relative,
            title=title.strip(),
            snippet=snippet[:2000],
            score=float(raw_score),
            canonical=True,
            relations=safe_relations,
        )


def _safe_relative_markdown(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    path = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
        or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", normalized)
        or ".." in path.parts
        or path.suffix.casefold() != ".md"
        or SENSITIVE_FILE_NAME.search(normalized)
    ):
        raise ValueError("Source must be a canonical relative Markdown note")
    return path.as_posix()


def _is_indexable_relative_path(relative: str) -> bool:
    try:
        normalized = _safe_relative_markdown(relative)
    except ValueError:
        return False
    return is_indexable_markdown(normalized)


def _path_uses_symlink(vault: Path, relative: Path) -> bool:
    current = vault
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _markdown_title(path: Path, text: str) -> str:
    for line in text.splitlines()[:80]:
        match = re.match(r"^#\s+(.+?)\s*$", line)
        if match:
            return match.group(1)[:240]
    return path.stem[:240]


def _bounded_snippet(
    text: str,
    lowered: str,
    matched_tokens: tuple[str, ...],
    *,
    max_chars: int = 1200,
) -> str:
    positions = [lowered.find(token) for token in matched_tokens]
    start = max(0, min(position for position in positions if position >= 0) - 240)
    end = min(len(text), start + max_chars)
    snippet = text[start:end].strip()
    if start:
        snippet = "…" + snippet
    if end < len(text):
        snippet += "…"
    return snippet
