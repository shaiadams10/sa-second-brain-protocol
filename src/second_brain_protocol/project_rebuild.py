from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .collector import (
    collect_projects,
    managed_vault_project,
    reconcile_existing_session_attribution,
)
from .config import RuntimePaths
from .dashboard import build_dashboard
from .project_catalog import project_catalog_health, publish_project_catalog
from .project_forgetting import (
    _clean_project_ids,
    _remove_observation_lines,
    _runtime_artifact_cleanup,
)
from .publisher import write_review_artifacts
from .review import build_review_groups
from .state import StateStore, canonical_hash


PROJECT_DERIVED_EVIDENCE_KINDS = {
    "code_graph_summary",
    "project_delta",
    "project_inventory",
}
PROJECT_MEMORY_KINDS = {"decision", "lesson", "project_fact"}
PROTECTED_VAULT_ROOTS = (
    "Career",
    "Evidence/VoiceSamples",
    "Experience",
    "Goals",
    "Identity",
    "Journal",
    "Skills",
)
GENERATED_PROJECT_SECTION = re.compile(
    r"(?s)(<!-- sb:generated (?P<section>inventory|canonical):start -->).*?"
    r"(<!-- sb:generated (?P=section):end -->)"
)


def _private_markdown(vault: Path) -> list[Path]:
    return sorted(
        path
        for path in vault.rglob("*.md")
        if not any(part in {".git", ".agents", "Protocol"} for part in path.parts)
    )


