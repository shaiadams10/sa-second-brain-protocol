from __future__ import annotations

import json
import hashlib
import re
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from .basic_memory_integration import reindex
from .collector import (
    collect_projects,
    collect_sessions,
    reconcile_existing_session_attribution,
)
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
from .model_runner import (
    ModelRunError,
    ModelRole,
    canary,
    run_model,
    select_evidence_for_packet,
    usage_from_receipt,
)
from .notifications import notify
from .profile import create_interview, import_linkedin_export, interview_status
from .project_catalog import publish_project_catalog
from .project_rebuild import rebuild_project_subsystem
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
from .service import session_index_summary
from .session_attribution import (
    register_current_project_paths,
    seed_project_paths_from_backups,
)
from .health import report as health_report
from .feedback_learning import knowledge_feedback_profile
from .source_integrity import capture as capture_integrity, compare as compare_integrity
from .state import StateStore, canonical_hash, utc_now


def context() -> tuple[RuntimePaths, dict[str, Any], dict[str, Any], StateStore]:
    paths = setup_runtime()
    config = load_runtime_config(paths)
    defaults = load_defaults(config)
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
            collection_paths=[Path(item) for item in config.get("project_collection_paths", [])],
            classification_overrides=config.get("project_classification_overrides", {}),
        )
        publish_project_catalog(vault_root(), project_result["projects"])
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
            collection_paths=[Path(item) for item in config.get("project_collection_paths", [])],
            classification_overrides=config.get("project_classification_overrides", {}),
        )
        publish_project_catalog(vault_root(), project_result["projects"])
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


def reconcile_project_sessions() -> dict[str, Any]:
    """Index and ingest only sessions that resolve to one current leaf project."""

    paths, config, _defaults, store = context()
    if store.bootstrap_state()["state"] != "completed":
        raise RuntimeError(
            "Project-session reconciliation is available after bootstrap approval."
        )
    with single_instance(paths.locks / "pipeline.lock"):
        projects = [
            project
            for project in store.present_projects()
            if project.get("classification") not in {"collection", "duplicate"}
            and project.get("local_path")
        ]
        register_current_project_paths(
            store,
            projects,
            previous_projects=projects,
        )
        aliases = seed_project_paths_from_backups(
            store,
            paths.runs / "backups",
            projects,
        )
        collection = collect_sessions(store, config)
        compaction = compact_session_evidence(store)
        attribution = reconcile_existing_session_attribution(store)
        return {
            "status": "completed",
            "model_called": False,
            "git_published": False,
            "historical_aliases": aliases,
            "collection": collection,
            "compaction": compaction,
            "attribution": attribution,
            "index": session_index_summary(store),
        }


def _validated_project_history_update(
    output: dict[str, Any],
    *,
    project: dict[str, Any],
    allowed_evidence_ids: set[str],
    session_evidence_ids: set[str],
) -> dict[str, Any]:
    if output.get("project_id") != project["id"]:
        raise RuntimeError(
            "Isolated project-history analysis attempted a cross-project update."
        )
    evidence_refs = {str(value) for value in output.get("evidence_refs", [])}
    unknown = evidence_refs - allowed_evidence_ids
    if unknown:
        raise RuntimeError(
            f"Project-history output referenced unknown evidence: {sorted(unknown)}"
        )
    if not (evidence_refs & session_evidence_ids):
        raise RuntimeError(
            "Project-history update must cite at least one indexed session digest."
        )
    summary = str(output.get("summary") or "").strip()
    headings = re.findall(r"(?m)^## (.+)$", summary)
    bullets = re.findall(r"(?m)^- \S", summary)
    if len(headings) < 2 or len(bullets) < 3:
        raise RuntimeError(
            "Project-history summary must contain at least two sections and three bullets."
        )
    if not summary.startswith("## ") or re.search(
        r"(?i)(?:indexed|session history).{0,60}(?:synthesi|analy)|"
        r"(?:canonical|project) name (?:was )?unavailable",
        summary,
    ):
        raise RuntimeError("Project-history summary contained synthesis boilerplate.")
    if str(project["id"]).casefold() in summary.casefold():
        raise RuntimeError("Project-history summary exposed a machine project ID.")
    return {
        "project_id": str(project["id"]),
        "name": str(project["name"]),
        "summary": summary,
        "evidence_refs": sorted(evidence_refs),
    }


