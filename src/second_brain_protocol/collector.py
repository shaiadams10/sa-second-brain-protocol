from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .activity import build_project_delta
from .scanner import ProjectScanner
from .session_attribution import (
    ProjectSessionResolver,
    SessionResolution,
    register_current_project_paths,
    workspace_hash,
)
from .sessions import (
    antigravity_database_tool_paths,
    antigravity_database_workspace,
    antigravity_session_id,
    antigravity_summary_session_metadata,
    codex_session_metadata,
    discover_antigravity_sources,
    discover_codex_sessions,
    inspect_antigravity_database,
    parse_antigravity_artifact,
    parse_antigravity_database,
    parse_antigravity_transcript,
    parse_codex_jsonl,
    validated_antigravity_database_resume_index,
    validated_jsonl_resume_offset,
)
from .state import StateStore


ANTIGRAVITY_TRANSCRIPT_COLLECTOR_VERSION = 3
ANTIGRAVITY_DATABASE_COLLECTOR_VERSION = 2


def _file_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        stat = path.stat()
        digest.update(str(stat.st_size).encode())
        digest.update(str(stat.st_mtime_ns).encode())
        with path.open("rb") as handle:
            digest.update(handle.read(65536))
            if stat.st_size > 65536:
                handle.seek(max(0, stat.st_size - 65536))
                digest.update(handle.read(65536))
    except OSError:
        return "missing"
    return digest.hexdigest()


def _database_fingerprint(path: Path) -> str:
    digest = hashlib.sha256(_file_fingerprint(path).encode("ascii"))
    wal = Path(str(path) + "-wal")
    if wal.exists():
        digest.update(_file_fingerprint(wal).encode("ascii"))
    return digest.hexdigest()


@contextmanager
def _database_read_snapshot(
    path: Path, runtime_root: Path
) -> Iterator[tuple[Path, bool]]:
    """Copy a live DB/WAL into runtime so SQLite never writes beside the source."""
    wal = Path(str(path) + "-wal")
    if not wal.exists():
        yield path, True
        return
    snapshot_root = runtime_root / "staging" / "sqlite-snapshots"
    snapshot_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f"{path.stem[:12]}-", dir=snapshot_root
    ) as temp:
        destination = Path(temp) / path.name
        try:
            for source in (path, wal, Path(str(path) + "-shm")):
                if source.exists():
                    shutil.copy2(source, Path(temp) / source.name)
        except OSError:
            # The immutable main file remains a safe, possibly slightly delayed fallback.
            yield path, True
            return
        yield destination, False


def _source_key(prefix: str, path: Path) -> str:
    return prefix + hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:24]


def _already_collected(store: StateStore, source_key: str, fingerprint: str) -> bool:
    receipt = store.collection_receipt(source_key)
    return bool(receipt and receipt["fingerprint"] == fingerprint)


def _project_for_cwd(cwd: str | None, projects: list[dict[str, Any]]) -> str | None:
    if not cwd:
        return None
    try:
        candidate = Path(cwd).resolve()
    except OSError:
        return None
    matches: list[tuple[int, str]] = []
    for project in projects:
        try:
            root = Path(project["local_path"]).resolve()
            candidate.relative_to(root)
            matches.append((len(root.parts), project["id"]))
        except (KeyError, OSError, ValueError):
            continue
    return max(matches)[1] if matches else None


def _project_for_record(
    record: dict[str, Any], projects: list[dict[str, Any]]
) -> str | None:
    # A project name mentioned in a conversation is not evidence that the
    # session belongs to that project. Attribution requires an authoritative
    # workspace path; ambiguous records stay unattributed.
    return _project_for_cwd(record.get("cwd"), projects)


def _attribution_projects(projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        project
        for project in projects
        if project.get("classification") not in {"collection", "duplicate"}
        and project.get("local_path")
        and (
            project.get("head_commit")
            or project.get("initial_commit")
            or int(project.get("tracked_file_count") or 0) > 0
            or project.get("tech_stack")
        )
    ]


