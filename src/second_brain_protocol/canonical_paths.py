from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .security import SENSITIVE_FILE_NAME


EXCLUDED_PREFIXES = {
    ".git",
    ".obsidian",
    ".venv",
    "Inbox/Raw",
    "Evidence/Raw",
    "System/Local",
}
EXCLUDED_SEGMENTS = {
    ".git",
    ".obsidian",
    ".venv",
    ".pytest_cache",
    ".mypy_cache",
    "__pycache__",
    "node_modules",
}


def is_indexable_markdown(relative_path: str | Path) -> bool:
    """Return whether a relative path may cross the canonical recall boundary."""

    raw = str(relative_path).replace("\\", "/").strip()
    relative = Path(raw)
    label = relative.as_posix().lstrip("/")
    folded_label = label.casefold()
    folded_parts = tuple(part.casefold() for part in relative.parts)
    return bool(
        raw
        and relative.parts
        and not relative.is_absolute()
        and not re.match(r"^[A-Za-z]:", raw)
        and not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", raw)
        and ".." not in relative.parts
        and relative.suffix.casefold() == ".md"
        and not SENSITIVE_FILE_NAME.search(label)
        and not any(
            part in {value.casefold() for value in EXCLUDED_SEGMENTS}
            or part.startswith(".")
            for part in folded_parts
        )
        and not any(
            folded_label == excluded.casefold()
            or folded_label.startswith(excluded.casefold() + "/")
            for excluded in EXCLUDED_PREFIXES
        )
    )


def canonical_markdown_fingerprints(vault: Path) -> dict[str, str]:
    """Snapshot safe canonical Markdown identity without reading excluded sources."""

    root = vault.resolve(strict=True)
    result: dict[str, str] = {}
    for candidate in sorted(root.rglob("*.md")):
        relative = candidate.relative_to(root).as_posix()
        if not is_indexable_markdown(relative) or candidate.is_symlink():
            continue
        try:
            resolved = candidate.resolve(strict=True)
            if not resolved.is_relative_to(root) or not resolved.is_file():
                continue
            content = resolved.read_bytes()
        except OSError:
            continue
        result[relative] = hashlib.sha256(content).hexdigest()
    return result


def changed_canonical_markdown_paths(
    before: dict[str, str],
    after: dict[str, str],
) -> list[str]:
    return sorted(
        path
        for path in set(before).union(after)
        if before.get(path) != after.get(path)
    )