def _project_session_digest_groups(
    store: StateStore,
) -> dict[str, list[dict[str, Any]]]:
    assignments = {
        (str(row["surface"]), str(row["session_id"])): str(row["project_id"])
        for row in store.session_project_index()
        if row["status"] == "matched" and row.get("project_id")
    }
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in store.evidence():
        if (
            row["source_type"] != "session-digest"
            or row["kind"] != "session_digest"
            or row.get("status") in {"superseded", "compacted"}
        ):
            continue
        payload = row.get("payload") or {}
        key = (str(payload.get("source") or ""), str(payload.get("session_id") or ""))
        project_id = assignments.get(key)
        if not project_id:
            continue
        if row.get("project_id") != project_id:
            raise RuntimeError(
                "Session digest attribution disagrees with the authoritative session index."
            )
        groups.setdefault(project_id, []).append(row)
    return groups


def _latest_project_inventory(
    store: StateStore, project_id: str
) -> dict[str, Any]:
    matches = [
        row
        for row in store.evidence()
        if row["kind"] == "project_inventory" and row.get("project_id") == project_id
    ]
    if not matches:
        raise RuntimeError(f"No deterministic project inventory for {project_id}.")
    return matches[-1]


def _analyze_one_project_session_history(
    *,
    store: StateStore,
    paths: RuntimePaths,
    defaults: dict[str, Any],
    project: dict[str, Any],
    session_digests: list[dict[str, Any]],
) -> dict[str, Any]:
    project_id = str(project["id"])
    analyzed = store.analyzed_project_session_evidence(project_id)
    remaining = [row for row in session_digests if row["id"] not in analyzed]
    if not remaining:
        return {
            "project_id": project_id,
            "project": project["name"],
            "status": "unchanged",
            "session_digests": 0,
            "model_calls": 0,
        }
    if any(row.get("project_id") != project_id for row in remaining):
        raise RuntimeError("Project-history packet contained cross-project evidence.")

    inventory = _latest_project_inventory(store, project_id)
    inventory_context = {
        **inventory,
        "payload": {
            "project_id": project_id,
            "canonical_name": str(project["name"]),
            "classification": str(project.get("classification") or ""),
        },
    }
    role = _role(defaults, "daily")
    max_calls = 8
    run_ids: list[str] = []
    run_evidence: list[tuple[str, list[str]]] = []
    updates: list[dict[str, Any]] = []
    processed: list[dict[str, Any]] = []
    while remaining and len(run_ids) < max_calls:
        selected = select_evidence_for_packet(
            [inventory_context, *remaining],
            max_chars=int(defaults["limits"]["max_packet_chars"]),
            max_item_chars=int(defaults["limits"]["max_evidence_text_chars"]),
            feedback_profile={},
        )
        selected_session = [row for row in selected if row["id"] != inventory["id"]]
        if not selected_session:
            raise RuntimeError("No session digest fit the isolated project packet.")
        run_id = store.start_run("project-history", role.name, role.reasoning)
        try:
            output, receipt = run_model(
                paths=paths,
                role=role,
                prompt_name="project-history.md",
                evidence=selected,
                run_id=run_id,
                max_packet_chars=int(defaults["limits"]["max_packet_chars"]),
                max_evidence_chars=int(
                    defaults["limits"]["max_evidence_text_chars"]
                ),
                schema_name="project-history-output.schema.json",
                feedback_profile={},
            )
            update = _validated_project_history_update(
                output,
                project=project,
                allowed_evidence_ids={row["id"] for row in selected},
                session_evidence_ids={row["id"] for row in selected_session},
            )
            store.finish_run(
                run_id,
                "validated",
                evidence_count=len(selected),
                receipt_path=str(receipt),
                usage=usage_from_receipt(receipt),
            )
        except Exception as error:
            store.finish_run(
                run_id,
                "failed",
                evidence_count=len(selected),
                error=str(error),
            )
            raise
        run_ids.append(run_id)
        selected_ids = {row["id"] for row in selected_session}
        run_evidence.append((run_id, sorted(selected_ids)))
        updates.append(update)
        processed.extend(selected_session)
        remaining = [row for row in remaining if row["id"] not in selected_ids]

    if remaining:
        raise RuntimeError(
            f"Project history exceeded {max_calls} bounded model packets."
        )
    summaries = [update["summary"] for update in updates if update["summary"]]
    evidence_refs = sorted({row["id"] for row in processed})
    merged = {
        "summary": (
            f"- {project['name']}: synthesized {len(processed)} indexed session "
            "digests in project isolation."
        ),
        "observations": [],
        "pattern_signals": [],
        "learning_signals": [],
        "project_updates": [
            {
                "project_id": project_id,
                "name": project["name"],
                "summary": "\n\n".join(summaries)[:12000],
                "evidence_refs": evidence_refs,
            }
        ],
        "skill_updates": [],
        "voice_samples": [],
        "review_items": [],
        "question_resolutions": [],
    }
    published = publish_model_output(
        vault=vault_root(),
        store=store,
        output=merged,
        run_kind=f"project-history:{project_id}",
        evidence_ids=[inventory["id"], *[row["id"] for row in processed]],
    )
    for run_id, evidence_ids in run_evidence:
        store.mark_project_session_evidence_analyzed(
            project_id,
            evidence_ids,
            run_id=run_id,
        )
        store.complete_validated_run(run_id)
    return {
        "project_id": project_id,
        "project": project["name"],
        "status": "completed",
        "session_digests": len(processed),
        "model_calls": len(run_ids),
        "project_note_written": bool(published.get("projects_written")),
        "analysis_path": published.get("synthesis_path"),
    }


