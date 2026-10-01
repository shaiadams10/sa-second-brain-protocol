"""File and commit activity per project, for projects the owner works on without an AI tool
or without git. Each project is walked once per run and bucketed by day."""

from __future__ import annotations

import os
import subprocess
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from brain import proc
from brain.projects import IGNORED_DIRS, Project

MAX_FILES = 200_000


class Activity:
    def __init__(self, git_authors: list[str]) -> None:
        self.git_authors = git_authors
        self._files: dict[str, Counter] = {}
        self._commits: dict[str, Counter] = {}

    def file_days(self, project: Project) -> Counter:
        """Files modified per day."""
        if project.id not in self._files:
            days: Counter = Counter()
            seen = 0
            for root in (project.path, *project.aliases):
                for dirpath, dirnames, filenames in os.walk(root):
                    dirnames[:] = [d for d in dirnames
                                   if d.lower() not in IGNORED_DIRS and not d.startswith(".")]
                    for name in filenames:
                        try:
                            mtime = os.stat(os.path.join(dirpath, name)).st_mtime
                        except OSError:
                            continue
                        days[date.fromtimestamp(mtime)] += 1
                        seen += 1
                    if seen >= MAX_FILES:
                        break
            self._files[project.id] = days
        return self._files[project.id]

    def commit_days(self, project: Project) -> Counter:
        """Commits by the owner per day, across all branches and worktrees."""
        if project.id not in self._commits:
            days: Counter = Counter()
            if project.has_git:
                cmd = ["git", "-C", str(project.path), "log", "--all", "--format=%ct"]
                cmd += [f"--author={a}" for a in self.git_authors]
                try:
                    out = proc.run(cmd, capture_output=True, text=True, timeout=60).stdout
                except (OSError, subprocess.TimeoutExpired):
                    out = ""
                for line in out.split():
                    days[date.fromtimestamp(int(line))] += 1
            self._commits[project.id] = days
        return self._commits[project.id]


def in_range(days: Counter, since: datetime, until: datetime) -> Counter:
    start, end = since.date(), until.date()
    return Counter({d: n for d, n in days.items() if start <= d < end})
