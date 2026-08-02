from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .canonical_paths import is_indexable_markdown
from .config import RuntimePaths


PROJECT_NAME = "personal-vault"


def _executable() -> str | None:
    candidates = [
        shutil.which("basic-memory"),
        shutil.which("bm"),
        str(Path(sys.executable).with_name("basic-memory.exe")),
        str(Path(sys.executable).with_name("bm.exe")),
    ]
    return next((item for item in candidates if item and Path(item).exists()), None)


def _env(paths: RuntimePaths) -> dict[str, str]:
    env = os.environ.copy()
    env["BASIC_MEMORY_CONFIG_DIR"] = str(paths.basic_memory)
    env["FASTEMBED_CACHE_PATH"] = str(paths.basic_memory / "fastembed_cache")
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    env["DO_NOT_TRACK"] = "1"
    return env


def _ensure_local_config(paths: RuntimePaths) -> None:
    paths.basic_memory.mkdir(parents=True, exist_ok=True)
    config_path = paths.basic_memory / "config.json"
    if config_path.exists():
        data = json.loads(config_path.read_text(encoding="utf-8"))
    else:
        data = {}
    data.update(
        {
            "auto_update": False,
            "cloud_promo_opt_out": True,
            "logfire_enabled": False,
            "logfire_send_to_logfire": False,
            "semantic_search_enabled": True,
            "semantic_embedding_provider": "fastembed",
            "semantic_embedding_model": "bge-small-en-v1.5",
            "disable_permalinks": True,
            "ensure_frontmatter_on_sync": False,
            "update_permalinks_on_move": False,
            "format_on_save": False,
        }
    )
    config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _run(
    paths: RuntimePaths, *args: str, timeout: int = 300
) -> subprocess.CompletedProcess[str]:
    _ensure_local_config(paths)
    executable = _executable()
    if not executable:
        raise RuntimeError("Basic Memory executable was not found")
    return subprocess.run(
        [executable, *args],
        env=_env(paths),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def build_index_mirror(paths: RuntimePaths, vault: Path) -> Path:
    """Copy canonical Markdown into runtime so Basic Memory never edits the vault."""
    destination = paths.basic_memory / "vault-mirror"
    pending = paths.basic_memory / "vault-mirror-next"
    if pending.exists():
        shutil.rmtree(pending)
    pending.mkdir(parents=True)
    for source in vault.rglob("*.md"):
        relative = source.relative_to(vault)
        if not is_indexable_markdown(relative):
            continue
        target = pending / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    if destination.exists():
        shutil.rmtree(destination)
    pending.replace(destination)
    return destination


def configure_project(paths: RuntimePaths, index_root: Path) -> None:
    _ensure_local_config(paths)
    config = json.loads(
        (paths.basic_memory / "config.json").read_text(encoding="utf-8")
    )
    existing = config.get("projects", {}).get(PROJECT_NAME)
    if existing:
        configured = Path(existing["path"]).resolve()
        if configured != index_root.resolve():
            result = _run(
                paths, "project", "move", PROJECT_NAME, str(index_root), timeout=120
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr or result.stdout)
        return
    result = _run(
        paths,
        "project",
        "add",
        PROJECT_NAME,
        str(index_root),
        "--local",
        "--default",
        timeout=120,
    )
    combined = (result.stdout + result.stderr).lower()
    if (
        result.returncode != 0
        and "already" not in combined
        and "exists" not in combined
    ):
        raise RuntimeError(result.stderr or result.stdout)


def update_index_mirror(
    paths: RuntimePaths, vault: Path, relative_paths: list[str]
) -> Path:
    """Update only changed canonical notes in the existing runtime mirror."""

    destination = paths.basic_memory / "vault-mirror"
    if not destination.is_dir():
        return build_index_mirror(paths, vault)
    vault_root = vault.resolve()
    mirror_root = destination.resolve()
    for value in sorted(set(relative_paths)):
        relative = Path(value)
        if not is_indexable_markdown(relative):
            raise ValueError("Invalid incremental index path")
        source = (vault_root / relative).resolve()
        target = (mirror_root / relative).resolve()
        source.relative_to(vault_root)
        target.relative_to(mirror_root)
        if source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        elif target.is_file():
            target.unlink()
    return destination


def _reindex_configured(paths: RuntimePaths, mirror: Path) -> str:
    configure_project(paths, mirror)
    result = _run(paths, "reindex", "--project", PROJECT_NAME, timeout=1800)
    if result.returncode != 0:
        raise RuntimeError(
            "Basic Memory reindex failed: " + (result.stderr or result.stdout)
        )
    status_result = _run(
        paths,
        "status",
        "--project",
        PROJECT_NAME,
        "--wait",
        "--timeout",
        "300",
        "--json",
        timeout=360,
    )
    if status_result.returncode != 0:
        raise RuntimeError(
            "Basic Memory indexing did not settle: "
            + (status_result.stderr or status_result.stdout)
        )
    return status_result.stdout.strip()


def reindex(paths: RuntimePaths, vault: Path) -> str:
    return _reindex_configured(paths, build_index_mirror(paths, vault))


def reindex_changed(paths: RuntimePaths, vault: Path, relative_paths: list[str]) -> str:
    return _reindex_configured(paths, update_index_mirror(paths, vault, relative_paths))


def search(paths: RuntimePaths, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
    candidates = [
        (
            "tool",
            "search-notes",
            query,
            "--project",
            PROJECT_NAME,
            "--local",
            "--hybrid",
            "--page-size",
            str(limit),
        ),
        (
            "tool",
            "search-notes",
            query,
            "--project",
            PROJECT_NAME,
            "--local",
            "--page-size",
            str(limit),
        ),
    ]
    for args in candidates:
        result = _run(paths, *args, timeout=120)
        if result.returncode != 0:
            continue
        try:
            data = json.loads(result.stdout)
            rows = _search_rows(data, limit=limit)
            if rows:
                return rows
        except json.JSONDecodeError:
            continue
    return []


def search_vector(
    paths: RuntimePaths, query: str, *, limit: int = 10
) -> list[dict[str, Any]]:
    """Run vector-only retrieval so recall ablations are not internally hybrid."""

    result = _run(
        paths,
        "tool",
        "search-notes",
        query,
        "--project",
        PROJECT_NAME,
        "--local",
        "--vector",
        "--page-size",
        str(limit),
        timeout=120,
    )
    if result.returncode != 0:
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    return _search_rows(data, limit=limit)


def _search_rows(data: Any, *, limit: int) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)][:limit]
    if not isinstance(data, dict):
        return []
    collection = data.get("results") or data.get("items")
    if collection is not None:
        if not isinstance(collection, list):
            return []
        return [row for row in collection if isinstance(row, dict)][:limit]
    if "file_path" in data or "path" in data:
        return [data]
    return []


def status(paths: RuntimePaths) -> dict[str, Any]:
    result = _run(
        paths, "status", "--project", PROJECT_NAME, "--json", "--local", timeout=120
    )
    output = (result.stdout or result.stderr).strip()
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        parsed = output
    return {"ok": result.returncode == 0, "output": parsed}
