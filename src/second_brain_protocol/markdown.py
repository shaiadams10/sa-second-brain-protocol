from __future__ import annotations

import re
import time
from pathlib import Path


class GeneratedSectionError(RuntimeError):
    pass


def slugify(value: str, *, max_length: int = 80) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return (value[:max_length].rstrip("-") or "item")


def replace_generated_section(text: str, section: str, body: str) -> str:
    start = f"<!-- sb:generated {section}:start -->"
    end = f"<!-- sb:generated {section}:end -->"
    start_count = text.count(start)
    end_count = text.count(end)
    if start_count != 1 or end_count != 1:
        raise GeneratedSectionError(
            f"Expected one generated marker pair for {section}; found start={start_count}, end={end_count}"
        )
    start_index = text.index(start)
    end_index = text.index(end)
    if end_index < start_index:
        raise GeneratedSectionError(f"Generated markers are reversed for {section}")
    prefix = text[: start_index + len(start)]
    suffix = text[end_index:]
    normalized = body.strip()
    return f"{prefix}\n{normalized}\n{suffix}"


def update_generated_file(path: Path, section: str, body: str) -> None:
    current = path.read_text(encoding="utf-8")
    updated = replace_generated_section(current, section, body)
    if updated == current:
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(updated, encoding="utf-8")
    _replace_with_windows_lock_retries(temporary, path)


def _replace_with_windows_lock_retries(source: Path, destination: Path) -> None:
    """Atomically replace a note while tolerating short-lived editor locks.

    Obsidian, search indexers, and antivirus scanners can briefly open Markdown
    files without delete sharing on Windows.  ``Path.replace`` then raises
    ``PermissionError`` even when the file and directory ACLs are correct.  The
    bounded retry keeps publication atomic; a persistent lock still fails and
    deliberately leaves the fully written ``.tmp`` file for recovery.
    """

    delays = (0.05, 0.1, 0.2, 0.4, 0.8, 1.0)
    for attempt in range(len(delays) + 1):
        try:
            source.replace(destination)
            return
        except PermissionError:
            if attempt == len(delays):
                raise
            time.sleep(delays[attempt])