def reconcile_existing_session_attribution(store: StateStore) -> dict[str, Any]:
    """Build a one-project-per-session index and repair legacy assignments."""

    projects = _attribution_projects(store.present_projects())
    project_ids = {str(project["id"]) for project in projects}
    resolver = ProjectSessionResolver(
        projects,
        store.project_path_aliases(project_ids=project_ids),
    )
    raw_rows = [
        row
        for row in store.evidence()
        if row["source_type"] in {"codex", "antigravity"}
        and row["kind"] in {"visible_message", "tool_metadata", "artifact"}
    ]
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in raw_rows:
        payload = row.get("payload") or {}
        session_id = str(payload.get("session_id") or "").strip()
        if session_id:
            groups.setdefault((str(row["source_type"]), session_id), []).append(row)

    source_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for source in store.session_sources():
        key = (str(source["surface"]), str(source["session_id"]))
        source_groups.setdefault(key, []).append(source)

    raw_assignments: dict[str, str | None] = {}
    index_records: list[dict[str, Any]] = []
    all_keys = sorted(set(groups) | set(source_groups))
    for surface, session_id in all_keys:
        rows = groups.get((surface, session_id), [])
        sources = source_groups.get((surface, session_id), [])
        workspaces = {
            str((row.get("payload") or {}).get("cwd") or "").strip()
            for row in rows
            if str((row.get("payload") or {}).get("cwd") or "").strip()
        }
        resolutions = [resolver.resolve(workspace) for workspace in sorted(workspaces)]
        source_projects = {
            str(source["project_id"])
            for source in sources
            if source.get("status") == "matched" and source.get("project_id")
        }
        matched_projects = {
            str(resolution.project_id)
            for resolution in resolutions
            if resolution.status == "matched" and resolution.project_id
        } | source_projects
        ambiguous = any(
            resolution.status == "ambiguous" for resolution in resolutions
        ) or any(source.get("status") == "ambiguous" for source in sources)
        if len(matched_projects) == 1 and not ambiguous:
            expected = next(iter(matched_projects))
            status = "matched"
            matched_resolutions = [
                resolution
                for resolution in resolutions
                if resolution.project_id == expected
            ]
            if matched_resolutions:
                best = max(matched_resolutions, key=lambda item: item.confidence)
                reason = best.resolver
                confidence = best.confidence
            else:
                matched_sources = [
                    source for source in sources if source.get("project_id") == expected
                ]
                best_source = max(
                    matched_sources,
                    key=lambda item: float(item.get("confidence") or 0.0),
                )
                reason = str(best_source.get("resolver") or "source_metadata")
                confidence = float(best_source.get("confidence") or 0.0)
        elif matched_projects or ambiguous:
            expected = None
            status = "ambiguous"
            reason = "conflicting_authoritative_metadata"
            confidence = 0.0
        else:
            expected = None
            status = "unmatched"
            reason = "missing_or_unmatched_workspace"
            confidence = 0.0
        for row in rows:
            if expected != row.get("project_id"):
                raw_assignments[str(row["id"])] = expected
                row["project_id"] = expected
        index_records.append(
            {
                "surface": surface,
                "session_id": session_id,
                "project_id": expected,
                "status": status,
                "resolver": reason,
                "confidence": confidence,
                "workspace_count": len(workspaces),
                "source_record_count": len(rows),
            }
        )
    store.reassign_evidence_projects(raw_assignments)
    store.replace_session_project_index(index_records)

    indexed_projects = {
        (item["surface"], item["session_id"]): item.get("project_id")
        for item in index_records
        if item["status"] == "matched"
    }
    digest_assignments: dict[str, str | None] = {}
    for digest in [
        row
        for row in store.evidence()
        if row["source_type"] == "session-digest" and row["kind"] == "session_digest"
    ]:
        payload = digest.get("payload") or {}
        key = (str(payload.get("source") or ""), str(payload.get("session_id") or ""))
        expected = indexed_projects.get(key)
        if expected != digest.get("project_id"):
            digest_assignments[str(digest["id"])] = expected
    store.reassign_evidence_projects(digest_assignments)
    status_counts = {
        status: sum(item["status"] == status for item in index_records)
        for status in ("matched", "unmatched", "ambiguous")
    }
    return {
        "raw_reassigned": len(raw_assignments),
        "digests_reassigned": len(digest_assignments),
        "sessions_indexed": len(index_records),
        **status_counts,
    }


