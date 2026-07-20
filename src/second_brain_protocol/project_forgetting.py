from __future__ import annotations

import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from .basic_memory_integration import reindex
from .config import RuntimePaths, load_runtime_config
from .dashboard import build_dashboard
from .markdown import slugify
from .publisher import write_bootstrap_review_artifacts, write_review_artifacts
from .state import StateStore, canonical_hash, utc_now


PROJECT_MEMORY_KINDS = {"clarification", "decision", "lesson", "project_fact"}
GENERATED_REVIEW_PATTERNS = ("Review-*.md", "Ledger-*.md")


def _resolve_projects(
    projects: list[dict[str, Any]], identifiers: list[str]
) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []
    for identifier in identifiers:
        folded = identifier.strip().casefold()
        matches = [
            item
            for item in projects
            if str(item.get("id") or "").casefold() == folded
            or str(item.get("name") or item.get("logical_name") or "").casefold()
            == folded
        ]
        if len(matches) != 1:
            raise RuntimeError(
                f"Project lookup returned {len(matches)} exact matches for {identifier!r}."
            )
        if matches[0]["id"] not in {item["id"] for item in resolved}:
            resolved.append(matches[0])
    return resolved


def _mask_protected(text: str, protected_names: list[str]) -> str:
    masked = text
    for name in sorted(protected_names, key=len, reverse=True):
        variants = {
            name,
            slugify(name),
            name.replace(" ", "-"),
            name.replace(" ", "_"),
        }
        for variant in sorted(variants, key=len, reverse=True):
            masked = re.sub(
                re.escape(variant), "[protected-project]", masked, flags=re.IGNORECASE
            )
    return masked


def _mentions_target(
    text: str, *, target_names: list[str], protected_names: list[str]
) -> bool:
    masked = _mask_protected(text, protected_names)
    return any(
        re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", masked, re.IGNORECASE)
        for name in target_names
    )


def _clean_project_ids(value: Any, target_ids: set[str]) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if (
                key in {"project_id", "canonical_project_id"}
                and str(item) in target_ids
            ):
                continue
            if key in {
                "project_ids",
                "project_ids_override",
                "child_project_ids",
            } and isinstance(item, list):
                cleaned[key] = [entry for entry in item if str(entry) not in target_ids]
            else:
                cleaned[key] = _clean_project_ids(item, target_ids)
        return cleaned
    if isinstance(value, list):
        return [_clean_project_ids(item, target_ids) for item in value]
    return value


def _redact_target_strings(
    value: Any, *, target_names: list[str], protected_names: list[str]
) -> Any:
    if isinstance(value, dict):
        return {
            key: _redact_target_strings(
                item, target_names=target_names, protected_names=protected_names
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _redact_target_strings(
                item, target_names=target_names, protected_names=protected_names
            )
            for item in value
        ]
    if isinstance(value, str) and _mentions_target(
        value, target_names=target_names, protected_names=protected_names
    ):
        return "[redacted forgotten-project content]"
    return value


def _observation_text(item: dict[str, Any]) -> str:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    return " ".join(
        str(value)
        for value in (
            item.get("subject"),
            item.get("claim"),
            payload.get("question"),
        )
        if value
    )


def _is_target_observation(
    item: dict[str, Any], *, target_names: list[str], protected_names: list[str]
) -> bool:
    if str(item.get("kind") or "") not in PROJECT_MEMORY_KINDS:
        return False
    text = _observation_text(item)
    if not _mentions_target(
        text, target_names=target_names, protected_names=protected_names
    ):
        return False
    protected_context = any(
        name.casefold() in text.casefold() for name in protected_names
    )
    relationship_only = any(
        term in text.casefold()
        for term in ("relationship", "boundaries", "same project", "separate project")
    )
    return not protected_context or relationship_only


def _remove_observation_lines(text: str, observation_ids: set[str]) -> str:
    current_section: str | None = None
    output: list[str] = []
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        start = re.fullmatch(r"<!-- sb:generated ([a-z0-9-]+):start -->", stripped)
        end = re.fullmatch(r"<!-- sb:generated ([a-z0-9-]+):end -->", stripped)
        if start:
            current_section = start.group(1)
            output.append(line)
            continue
        if end:
            current_section = None
            output.append(line)
            continue
        if current_section and any(
            f"^{observation_id}" in line for observation_id in observation_ids
        ):
            continue
        output.append(line)
    return "".join(output)


def _remove_alias_lines(
    text: str, *, target_names: list[str], protected_names: list[str]
) -> str:
    current_section: str | None = None
    output: list[str] = []
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        start = re.fullmatch(r"<!-- sb:generated ([a-z0-9-]+):start -->", stripped)
        end = re.fullmatch(r"<!-- sb:generated ([a-z0-9-]+):end -->", stripped)
        if start:
            current_section = start.group(1)
            output.append(line)
            continue
        if end:
            current_section = None
            output.append(line)
            continue
        if current_section and _mentions_target(
            line, target_names=target_names, protected_names=protected_names
        ):
            continue
        output.append(line)
    return "".join(output)


def _remove_inventory_blocks(
    text: str, *, target_names: list[str], protected_names: list[str]
) -> str:
    lines = text.splitlines(keepends=True)
    output: list[str] = []
    removed = 0
    index = 0
    while index < len(lines):
        heading = re.fullmatch(r"##\s+(.+?)\s*", lines[index].strip())
        if heading and _mentions_target(
            heading.group(1), target_names=target_names, protected_names=protected_names
        ):
            removed += 1
            index += 1
            while index < len(lines) and not lines[index].startswith("## "):
                index += 1
            continue
        output.append(lines[index])
        index += 1
    updated = "".join(output)
    count = re.search(r"Repositories discovered:\s*(\d+)", updated)
    if count and removed:
        updated = (
            updated[: count.start(1)]
            + str(max(0, int(count.group(1)) - removed))
            + updated[count.end(1) :]
        )
    return updated


def _safe_rmtree(path: Path, root: Path) -> bool:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()) or resolved == root.resolve():
        raise RuntimeError(f"Refusing to remove an unsafe derived path: {resolved}")
    if not resolved.exists():
        return False
    shutil.rmtree(resolved)
    return True