def _file_hashes(vault: Path, roots: tuple[str, ...]) -> dict[str, str]:
    result: dict[str, str] = {}
    for relative_root in roots:
        root = vault / Path(relative_root)
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            result[path.relative_to(vault).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return result


def _project_note_has_manual_content(text: str) -> bool:
    body = text
    if body.startswith("---\n") and "\n---\n" in body[4:]:
        body = body.split("\n---\n", 1)[1]
    body = GENERATED_PROJECT_SECTION.sub("", body)
    meaningful = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("# ") or stripped == "## Manual notes":
            continue
        meaningful.append(stripped)
    return bool(meaningful)


def _clear_or_remove_project_notes(vault: Path) -> dict[str, int]:
    root = vault / "Projects"
    if not root.exists():
        return {"removed": 0, "manual_preserved": 0}
    removed = 0
    manual_preserved = 0
    for path in sorted(root.glob("*.md")):
        if path.name == "Index.md":
            continue
        text = path.read_text(encoding="utf-8")
        if not _project_note_has_manual_content(text):
            path.unlink()
            removed += 1
            continue

        def replace(match: re.Match[str]) -> str:
            section = match.group("section")
            placeholder = (
                "_Awaiting fresh project scan._"
                if section == "inventory"
                else "_No revalidated project knowledge yet._"
            )
            return f"{match.group(1)}\n{placeholder}\n{match.group(3)}"

        updated = GENERATED_PROJECT_SECTION.sub(replace, text)
        if updated != text:
            path.write_text(updated, encoding="utf-8")
        manual_preserved += 1
    return {"removed": removed, "manual_preserved": manual_preserved}


def _observation_group_key(item: dict[str, Any]) -> str | None:
    groups = build_review_groups([item])
    return str(groups[0]["key"]) if groups else None


def _project_observation_ids(
    observations: list[dict[str, Any]], project_evidence_ids: set[str]
) -> set[str]:
    result: set[str] = set()
    for item in observations:
        kind = str(item.get("kind") or "")
        if kind == "clarification":
            if _observation_group_key(item) != "questions-profile-privacy":
                result.add(str(item["id"]))
            continue
        if kind == "project_fact":
            result.add(str(item["id"]))
            continue
        if kind in {"decision", "lesson"}:
            payload = item.get("payload") or {}
            scope = str(payload.get("scope") or "").casefold()
            project_scoped = scope == "project"
            project_backed = bool(
                project_evidence_ids & set(item.get("evidence_refs") or [])
            )
            legacy_pending_project_knowledge = (
                item.get("status") == "pending" and scope != "global"
            )
            if project_scoped or project_backed or legacy_pending_project_knowledge:
                result.add(str(item["id"]))
    return result


def _backup(
    paths: RuntimePaths,
    vault: Path,
    store: StateStore,
    *,
    manifest: dict[str, Any],
) -> Path:
    operation_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup_root = paths.runs / "backups" / f"project-rebuild-{operation_id}"
    backup_root.mkdir(parents=True, exist_ok=False)
    store.backup(backup_root / "state-before.sqlite")
    if paths.config.exists():
        shutil.copy2(paths.config, backup_root / "runtime-before.json")
    for path in _private_markdown(vault):
        destination = backup_root / "vault" / path.relative_to(vault)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    (backup_root / "manifest.json").write_text(
        json.dumps(
            {**manifest, "operation_id": operation_id},
            indent=2,
            ensure_ascii=False,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    return backup_root


def _restore_backup(
    paths: RuntimePaths,
    vault: Path,
    backup_root: Path,
    original_markdown: set[Path],
) -> None:
    state_backup = backup_root / "state-before.sqlite"
    if state_backup.exists():
        Path(str(paths.state) + "-wal").unlink(missing_ok=True)
        Path(str(paths.state) + "-shm").unlink(missing_ok=True)
        shutil.copy2(state_backup, paths.state)
    config_backup = backup_root / "runtime-before.json"
    if config_backup.exists():
        shutil.copy2(config_backup, paths.config)
    for path in _private_markdown(vault):
        if path in original_markdown:
            continue
        relative = path.relative_to(vault)
        if relative.parts[:1] == ("Projects",) or relative.parts[:2] == (
            "Inbox",
            "Review",
        ):
            path.unlink()
    saved_root = backup_root / "vault"
    for saved in saved_root.rglob("*.md") if saved_root.exists() else []:
        destination = vault / saved.relative_to(saved_root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(saved, destination)


def _resolve_collection_paths(
    projects_root: Path, names_or_paths: list[str]
) -> list[Path]:
    root = projects_root.resolve()
    result: list[Path] = []
    for value in names_or_paths:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve()
        if not resolved.is_relative_to(root) or resolved == root:
            raise RuntimeError(f"Collection is outside the configured project root: {value}")
        if not resolved.is_dir():
            raise RuntimeError(f"Collection directory does not exist: {value}")
        if resolved not in result:
            result.append(resolved)
    return sorted(result, key=lambda item: item.as_posix().casefold())


def rebuild_project_subsystem(
    paths: RuntimePaths,
    vault: Path,
    store: StateStore,
    *,
    config: dict[str, Any],
    defaults: dict[str, Any],
    collection_names: list[str] | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Back up and rebuild only deterministic/derived project state."""

    projects = store.projects()
    project_ids = {str(item["id"]) for item in projects}
    evidence = store.evidence()
    project_evidence = [
        item
        for item in evidence
        if item.get("project_id") in project_ids
        or project_ids
        & {
            str(project_id)
            for project_id in (item.get("payload") or {}).get("project_ids", [])
        }
    ]
    project_evidence_ids = {str(item["id"]) for item in project_evidence}
    observations = store.observations()
    observation_ids = _project_observation_ids(observations, project_evidence_ids)
    patterns = []
    with store.connect() as connection:
        for row in connection.execute("SELECT * FROM pattern_signals").fetchall():
            pattern = dict(row)
            pattern["payload"] = json.loads(pattern.pop("payload_json"))
            pattern["evidence_refs"] = json.loads(pattern.pop("evidence_refs_json"))
            patterns.append(pattern)
    pattern_keys = {
        str(item["pattern_key"])
        for item in patterns
        if item.get("observation_id") in observation_ids
        or str((item.get("payload") or {}).get("scope") or "") == "project"
    }
    derived_evidence_ids = {
        str(item["id"])
        for item in evidence
        if str(item.get("kind") or "") in PROJECT_DERIVED_EVIDENCE_KINDS
    }
    retained_observation_refs = {
        str(evidence_id)
        for item in observations
        if item["id"] not in observation_ids
        for evidence_id in (item.get("evidence_refs") or [])
    }
    retained_pattern_refs = {
        str(evidence_id)
        for item in patterns
        if item["pattern_key"] not in pattern_keys
        for evidence_id in (item.get("evidence_refs") or [])
    }
    redacted_evidence_ids = derived_evidence_ids & (
        retained_observation_refs | retained_pattern_refs
    )
    deleted_evidence_ids = derived_evidence_ids - redacted_evidence_ids
    projects_root = Path(config["projects_root"])
    collection_values = list(
        collection_names
        if collection_names is not None
        else config.get("project_collection_paths", [])
    )
    collection_paths = _resolve_collection_paths(projects_root, collection_values)
    preview = {
        "status": "preview" if not confirm else "confirmed",
        "project_records": len(projects),
        "project_linked_evidence_to_unlink": len(project_evidence_ids),
        "derived_project_evidence_to_delete": len(deleted_evidence_ids),
        "derived_support_stubs_to_preserve": len(redacted_evidence_ids),
        "project_observations_to_remove": len(observation_ids),
        "project_patterns_to_remove": len(pattern_keys),
        "collections": [path.name for path in collection_paths],
        "personal_layers_preserved": list(PROTECTED_VAULT_ROOTS),
        "source_projects_untouched": True,
        "model_called": False,
    }
    if not confirm:
        return preview

    protected_before = _file_hashes(vault, PROTECTED_VAULT_ROOTS)
    backup_root = _backup(paths, vault, store, manifest=preview)
    private_markdown = _private_markdown(vault)
    original_markdown = set(private_markdown)
    evidence_by_id = {str(item["id"]): item for item in evidence}
    try:
        for path in (
            vault / "Memory" / "LongTermMemory.md",
            vault / "Memory" / "Decisions.md",
            vault / "Memory" / "Lessons.md",
        ):
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            updated = _remove_observation_lines(text, observation_ids)
            if updated != text:
                path.write_text(updated, encoding="utf-8")
        note_cleanup = _clear_or_remove_project_notes(vault)

        with store.transaction() as connection:
            for pattern_key in pattern_keys:
                connection.execute(
                    "DELETE FROM pattern_signals WHERE pattern_key=?", (pattern_key,)
                )
            for observation_id in observation_ids:
                connection.execute(
                    "DELETE FROM observations WHERE id=?", (observation_id,)
                )

            for evidence_id in redacted_evidence_ids:
                payload = {
                    "redacted_after_project_rebuild": True,
                    "retained_only_as_support_for_preserved_personal_knowledge": True,
                }
                connection.execute(
                    """UPDATE evidence SET source_type='retained-canonical',
                    source_ref=?,project_id=NULL,kind='redacted_support',payload_json=?,
                    content_hash=?,status='processed' WHERE id=?""",
                    (
                        f"project-rebuild:{evidence_id}",
                        json.dumps(payload, ensure_ascii=False, sort_keys=True),
                        canonical_hash(payload),
                        evidence_id,
                    ),
                )
            for evidence_id in deleted_evidence_ids:
                connection.execute("DELETE FROM evidence WHERE id=?", (evidence_id,))

            retained_project_evidence_ids = project_evidence_ids - derived_evidence_ids
            for evidence_id in retained_project_evidence_ids:
                item = evidence_by_id[evidence_id]
                payload = _clean_project_ids(item.get("payload") or {}, project_ids)
                connection.execute(
                    """UPDATE evidence SET project_id=NULL,payload_json=?,content_hash=?
                    WHERE id=?""",
                    (
                        json.dumps(payload, ensure_ascii=False, sort_keys=True),
                        canonical_hash(payload),
                        evidence_id,
                    ),
                )

            for project_id in project_ids:
                connection.execute(
                    "DELETE FROM project_presence WHERE project_id=?", (project_id,)
                )
                connection.execute("DELETE FROM projects WHERE id=?", (project_id,))

            connection.execute(
                "DELETE FROM collection_receipts WHERE source_key LIKE 'project-%'"
            )
            connection.execute("DELETE FROM checkpoints WHERE source_key LIKE 'project-%'")
        updated_config = dict(config)
        updated_config["project_collection_paths"] = [
            str(path) for path in collection_paths
        ]
        paths.config.write_text(
            json.dumps(updated_config, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        runtime_cleanup_warning = None
        try:
            runtime_artifacts_deleted = _runtime_artifact_cleanup(
                paths,
                target_ids=project_ids,
                target_evidence_ids=set(),
            )
        except Exception as error:  # derived cleanup must not invalidate core reset
            runtime_artifacts_deleted = 0
            runtime_cleanup_warning = str(error)

        project_result = collect_projects(
            store,
            projects_root=projects_root,
            defaults=defaults,
            ignored_paths=[
                Path(item) for item in updated_config.get("ignored_project_paths", [])
            ],
            collection_paths=collection_paths,
            classification_overrides=updated_config.get(
                "project_classification_overrides", {}
            ),
            managed_projects=[
                managed_vault_project(
                    vault,
                    name=vault.name,
                )
            ],
            baseline=True,
        )
        attribution = reconcile_existing_session_attribution(store)
        catalog = publish_project_catalog(vault, project_result["projects"])
        with store.connect() as connection:
            connection.execute(
                """UPDATE evidence SET status='processed'
                WHERE kind IN ('project_inventory','project_delta') AND status='new'"""
            )
        review = write_review_artifacts(vault, store)
        dashboard = build_dashboard(paths, vault)
        health = project_catalog_health(vault, store.present_projects())
        protected_after = _file_hashes(vault, PROTECTED_VAULT_ROOTS)
        if protected_before != protected_after:
            changed = sorted(
                set(protected_before) ^ set(protected_after)
                | {
                    path
                    for path in set(protected_before) & set(protected_after)
                    if protected_before[path] != protected_after[path]
                }
            )
            raise RuntimeError(
                "Protected personal files changed during project rebuild: "
                + ", ".join(changed[:20])
            )
        return {
            **preview,
            "status": "rebuilt",
            "backup": str(backup_root),
            "project_notes": note_cleanup,
            "runtime_artifacts_deleted": runtime_artifacts_deleted,
            "runtime_cleanup_warning": runtime_cleanup_warning,
            "catalog": catalog,
            "catalog_health": health,
            "session_attribution": attribution,
            "review_pending": review["pending"],
            "dashboard": str(dashboard),
            "protected_files_verified": len(protected_after),
        }
    except Exception:
        _restore_backup(paths, vault, backup_root, original_markdown)
        raise