def analyze_project_sessions(*, force: bool = False) -> dict[str, Any]:
    """Backfill indexed session history one project at a time, locally."""

    paths, config, defaults, store = context()
    if store.bootstrap_state()["state"] != "completed":
        raise RuntimeError(
            "Project-session analysis is available after bootstrap approval."
        )
    with single_instance(paths.locks / "pipeline.lock"):
        projects = {
            str(project["id"]): project for project in store.present_projects()
        }
        register_current_project_paths(
            store,
            list(projects.values()),
            previous_projects=list(projects.values()),
        )
        seed_project_paths_from_backups(
            store,
            paths.runs / "backups",
            list(projects.values()),
        )
        collection = collect_sessions(store, config)
        compaction = compact_session_evidence(store)
        attribution = reconcile_existing_session_attribution(store)
        groups = _project_session_digest_groups(store)
        analysis_rows_reset = (
            store.clear_project_session_analysis(set(groups)) if force else 0
        )
        results: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        model_runtime_failed = False
        ordered = sorted(
            groups.items(),
            key=lambda item: (len(item[1]), str(projects[item[0]]["name"]).casefold()),
        )
        for project_id, digests in ordered:
            project = projects.get(project_id)
            if project is None:
                continue
            try:
                results.append(
                    _analyze_one_project_session_history(
                        store=store,
                        paths=paths,
                        defaults=defaults,
                        project=project,
                        session_digests=digests,
                    )
                )
            except Exception as error:
                failures.append(
                    {
                        "project_id": project_id,
                        "project": str(project["name"]),
                        "error": f"{type(error).__name__}: {error}",
                    }
                )
                if isinstance(error, ModelRunError) and str(error).startswith(
                    "Codex model run failed"
                ):
                    model_runtime_failed = True
                    break
        if any(result["status"] == "completed" for result in results):
            reindex(paths, vault_root())
        return {
            "status": "completed" if not failures else "partial",
            "model_called": any(result["model_calls"] for result in results),
            "git_published": False,
            "personal_profile_touched": False,
            "halted_after_model_runtime_failure": model_runtime_failed,
            "analysis_rows_reset": analysis_rows_reset,
            "collection": collection,
            "compaction": compaction,
            "attribution": attribution,
            "projects": results,
            "failures": failures,
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
    summary_period: str | None = None,
) -> dict[str, Any]:
    if evidence_override is None:
        compact_session_evidence(store)
    if evidence_override is not None:
        evidence = evidence_override
    elif kind == "weekly":
        if summary_period and re.fullmatch(r"\d{4}-W\d{2}", summary_period):
            year, week = (int(value) for value in summary_period.replace("W", "").split("-"))
            start_date = date.fromisocalendar(year, week, 1)
            local_tz = datetime.now().astimezone().tzinfo
            start = datetime.combine(start_date, datetime.min.time(), tzinfo=local_tz).astimezone(UTC)
            end = start + timedelta(days=7)
        else:
            start = datetime.now(UTC) - timedelta(days=7)
            end = datetime.now(UTC) + timedelta(seconds=1)
        evidence = [
            item
            for item in store.evidence_since(start.isoformat())
            if str(item.get("occurred_at") or item.get("created_at") or "") < end.isoformat()
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
    feedback_profile = knowledge_feedback_profile(store)
    if not evidence:
        if kind == "weekly":
            published = publish_model_output(
                vault=vault_root(),
                store=store,
                output={
                    "summary": "- No new project or agent-session activity was available for this period; stewardship checks still ran.",
                    "observations": [],
                    "pattern_signals": [],
                    "learning_signals": [],
                    "project_updates": [],
                    "session_summaries": [],
                    "skill_updates": [],
                    "voice_samples": [],
                    "review_items": [],
                    "question_resolutions": [],
                },
                run_kind=kind,
                evidence_ids=[],
                question_ids=[item["id"] for item in pending_questions],
                summary_period=summary_period,
            )
            return {
                "status": "completed",
                "run_ids": [],
                "evidence_count": 0,
                "remaining_evidence": 0,
                "model_called": False,
                **published,
            }
        return {"status": "empty", "evidence_count": 0, "model_called": False}
    weekly_fingerprint = None
    if kind == "weekly":
        weekly_fingerprint = canonical_hash(
            {
                "evidence": [item["id"] for item in evidence],
                "questions": [item["id"] for item in pending_questions],
                "feedback_profile": feedback_profile,
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
            feedback_profile=feedback_profile,
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
                feedback_profile=feedback_profile,
            )
            packet_ids = {item["id"] for item in selected}
            used_ids = set()
            for collection in (
                output["observations"],
                output.get("pattern_signals", []),
                output.get("learning_signals", []),
                output["project_updates"],
                output["skill_updates"],
            ):
                for item in collection:
                    used_ids.update(item.get("evidence_refs", []))
            used_ids.update(item["evidence_ref"] for item in output["voice_samples"])
            used_ids.update(
                item["evidence_ref"] for item in output.get("session_summaries", [])
            )
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
                usage=usage_from_receipt(receipt),
            )
        except Exception as error:
            store.finish_run(run_id, "failed", evidence_count=len(selected), error=str(error))
            raise
    if not outputs:
        return {"status": "empty", "evidence_count": 0, "model_called": False}
    project_updates: dict[str, dict[str, Any]] = {}
    skill_updates: dict[str, dict[str, Any]] = {}
    pattern_signals: dict[str, dict[str, Any]] = {}
    learning_signals: list[dict[str, Any]] = []
    question_resolutions: dict[str, dict[str, Any]] = {}
    session_summaries: dict[str, dict[str, Any]] = {}
    for output in outputs:
        learning_signals.extend(
            dict(item) for item in output.get("learning_signals", [])
        )
        for item in output.get("session_summaries", []):
            evidence_ref = str(item["evidence_ref"])
            session_summaries.setdefault(evidence_ref, dict(item))
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
                if item.get("scope") == "global":
                    existing["scope"] = "global"
                elif not existing.get("scope") and item.get("scope"):
                    existing["scope"] = item["scope"]
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
        "learning_signals": learning_signals,
        "project_updates": list(project_updates.values()),
        "session_summaries": list(session_summaries.values()),
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
            "learning_signals",
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
        summary_period=summary_period,
    )
    if kind in {"daily", "weekly"}:
        store.replace_summary_runs(
            kind,
            Path(published["synthesis_path"]).stem,
            run_ids,
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


def incremental(
    kind: str, *, trigger: str = "manual", summary_period: str | None = None
) -> dict[str, Any]:
    paths, config, defaults, store = context()
    if store.bootstrap_state()["state"] != "completed":
        raise RuntimeError("Complete and approve bootstrap before incremental runs.")
    pipeline_run_id = store.start_pipeline_run(
        kind, trigger=trigger, stage="waiting_for_lock"
    )

    def stage(name: str) -> None:
        store.set_pipeline_stage(pipeline_run_id, name)

    try:
        with single_instance(paths.locks / "pipeline.lock"):
            stage("snapshot_manual_notes")
            snapshot_manual_markdown(vault_root())
            stage("collect_projects")
            project_result = collect_projects(
                store,
                projects_root=Path(config["projects_root"]),
                defaults=defaults,
                ignored_paths=[Path(item) for item in config.get("ignored_project_paths", [])],
                collection_paths=[Path(item) for item in config.get("project_collection_paths", [])],
                classification_overrides=config.get("project_classification_overrides", {}),
            )
            stage("publish_project_catalog")
            publish_project_catalog(vault_root(), project_result["projects"])
            stage("collect_sessions")
            collect_sessions(store, config)
            stage("update_code_graphs")
            _update_graphs(
                store,
                paths,
                project_result["projects"],
                project_result["changed_project_ids"],
                strict=False,
            )
            stage("synthesize")
            result = _synthesize(
                kind,
                store=store,
                paths=paths,
                defaults=defaults,
                summary_period=summary_period,
            )
            if result["status"] == "empty":
                stage("notify")
                notify(
                    "Second brain",
                    f"{kind.title()} run: no new evidence",
                    vault=vault_root(),
                )
                store.finish_pipeline_run(pipeline_run_id, "completed")
                return result
            stage("reindex")
            reindex(paths, vault_root())
            stage("commit")
            commit_if_changed(
                vault_root(), f"Second brain {kind} update {date.today().isoformat()}"
            )
            stage("push")
            safe_push_private(vault_root())
            review = Path(result["review_path"]) if result.get("review_path") else None
            stage("notify")
            notify(
                "Second brain updated",
                f"{result.get('promoted', 0)} promoted, {result.get('pending', 0)} need review",
                vault=vault_root(),
                note=review,
            )
            store.finish_pipeline_run(pipeline_run_id, "completed")
            return result
    except Exception as error:
        store.finish_pipeline_run(
            pipeline_run_id,
            "failed",
            error=f"{type(error).__name__}: {error}",
        )
        raise


def sync_project_index() -> dict[str, Any]:
    """Refresh scanner state and the canonical catalog without model or Git publication."""

    paths, config, defaults, store = context()
    if store.bootstrap_state()["state"] != "completed":
        raise RuntimeError("Complete and approve bootstrap before refreshing the project catalog.")
    with single_instance(paths.locks / "pipeline.lock"):
        project_result = collect_projects(
            store,
            projects_root=Path(config["projects_root"]),
            defaults=defaults,
            ignored_paths=[Path(item) for item in config.get("ignored_project_paths", [])],
            collection_paths=[Path(item) for item in config.get("project_collection_paths", [])],
            classification_overrides=config.get("project_classification_overrides", {}),
        )
        catalog = publish_project_catalog(vault_root(), project_result["projects"])
        reindex(paths, vault_root())
        return {
            "status": "completed",
            "model_called": False,
            "git_published": False,
            "changed_project_ids": project_result["changed_project_ids"],
            **catalog,
        }


def rebuild_projects(
    *, collection_names: list[str] | None = None, confirm: bool = False
) -> dict[str, Any]:
    """Create a recoverable fresh baseline for only the project subsystem."""

    paths, config, defaults, store = context()
    if store.bootstrap_state()["state"] != "completed":
        raise RuntimeError("Complete and approve bootstrap before rebuilding projects.")
    with single_instance(paths.locks / "pipeline.lock"):
        return rebuild_project_subsystem(
            paths,
            vault_root(),
            store,
            config=config,
            defaults=defaults,
            collection_names=collection_names,
            confirm=confirm,
        )


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
            collection_paths=[Path(item) for item in config.get("project_collection_paths", [])],
            classification_overrides=config.get("project_classification_overrides", {}),
        )
        publish_project_catalog(vault_root(), project_result["projects"])
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


def _weekly_target_period(today: date) -> str:
    # Saturday is the normal synthesis day and Sunday is its immediate catch-up
    # window.  From Monday onward, catch up the preceding ISO week rather than
    # writing late evidence into the new week's note.
    target = today if today.weekday() >= 5 else today - timedelta(days=7)
    week = target.isocalendar()
    return f"{week.year}-W{week.week:02d}"


def scheduled() -> dict[str, Any]:
    try:
        daily_result = incremental("daily", trigger="scheduled")
        result = {"daily": daily_result}
        today = date.today()
        weekly_period = _weekly_target_period(today)
        weekly_note = vault_root() / "Journal" / "Weekly" / f"{weekly_period}.md"
        if today.strftime("%A") == "Saturday" or not weekly_note.is_file():
            result["weekly"] = incremental(
                "weekly", trigger="scheduled", summary_period=weekly_period
            )
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
    run_canary(scheduled_script)
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
