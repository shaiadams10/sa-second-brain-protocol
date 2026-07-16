from __future__ import annotations

import json
import hashlib
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from .basic_memory_integration import reindex
from .collector import collect_projects, collect_sessions, reconcile_existing_session_attribution
from .config import RuntimePaths, load_defaults, load_runtime_config, protocol_root, setup_runtime, vault_root
from .evidence_compaction import (
    SESSION_EVENT_KINDS,
    SESSION_SOURCE_TYPES,
    compact_session_evidence,
)
from .gitops import (
    commit_if_changed,
    ensure_private_remote,
    publish_protocol_draft,
    safe_push_private,
    snapshot_manual_markdown,
)
from .graphify_integration import build_cross_project_graph, graph_summary, update_project_graph
from .locking import single_instance
from .model_runner import ModelRole, canary, run_model, select_evidence_for_packet
from .notifications import notify
from .profile import create_interview, import_linkedin_export, interview_status
from .question_followup import pending_question_context
from .publisher import (
    promote_approved_observation,
    promote_observation_group,
    publish_interview_profile,
    publish_model_output,
    repair_generated_markdown,
    write_bootstrap_review_artifacts,
    write_review_artifacts,
)
from .review import find_review_group
from .scheduler import install_task, run_canary
from .health import report as health_report
from .source_integrity import capture as capture_integrity, compare as compare_integrity
from .state import StateStore, canonical_hash, utc_now


def context() -> tuple[RuntimePaths, dict[str, Any], dict[str, Any], StateStore]:
    paths = setup_runtime()
    config = load_runtime_config(paths)
    defaults = load_defaults()
    store = StateStore(paths.state)
    store.backup(paths.runs / "backups" / f"state-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}.sqlite")
    return paths, config, defaults, store


def _refresh_review_artifacts(store: StateStore) -> None:
    if store.bootstrap_state()["state"] == "awaiting_review":
        write_bootstrap_review_artifacts(vault_root(), store)
    else:
        write_review_artifacts(vault_root(), store)


