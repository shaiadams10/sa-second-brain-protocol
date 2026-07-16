from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .basic_memory_integration import search as memory_search
from .config import RuntimePaths
from .graphify_integration import graph_summary, query_graph
from .security import sanitize_text


def read_note(vault: Path, note: str) -> str:
    candidate = (vault / note).resolve()
    candidate.relative_to(vault.resolve())
    if candidate.suffix.lower() != ".md" or not candidate.is_file():
        raise FileNotFoundError(note)
    return candidate.read_text(encoding="utf-8")


def search(paths: RuntimePaths, vault: Path, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
    results = memory_search(paths, query, limit=limit)
    if results:
        return results
    lowered = query.casefold()
    fallback = []
    for path in vault.rglob("*.md"):
        if ".git" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if lowered in text.casefold() or lowered in path.stem.casefold():
            fallback.append({"path": path.relative_to(vault).as_posix(), "excerpt": text[:600]})
        if len(fallback) >= limit:
            break
    return fallback


def recent_activity(vault: Path, *, days: int = 7) -> list[dict[str, str]]:
    cutoff = date.today() - timedelta(days=days)
    rows = []
    for folder in (vault / "Journal" / "Daily", vault / "Journal" / "Weekly"):
        for path in sorted(folder.glob("*.md"), reverse=True):
            if date.fromtimestamp(path.stat().st_mtime) >= cutoff:
                rows.append({"note": path.relative_to(vault).as_posix(), "content": path.read_text(encoding="utf-8")})
    return rows


def build_context(paths: RuntimePaths, vault: Path, query: str, *, limit: int = 8) -> str:
    return json.dumps(search(paths, vault, query, limit=limit), indent=2, ensure_ascii=False)


def query_project_graph(paths: RuntimePaths, project_id: str, question: str) -> str:
    graph = _project_graph(paths, project_id)
    if not graph.exists():
        raise FileNotFoundError(graph)
    return sanitize_text(query_graph(graph, question))


def get_project_neighbors(paths: RuntimePaths, project_id: str) -> dict[str, Any]:
    graph = _project_graph(paths, project_id)
    if not graph.exists():
        raise FileNotFoundError(graph)
    return graph_summary(graph)


def trace_project_path(paths: RuntimePaths, project_id: str, source: str, target: str) -> str:
    return query_project_graph(paths, project_id, f"Find the shortest path from {source} to {target}")


def _project_graph(paths: RuntimePaths, project_id: str) -> Path:
    if not project_id or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in project_id.lower()):
        raise ValueError("Invalid project id")
    graph = (paths.graphify / project_id / "graph.json").resolve()
    graph.relative_to(paths.graphify.resolve())
    return graph
