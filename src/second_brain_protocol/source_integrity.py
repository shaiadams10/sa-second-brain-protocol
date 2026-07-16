from __future__ import annotations

import gzip
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .scanner import discover_git_roots, run_git


@dataclass(frozen=True)
class IntegrityComparison:
    unchanged: bool
    added: list[str]
    removed: list[str]
    modified: list[str]
    git_status_changed: list[str]


def capture(projects_root: Path, destination: Path) -> Path:
    root = projects_root.resolve()
    files: dict[str, list[int]] = {}
    for current, _directories, names in os.walk(root):
        for name in names:
            path = Path(current) / name
            try:
                stat = path.stat()
            except OSError:
                continue
            files[path.relative_to(root).as_posix()] = [stat.st_size, stat.st_mtime_ns]
    statuses = {}
    for repo in discover_git_roots(root, max_depth=8):
        statuses[repo.relative_to(root).as_posix()] = run_git(
            repo, "status", "--porcelain=v1", "--untracked-files=all"
        ).splitlines()
    payload = {"schema_version": 1, "root_label": "configured-projects-root", "files": files, "git_status": statuses}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(destination, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
    return destination


def _load(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def compare(before: Path, after: Path) -> IntegrityComparison:
    left = _load(before)
    right = _load(after)
    before_files = left["files"]
    after_files = right["files"]
    added = sorted(set(after_files) - set(before_files))
    removed = sorted(set(before_files) - set(after_files))
    modified = sorted(path for path in set(before_files) & set(after_files) if before_files[path] != after_files[path])
    repositories = set(left["git_status"]) | set(right["git_status"])
    git_status_changed = sorted(
        repo for repo in repositories if left["git_status"].get(repo) != right["git_status"].get(repo)
    )
    return IntegrityComparison(
        unchanged=not (added or removed or modified or git_status_changed),
        added=added,
        removed=removed,
        modified=modified,
        git_status_changed=git_status_changed,
    )
