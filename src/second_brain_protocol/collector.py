from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .activity import build_project_delta
from .scanner import ProjectScanner
from .sessions import (
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


ANTIGRAVITY_TRANSCRIPT_COLLECTOR_VERSION = 2
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
    with tempfile.TemporaryDirectory(prefix=f"{path.stem[:12]}-", dir=snapshot_root) as temp:
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


def _project_for_record(record: dict[str, Any], projects: list[dict[str, Any]]) -> str | None:
    by_cwd = _project_for_cwd(record.get("cwd"), projects)
    if by_cwd:
        return by_cwd
    text = str(record.get("text") or "").casefold()
    matches = []
    for project in projects:
        name = project["name"].strip().casefold()
        if len(name) < 5 or name in {"random", "playground"}:
            continue
        pattern = rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])"
        if re.search(pattern, text):
            matches.append(project)
    if not matches:
        return None
    return max(matches, key=lambda item: len(item["name"]))["id"]


def reconcile_existing_session_attribution(store: StateStore) -> dict[str, int]:
    """Repair legacy text-fallback assignments after resolver rule changes."""
    projects = store.projects()
    raw_rows = [
        row
        for row in store.evidence()
        if row["source_type"] in {"codex", "antigravity"}
        and row["kind"] in {"visible_message", "tool_metadata", "artifact"}
    ]
    raw_reassigned = 0
    for row in raw_rows:
        if row.get("project_id") is None:
            continue
        expected = _project_for_record(row["payload"], projects)
        if expected != row.get("project_id"):
            store.reassign_evidence_project(row["id"], expected)
            row["project_id"] = expected
            raw_reassigned += 1

    raw_by_id = {row["id"]: row for row in raw_rows}
    digests_reassigned = 0
    for digest in [
        row
        for row in store.evidence()
        if row["source_type"] == "session-digest" and row["kind"] == "session_digest"
    ]:
        source_ids = digest["payload"].get("source_evidence_ids", [])
        project_ids = sorted(
            {
                raw_by_id[source_id]["project_id"]
                for source_id in source_ids
                if source_id in raw_by_id and raw_by_id[source_id].get("project_id")
            }
        )
        expected = project_ids[0] if len(project_ids) == 1 else None
        if expected != digest.get("project_id"):
            store.reassign_evidence_project(digest["id"], expected)
            digests_reassigned += 1
    return {
        "raw_reassigned": raw_reassigned,
        "digests_reassigned": digests_reassigned,
    }