def _inventory_report(vault: Path, projects: list[dict[str, Any]]) -> Path:
    path = vault / "System" / "Audits" / "Bootstrap" / "ProjectInventory.md"
    lines = [
        "---",
        "id: bootstrap-project-inventory",
        "type: audit-report",
        f"generated: {utc_now()}",
        "---",
        "",
        "# Project inventory",
        "",
        f"Repositories discovered: {len(projects)}",
        "",
    ]
    for project in sorted(projects, key=lambda item: (item["name"].casefold(), item["id"])):
        lines.extend(
            [
                f"## {project['name']}",
                "",
                f"- Stable ID: {project['id']}",
                f"- Classification: {project['classification']}",
                f"- Basis: {'; '.join(project['classification_reasons'])}",
                f"- Technology: {', '.join(project['tech_stack']) or 'not detected'}",
                f"- Tracked files: {project['tracked_file_count']}",
                f"- Existing second-brain files: {len(project['second_brain_files'])}",
                f"- Remote: {project.get('remote_url') or 'none'}",
                "",
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _methodology_report(
    vault: Path, store: StateStore, counts: dict[str, int], graph_count: int
) -> Path:
    path = vault / "System" / "Audits" / "Bootstrap" / "MethodologyAndProvenance.md"
    totals = store.evidence_counts_by_source()
    path.write_text(
        "---\nid: bootstrap-methodology\ntype: audit-report\n---\n\n"
        "# Bootstrap methodology and provenance\n\n"
        "Project repositories and agent histories were read without executing project code, installing project dependencies, or writing to source repositories. "
        "Visible messages, final outputs, metadata, Git facts, project documentation, and deterministic code graphs are evidence; hidden reasoning and raw tool dumps are excluded.\n\n"
        f"- Total Codex evidence: {totals.get('codex', 0)}\n"
        f"- Total Antigravity evidence and artifact records: {totals.get('antigravity', 0)}\n"
        f"- Total LinkedIn source records: {totals.get('linkedin', 0)}\n"
        f"- Total Graphify summaries: {totals.get('graphify', 0)}\n"
        f"- Latest pass new Codex evidence: {counts['codex']}\n"
        f"- Latest pass new Antigravity evidence: {counts['antigravity']}\n"
        f"- Latest pass new Antigravity artifacts: {counts['artifacts']}\n"
        f"- Latest pass conversation database inventories: {counts['databases']}\n"
        f"- Latest pass first-party graphs updated: {graph_count}\n",
        encoding="utf-8",
    )
    return path


def _update_graphs(
    store: StateStore,
    paths: RuntimePaths,
    projects: list[dict[str, Any]],
    changed_ids: list[str],
    *,
    strict: bool,
    only_project_ids: set[str] | None = None,
) -> list[str]:
    updated: list[str] = []
    errors: list[str] = []
    for project in projects:
        if only_project_ids is not None and project["id"] not in only_project_ids:
            continue
        graph_changed = store.get_meta(f"graph-fingerprint:{project['id']}") != project["fingerprint"]
        if project["classification"] != "first-party" or not graph_changed:
            continue
        try:
            graph = update_project_graph(project, paths.graphify)
            summary = graph_summary(graph)
            store.add_evidence(
                source_type="graphify",
                source_ref=f"graphify:{project['id']}:{project['fingerprint']}",
                kind="code_graph_summary",
                payload={"project_id": project["id"], "project_name": project["name"], **summary},
                project_id=project["id"],
            )
            store.set_meta(f"graph-fingerprint:{project['id']}", project["fingerprint"])
            updated.append(project["id"])
        except Exception as error:
            errors.append(f"{project['name']}: {error}")
    cross_fingerprint = hashlib.sha256(
        json.dumps(
            sorted(
                (item["id"], item["fingerprint"])
                for item in projects
                if item["classification"] == "first-party"
            )
        ).encode()
    ).hexdigest()
    if store.get_meta("graph-fingerprint:cross-project") != cross_fingerprint:
        cross = build_cross_project_graph(projects, paths.graphify)
        store.add_evidence(
            source_type="graphify",
            source_ref="graphify:cross-project:" + cross_fingerprint,
            kind="cross_project_graph_summary",
            payload=graph_summary(cross),
        )
        store.set_meta("graph-fingerprint:cross-project", cross_fingerprint)
    if strict and errors:
        raise RuntimeError("Graphify failed for first-party repositories: " + " | ".join(errors))
    return updated


def bootstrap(*, linkedin_export: Path | None = None) -> dict[str, Any]:
    paths, config, defaults, store = context()
    state = store.bootstrap_state()
    if state["state"] == "completed":
        raise RuntimeError("Bootstrap is completed and cannot rerun. Use refresh-project or interview.")
    if state["state"] == "awaiting_review":
        return {
            "state": "awaiting_review",
            "review_path": state.get("review_path"),
            "next": "Review the final packet, then run `sb bootstrap --approve` after explicit approval.",
        }
    if state["state"] == "synthesis_ready":
        with single_instance(paths.locks / "pipeline.lock"):
            return _bootstrap_synthesis_step(store=store, paths=paths, defaults=defaults)
    with single_instance(paths.locks / "pipeline.lock"):
        if state["state"] == "not_started":
            store.set_bootstrap_state("collecting")
        integrity_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        integrity_before = paths.runs / f"bootstrap-{integrity_id}-projects-before.json.gz"
        integrity_after = paths.runs / f"bootstrap-{integrity_id}-projects-after.json.gz"
        capture_integrity(Path(config["projects_root"]), integrity_before)
        interview_note = create_interview(vault_root(), paths.root)
        if linkedin_export:
            import_linkedin_export(store, linkedin_export.resolve())
        project_result = collect_projects(
            store,
            projects_root=Path(config["projects_root"]),
            defaults=defaults,
            ignored_paths=[Path(item) for item in config.get("ignored_project_paths", [])],
            classification_overrides=config.get("project_classification_overrides", {}),
        )
        _inventory_report(vault_root(), project_result["projects"])
        counts = collect_sessions(store, config)
        reconcile_existing_session_attribution(store)
        graph_error: Exception | None = None
        try:
            updated = _update_graphs(
                store,
                paths,
                project_result["projects"],
                project_result["changed_project_ids"],
                strict=True,
            )
        except Exception as error:
            graph_error = error
            updated = []
        finally:
            capture_integrity(Path(config["projects_root"]), integrity_after)
        integrity = compare_integrity(integrity_before, integrity_after)
        integrity_note = vault_root() / "System" / "Audits" / "Bootstrap" / "SourceIntegrity.md"
        integrity_note.write_text(
            "---\nid: bootstrap-source-integrity\ntype: audit-report\n---\n\n"
            "# Source integrity\n\n"
            f"Unchanged during scanner and graph collection: {str(integrity.unchanged).lower()}\n\n"
            f"Added: {len(integrity.added)}; removed: {len(integrity.removed)}; modified: {len(integrity.modified)}; Git-status changes: {len(integrity.git_status_changed)}.\n\n"
            "Detailed before/after manifests remain in the local runtime and are never committed.\n",
            encoding="utf-8",
        )
        if not integrity.unchanged:
            receipt = paths.runs / f"bootstrap-{integrity_id}-source-integrity-failure.json"
            receipt.write_text(
                json.dumps(
                    {
                        "added": integrity.added,
                        "removed": integrity.removed,
                        "modified": integrity.modified,
                        "git_status_changed": integrity.git_status_changed,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            raise RuntimeError(f"Configured project sources changed during audit; review {receipt} and resume.")
        if graph_error:
            raise graph_error
        _methodology_report(vault_root(), store, counts, len(updated))
        profile = interview_status(paths.root)
        if not profile["complete"] or store.get_meta("linkedin_imported") != "true":
            return {
                "state": "collecting",
                "projects": len(project_result["projects"]),
                "graphs": len(updated),
                "interview": profile,
                "linkedin_imported": store.get_meta("linkedin_imported") == "true",
                "next": str(interview_note),
            }
        store.set_bootstrap_state("synthesis_ready")
        return _bootstrap_synthesis_step(store=store, paths=paths, defaults=defaults)


def refresh_bootstrap_evidence() -> dict[str, Any]:
    """Collect protocol/parser backfills before final approval without restarting bootstrap."""
    paths, config, defaults, store = context()
    if store.bootstrap_state()["state"] != "awaiting_review":
        raise RuntimeError(
            "Bootstrap evidence refresh is available only while awaiting final review."
        )
    with single_instance(paths.locks / "pipeline.lock"):
        snapshot_manual_markdown(vault_root())
        integrity_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        integrity_before = paths.runs / f"bootstrap-refresh-{integrity_id}-before.json.gz"
        integrity_after = paths.runs / f"bootstrap-refresh-{integrity_id}-after.json.gz"
        capture_integrity(Path(config["projects_root"]), integrity_before)
        project_result = collect_projects(
            store,
            projects_root=Path(config["projects_root"]),
            defaults=defaults,
            ignored_paths=[Path(item) for item in config.get("ignored_project_paths", [])],
            classification_overrides=config.get("project_classification_overrides", {}),
        )
        session_counts = collect_sessions(store, config)
        attribution = reconcile_existing_session_attribution(store)
        graph_error: Exception | None = None
        try:
            graph_updates = _update_graphs(
                store,
                paths,
                project_result["projects"],
                project_result["changed_project_ids"],
                strict=True,
            )
        except Exception as error:
            graph_error = error
            graph_updates = []
        finally:
            capture_integrity(Path(config["projects_root"]), integrity_after)
        integrity = compare_integrity(integrity_before, integrity_after)
        if not integrity.unchanged:
            raise RuntimeError(
                "Bootstrap evidence refresh changed a source project; review local integrity receipts."
            )
        if graph_error:
            raise graph_error
        store.set_bootstrap_state("synthesis_ready")
        synthesis = _bootstrap_synthesis_step(
            store=store, paths=paths, defaults=defaults
        )
        return {
            "refresh": "completed",
            "project_deltas": project_result.get("project_deltas", 0),
            "session_counts": session_counts,
            "attribution": attribution,
            "graph_updates": graph_updates,
            "source_integrity_unchanged": True,
            **synthesis,
        }


def _role(defaults: dict[str, Any], kind: str) -> ModelRole:
    values = defaults["models"][kind]
    return ModelRole(name=values["name"], reasoning=values["reasoning"])


def _synthesize(
    kind: str,
    *,
    store: StateStore,
    paths: RuntimePaths,
    defaults: dict[str, Any],
    evidence_override: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if evidence_override is None:
        compact_session_evidence(store)
    if evidence_override is not None:
        evidence = evidence_override
    elif kind == "weekly":
        since = (datetime.now(UTC) - timedelta(days=7)).isoformat()
        evidence = [
            item
            for item in store.evidence_since(since)
            if item.get("status") not in {"compacted", "superseded"}
            and not (
                item["source_type"] in SESSION_SOURCE_TYPES
                and item["kind"] in SESSION_EVENT_KINDS
            )
        ]
    else:
        evidence = store.evidence(status="new")
    pending_questions = (
        pending_question_context(store.observations("pending"))
        if kind == "weekly"
        else []
    )
    if not evidence:
        return {"status": "empty", "evidence_count": 0, "model_called": False}
    weekly_fingerprint = None
    if kind == "weekly":
        weekly_fingerprint = canonical_hash(
            {
                "evidence": [item["id"] for item in evidence],
                "questions": [item["id"] for item in pending_questions],
            }
        )
        if store.get_meta("weekly-evidence-fingerprint") == weekly_fingerprint:
            return {"status": "empty", "evidence_count": 0, "model_called": False, "reason": "weekly evidence unchanged"}
    max_calls = int(defaults["limits"][f"max_model_calls_{kind}"])
    remaining = list(evidence)
    processed: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []
    run_ids: list[str] = []
    for _batch_number in range(max_calls):
        selected = select_evidence_for_packet(
            remaining,
            max_chars=int(defaults["limits"]["max_packet_chars"]),
            max_item_chars=int(defaults["limits"]["max_evidence_text_chars"]),
            pending_questions=pending_questions,
        )
        if not selected:
            break
        run_id = store.start_run(kind, _role(defaults, kind).name, _role(defaults, kind).reasoning)
        run_ids.append(run_id)
        try:
            output, receipt = run_model(
                paths=paths,
                role=_role(defaults, kind),
                prompt_name=f"{kind}.md",
                evidence=selected,
                run_id=run_id,
                max_packet_chars=int(defaults["limits"]["max_packet_chars"]),
                max_evidence_chars=int(defaults["limits"]["max_evidence_text_chars"]),
                pending_questions=pending_questions,
            )
            packet_ids = {item["id"] for item in selected}
            used_ids = set()
            for collection in (
                output["observations"],
                output.get("pattern_signals", []),
                output["project_updates"],
                output["skill_updates"],
            ):
                for item in collection:
                    used_ids.update(item.get("evidence_refs", []))
            used_ids.update(item["evidence_ref"] for item in output["voice_samples"])
            for item in output.get("review_items", []):
                used_ids.update(item.get("evidence_refs", []))
            for item in output.get("question_resolutions", []):
                used_ids.update(item.get("evidence_refs", []))
            unknown = used_ids - packet_ids
            if unknown:
                raise RuntimeError(f"Model output referenced unknown evidence: {sorted(unknown)}")
            outputs.append(output)
            processed.extend(selected)
            selected_ids = {item["id"] for item in selected}
            remaining = [item for item in remaining if item["id"] not in selected_ids]
            store.finish_run(
                run_id,
                "validated",
                evidence_count=len(selected),
                receipt_path=str(receipt),
            )
        except Exception as error:
            store.finish_run(run_id, "failed", evidence_count=len(selected), error=str(error))
            raise
    if not outputs:
        return {"status": "empty", "evidence_count": 0, "model_called": False}
    project_updates: dict[str, dict[str, Any]] = {}
    skill_updates: dict[str, dict[str, Any]] = {}
    pattern_signals: dict[str, dict[str, Any]] = {}
    question_resolutions: dict[str, dict[str, Any]] = {}
    for output in outputs:
        for item in output.get("question_resolutions", []):
            existing = question_resolutions.get(item["question_id"])
            if not existing or item["confidence"] > existing["confidence"]:
                question_resolutions[item["question_id"]] = dict(item)
            elif existing:
                existing["evidence_refs"] = sorted(
                    set(existing["evidence_refs"] + item["evidence_refs"])
                )
                existing["authoritative"] = (
                    existing["authoritative"] or item["authoritative"]
                )
        for item in output.get("pattern_signals", []):
            existing = pattern_signals.get(item["pattern_key"])
            if not existing:
                pattern_signals[item["pattern_key"]] = dict(item)
            else:
                existing["evidence_refs"] = sorted(
                    set(existing["evidence_refs"] + item["evidence_refs"])
                )
                if item["confidence"] > existing["confidence"]:
                    existing["label"] = item["label"]
                    existing["claim"] = item["claim"]
                    existing["confidence"] = item["confidence"]
                existing["explicit"] = existing["explicit"] or item["explicit"]
        for item in output["project_updates"]:
            existing = project_updates.get(item["project_id"])
            if not existing:
                project_updates[item["project_id"]] = dict(item)
            else:
                if item["summary"] not in existing["summary"]:
                    existing["summary"] += "\n\n" + item["summary"]
                existing["evidence_refs"] = sorted(set(existing["evidence_refs"] + item["evidence_refs"]))
        for item in output["skill_updates"]:
            existing = skill_updates.get(item["skill_id"])
            if not existing:
                skill_updates[item["skill_id"]] = dict(item)
            else:
                if item["claim"] not in existing["claim"]:
                    existing["claim"] += "\n\n" + item["claim"]
                existing["evidence_refs"] = sorted(set(existing["evidence_refs"] + item["evidence_refs"]))
                existing["confidence"] = max(existing["confidence"], item["confidence"])
                existing["authorship_confirmed"] = existing["authorship_confirmed"] and item["authorship_confirmed"]
                existing["successful_implementation"] = existing["successful_implementation"] and item["successful_implementation"]
    voice_samples = []
    seen_voice = set()
    for output in outputs:
        for item in output["voice_samples"]:
            key = (item["evidence_ref"], item["excerpt"])
            if key not in seen_voice:
                voice_samples.append(item)
                seen_voice.add(key)
    merged = {
        "summary": "\n\n".join(output["summary"] for output in outputs),
        "observations": [item for output in outputs for item in output["observations"]],
        "pattern_signals": list(pattern_signals.values()),
        "project_updates": list(project_updates.values()),
        "skill_updates": list(skill_updates.values()),
        "voice_samples": voice_samples,
        "review_items": [item for output in outputs for item in output.get("review_items", [])],
        "question_resolutions": list(question_resolutions.values()),
    }
    if kind == "bootstrap" and not any(
        merged[name]
        for name in (
            "observations",
            "pattern_signals",
            "project_updates",
            "skill_updates",
            "voice_samples",
            "review_items",
            "question_resolutions",
        )
    ):
        raise RuntimeError(
            "Bootstrap synthesis contained no evidence-backed structured output; "
            "check the model receipt and retry without advancing checkpoints."
        )
    processed_ids = [item["id"] for item in processed]
    published = publish_model_output(
        vault=vault_root(),
        store=store,
        output=merged,
        run_kind=kind,
        evidence_ids=processed_ids,
        question_ids=[item["id"] for item in pending_questions],
    )
    for run_id in run_ids:
        store.complete_validated_run(run_id)
    if weekly_fingerprint:
        store.set_meta("weekly-evidence-fingerprint", weekly_fingerprint)
    return {
        "status": "completed",
        "run_ids": run_ids,
        "evidence_count": len(processed),
        "remaining_evidence": len(remaining),
        **published,
    }


def _bootstrap_synthesis_step(
    *, store: StateStore, paths: RuntimePaths, defaults: dict[str, Any]
) -> dict[str, Any]:
    snapshot_manual_markdown(vault_root())
    result = _synthesize("bootstrap", store=store, paths=paths, defaults=defaults)
    remaining = store.evidence_count(status="new")
    if remaining:
        return {
            **result,
            "state": "synthesis_ready",
            "remaining_evidence": remaining,
            "next": "Run `sb bootstrap` again to continue the bounded synthesis.",
        }
    profile_result = publish_interview_profile(vault_root(), store, paths.root)
    repaired_files = repair_generated_markdown(vault_root())
    review_artifacts = write_bootstrap_review_artifacts(vault_root(), store)
    review_path = review_artifacts.get("review_path") or str(vault_root() / "Inbox" / "Review")
    store.set_bootstrap_state("awaiting_review", review_path=review_path)
    return {
        **result,
        **profile_result,
        **review_artifacts,
        "repaired_files": repaired_files,
        "state": "awaiting_review",
    }


def incremental(kind: str) -> dict[str, Any]:
    paths, config, defaults, store = context()
    if store.bootstrap_state()["state"] != "completed":
        raise RuntimeError("Complete and approve bootstrap before incremental runs.")
    with single_instance(paths.locks / "pipeline.lock"):
        snapshot_manual_markdown(vault_root())
        project_result = collect_projects(
            store,
            projects_root=Path(config["projects_root"]),
            defaults=defaults,
            ignored_paths=[Path(item) for item in config.get("ignored_project_paths", [])],
            classification_overrides=config.get("project_classification_overrides", {}),
        )
        collect_sessions(store, config)
        _update_graphs(store, paths, project_result["projects"], project_result["changed_project_ids"], strict=False)
        result = _synthesize(kind, store=store, paths=paths, defaults=defaults)
        if result["status"] == "empty":
            notify("Second brain", f"{kind.title()} run: no new evidence", vault=vault_root())
            return result
        reindex(paths, vault_root())
        commit_if_changed(vault_root(), f"Second brain {kind} update {date.today().isoformat()}")
        safe_push_private(vault_root())
        review = Path(result["review_path"]) if result.get("review_path") else None
        notify(
            "Second brain updated",
            f"{result.get('promoted', 0)} promoted, {result.get('pending', 0)} need review",
            vault=vault_root(),
            note=review,
        )
        return result


def refresh_project(identifier: str) -> dict[str, Any]:
    paths, config, defaults, store = context()
    if store.bootstrap_state()["state"] != "completed":
        raise RuntimeError("Complete and approve bootstrap before project refreshes.")
    with single_instance(paths.locks / "pipeline.lock"):
        snapshot_manual_markdown(vault_root())
        project_result = collect_projects(
            store,
            projects_root=Path(config["projects_root"]),
            defaults=defaults,
            ignored_paths=[Path(item) for item in config.get("ignored_project_paths", [])],
            classification_overrides=config.get("project_classification_overrides", {}),
        )
        matches = [
            item
            for item in project_result["projects"]
            if item["id"].casefold() == identifier.casefold() or item["name"].casefold() == identifier.casefold()
        ]
        if len(matches) != 1:
            raise RuntimeError(f"Project lookup returned {len(matches)} matches for {identifier!r}.")
        project = matches[0]
        collect_sessions(store, config)
        _update_graphs(
            store,
            paths,
            project_result["projects"],
            [project["id"]],
            strict=project["classification"] == "first-party",
            only_project_ids={project["id"]},
        )
        evidence = [item for item in store.evidence(status="new") if item.get("project_id") == project["id"]]
        if not evidence:
            return {"status": "empty", "project_id": project["id"], "model_called": False}
        result = _synthesize("daily", store=store, paths=paths, defaults=defaults, evidence_override=evidence)
        reindex(paths, vault_root())
        commit_if_changed(vault_root(), f"Refresh second-brain project {project['name']}")
        safe_push_private(vault_root())
        return {"project_id": project["id"], "project": project["name"], **result}


def scheduled() -> dict[str, Any]:
    try:
        daily_result = incremental("daily")
        result = {"daily": daily_result}
        if datetime.now().strftime("%A") == "Saturday":
            result["weekly"] = incremental("weekly")
        return result
    except Exception as error:
        notify(
            "Second brain run failed",
            f"Evidence was preserved for retry. {str(error)[:180]}",
            vault=vault_root(),
            note=vault_root() / "System" / "Health.md" if (vault_root() / "System" / "Health.md").exists() else None,
        )
        raise


def approve_bootstrap() -> dict[str, Any]:
    paths, config, defaults, store = context()
    if store.bootstrap_state()["state"] != "awaiting_review":
        raise RuntimeError("Bootstrap is not awaiting review.")
    model_results = canary(paths, defaults["models"])
    if any(value != "ok" for value in model_results.values()):
        raise RuntimeError(f"Model canary failed without fallback: {model_results}")
    tests = subprocess.run(
        ["uv", "run", "--project", str(protocol_root()), "pytest"],
        cwd=vault_root(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
        check=False,
    )
    if tests.returncode != 0:
        raise RuntimeError("Protocol tests failed: " + (tests.stdout + tests.stderr)[-4000:])
    reindex(paths, vault_root())
    marker = vault_root() / "System" / "Audits" / "Bootstrap" / "COMPLETED.md"
    marker.write_text(
        f"---\nid: bootstrap-completed\ntype: completion-marker\napproved_at: {utc_now()}\n---\n\n"
        "# Bootstrap completed\n\nThis bootstrap is one-time and must not be rerun.\n",
        encoding="utf-8",
    )
    commit_if_changed(vault_root(), "Complete approved second-brain bootstrap")
    ensure_private_remote(vault_root(), paths, config["private_repository"])
    safe_push_private(vault_root())
    pr_url = publish_protocol_draft(vault_root(), paths, config["public_repository"])
    scheduled_script = protocol_root() / "scripts" / "scheduled-run.ps1"
    install_task(
        task_name=config["task_name"],
        script_path=scheduled_script,
    )
    task_canary = run_canary(scheduled_script)
    health = health_report(paths)
    required = {
        "vault_exists": health["vault_exists"],
        "runtime_exists": health["runtime_exists"],
        "projects_root_readable": health["projects_root_readable"],
        "codex": not str(health["codex"]).startswith("unavailable"),
        "basic_memory": bool(health.get("basic_memory", {}).get("ok")),
        "graphify": not str(health["graphify_package"]).startswith("unavailable"),
        "schedule": health["schedule"] != "not installed",
    }
    if not all(required.values()):
        raise RuntimeError(f"Final health gate failed: {required}")
    commit_if_changed(vault_root(), "Record approved second-brain health canary")
    safe_push_private(vault_root())
    store.set_bootstrap_state("completed")
    return {
        "state": "completed",
        "models": model_results,
        "tests": "passed",
        "health": required,
        "scheduled_task_canary": "passed",
        "public_draft_pr": pr_url,
    }


def decide_review(observation_id: str, decision: str, *, reason: str | None = None) -> dict[str, Any]:
    paths, config, _defaults, store = context()
    if decision == "approved":
        matches = [item for item in store.observations("pending") if item["id"] == observation_id]
        if matches and matches[0]["kind"] == "clarification":
            raise RuntimeError("Clarification items need `sb review resolve <id> --answer ...`, not approval.")
        store.decide_observation(observation_id, "approved")
        promote_approved_observation(vault_root(), store, observation_id)
        _refresh_review_artifacts(store)
        reindex(paths, vault_root())
        commit_if_changed(vault_root(), f"Approve second-brain observation {observation_id}")
        if store.bootstrap_state()["state"] == "completed":
            safe_push_private(vault_root())
        return {"id": observation_id, "status": "promoted"}
    if decision == "rejected":
        if not reason:
            raise ValueError("A rejection reason is required")
        store.decide_observation(observation_id, "rejected", reason)
        _refresh_review_artifacts(store)
        return {"id": observation_id, "status": "rejected", "reason": reason}
    if decision == "resolved":
        if not reason:
            raise ValueError("A resolution answer is required")
        matches = [item for item in store.observations("pending") if item["id"] == observation_id]
        if not matches:
            raise KeyError(observation_id)
        item = matches[0]
        store.add_evidence(
            source_type="interview",
            source_ref=f"review-resolution:{observation_id}",
            kind="explicit_profile_answer",
            payload={"question": item["claim"], "answer": reason, "explicit": True},
        )
        store.decide_observation(observation_id, "resolved", reason)
        _refresh_review_artifacts(store)
        return {"id": observation_id, "status": "resolved", "answer_saved": True}
    raise ValueError(decision)


def decide_review_group(group_id: str, decision: str, *, reason: str | None = None) -> dict[str, Any]:
    paths, _config, _defaults, store = context()
    group = find_review_group(store.observations("pending"), group_id)
    if group["mode"] == "answer":
        raise RuntimeError(
            "Clarification groups are answer-only. Resolve individual questions or leave them pending."
        )
    ids = [item["id"] for item in group["items"]]
    if decision == "approved":
        promote_observation_group(vault_root(), store, ids)
        _refresh_review_artifacts(store)
        reindex(paths, vault_root())
        commit_if_changed(vault_root(), f"Approve second-brain review group {group['key']}")
        if store.bootstrap_state()["state"] == "completed":
            safe_push_private(vault_root())
        return {"group": group_id, "status": "promoted", "count": len(ids), "ids": ids}
    if decision == "rejected":
        if not reason:
            raise ValueError("A batch rejection reason is required")
        store.decide_observations(ids, "rejected", reason)
        _refresh_review_artifacts(store)
        return {
            "group": group_id,
            "status": "rejected",
            "count": len(ids),
            "ids": ids,
            "reason": reason,
        }
    raise ValueError(decision)
