from pathlib import Path

from second_brain_protocol.project_catalog import (
    project_catalog_health,
    publish_project_catalog,
)
from second_brain_protocol.publisher import publish_model_output
from second_brain_protocol.state import StateStore


def _project(
    project_id: str,
    name: str,
    *,
    classification: str = "review",
    tracked: int = 1,
    children: list[str] | None = None,
) -> dict:
    return {
        "id": project_id,
        "name": name,
        "classification": classification,
        "classification_reasons": ["test"],
        "lifecycle": "active",
        "tracked_file_count": tracked,
        "tech_stack": [],
        "second_brain_files": [],
        "child_project_ids": children or [],
        "fingerprint": f"fingerprint-{project_id}",
        "remote_url": None,
        "initial_commit": None,
        "head_commit": None,
    }


def _index(vault: Path) -> Path:
    path = vault / "Projects" / "Index.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\nid: projects-index\ntype: index\n---\n\n"
        "# Projects\n\n"
        "<!-- sb:generated projects-index:start -->\n"
        "- stale model alias\n"
        "<!-- sb:generated projects-index:end -->\n\n"
        "## Manual notes\n\nKeep this sentence.\n",
        encoding="utf-8",
    )
    return path


def test_project_catalog_rebuilds_from_scanner_truth_and_separates_folders(
    tmp_path: Path,
) -> None:
    path = _index(tmp_path)
    home = _project("folder-home", "HomeDrop", tracked=8)
    collection = _project(
        "folder-utils",
        "Utilities & Automation",
        classification="collection",
        tracked=0,
        children=[home["id"]],
    )
    empty = _project("folder-src", "src", tracked=0)
    duplicate = {
        **_project("duplicate-home", "Home Drop Alias"),
        "classification": "duplicate",
        "canonical_project_id": home["id"],
    }
    projects = [duplicate, empty, collection, home]

    result = publish_project_catalog(tmp_path, projects)

    text = path.read_text(encoding="utf-8")
    assert result == {
        "path": str(path),
        "changed": True,
        "projects": 1,
        "collections": 1,
        "other_folders": 1,
    }
    assert "[[Projects/homedrop|HomeDrop]] — in Utilities & Automation" in text
    assert "**Utilities & Automation** — 1 project" in text
    assert "`src`" in text
    assert "Home Drop Alias" not in text
    assert "stale model alias" not in text
    assert "Keep this sentence." in text
    assert "folder-home" not in text
    dossier = tmp_path / "Projects" / "homedrop.md"
    assert dossier.is_file()
    dossier_text = dossier.read_text(encoding="utf-8")
    assert "Catalog status: project" in dossier_text
    assert "Collection: Utilities & Automation" in dossier_text
    assert "sb:generated canonical:start" in dossier_text
    assert project_catalog_health(tmp_path, projects)["ok"] is True


def test_model_project_update_uses_scanner_name_without_appending_alias(
    tmp_path: Path,
) -> None:
    _index(tmp_path)
    store = StateStore(tmp_path / "state.sqlite")
    home = _project("folder-home", "HomeDrop", tracked=8)
    store.upsert_project(home)
    store.set_project_presence(home["id"], present=True)
    publish_project_catalog(tmp_path, [home])
    evidence_id, _ = store.add_evidence(
        source_type="filesystem",
        source_ref="project-inventory:home",
        kind="project_inventory",
        payload={"name": "HomeDrop"},
        project_id=home["id"],
    )

    result = publish_model_output(
        vault=tmp_path,
        store=store,
        output={
            "summary": "HomeDrop changed.",
            "observations": [],
            "project_updates": [
                {
                    "project_id": home["id"],
                    "name": "Home Drop Utility Alias",
                    "summary": "Added a local file-sharing workflow.",
                    "evidence_refs": [evidence_id],
                }
            ],
            "skill_updates": [],
            "voice_samples": [],
            "review_items": [],
        },
        run_kind="bootstrap",
        evidence_ids=[evidence_id],
    )

    assert result["projects_written"] == 1
    assert "Added a local file-sharing workflow." in (
        tmp_path / "Projects" / "homedrop.md"
    ).read_text(encoding="utf-8")
    assert not (tmp_path / "Projects" / "home-drop-utility-alias.md").exists()
    index = (tmp_path / "Projects" / "Index.md").read_text(encoding="utf-8")
    assert "Home Drop Utility Alias" not in index
