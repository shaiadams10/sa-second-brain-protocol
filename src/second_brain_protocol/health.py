from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .basic_memory_integration import status as memory_status
from .config import RuntimePaths, load_runtime_config, vault_root
from .model_runner import find_codex_executable
from .project_catalog import project_catalog_health
from .scheduler import task_details
from .state import StateStore


_GOVERNED_CUTOVER_KEY = "daily-weekly-governed-cutover-baseline-v1"


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _governed_cutover_at(store: StateStore) -> datetime | None:
    raw = store.get_meta(_GOVERNED_CUTOVER_KEY)
    try:
        return _parse_datetime(json.loads(raw)["completed_at"]) if raw else None
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def _current_schedule(
    store: StateStore, schedule: dict[str, Any]
) -> dict[str, Any]:
    """Hide a scheduler receipt that belongs to the retired execution era."""

    cutover = _governed_cutover_at(store)
    last_run = _parse_datetime(schedule.get("last_run"))
    if cutover is None or last_run is None or last_run >= cutover:
        return schedule
    return {**schedule, "last_run": None, "last_result": None, "missed_runs": 0}


def _current_run_history(
    store: StateStore, *, limit: int = 10
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Exclude retired Daily/Weekly history after the governed cutover."""

    cutover = _governed_cutover_at(store)

    def current(row: dict[str, Any]) -> bool:
        if cutover is None or row.get("kind") not in {"daily", "weekly"}:
            return True
        started = _parse_datetime(row.get("started_at"))
        return started is not None and started >= cutover

    runs = [row for row in store.runs(limit=30) if current(row)][:limit]
    pipelines = [row for row in store.pipeline_runs(limit=30) if current(row)][:limit]
    return runs, pipelines


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
    recent_runs, recent_pipeline_runs = _current_run_history(store)
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
        "schedule": _current_schedule(store, task_details(config["task_name"])),
        "pending_evidence": store.evidence_count(status="new"),
        "pending_review": len(store.observations("pending")),
        "project_catalog": catalog,
        "session_coverage": store.session_coverage(),
        "recurring_patterns": {
            status: len(store.pattern_signals(status))
            for status in ("tracking", "promoted", "pending", "conflict", "rejected")
        },
        "learning_topics": {
            state: sum(
                item["current_state"] == state for item in store.learning_topics()
            )
            for state in (
                "exploring",
                "developing",
                "demonstrated",
                "applied",
                "verified",
                "mixed",
            )
        },
        "recent_runs": recent_runs,
        "recent_pipeline_runs": recent_pipeline_runs,
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