def collect_projects(
    store: StateStore,
    *,
    projects_root: Path,
    defaults: dict[str, Any],
    ignored_paths: list[Path] | None = None,
    classification_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scanner = ProjectScanner(projects_root, defaults, tuple(ignored_paths or ()))
    projects = scanner.scan_all()
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
    previous_projects = {item["id"]: item for item in store.projects()}
    changed: list[str] = []
    delta_count = 0
    for project in projects:
        previous = previous_projects.get(project["id"])
        presence = store.project_presence(project["id"])
        delta = build_project_delta(
            previous,
            project,
            was_present=bool(presence["present"]) if presence is not None else None,
        )
        store.upsert_project(project)
        store.set_project_presence(project["id"], present=True)
        if not previous or previous.get("fingerprint") != project.get("fingerprint"):
            changed.append(project["id"])
        public_payload = {key: value for key, value in project.items() if key not in {"local_path", "manifest"}}
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
        delta = build_project_delta(
            previous,
            None,
            was_present=bool(presence["present"]) if presence is not None else None,
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
    projects = store.projects()
    counts = {
        "codex": 0,
        "antigravity": 0,
        "artifacts": 0,
        "databases": 0,
        "database_messages": 0,
    }
    codex_roots = [Path(config["codex_sessions"]), Path(config["codex_archived_sessions"])]
    for path in discover_codex_sessions(codex_roots):
        fingerprint = _file_fingerprint(path)
        source_key = _source_key("codex-file:", path)
        checkpoint = store.checkpoint(source_key)
        if (checkpoint and checkpoint.get("fingerprint") == fingerprint) or _already_collected(
            store, source_key, fingerprint
        ):
            continue
        start_offset = validated_jsonl_resume_offset(
            path, checkpoint.get("cursor") if checkpoint else None
        )
        for record in parse_codex_jsonl(path, start_offset=start_offset):
            payload = dict(record)
            cursor = payload.pop("_cursor", record["source_ref"])
            project_id = _project_for_record(payload, projects)
            evidence_id, added = store.add_evidence(
                source_type="codex",
                source_ref=record["source_ref"],
                kind=record["kind"],
                payload=payload,
                project_id=project_id,
                occurred_at=record.get("timestamp"),
            )
            store.attach_checkpoint_candidate(
                evidence_id, source_key=source_key, cursor=cursor, fingerprint=fingerprint
            )
            counts["codex"] += int(added)
        store.set_collection_receipt(source_key, fingerprint)

    sources = discover_antigravity_sources(
        Path(config["antigravity_brain"]), Path(config["antigravity_conversations"])
    )
    transcript_session_ids = {
        next((part for part in reversed(path.parts) if len(part) >= 20), path.stem)
        for path in sources["transcripts"]
    }
    for path in sources["transcripts"]:
        fingerprint = _file_fingerprint(path)
        source_key = _source_key(
            f"antigravity-file-v{ANTIGRAVITY_TRANSCRIPT_COLLECTOR_VERSION}:", path
        )
        checkpoint = store.checkpoint(source_key)
        if (checkpoint and checkpoint.get("fingerprint") == fingerprint) or _already_collected(
            store, source_key, fingerprint
        ):
            continue
        start_offset = validated_jsonl_resume_offset(
            path, checkpoint.get("cursor") if checkpoint else None
        )
        for record in parse_antigravity_transcript(path, start_offset=start_offset):
            payload = dict(record)
            cursor = payload.pop("_cursor", record["source_ref"])
            project_id = _project_for_record(payload, projects)
            evidence_id, added = store.add_evidence(
                source_type="antigravity",
                source_ref=record["source_ref"],
                kind=record["kind"],
                payload=payload,
                project_id=project_id,
                occurred_at=record.get("timestamp"),
            )
            store.attach_checkpoint_candidate(
                evidence_id, source_key=source_key, cursor=cursor, fingerprint=fingerprint
            )
            counts["antigravity"] += int(added)
        store.set_collection_receipt(source_key, fingerprint)

    for path in sources["artifacts"]:
        fingerprint = _file_fingerprint(path)
        source_key = _source_key("antigravity-artifact:", path)
        if _already_collected(store, source_key, fingerprint):
            continue
        record = parse_antigravity_artifact(path)
        if record:
            project_id = _project_for_record(record, projects)
            _, added = store.add_evidence(
                source_type="antigravity",
                source_ref=record["source_ref"],
                kind=record["kind"],
                payload=record,
                project_id=project_id,
            )
            counts["artifacts"] += int(added)
        store.set_collection_receipt(source_key, fingerprint)

    for path in sources["databases"]:
        fingerprint = _database_fingerprint(path)
        source_key = _source_key(
            f"antigravity-database-v{ANTIGRAVITY_DATABASE_COLLECTOR_VERSION}:",
            path,
        )
        checkpoint = store.checkpoint(source_key)
        if (checkpoint and checkpoint.get("fingerprint") == fingerprint) or _already_collected(
            store, source_key, fingerprint
        ):
            continue
        with _database_read_snapshot(path, Path(config["runtime_root"])) as (
            database_path,
            immutable,
        ):
            payload = inspect_antigravity_database(
                database_path, immutable=immutable
            )
            _, added = store.add_evidence(
                source_type="antigravity",
                source_ref="antigravity-db-schema:" + hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:24],
                kind="database_inventory",
                payload={"database": path.name, "schema": payload.get("tables", []), "error": payload.get("error")},
            )
            counts["databases"] += int(added)
            if path.stem not in transcript_session_ids:
                start_idx = validated_antigravity_database_resume_index(
                    database_path,
                    checkpoint.get("cursor") if checkpoint else None,
                    immutable=immutable,
                )
                for record in parse_antigravity_database(
                    database_path, start_idx=start_idx, immutable=immutable
                ):
                    record_payload = dict(record)
                    cursor = record_payload.pop("_cursor", record["source_ref"])
                    project_id = _project_for_record(record_payload, projects)
                    evidence_id, record_added = store.add_evidence(
                        source_type="antigravity",
                        source_ref=record["source_ref"],
                        kind=record["kind"],
                        payload=record_payload,
                        project_id=project_id,
                        occurred_at=record.get("timestamp"),
                    )
                    store.attach_checkpoint_candidate(
                        evidence_id,
                        source_key=source_key,
                        cursor=cursor,
                        fingerprint=fingerprint,
                    )
                    counts["database_messages"] += int(record_added)
        store.set_collection_receipt(source_key, fingerprint)
    return counts


def export_project_inventory(store: StateStore, destination: Path) -> None:
    safe = []
    for project in store.projects():
        item = {key: value for key, value in project.items() if key not in {"local_path", "manifest"}}
        safe.append(item)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(safe, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