def _runtime_artifact_cleanup(
    paths: RuntimePaths, *, target_ids: set[str], target_evidence_ids: set[str]
) -> int:
    removed = 0
    for graph_root in (paths.graphify, paths.root / "graphify-canary"):
        for project_id in target_ids:
            removed += int(_safe_rmtree(graph_root / project_id, paths.root))
        removed += int(_safe_rmtree(graph_root / "cross-project", paths.root))

    tokens = sorted(target_ids | target_evidence_ids)
    pattern = (
        re.compile("|".join(re.escape(token) for token in tokens)) if tokens else None
    )
    for stage in paths.staging.iterdir() if paths.staging.exists() else []:
        if not stage.is_dir() or pattern is None:
            continue
        matched = False
        for candidate in stage.rglob("*.json"):
            try:
                if pattern.search(
                    candidate.read_text(encoding="utf-8", errors="ignore")
                ):
                    matched = True
                    break
            except OSError:
                continue
        if matched:
            removed += int(_safe_rmtree(stage, paths.root))
    cache_root = paths.runs / "model-cache"
    if cache_root.exists() and pattern is not None:
        for candidate in cache_root.glob("*.json"):
            try:
                matched = pattern.search(
                    candidate.read_text(encoding="utf-8", errors="ignore")
                )
            except OSError:
                continue
            if matched:
                candidate.unlink()
                removed += 1
    return removed


def _review_artifacts(vault: Path) -> list[Path]:
    review_root = vault / "Inbox" / "Review"
    result: list[Path] = []
    for pattern in GENERATED_REVIEW_PATTERNS:
        result.extend(review_root.glob(pattern))
    groups = review_root / "Groups"
    if groups.exists():
        result.extend(groups.glob("*.md"))
    return sorted(set(result))


