from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .basic_memory_integration import status as memory_status
from .config import RuntimePaths, load_runtime_config, vault_root
from .model_runner import find_codex_executable
from .project_catalog import project_catalog_health
from .scheduler import task_details
from .state import StateStore


def _version(command: list[str]) -> str:
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=30, check=False
        )
        return (result.stdout or result.stderr).strip().splitlines()[0]
    except Exception as error:
        return f"unavailable: {error}"


def report(paths: RuntimePaths, *, include_memory: bool = True) -> dict[str, Any]:
    config = load_runtime_config(paths)
    store = StateStore(paths.state)
    try:
        codex = str(find_codex_executable(paths))
        codex_version = _version([codex, "--version"])
    except Exception as error:
        codex_version = f"unavailable: {error}"
    catalog = project_catalog_health(vault_root(), store.present_projects())
    missing_projects = store.missing_projects()
    catalog["missing_projects"] = len(missing_projects)
    catalog["missing_project_names"] = [item["project"] for item in missing_projects]
    data: dict[str, Any] = {
        "vault_exists": vault_root().is_dir(),
        "runtime_exists": paths.root.is_dir(),
        "projects_root_readable": Path(config["projects_root"]).is_dir(),
        "model_provider": config.get("model_provider", "openai"),
        "model_policy": config.get("model_policy", "chatgpt-direct-v1"),
        "bootstrap": store.bootstrap_state(),
        "codex": codex_version,
        "basic_memory_package": _version(
            [str(Path(shutil.which("basic-memory") or "basic-memory")), "--version"]
        ),
        "graphify_package": _version(
            [str(Path(shutil.which("graphify") or "graphify")), "--version"]
        ),
        "git": _version(["git", "--version"]),
        "github_cli": _version(["gh", "--version"]),
        "schedule": task_details(config["task_name"]),
        "pending_evidence": store.evidence_count(status="new"),
        "pending_review": len(store.observations("pending")),
        "project_catalog": catalog,
        "session_coverage": store.session_coverage(),
        "recurring_patterns": {
            status: len(store.pattern_signals(status))
            for status in ("tracking", "promoted", "pending", "conflict", "rejected")
        },
        "recent_runs": store.runs(limit=10),
        "recent_pipeline_runs": store.pipeline_runs(limit=10),
    }
    if include_memory:
        try:
            data["basic_memory"] = memory_status(paths)
        except Exception as error:
            data["basic_memory"] = {"ok": False, "error": str(error)}
    return data


def write_report(paths: RuntimePaths) -> Path:
    data = report(paths)
    # Health output contains machine-only run state and never belongs in the
    # tracked Obsidian vault.
    path = paths.runs / "health-report.json"
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path
