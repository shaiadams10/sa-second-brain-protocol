from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .basic_memory_integration import search as memory_search
from .config import RuntimePaths
from .graphify_integration import graph_summary, query_graph
from .publisher import publish_skill_update
from .security import sanitize_text
from .state import StateStore, canonical_hash


SESSION_SURFACES = {"codex", "antigravity"}


def _clean_user_message(value: Any) -> str:
    text = sanitize_text(str(value or ""), max_chars=2000).strip()
    match = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", text, re.DOTALL)
    return sanitize_text(match.group(1), max_chars=2000).strip() if match else text


def configure_session_project_link(
    paths: RuntimePaths,
    store: StateStore,
    *,
    surface: str,
    session_id: str,
    project: str,
    confirm: bool = False,
) -> dict[str, Any]:
    """Preview or persist one owner-confirmed session attribution override."""

    normalized_surface = surface.strip().casefold()
    if normalized_surface not in SESSION_SURFACES:
        raise ValueError("Surface must be codex or antigravity")
    normalized_session = session_id.strip()
    indexed = [
        item
        for item in store.session_project_index()
        if str(item["surface"]) == normalized_surface
        and str(item["session_id"]) == normalized_session
    ]
    if len(indexed) != 1:
        raise RuntimeError(
            f"Session lookup returned {len(indexed)} exact matches for this identifier."
        )

    needle = project.strip().casefold()
    candidates = [
        item
        for item in store.present_projects()
        if item.get("classification") not in {"collection", "duplicate"}
        and needle
        in {
            str(item["id"]).casefold(),
            str(item.get("name") or item.get("logical_name") or "").casefold(),
        }
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            f"Project lookup returned {len(candidates)} exact matches for {project!r}."
        )
    target = candidates[0]
    projects_by_id = {str(item["id"]): item for item in store.projects()}
    current_id = str(indexed[0].get("project_id") or "") or None
    current = projects_by_id.get(current_id or "")
    result = {
        "status": "linked" if confirm else "preview",
        "surface": normalized_surface,
        "session_id": normalized_session,
        "current_project": (
            str(current.get("name") or current_id) if current_id else None
        ),
        "target_project": str(target.get("name") or target["id"]),
        "changes_attribution": current_id != str(target["id"]),
        "requires_reconciliation": True,
    }
    if not confirm:
        return result

    try:
        config = json.loads(paths.config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Runtime configuration is unavailable: {error}") from error
    overrides = config.get("session_project_overrides")
    if not isinstance(overrides, dict):
        overrides = {}
    overrides[f"{normalized_surface}:{normalized_session}"] = str(target["id"])
    config["session_project_overrides"] = dict(
        sorted(overrides.items(), key=lambda item: item[0].casefold())
    )
    paths.config.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return result


def remember_explicit_skill(
    vault: Path,
    store: StateStore,
    *,
    skill_id: str,
    name: str,
    claim: str,
    supporting_evidence: list[str] | None = None,
    successful_implementation: bool = False,
) -> dict[str, Any]:
    """Publish one explicit owner-confirmed private capability."""

    clean_name = sanitize_text(name, max_chars=160).strip()
    clean_claim = sanitize_text(claim, max_chars=1200).strip()
    if not clean_name or not clean_claim:
        raise ValueError("Skill name and claim are required")
    supporting = sorted(set(supporting_evidence or []))
    known = {item["id"] for item in store.evidence_by_ids(supporting)}
    unknown = set(supporting) - known
    if unknown:
        raise ValueError(f"Unknown supporting evidence: {sorted(unknown)}")
    statement_ref = (
        "owner-skill:"
        + canonical_hash({"skill_id": skill_id, "claim": clean_claim})[:24]
    )
    owner_evidence, _added = store.add_evidence(
        source_type="interview",
        source_ref=statement_ref,
        kind="explicit_fact",
        payload={
            "role": "user",
            "text": clean_claim,
            "explicit": True,
            "scope": "global",
        },
    )
    evidence_refs = [owner_evidence, *supporting]
    result = publish_skill_update(
        vault,
        store,
        {
            "skill_id": skill_id,
            "name": clean_name,
            "claim": clean_claim,
            "evidence_refs": evidence_refs,
            "authorship_confirmed": True,
            "successful_implementation": successful_implementation,
            "confidence": 1.0,
        },
    )
    store.mark_evidence([owner_evidence], "processed")
    return {**result, "skill": clean_name, "evidence_refs": evidence_refs}


def session_index_summary(store: StateStore) -> dict[str, Any]:
    """Summarize metadata-only session routing without exposing workspace paths."""

    rows = store.session_project_index()
    projects = {
        str(project["id"]): str(
            project.get("name") or project.get("logical_name") or project["id"]
        )
        for project in store.present_projects()
    }
    by_status = {
        status: sum(row["status"] == status for row in rows)
        for status in ("matched", "unmatched", "ambiguous")
    }
    by_surface: dict[str, dict[str, int]] = {}
    by_project: dict[str, dict[str, Any]] = {}
    for row in rows:
        surface = str(row["surface"])
        surface_counts = by_surface.setdefault(
            surface, {"matched": 0, "unmatched": 0, "ambiguous": 0}
        )
        surface_counts[str(row["status"])] += 1
        project_id = str(row.get("project_id") or "")
        if row["status"] != "matched" or not project_id:
            continue
        project_counts = by_project.setdefault(
            project_id,
            {
                "project_id": project_id,
                "project": projects.get(project_id, project_id),
                "sessions": 0,
                "by_surface": {},
            },
        )
        project_counts["sessions"] += 1
        project_counts["by_surface"][surface] = (
            int(project_counts["by_surface"].get(surface, 0)) + 1
        )

    sources = store.session_sources()
    return {
        "sessions_indexed": len(rows),
        **by_status,
        "by_surface": by_surface,
        "matched_projects": sorted(
            by_project.values(),
            key=lambda item: (-int(item["sessions"]), str(item["project"]).casefold()),
        ),
        "source_files": {
            "indexed": len(sources),
            "matched": sum(source.get("status") == "matched" for source in sources),
            "fully_ingested": sum(
                source.get("status") == "matched" and bool(source.get("ingested"))
                for source in sources
            ),
            "skipped": sum(source.get("status") != "matched" for source in sources),
        },
    }


def latest_sessions(
    store: StateStore,
    *,
    surface: str | None = None,
    project: str | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Return a bounded local view of recent sessions without publishing raw chat."""

    normalized_surface = str(surface or "").strip().casefold() or None
    if normalized_surface and normalized_surface not in SESSION_SURFACES:
        raise ValueError("Surface must be codex or antigravity")
    if limit < 1 or limit > 50:
        raise ValueError("Limit must be between 1 and 50")

    projects = store.present_projects()
    projects_by_id = {str(item["id"]): item for item in projects}
    indexed_sessions = {
        (str(item["surface"]), str(item["session_id"])): item
        for item in store.session_project_index()
    }
    index_active = bool(indexed_sessions)
    project_ids: set[str] | None = None
    if project:
        needle = str(project).strip().casefold()
        project_ids = {
            str(item["id"])
            for item in projects
            if needle
            in {
                str(item["id"]).casefold(),
                str(item.get("name") or item.get("logical_name") or "").casefold(),
            }
        }
        if not project_ids:
            return []

    with store.connect() as connection:
        rows = connection.execute(
            """SELECT id,project_id,occurred_at,created_at,payload_json,status
            FROM evidence
            WHERE source_type='session-digest' AND kind='session_digest'
            AND status NOT IN ('superseded','compacted')
            ORDER BY COALESCE(json_extract(payload_json,'$.ended_at'),occurred_at,created_at) DESC,
            created_at DESC,id DESC"""
        ).fetchall()

    candidates: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        row_surface = str(payload.get("source") or "").casefold()
        if row_surface not in SESSION_SURFACES:
            continue
        session_id = str(payload.get("session_id") or "")
        if row_surface == "antigravity" and session_id.casefold() in {
            "transcript.jsonl",
            "transcript_full.jsonl",
        }:
            # Collector v2 could mistake the filename for the trajectory ID and
            # merge unrelated exports. Keep the audit row, but never expose it
            # as a real session.
            continue
        if normalized_surface and row_surface != normalized_surface:
            continue
        if index_active:
            assignment = indexed_sessions.get((row_surface, session_id))
            if (
                assignment is None
                or assignment.get("status") != "matched"
                or not assignment.get("project_id")
            ):
                continue
            row_project_ids = {str(assignment["project_id"])}
        else:
            row_project_ids = {
                str(value) for value in payload.get("project_ids", []) if value
            }
            if row["project_id"]:
                row_project_ids.add(str(row["project_id"]))
        if project_ids is not None and not (row_project_ids & project_ids):
            continue
        user_messages = [
            item for item in payload.get("user_messages", []) if isinstance(item, dict)
        ]
        last_user = user_messages[-1] if user_messages else {}
        signature_payload = {
            "surface": row_surface,
            "started_at": payload.get("started_at") or row["occurred_at"],
            "ended_at": payload.get("ended_at") or row["occurred_at"],
            "user_messages": [item.get("text") for item in user_messages],
        }
        signature = hashlib.sha256(
            json.dumps(signature_payload, ensure_ascii=False, sort_keys=True).encode(
                "utf-8"
            )
        ).hexdigest()
        candidates.append(
            {
                "dedupe_key": (
                    f"{row_surface}:{session_id}" if index_active else signature
                ),
                "signature": signature,
                "surface": row_surface,
                "session_id": session_id,
                "started_at": payload.get("started_at") or row["occurred_at"],
                "ended_at": payload.get("ended_at") or row["occurred_at"],
                "project_ids": row_project_ids,
                "projects": sorted(
                    {
                        str(
                            projects_by_id[value].get("name")
                            or projects_by_id[value].get("logical_name")
                            or value
                        )
                        for value in row_project_ids
                        if value in projects_by_id
                    },
                    key=str.casefold,
                ),
                "last_user_message": _clean_user_message(last_user.get("text")),
                "last_user_message_at": last_user.get("occurred_at"),
            }
        )

    # Old Antigravity exports may have produced transcript.jsonl and
    # transcript_full.jsonl digests for the same turns. Prefer the attributed
    # or UUID-identified record while returning only one conversation.
    deduplicated: dict[str, dict[str, Any]] = {}
    for item in candidates:
        key = str(item["dedupe_key"])
        existing = deduplicated.get(key)
        if existing is None:
            deduplicated[key] = item
            continue
        existing_score = (
            bool(existing["last_user_message"]),
            bool(existing["project_ids"]),
            bool(re.fullmatch(r"[0-9a-f-]{36}", existing["session_id"], re.I)),
        )
        item_score = (
            bool(item["last_user_message"]),
            bool(item["project_ids"]),
            bool(re.fullmatch(r"[0-9a-f-]{36}", item["session_id"], re.I)),
        )
        if item_score > existing_score:
            deduplicated[key] = item

    result = sorted(
        deduplicated.values(),
        key=lambda item: str(item.get("ended_at") or item.get("started_at") or ""),
        reverse=True,
    )[:limit]
    for item in result:
        item.pop("signature", None)
        item.pop("dedupe_key", None)
        item.pop("project_ids", None)
        item.pop("session_id", None)
        item["attributed"] = bool(item["projects"])
    return result


def _search_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_search_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_search_text(item) for item in value)
    return str(value) if isinstance(value, (str, int, float)) else ""


def _normalized_search_text(value: Any) -> str:
    return re.sub(r"\s+", " ", _search_text(value)).strip().casefold()


def _filter_pending_tombstones(
    store: StateStore, results: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if int(store.search_refresh_status().get("pending") or 0) == 0:
        return results
    tombstones = [
        (str(item["id"]).casefold(), _normalized_search_text(item.get("claim") or ""))
        for item in store.observations("rejected")
        if item.get("claim")
    ]
    if not tombstones:
        return results
    filtered = []
    for result in results:
        text = _normalized_search_text(result)
        if any(
            observation_id in text or (claim and claim in text)
            for observation_id, claim in tombstones
        ):
            continue
        filtered.append(result)
    return filtered


def read_note(vault: Path, note: str) -> str:
    candidate = (vault / note).resolve()
    candidate.relative_to(vault.resolve())
    if candidate.suffix.lower() != ".md" or not candidate.is_file():
        raise FileNotFoundError(note)
    return candidate.read_text(encoding="utf-8")


def search(
    paths: RuntimePaths, vault: Path, query: str, *, limit: int = 10
) -> list[dict[str, Any]]:
    store = StateStore(paths.state)
    results = _filter_pending_tombstones(
        store, memory_search(paths, query, limit=limit)
    )
    if results:
        return results
    lowered = query.casefold()
    fallback = []
    for path in vault.rglob("*.md"):
        if ".git" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if lowered in text.casefold() or lowered in path.stem.casefold():
            fallback.append(
                {"path": path.relative_to(vault).as_posix(), "excerpt": text[:600]}
            )
        if len(fallback) >= limit:
            break
    return fallback


def recent_activity(vault: Path, *, days: int = 7) -> list[dict[str, str]]:
    cutoff = date.today() - timedelta(days=days)
    rows = []
    for folder in (vault / "Journal" / "Daily", vault / "Journal" / "Weekly"):
        for path in sorted(folder.glob("*.md"), reverse=True):
            if date.fromtimestamp(path.stat().st_mtime) >= cutoff:
                rows.append(
                    {
                        "note": path.relative_to(vault).as_posix(),
                        "content": path.read_text(encoding="utf-8"),
                    }
                )
    return rows


def build_context(
    paths: RuntimePaths, vault: Path, query: str, *, limit: int = 8
) -> str:
    return json.dumps(
        search(paths, vault, query, limit=limit), indent=2, ensure_ascii=False
    )


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


def trace_project_path(
    paths: RuntimePaths, project_id: str, source: str, target: str
) -> str:
    return query_project_graph(
        paths, project_id, f"Find the shortest path from {source} to {target}"
    )


def _project_graph(paths: RuntimePaths, project_id: str) -> Path:
    if not project_id or any(
        char not in "abcdefghijklmnopqrstuvwxyz0123456789-"
        for char in project_id.lower()
    ):
        raise ValueError("Invalid project id")
    graph = (paths.graphify / project_id / "graph.json").resolve()
    graph.relative_to(paths.graphify.resolve())
    return graph
