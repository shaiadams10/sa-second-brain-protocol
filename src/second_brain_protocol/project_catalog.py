from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from .markdown import GeneratedSectionError, slugify, update_generated_file
from .security import sanitize_text


PROJECT_INDEX_SECTION = "projects-index"
PROJECT_INVENTORY_SECTION = "inventory"
PROJECT_KNOWLEDGE_SECTION = "canonical"


def _is_collection(project: dict[str, Any]) -> bool:
    return project.get("classification") == "collection" or bool(
        project.get("child_project_ids")
    )


def _is_duplicate(project: dict[str, Any]) -> bool:
    return project.get("classification") == "duplicate" or bool(
        project.get("canonical_project_id")
    )


def _has_project_content(project: dict[str, Any]) -> bool:
    return bool(
        project.get("head_commit")
        or project.get("initial_commit")
        or project.get("remote_url")
        or int(project.get("tracked_file_count") or 0) > 0
        or project.get("tech_stack")
        or project.get("second_brain_files")
    )


def catalog_groups(
    projects: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Separate real leaf projects, collection containers, and empty folders."""

    unique: dict[str, dict[str, Any]] = {}
    for project in projects:
        project_id = str(project.get("id") or "")
        if not project_id or _is_duplicate(project):
            continue
        unique[project_id] = project
    collections = [item for item in unique.values() if _is_collection(item)]
    leaf = [item for item in unique.values() if not _is_collection(item)]
    actual = [item for item in leaf if _has_project_content(item)]
    folders = [item for item in leaf if not _has_project_content(item)]
    def key(item: dict[str, Any]) -> tuple[str, str]:
        return str(item.get("name") or "").casefold(), str(item["id"])

    return sorted(actual, key=key), sorted(collections, key=key), sorted(folders, key=key)


def project_note_paths(projects: list[dict[str, Any]]) -> dict[str, str]:
    """Return deterministic, collision-safe note stems for current projects."""

    result: dict[str, str] = {}
    used: dict[str, str] = {}
    for project in sorted(
        projects,
        key=lambda item: (str(item.get("name") or "").casefold(), str(item["id"])),
    ):
        project_id = str(project["id"])
        base = slugify(str(project.get("name") or project_id))
        stem = base
        if stem in used and used[stem] != project_id:
            stem = f"{base}-{slugify(project_id)[-8:]}"
        used[stem] = project_id
        result[project_id] = stem
    return result


def _collection_memberships(
    collections: list[dict[str, Any]],
) -> dict[str, list[str]]:
    memberships: dict[str, list[str]] = {}
    for collection in collections:
        name = sanitize_text(str(collection.get("name") or "Collection"), max_chars=120)
        for project_id in collection.get("child_project_ids", []):
            memberships.setdefault(str(project_id), []).append(name)
    for names in memberships.values():
        names.sort(key=str.casefold)
    return memberships


def _project_suffix(project: dict[str, Any], parents: list[str]) -> str:
    details: list[str] = []
    if parents:
        details.append("in " + ", ".join(parents))
    classification = str(project.get("classification") or "")
    if classification in {"third-party", "reference"}:
        details.append("external/reference")
    elif classification in {"fork", "modified-fork"}:
        details.append("fork")
    if str(project.get("lifecycle") or "") == "archived":
        details.append("archived")
    return " — " + "; ".join(details) if details else ""


def render_project_catalog(projects: list[dict[str, Any]]) -> str:
    actual, collections, folders = catalog_groups(projects)
    note_paths = project_note_paths(actual)
    memberships = _collection_memberships(collections)
    lines = [
        "## Projects",
        "",
        "Current project folders with detected project content. Names and links come from the deterministic scanner.",
        "",
    ]
    if actual:
        for project in actual:
            project_id = str(project["id"])
            name = sanitize_text(str(project.get("name") or project_id), max_chars=120)
            suffix = _project_suffix(project, memberships.get(project_id, []))
            lines.append(f"- [[Projects/{note_paths[project_id]}|{name}]]{suffix}")
    else:
        lines.append("_No projects detected._")

    lines.extend(
        [
            "",
            "## Collections",
            "",
            "Grouping folders used to organize projects. These are not counted as projects.",
            "",
        ]
    )
    if collections:
        for collection in collections:
            name = sanitize_text(
                str(collection.get("name") or "Collection"), max_chars=120
            )
            child_count = len(set(collection.get("child_project_ids", [])))
            noun = "project" if child_count == 1 else "projects"
            lines.append(f"- **{name}** — {child_count} {noun}")
    else:
        lines.append("_No collection folders detected._")

    if folders:
        lines.extend(
            [
                "",
                "## Other folders detected",
                "",
                "Folders with no detectable project files. They remain visible for review but are not counted as projects.",
                "",
            ]
        )
        for folder in folders:
            name = sanitize_text(str(folder.get("name") or "Folder"), max_chars=120)
            lines.append(f"- `{name}`")
    return "\n".join(lines)


def _ensure_project_index(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        "id: projects-index\n"
        "type: index\n"
        "status: active\n"
        "aliases: []\n"
        "confidence: 0\n"
        "provenance: []\n"
        "first_seen: null\n"
        "last_verified: null\n"
        "tags: [project, index]\n"
        "permalink: personal-vault/projects/index\n"
        "---\n\n"
        "# Projects\n\n"
        f"<!-- sb:generated {PROJECT_INDEX_SECTION}:start -->\n"
        "_No generated content yet._\n"
        f"<!-- sb:generated {PROJECT_INDEX_SECTION}:end -->\n",
        encoding="utf-8",
    )


def _project_inventory_body(
    project: dict[str, Any], parents: list[str]
) -> str:
    classification = sanitize_text(
        str(project.get("classification") or "unclassified"), max_chars=60
    )
    lifecycle = sanitize_text(str(project.get("lifecycle") or "active"), max_chars=60)
    lines = [
        "- Catalog status: project",
        f"- Classification: {classification}",
        f"- Lifecycle: {lifecycle}",
        f"- Tracked files: {int(project.get('tracked_file_count') or 0)}",
    ]
    if parents:
        lines.append(f"- Collection: {', '.join(parents)}")
    return "\n".join(lines)


def _ensure_project_note(
    path: Path, project: dict[str, Any], parents: list[str]
) -> None:
    inventory_start = f"<!-- sb:generated {PROJECT_INVENTORY_SECTION}:start -->"
    inventory_end = f"<!-- sb:generated {PROJECT_INVENTORY_SECTION}:end -->"
    knowledge_start = f"<!-- sb:generated {PROJECT_KNOWLEDGE_SECTION}:start -->"
    knowledge_end = f"<!-- sb:generated {PROJECT_KNOWLEDGE_SECTION}:end -->"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        today = date.today().isoformat()
        name = sanitize_text(str(project.get("name") or project["id"]), max_chars=120)
        path.write_text(
            "---\n"
            f"id: project-{project['id']}\n"
            "type: project\n"
            "aliases: []\n"
            "confidence: 0\n"
            "provenance: []\n"
            f"first_seen: {today}\n"
            f"last_verified: {today}\n"
            "tags: [project, second-brain]\n"
            "---\n\n"
            f"# {name}\n\n"
            f"{inventory_start}\n_No inventory yet._\n{inventory_end}\n\n"
            f"{knowledge_start}\n_No synthesized project knowledge yet._\n{knowledge_end}\n\n"
            "## Manual notes\n\n",
            encoding="utf-8",
        )
    current = path.read_text(encoding="utf-8")
    expected_id = f"id: project-{project['id']}"
    if expected_id not in current:
        raise GeneratedSectionError(
            f"Project note path collision at {path}; expected {expected_id}"
        )
    if inventory_start not in current or inventory_end not in current:
        insertion = (
            f"{inventory_start}\n_No inventory yet._\n{inventory_end}\n\n"
        )
        if knowledge_start in current:
            current = current.replace(knowledge_start, insertion + knowledge_start, 1)
        elif "## Manual notes" in current:
            current = current.replace("## Manual notes", insertion + "## Manual notes", 1)
        else:
            current = current.rstrip() + "\n\n" + insertion
        path.write_text(current, encoding="utf-8")
    if knowledge_start not in current or knowledge_end not in current:
        raise GeneratedSectionError(
            f"Project note lacks a valid knowledge section: {path}"
        )
    update_generated_file(
        path, PROJECT_INVENTORY_SECTION, _project_inventory_body(project, parents)
    )


def publish_project_catalog(
    vault: Path, projects: list[dict[str, Any]]
) -> dict[str, Any]:
    """Rebuild the bounded canonical project index from scanner truth."""

    actual, collections, folders = catalog_groups(projects)
    memberships = _collection_memberships(collections)
    note_paths = project_note_paths(actual)
    index_path = vault / "Projects" / "Index.md"
    _ensure_project_index(index_path)
    before = index_path.read_text(encoding="utf-8")
    for project in actual:
        project_id = str(project["id"])
        _ensure_project_note(
            vault / "Projects" / f"{note_paths[project_id]}.md",
            project,
            memberships.get(project_id, []),
        )
    update_generated_file(index_path, PROJECT_INDEX_SECTION, render_project_catalog(projects))
    after = index_path.read_text(encoding="utf-8")
    return {
        "path": str(index_path),
        "changed": before != after,
        "projects": len(actual),
        "collections": len(collections),
        "other_folders": len(folders),
    }


def project_catalog_health(
    vault: Path, projects: list[dict[str, Any]]
) -> dict[str, Any]:
    actual, collections, folders = catalog_groups(projects)
    path = vault / "Projects" / "Index.md"
    result = {
        "ok": False,
        "projects": len(actual),
        "collections": len(collections),
        "other_folders": len(folders),
        "reason": "Project index is missing",
    }
    if not path.is_file():
        return result
    text = path.read_text(encoding="utf-8")
    start = f"<!-- sb:generated {PROJECT_INDEX_SECTION}:start -->"
    end = f"<!-- sb:generated {PROJECT_INDEX_SECTION}:end -->"
    if text.count(start) != 1 or text.count(end) != 1:
        result["reason"] = "Project index generated markers are malformed"
        return result
    body = text.split(start, 1)[1].split(end, 1)[0].strip()
    expected = render_project_catalog(projects).strip()
    result["ok"] = body == expected
    result["reason"] = "Project catalog matches current scanner state" if result["ok"] else (
        "Project catalog does not match the current runtime project registry"
    )
    return result