def collect_projects(
    store: StateStore,
    *,
    projects_root: Path,
    defaults: dict[str, Any],
    ignored_paths: list[Path] | None = None,
    collection_paths: list[Path] | None = None,
    classification_overrides: dict[str, Any] | None = None,
    baseline: bool = False,
) -> dict[str, Any]:
    scanner = ProjectScanner(
        projects_root,
        defaults,
        tuple(ignored_paths or ()),
        tuple(collection_paths or ()),
    )
    projects = scanner.scan_all()
    previous_projects_list = store.projects()
    previous_by_path: dict[str, dict[str, Any]] = {}
    for previous in previous_projects_list:
        local_path = str(previous.get("local_path") or "").strip()
        if not local_path:
            continue
        key = str(Path(local_path).resolve()).casefold()
        presence = store.project_presence(previous["id"])
        existing = previous_by_path.get(key)
        if existing is None or (presence and bool(presence.get("present"))):
            previous_by_path[key] = previous
    for project in projects:
        local_path = str(project.get("local_path") or "").strip()
        if not local_path:
            continue
        previous = previous_by_path.get(str(Path(local_path).resolve()).casefold())
        if previous is None or previous["id"] == project["id"]:
            continue
        scanner_id = str(project["id"])
        project["id"] = str(previous["id"])
        project["scanner_identity_aliases"] = sorted(
            {
                scanner_id,
                *(
                    str(item)
                    for item in previous.get("scanner_identity_aliases", [])
                    if item
                ),
            }
        )
        project["identity_reconciliation_reason"] = (
            "same configured source directory as the previous validated scan"
        )
    overrides = classification_overrides or {}
    allowed_classifications = {
        "first-party",
        "fork",
        "third-party",
        "experiment",
        "reference",
        "review",
    }
    for project in projects:
        override = overrides.get(project["id"])
        if not override:
            continue
        if isinstance(override, str):
            classification = override
            reason = "explicit user classification"
        else:
            classification = str(override.get("classification", ""))
            reason = str(override.get("reason") or "explicit user classification")
        if classification not in allowed_classifications:
            raise ValueError(
                f"Invalid project classification override for {project['id']}: {classification}"
            )
        project["classification"] = classification
        project["classification_reasons"] = [reason]
    register_current_project_paths(
        store,
        projects,
        previous_projects=previous_projects_list,
    )
    previous_projects = {item["id"]: item for item in previous_projects_list}
    changed: list[str] = []
    delta_count = 0
    for project in projects:
        previous = previous_projects.get(project["id"])
        presence = store.project_presence(project["id"])
        delta = (
            None
            if baseline
            else build_project_delta(
                previous,
                project,
                was_present=bool(presence["present"]) if presence is not None else None,
            )
        )
        store.upsert_project(project)
        store.set_project_presence(project["id"], present=True)
        if not previous or previous.get("fingerprint") != project.get("fingerprint"):
            changed.append(project["id"])
        public_payload = {
            key: value
            for key, value in project.items()
            if key not in {"local_path", "manifest"}
        }
        source_type = "git" if project.get("head_commit") else "filesystem"
        inventory_version = project["fingerprint"]
        if previous and previous.get("fingerprint") == project.get("fingerprint"):
            # Preserve compatibility with bootstrap inventory IDs for unchanged repos.
            inventory_version = project.get("head_commit") or project["fingerprint"]
        store.add_evidence(
            source_type=source_type,
            source_ref=f"project-inventory:{project['id']}:{inventory_version}",
            kind="project_inventory",
            payload=public_payload,
            project_id=project["id"],
        )
        if delta:
            delta_hash = hashlib.sha256(
                json.dumps(delta, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()[:24]
            _, added = store.add_evidence(
                source_type="project-activity",
                source_ref=f"project-delta:{project['id']}:{delta_hash}",
                kind="project_delta",
                payload=delta,
                project_id=project["id"],
            )
            delta_count += int(added)

    current_ids = {project["id"] for project in projects}
    for project_id, previous in previous_projects.items():
        if project_id in current_ids:
            continue
        presence = store.project_presence(project_id)
        delta = (
            None
            if baseline
            else build_project_delta(
                previous,
                None,
                was_present=bool(presence["present"]) if presence is not None else None,
            )
        )
        store.set_project_presence(project_id, present=False)
        if not delta:
            continue
        delta_hash = hashlib.sha256(
            json.dumps(delta, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:24]
        _, added = store.add_evidence(
            source_type="project-activity",
            source_ref=f"project-delta:{project_id}:{delta_hash}",
            kind="project_delta",
            payload=delta,
            project_id=project_id,
        )
        delta_count += int(added)
    return {
        "projects": projects,
        "changed_project_ids": changed,
        "project_deltas": delta_count,
    }


def collect_sessions(store: StateStore, config: dict[str, Any]) -> dict[str, int]:
    projects = _attribution_projects(store.present_projects())
    project_ids = {str(project["id"]) for project in projects}
    resolver = ProjectSessionResolver(
        projects,
        store.project_path_aliases(project_ids=project_ids),
    )
    project_tokens: dict[str, set[str]] = {}
    for project in projects:
        for token in (project.get("id"), project.get("name")):
            normalized = str(token or "").strip().casefold()
            if normalized:
                project_tokens.setdefault(normalized, set()).add(str(project["id"]))
    session_overrides: dict[str, str] = {}
    raw_overrides = config.get("session_project_overrides", {})
    if isinstance(raw_overrides, dict):
        for source, target in raw_overrides.items():
            candidates = project_tokens.get(str(target or "").strip().casefold(), set())
            if len(candidates) == 1:
                session_overrides[str(source).strip().casefold()] = next(
                    iter(candidates)
                )

    def explicit_project(surface: str, session_id: str) -> str | None:
        return session_overrides.get(f"{surface}:{session_id}".casefold())

    def tool_path_resolution(paths: list[str]) -> SessionResolution:
        matched: set[str] = set()
        for path in paths:
            candidate = resolver.resolve(path)
            if candidate.status == "ambiguous":
                return SessionResolution(
                    "ambiguous", None, "conflicting_antigravity_tool_paths", 0.0
                )
            if candidate.status == "matched" and candidate.project_id:
                matched.add(candidate.project_id)
        if len(matched) == 1:
            return SessionResolution(
                "matched",
                next(iter(matched)),
                "antigravity_tool_path",
                0.9,
            )
        if len(matched) > 1:
            return SessionResolution(
                "ambiguous", None, "conflicting_antigravity_tool_paths", 0.0
            )
        return SessionResolution("unmatched", None, "no_matching_tool_path", 0.0)

    counts = {
        "codex": 0,
        "antigravity": 0,
        "artifacts": 0,
        "databases": 0,
        "database_messages": 0,
        "sources_indexed": 0,
        "sources_matched": 0,
        "sources_unmatched": 0,
        "sources_ambiguous": 0,
        "sources_skipped_without_project": 0,
        "sources_ingested_profile_only": 0,
    }

    def route_source(
        *,
        source_key: str,
        surface: str,
        session_id: str,
        fingerprint: str,
        workspace: str | None,
        remote_url: str | None = None,
        commit_hash: str | None = None,
        fallback_paths: list[str] | None = None,
        project_override: str | None = None,
        override_resolver: str = "related_session_source",
    ) -> tuple[str | None, bool, str, str]:
        resolution = resolver.resolve(
            workspace,
            remote_url=remote_url,
            commit_hash=commit_hash,
        )
        if fallback_paths:
            fallback = tool_path_resolution(fallback_paths)
            if resolution.status == "unmatched":
                resolution = fallback
            elif (
                resolution.status == "matched"
                and fallback.status == "matched"
                and resolution.project_id != fallback.project_id
            ):
                resolution = SessionResolution(
                    "ambiguous", None, "conflicting_workspace_tool_path", 0.0
                )
        if project_override in project_ids:
            resolution = type(resolution)(
                "matched", project_override, override_resolver, 1.0
            )
        previous = store.session_source(source_key)
        receipt = store.collection_receipt(source_key)
        checkpoint = store.checkpoint(source_key)
        already_read = bool(
            (receipt and receipt.get("fingerprint") == fingerprint)
            or (checkpoint and checkpoint.get("fingerprint") == fingerprint)
        )
        same_indexed_source = bool(
            previous
            and previous.get("fingerprint") == fingerprint
            and previous.get("status") == resolution.status
            and previous.get("project_id") == resolution.project_id
        )
        ingested = bool(
            (same_indexed_source and previous and previous.get("ingested"))
            or (previous is None and already_read and resolution.status == "matched")
        )
        store.upsert_session_source(
            source_key=source_key,
            surface=surface,
            session_id=session_id,
            fingerprint=fingerprint,
            workspace_hash=workspace_hash(workspace),
            project_id=resolution.project_id,
            status=resolution.status,
            resolver=resolution.resolver,
            confidence=resolution.confidence,
            ingested=ingested,
        )
        counts["sources_indexed"] += 1
        counts[f"sources_{resolution.status}"] += 1
        if resolution.status != "matched" or not resolution.project_id:
            if not ingested:
                counts["sources_ingested_profile_only"] += 1
            return None, not ingested, "profile_only", resolution.status
        return resolution.project_id, not ingested, "full", resolution.status

    codex_roots = [
        Path(config["codex_sessions"]),
        Path(config["codex_archived_sessions"]),
    ]
    for path in discover_codex_sessions(codex_roots):
        fingerprint = _file_fingerprint(path)
        source_key = _source_key("codex-file:", path)
        metadata = codex_session_metadata(path)
        project_id, should_ingest, analysis_lane, attribution_status = route_source(
            source_key=source_key,
            surface="codex",
            session_id=str(metadata["session_id"] or path.stem),
            fingerprint=fingerprint,
            workspace=metadata.get("cwd"),
            remote_url=metadata.get("remote_url"),
            commit_hash=metadata.get("commit_hash"),
            project_override=explicit_project(
                "codex", str(metadata["session_id"] or path.stem)
            ),
            override_resolver="explicit_user_session_mapping",
        )
        if not should_ingest:
            continue
        checkpoint = store.checkpoint(source_key)
        start_offset = validated_jsonl_resume_offset(
            path, checkpoint.get("cursor") if checkpoint else None
        )
        batch: list[dict[str, Any]] = []
        for record in parse_codex_jsonl(path, start_offset=start_offset):
            if (
                analysis_lane == "profile_only"
                and record["kind"] not in {"visible_message", "tool_metadata"}
            ):
                continue
            payload = dict(record)
            cursor = payload.pop("_cursor", record["source_ref"])
            payload["analysis_lane"] = analysis_lane
            payload["attribution_status"] = attribution_status
            batch.append(
                {
                    "source_type": "codex",
                    "source_ref": record["source_ref"],
                    "kind": record["kind"],
                    "payload": payload,
                    "project_id": project_id,
                    "occurred_at": record.get("timestamp"),
                    "cursor": cursor,
                }
            )
        results = store.add_evidence_batch(
            batch, source_key=source_key, fingerprint=fingerprint
        )
        counts["codex"] += sum(int(added) for _evidence_id, added in results)
        store.mark_session_source_ingested(source_key, fingerprint=fingerprint)
        store.set_collection_receipt(source_key, fingerprint)

    sources = discover_antigravity_sources(
        Path(config["antigravity_brain"]), Path(config["antigravity_conversations"])
    )
    transcript_session_ids = {
        antigravity_session_id(path) for path in sources["transcripts"]
    }
    databases_by_session = {path.stem: path for path in sources["databases"]}
    known_antigravity_sessions = transcript_session_ids | set(databases_by_session)
    summary_metadata = antigravity_summary_session_metadata(
        Path(config["antigravity_conversations"]).parent / "agyhub_summaries_proto.pb",
        known_session_ids=known_antigravity_sessions,
    )
    database_workspaces: dict[str, str | None] = {}
    database_tool_paths: dict[str, list[str]] = {}
    for session_id, database in databases_by_session.items():
        with _database_read_snapshot(database, Path(config["runtime_root"])) as (
            database_path,
            immutable,
        ):
            workspace = antigravity_database_workspace(
                database_path, immutable=immutable
            ) or summary_metadata.get(session_id, {}).get("workspace")
            database_workspaces[session_id] = workspace
            primary = resolver.resolve(
                workspace,
                remote_url=summary_metadata.get(session_id, {}).get("remote_url"),
            )
            database_tool_paths[session_id] = (
                antigravity_database_tool_paths(database_path, immutable=immutable)
                if primary.status == "unmatched"
                else []
            )
    for path in sources["transcripts"]:
        fingerprint = _file_fingerprint(path)
        source_key = _source_key(
            f"antigravity-file-v{ANTIGRAVITY_TRANSCRIPT_COLLECTOR_VERSION}:", path
        )
        session_id = antigravity_session_id(path)
        workspace = database_workspaces.get(session_id)
        project_id, should_ingest, analysis_lane, attribution_status = route_source(
            source_key=source_key,
            surface="antigravity",
            session_id=session_id,
            fingerprint=fingerprint,
            workspace=workspace,
            remote_url=summary_metadata.get(session_id, {}).get("remote_url"),
            fallback_paths=database_tool_paths.get(session_id),
            project_override=explicit_project("antigravity", session_id),
            override_resolver="explicit_user_session_mapping",
        )
        if not should_ingest:
            continue
        checkpoint = store.checkpoint(source_key)
        start_offset = validated_jsonl_resume_offset(
            path, checkpoint.get("cursor") if checkpoint else None
        )
        batch = []
        for record in parse_antigravity_transcript(
            path, start_offset=start_offset, workspace=workspace
        ):
            if (
                analysis_lane == "profile_only"
                and record["kind"] not in {"visible_message", "tool_metadata"}
            ):
                continue
            payload = dict(record)
            cursor = payload.pop("_cursor", record["source_ref"])
            payload["analysis_lane"] = analysis_lane
            payload["attribution_status"] = attribution_status
            batch.append(
                {
                    "source_type": "antigravity",
                    "source_ref": record["source_ref"],
                    "kind": record["kind"],
                    "payload": payload,
                    "project_id": project_id,
                    "occurred_at": record.get("timestamp"),
                    "cursor": cursor,
                }
            )
        results = store.add_evidence_batch(
            batch,
            source_key=source_key,
            fingerprint=fingerprint,
            supersede_mutable_source=True,
        )
        counts["antigravity"] += sum(int(added) for _evidence_id, added in results)
        store.mark_session_source_ingested(source_key, fingerprint=fingerprint)
        store.set_collection_receipt(source_key, fingerprint)

    for path in sources["artifacts"]:
        fingerprint = _file_fingerprint(path)
        source_key = _source_key("antigravity-artifact:", path)
        record = parse_antigravity_artifact(path)
        if record:
            session_id = str(record.get("session_id") or path.parent.name)
            related_project = store.resolved_session_source_project(
                "antigravity", session_id
            )
            project_id, should_ingest, analysis_lane, _attribution_status = route_source(
                source_key=source_key,
                surface="antigravity",
                session_id=session_id,
                fingerprint=fingerprint,
                workspace=None,
                project_override=(
                    related_project or explicit_project("antigravity", session_id)
                ),
            )
            if not should_ingest:
                continue
            if analysis_lane == "profile_only":
                store.mark_session_source_ingested(
                    source_key, fingerprint=fingerprint
                )
                store.set_collection_receipt(source_key, fingerprint)
                continue
            _, added = store.add_evidence(
                source_type="antigravity",
                source_ref=record["source_ref"],
                kind=record["kind"],
                payload=record,
                project_id=project_id,
            )
            counts["artifacts"] += int(added)
            store.mark_session_source_ingested(source_key, fingerprint=fingerprint)
        store.set_collection_receipt(source_key, fingerprint)

    for path in sources["databases"]:
        fingerprint = _database_fingerprint(path)
        source_key = _source_key(
            f"antigravity-database-v{ANTIGRAVITY_DATABASE_COLLECTOR_VERSION}:",
            path,
        )
        session_id = path.stem
        project_id, should_ingest, analysis_lane, attribution_status = route_source(
            source_key=source_key,
            surface="antigravity",
            session_id=session_id,
            fingerprint=fingerprint,
            workspace=database_workspaces.get(session_id),
            remote_url=summary_metadata.get(session_id, {}).get("remote_url"),
            fallback_paths=database_tool_paths.get(session_id),
            project_override=explicit_project("antigravity", session_id),
            override_resolver="explicit_user_session_mapping",
        )
        if session_id in transcript_session_ids:
            # The canonical transcript is the evidence source. The paired DB
            # contributes only authoritative workspace metadata.
            store.mark_session_source_ingested(source_key, fingerprint=fingerprint)
            store.set_collection_receipt(source_key, fingerprint)
            continue
        if not should_ingest:
            continue
        checkpoint = store.checkpoint(source_key)
        with _database_read_snapshot(path, Path(config["runtime_root"])) as (
            database_path,
            immutable,
        ):
            if analysis_lane == "full" and project_id:
                payload = inspect_antigravity_database(
                    database_path, immutable=immutable
                )
                _, added = store.add_evidence(
                    source_type="antigravity",
                    source_ref=(
                        "antigravity-db-schema:"
                        + hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:24]
                    ),
                    kind="database_inventory",
                    payload={
                        "database": path.name,
                        "schema": payload.get("tables", []),
                        "error": payload.get("error"),
                    },
                    project_id=project_id,
                )
                counts["databases"] += int(added)
            if path.stem not in transcript_session_ids:
                start_idx = validated_antigravity_database_resume_index(
                    database_path,
                    checkpoint.get("cursor") if checkpoint else None,
                    immutable=immutable,
                )
                batch = []
                for record in parse_antigravity_database(
                    database_path, start_idx=start_idx, immutable=immutable
                ):
                    if (
                        analysis_lane == "profile_only"
                        and record["kind"] not in {"visible_message", "tool_metadata"}
                    ):
                        continue
                    record_payload = dict(record)
                    cursor = record_payload.pop("_cursor", record["source_ref"])
                    record_payload["analysis_lane"] = analysis_lane
                    record_payload["attribution_status"] = attribution_status
                    batch.append(
                        {
                            "source_type": "antigravity",
                            "source_ref": record["source_ref"],
                            "kind": record["kind"],
                            "payload": record_payload,
                            "project_id": project_id,
                            "occurred_at": record.get("timestamp"),
                            "cursor": cursor,
                        }
                    )
                results = store.add_evidence_batch(
                    batch, source_key=source_key, fingerprint=fingerprint
                )
                counts["database_messages"] += sum(
                    int(added) for _evidence_id, added in results
                )
        store.mark_session_source_ingested(source_key, fingerprint=fingerprint)
        store.set_collection_receipt(source_key, fingerprint)
    attribution = reconcile_existing_session_attribution(store)
    counts.update({f"index_{key}": value for key, value in attribution.items()})
    return counts


def export_project_inventory(store: StateStore, destination: Path) -> None:
    safe = []
    for project in store.projects():
        item = {
            key: value
            for key, value in project.items()
            if key not in {"local_path", "manifest"}
        }
        safe.append(item)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(safe, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