def _update_ignored_paths(paths: RuntimePaths, project_paths: list[str]) -> None:
    config = load_runtime_config(paths)
    ignored = {
        str(Path(item).resolve())
        for item in config.get("ignored_project_paths", [])
        if item
    }
    ignored.update(str(Path(item).resolve()) for item in project_paths if item)
    config["ignored_project_paths"] = sorted(ignored, key=str.casefold)
    paths.config.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def forget_projects(
    paths: RuntimePaths,
    vault: Path,
    store: StateStore,
    *,
    identifiers: list[str],
    protected_identifiers: list[str] | None = None,
    confirm: bool = False,
    reindexer: Callable[[RuntimePaths, Path], Any] = reindex,
    dashboard_builder: Callable[[RuntimePaths, Path], Any] = build_dashboard,
) -> dict[str, Any]:
    """Forget exact project identities without touching source project directories."""

    projects = store.projects()
    targets = _resolve_projects(projects, identifiers)
    protected = _resolve_projects(projects, protected_identifiers or [])
    target_ids = {str(item["id"]) for item in targets}
    protected_ids = {str(item["id"]) for item in protected}
    if target_ids & protected_ids:
        raise RuntimeError("A protected project cannot also be forgotten")
    target_names = [str(item.get("name") or item["id"]) for item in targets]
    protected_names = [str(item.get("name") or item["id"]) for item in protected]
    all_evidence = store.evidence()
    target_evidence = [
        item for item in all_evidence if item.get("project_id") in target_ids
    ]
    target_evidence_ids = {str(item["id"]) for item in target_evidence}
    shared_evidence = [
        item
        for item in all_evidence
        if item.get("project_id") not in target_ids
        and target_ids
        & {
            str(project_id)
            for project_id in (item.get("payload") or {}).get("project_ids", [])
        }
    ]
    observations = store.observations()
    target_observations = [
        item
        for item in observations
        if (
            (
                target_evidence_ids & set(item.get("evidence_refs") or [])
                or target_ids
                & {
                    str(project_id)
                    for project_id in (item.get("payload") or {}).get("project_ids", [])
                }
            )
            or (
                str(item.get("kind") or "") == "clarification"
                and _mentions_target(
                    _observation_text(item),
                    target_names=target_names,
                    protected_names=protected_names,
                )
            )
        )
        and _is_target_observation(
            item, target_names=target_names, protected_names=protected_names
        )
    ]
    target_observation_ids = {str(item["id"]) for item in target_observations}
    target_notes = {
        vault / "Projects" / f"{slugify(str(item.get('name') or item['id']))}.md"
        for item in targets
    }
    protected_notes = {
        vault / "Projects" / f"{slugify(str(item.get('name') or item['id']))}.md"
        for item in protected
    }
    review_files = _review_artifacts(vault)
    voice_files = {
        vault / "Evidence" / "VoiceSamples" / f"{slugify(evidence_id)}.md"
        for evidence_id in target_evidence_ids
    }
    plan = {
        "status": "preview" if not confirm else "confirmed",
        "projects": [item["name"] for item in targets],
        "protected_projects": [item["name"] for item in protected],
        "project_records": len(targets),
        "project_evidence": len(target_evidence),
        "shared_evidence_to_clean": len(shared_evidence),
        "project_observations": len(target_observations),
        "project_notes": sum(path.exists() for path in target_notes),
        "review_artifacts_to_rebuild": len(review_files),
        "source_paths_to_ignore": [item.get("local_path") for item in targets],
    }
    if not confirm:
        return plan

    operation_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup_root = paths.runs / "backups" / f"project-forget-{operation_id}"
    backup_root.mkdir(parents=True, exist_ok=False)
    store.backup(backup_root / "state-before.sqlite")
    if paths.config.exists():
        shutil.copy2(paths.config, backup_root / "runtime-before.json")

    private_markdown = [
        path
        for path in vault.rglob("*.md")
        if not any(part in {".git", ".agents", "Protocol"} for part in path.parts)
    ]
    affected_files = set(review_files) | {
        path for path in target_notes | voice_files if path.exists()
    }
    for path in private_markdown:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if any(
            f"^{observation_id}" in text for observation_id in target_observation_ids
        ):
            affected_files.add(path)
        relative_parts = path.relative_to(vault).parts
        if relative_parts[:3] == ("System", "Audits", "Bootstrap"):
            affected_files.add(path)
        if relative_parts[0] in {"Journal", "Projects"} and _mentions_target(
            text, target_names=target_names, protected_names=protected_names
        ):
            affected_files.add(path)
    for path in sorted(affected_files):
        if not path.exists() or not path.is_relative_to(vault):
            continue
        destination = backup_root / "vault" / path.relative_to(vault)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    (backup_root / "manifest.json").write_text(
        json.dumps({**plan, "operation_id": operation_id}, indent=2, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )

    core_committed = False
    try:
        for path in review_files:
            if path.exists():
                path.unlink()
        for path in target_notes | voice_files:
            if path.exists():
                path.unlink()
        for path in private_markdown:
            if not path.exists() or path in protected_notes:
                continue
            text = path.read_text(encoding="utf-8")
            updated = _remove_observation_lines(text, target_observation_ids)
            if (
                path
                == vault / "System" / "Audits" / "Bootstrap" / "ProjectInventory.md"
            ):
                updated = _remove_inventory_blocks(
                    updated,
                    target_names=target_names,
                    protected_names=protected_names,
                )
            if path.relative_to(vault).parts[0] in {"Journal", "Projects"}:
                updated = _remove_alias_lines(
                    updated,
                    target_names=target_names,
                    protected_names=protected_names,
                )
            if updated != text:
                path.write_text(updated, encoding="utf-8")

        remaining_text = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for path in private_markdown
            if path.exists()
        )
        redacted_evidence_ids = {
            evidence_id
            for evidence_id in target_evidence_ids
            if evidence_id in remaining_text
        }
        for item in observations:
            if item["id"] in target_observation_ids:
                continue
            redacted_evidence_ids.update(
                target_evidence_ids & set(item.get("evidence_refs") or [])
            )
        patterns = []
        with store.connect() as connection:
            for row in connection.execute("SELECT * FROM pattern_signals").fetchall():
                pattern = dict(row)
                pattern["evidence_refs"] = json.loads(pattern.pop("evidence_refs_json"))
                pattern["payload"] = json.loads(pattern.pop("payload_json"))
                patterns.append(pattern)
        target_pattern_keys = {
            str(item["pattern_key"])
            for item in patterns
            if item.get("observation_id") in target_observation_ids
            or (
                _mentions_target(
                    f"{item.get('label', '')} {item.get('claim', '')}",
                    target_names=target_names,
                    protected_names=protected_names,
                )
                and str((item.get("payload") or {}).get("scope") or "") == "project"
            )
        }
        for item in patterns:
            if item["pattern_key"] not in target_pattern_keys:
                redacted_evidence_ids.update(
                    target_evidence_ids & set(item.get("evidence_refs") or [])
                )

        _update_ignored_paths(
            paths,
            [str(item.get("local_path") or "") for item in targets],
        )
        evidence_by_id = {str(item["id"]): item for item in all_evidence}
        with store.transaction() as connection:
            for item in observations:
                if item["id"] in target_observation_ids:
                    continue
                item_refs = set(item.get("evidence_refs") or [])
                item_payload = item.get("payload") or {}
                item_payload_ids = {
                    str(project_id)
                    for project_id in (
                        list(item_payload.get("project_ids", []))
                        + list(item_payload.get("project_ids_override", []))
                    )
                    if project_id
                }
                if not (
                    item_refs & target_evidence_ids or item_payload_ids & target_ids
                ):
                    continue
                payload = _clean_project_ids(item.get("payload") or {}, target_ids)
                observed_target_ids = set(item_payload_ids & target_ids)
                for evidence_id in item_refs:
                    evidence_item = evidence_by_id.get(evidence_id)
                    if not evidence_item:
                        continue
                    if evidence_item.get("project_id") in target_ids:
                        observed_target_ids.add(str(evidence_item["project_id"]))
                    observed_target_ids.update(
                        target_ids
                        & {
                            str(project_id)
                            for project_id in (evidence_item.get("payload") or {}).get(
                                "project_ids", []
                            )
                        }
                    )
                connection.execute(
                    "UPDATE observations SET project_count=?,payload_json=?,updated_at=? WHERE id=?",
                    (
                        max(
                            0,
                            int(item.get("project_count") or 0)
                            - len(observed_target_ids),
                        ),
                        json.dumps(payload, ensure_ascii=False, sort_keys=True),
                        utc_now(),
                        item["id"],
                    ),
                )
            for observation_id in target_observation_ids:
                connection.execute(
                    "DELETE FROM observations WHERE id=?", (observation_id,)
                )
            for item in patterns:
                if item["pattern_key"] in target_pattern_keys:
                    connection.execute(
                        "DELETE FROM pattern_signals WHERE pattern_key=?",
                        (item["pattern_key"],),
                    )
                    continue
                item_refs = set(item.get("evidence_refs") or [])
                payload_ids = {
                    str(project_id)
                    for project_id in (item.get("payload") or {}).get("project_ids", [])
                }
                if not (item_refs & target_evidence_ids or payload_ids & target_ids):
                    continue
                payload = _clean_project_ids(item.get("payload") or {}, target_ids)
                observed_target_ids = set(payload_ids & target_ids)
                for evidence_id in item_refs:
                    evidence_item = evidence_by_id.get(evidence_id)
                    if not evidence_item:
                        continue
                    if evidence_item.get("project_id") in target_ids:
                        observed_target_ids.add(str(evidence_item["project_id"]))
                    observed_target_ids.update(
                        target_ids
                        & {
                            str(project_id)
                            for project_id in (evidence_item.get("payload") or {}).get(
                                "project_ids", []
                            )
                        }
                    )
                connection.execute(
                    "UPDATE pattern_signals SET project_count=?,payload_json=?,last_seen=? WHERE pattern_key=?",
                    (
                        max(
                            0,
                            int(item.get("project_count") or 0)
                            - len(observed_target_ids),
                        ),
                        json.dumps(payload, ensure_ascii=False, sort_keys=True),
                        utc_now(),
                        item["pattern_key"],
                    ),
                )
            for item in shared_evidence:
                payload = _redact_target_strings(
                    _clean_project_ids(item.get("payload") or {}, target_ids),
                    target_names=target_names,
                    protected_names=protected_names,
                )
                connection.execute(
                    "UPDATE evidence SET payload_json=?,content_hash=? WHERE id=?",
                    (
                        json.dumps(payload, ensure_ascii=False, sort_keys=True),
                        canonical_hash(payload),
                        item["id"],
                    ),
                )
            for item in target_evidence:
                if item["id"] not in redacted_evidence_ids:
                    continue
                payload = {
                    "redacted_after_project_forget": True,
                    "retained_only_as_support_for_unrelated_canonical_knowledge": True,
                }
                connection.execute(
                    """UPDATE evidence SET source_type='retained-canonical',source_ref=?,project_id=NULL,
                    kind='redacted_support',payload_json=?,content_hash=?,status='processed' WHERE id=?""",
                    (
                        f"project-forget:{operation_id}:{item['id']}",
                        json.dumps(payload, ensure_ascii=False, sort_keys=True),
                        canonical_hash(payload),
                        item["id"],
                    ),
                )
            delete_evidence_ids = target_evidence_ids - redacted_evidence_ids
            for evidence_id in delete_evidence_ids:
                connection.execute("DELETE FROM evidence WHERE id=?", (evidence_id,))
            for project_id in target_ids:
                connection.execute(
                    "DELETE FROM project_presence WHERE project_id=?", (project_id,)
                )
                connection.execute("DELETE FROM projects WHERE id=?", (project_id,))
            path_tokens = [
                str(item.get("local_path") or "").casefold()
                for item in targets
                if item.get("local_path")
            ]
            for table in ("checkpoints", "collection_receipts"):
                key_column = "source_key"
                for row in connection.execute(
                    f"SELECT {key_column} FROM {table}"
                ).fetchall():
                    key = str(row[key_column])
                    folded = key.casefold()
                    if any(project_id in key for project_id in target_ids) or any(
                        token and token in folded for token in path_tokens
                    ):
                        connection.execute(
                            f"DELETE FROM {table} WHERE {key_column}=?", (key,)
                        )
        core_committed = True
    except Exception:
        if not core_committed:
            for saved in (backup_root / "vault").rglob("*"):
                if saved.is_file():
                    destination = vault / saved.relative_to(backup_root / "vault")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(saved, destination)
            if (backup_root / "runtime-before.json").exists():
                shutil.copy2(backup_root / "runtime-before.json", paths.config)
        raise

    warnings: list[str] = []
    removed_runtime_artifacts = 0
    try:
        removed_runtime_artifacts = _runtime_artifact_cleanup(
            paths,
            target_ids=target_ids,
            target_evidence_ids=target_evidence_ids,
        )
    except Exception as error:
        warnings.append(f"derived runtime cleanup: {error}")
    try:
        if store.bootstrap_state().get("state") == "awaiting_review":
            write_bootstrap_review_artifacts(vault, store)
        else:
            write_review_artifacts(vault, store)
    except Exception as error:
        warnings.append(f"review artifact refresh: {error}")
    try:
        reindexer(paths, vault)
    except Exception as error:
        warnings.append(f"search reindex: {error}")
    try:
        dashboard_builder(paths, vault)
    except Exception as error:
        warnings.append(f"dashboard rebuild: {error}")

    return {
        **plan,
        "status": "forgotten",
        "backup": str(backup_root),
        "evidence_deleted": len(target_evidence_ids - redacted_evidence_ids),
        "evidence_redacted": len(redacted_evidence_ids),
        "shared_evidence_cleaned": len(shared_evidence),
        "observations_deleted": len(target_observation_ids),
        "patterns_deleted": len(target_pattern_keys),
        "runtime_artifacts_deleted": removed_runtime_artifacts,
        "source_projects_untouched": True,
        "warnings": warnings,
    }
