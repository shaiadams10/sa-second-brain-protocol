from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .scanner import run_git
from .state import StateStore


def _workspace_path_text(value: str | Path | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.casefold().startswith("file://"):
        parsed = urlparse(raw)
        raw = unquote(parsed.path or "")
        if re.match(r"^/[A-Za-z]:/", raw):
            raw = raw[1:]
    return raw


def normalize_workspace_path(value: str | Path | None) -> str:
    """Normalize a local workspace path without requiring it to still exist."""

    raw = _workspace_path_text(value)
    if not raw:
        return ""
    normalized = re.sub(r"/+", "/", raw.replace("\\", "/"))
    if len(normalized) > 3:
        normalized = normalized.rstrip("/")
    return normalized.casefold()


def workspace_hash(value: str | Path | None) -> str | None:
    normalized = normalize_workspace_path(value)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else None


def _inside(candidate: str, root: str) -> bool:
    return bool(candidate and root and (candidate == root or candidate.startswith(root + "/")))


def _normalized_remote(value: str | None) -> str:
    remote = str(value or "").strip().replace("\\", "/").rstrip("/")
    if not remote:
        return ""
    if "://" in remote:
        parsed = urlparse(remote)
        host = parsed.hostname or ""
        remote = f"{host}/{parsed.path.lstrip('/')}"
    else:
        scp_style = re.match(r"^(?:[^@/:]+@)?([^/:]+):(.+)$", remote)
        if scp_style and not re.match(r"^[A-Za-z]:/", remote):
            remote = f"{scp_style.group(1)}/{scp_style.group(2)}"
    remote = remote.casefold().removesuffix(".git")
    return remote.rstrip("/")


@dataclass(frozen=True)
class SessionResolution:
    status: str
    project_id: str | None
    resolver: str
    confidence: float


class ProjectSessionResolver:
    """Resolve session workspaces to one leaf project using durable metadata."""

    def __init__(
        self,
        projects: list[dict[str, Any]],
        aliases: list[dict[str, Any]],
    ) -> None:
        self.projects = {
            str(project["id"]): project
            for project in projects
            if project.get("local_path")
            and project.get("classification") not in {"collection", "duplicate"}
        }
        self.current_roots = [
            (normalize_workspace_path(project["local_path"]), project_id)
            for project_id, project in self.projects.items()
        ]
        self.alias_roots = [
            (
                normalize_workspace_path(alias.get("normalized_path")),
                str(alias.get("project_id") or ""),
                bool(alias.get("is_current")),
                float(alias.get("confidence") or 0.0),
            )
            for alias in aliases
            if str(alias.get("project_id") or "") in self.projects
        ]
        leaf_values: dict[str, set[str]] = {}
        for project_id, project in self.projects.items():
            leaf = normalize_workspace_path(project["local_path"]).rsplit("/", 1)[-1]
            if leaf:
                leaf_values.setdefault(leaf, set()).add(project_id)
        self.unique_current_leaves = {
            leaf: next(iter(project_ids))
            for leaf, project_ids in leaf_values.items()
            if len(project_ids) == 1
        }
        self.remote_index = self._unique_index("remote_url", _normalized_remote)
        commit_values: dict[str, set[str]] = {}
        for project_id, project in self.projects.items():
            for field in ("initial_commit", "head_commit"):
                commit = str(project.get(field) or "").casefold()
                if commit:
                    commit_values.setdefault(commit, set()).add(project_id)
        self.commit_index = {
            commit: next(iter(project_ids))
            for commit, project_ids in commit_values.items()
            if len(project_ids) == 1
        }
        self._cache: dict[str, SessionResolution] = {}

    def _unique_index(self, field: str, normalize: Any) -> dict[str, str]:
        values: dict[str, set[str]] = {}
        for project_id, project in self.projects.items():
            value = normalize(project.get(field))
            if value:
                values.setdefault(value, set()).add(project_id)
        return {
            value: next(iter(project_ids))
            for value, project_ids in values.items()
            if len(project_ids) == 1
        }

    @staticmethod
    def _path_resolution(
        workspace: str,
        roots: list[tuple[str, str]],
        *,
        resolver: str,
        confidence: float,
    ) -> SessionResolution | None:
        matches = [
            (len(root), project_id)
            for root, project_id in roots
            if _inside(workspace, root)
        ]
        if not matches:
            return None
        depth = max(length for length, _project_id in matches)
        candidates = {project_id for length, project_id in matches if length == depth}
        if len(candidates) != 1:
            return SessionResolution("ambiguous", None, resolver, 0.0)
        return SessionResolution(
            "matched", next(iter(candidates)), resolver, confidence
        )

    def _git_resolution(self, workspace: str) -> SessionResolution | None:
        path = Path(workspace)
        if not path.exists():
            return None
        root = run_git(path, "rev-parse", "--show-toplevel", timeout=10)
        if not root:
            return None
        repository = Path(root)
        remote = _normalized_remote(
            run_git(repository, "remote", "get-url", "origin", timeout=10)
        )
        initial = run_git(
            repository, "rev-list", "--max-parents=0", "HEAD", timeout=10
        ).splitlines()
        candidates: set[str] = set()
        if remote and remote in self.remote_index:
            candidates.add(self.remote_index[remote])
        for commit in initial:
            project_id = self.commit_index.get(commit.casefold())
            if project_id:
                candidates.add(project_id)
        if len(candidates) > 1:
            return SessionResolution("ambiguous", None, "git_identity", 0.0)
        if len(candidates) == 1:
            return SessionResolution(
                "matched", next(iter(candidates)), "git_identity", 0.99
            )
        return None

    def _session_git_resolution(
        self, remote_url: str | None, commit_hash: str | None
    ) -> SessionResolution | None:
        candidates: set[str] = set()
        remote = _normalized_remote(remote_url)
        if remote and remote in self.remote_index:
            candidates.add(self.remote_index[remote])
        commit = str(commit_hash or "").casefold()
        if commit and commit in self.commit_index:
            candidates.add(self.commit_index[commit])
        if len(candidates) > 1:
            return SessionResolution(
                "ambiguous", None, "conflicting_session_git_metadata", 0.0
            )
        if len(candidates) == 1:
            return SessionResolution(
                "matched", next(iter(candidates)), "session_git_identity", 0.99
            )
        return None

    @staticmethod
    def _combine_authoritative(
        path_resolution: SessionResolution | None,
        git_resolution: SessionResolution | None,
    ) -> SessionResolution | None:
        if path_resolution and path_resolution.status == "ambiguous":
            return path_resolution
        if git_resolution and git_resolution.status == "ambiguous":
            return git_resolution
        if path_resolution and git_resolution:
            if path_resolution.project_id != git_resolution.project_id:
                return SessionResolution(
                    "ambiguous", None, "conflicting_path_git_metadata", 0.0
                )
            return path_resolution
        return path_resolution or git_resolution

    def resolve(
        self,
        workspace: str | Path | None,
        *,
        remote_url: str | None = None,
        commit_hash: str | None = None,
    ) -> SessionResolution:
        normalized = normalize_workspace_path(workspace)
        metadata_key = (_normalized_remote(remote_url), str(commit_hash or "").casefold())
        cache_key = f"{normalized}\0{metadata_key[0]}\0{metadata_key[1]}"
        if not normalized and not any(metadata_key):
            return SessionResolution("unmatched", None, "missing_workspace", 0.0)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        current = self._path_resolution(
            normalized,
            self.current_roots,
            resolver="current_path",
            confidence=1.0,
        )
        path_resolution = current
        if path_resolution is None:
            authoritative_aliases = [
                (root, project_id)
                for root, project_id, _current, confidence in self.alias_roots
                if confidence >= 0.9
            ]
            path_resolution = self._path_resolution(
                normalized,
                authoritative_aliases,
                resolver="historical_path",
                confidence=0.98,
            )
        git_resolution = self._session_git_resolution(remote_url, commit_hash)
        result = self._combine_authoritative(path_resolution, git_resolution)
        if result is None and normalized:
            result = self._git_resolution(_workspace_path_text(workspace))
        if result is None and normalized:
            raw_workspace = Path(_workspace_path_text(workspace))
            leaf = normalized.rsplit("/", 1)[-1]
            project_id = self.unique_current_leaves.get(leaf)
            if project_id and not raw_workspace.exists():
                result = SessionResolution(
                    "matched",
                    project_id,
                    "unique_extinct_workspace_leaf",
                    0.92,
                )
        if result is not None:
            self._cache[cache_key] = result
            return result

        result = SessionResolution("unmatched", None, "no_authoritative_match", 0.0)
        self._cache[cache_key] = result
        return result


def register_current_project_paths(
    store: StateStore,
    projects: list[dict[str, Any]],
    *,
    previous_projects: list[dict[str, Any]] = (),
) -> None:
    """Retain prior paths as aliases and mark only this scan's paths current."""

    store.clear_current_project_paths()
    for project in previous_projects:
        if project.get("local_path"):
            store.register_project_path(
                str(project["id"]),
                normalize_workspace_path(project["local_path"]),
                source="validated_scan_history",
                confidence=1.0,
            )
    for project in projects:
        if project.get("local_path"):
            store.register_project_path(
                str(project["id"]),
                normalize_workspace_path(project["local_path"]),
                source="current_scan",
                confidence=1.0,
                current=True,
            )


def seed_project_paths_from_backups(
    store: StateStore,
    backup_root: Path,
    projects: list[dict[str, Any]],
) -> dict[str, int]:
    """Recover authoritative historical paths from machine-local state backups."""

    current = {str(project["id"]): project for project in projects}

    def unique(field: str, normalize: Any) -> dict[str, str]:
        values: dict[str, set[str]] = {}
        for project_id, project in current.items():
            value = normalize(project.get(field))
            if value:
                values.setdefault(value, set()).add(project_id)
        return {
            value: next(iter(project_ids))
            for value, project_ids in values.items()
            if len(project_ids) == 1
        }

    remotes = unique("remote_url", _normalized_remote)
    commits = unique("initial_commit", lambda value: str(value or "").casefold())
    current_paths = {
        project_id: normalize_workspace_path(project.get("local_path"))
        for project_id, project in current.items()
    }
    known_aliases = {
        (str(alias["project_id"]), str(alias["normalized_path"]))
        for alias in store.project_path_aliases(project_ids=set(current))
    }
    databases = sorted(backup_root.rglob("*.sqlite")) if backup_root.exists() else []
    recovered: set[tuple[str, str]] = set()
    for database in databases:
        try:
            uri = f"file:{database.as_posix()}?mode=ro"
            with sqlite3.connect(uri, uri=True) as connection:
                rows = connection.execute(
                    "SELECT metadata_json FROM projects"
                ).fetchall()
        except (OSError, sqlite3.Error):
            continue
        for (raw,) in rows:
            try:
                project = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            candidates: set[str] = set()
            project_id = str(project.get("id") or "")
            if project_id in current:
                candidates.add(project_id)
            remote = _normalized_remote(project.get("remote_url"))
            if remote and remote in remotes:
                candidates.add(remotes[remote])
            commit = str(project.get("initial_commit") or "").casefold()
            if commit and commit in commits:
                candidates.add(commits[commit])
            if len(candidates) != 1 or not project.get("local_path"):
                continue
            matched_project = next(iter(candidates))
            historical_path = normalize_workspace_path(project["local_path"])
            if not historical_path or historical_path == current_paths.get(matched_project):
                continue
            recovered.add((matched_project, historical_path))
    for project_id, historical_path in sorted(recovered):
        if (project_id, historical_path) in known_aliases:
            continue
        store.register_project_path(
            project_id,
            historical_path,
            source="state_backup",
            confidence=1.0,
        )
    registered = len(recovered - known_aliases)
    return {"backup_databases": len(databases), "historical_paths_registered": registered}
