from pathlib import Path

from second_brain_protocol import project_rebuild
from second_brain_protocol.config import (
    RuntimePaths,
    default_runtime_config,
    load_defaults,
    setup_runtime,
)
from second_brain_protocol.project_catalog import catalog_groups
from second_brain_protocol.project_rebuild import rebuild_project_subsystem
from second_brain_protocol.state import StateStore


def _generated_note(path: Path, title: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f"id: {title.casefold().replace(' ', '-')}\n"
        "type: note\n"
        "---\n\n"
        f"# {title}\n\n"
        "<!-- sb:generated canonical:start -->\n"
        f"{body}\n"
        "<!-- sb:generated canonical:end -->\n\n"
        "## Manual notes\n\n",
        encoding="utf-8",
    )


def test_project_rebuild_preserves_personal_layers_and_counts_collection_children(
    tmp_path: Path, monkeypatch
) -> None:
    paths = setup_runtime(RuntimePaths.from_root(tmp_path / "runtime"))
    store = StateStore(paths.state)
    store.set_bootstrap_state("completed")
    vault = tmp_path / "vault"
    projects_root = tmp_path / "Projects"
    collection = projects_root / "Utilities & Automation"
    child = collection / "HomeDrop"
    child.mkdir(parents=True)
    (child / "README.md").write_text("# HomeDrop\n", encoding="utf-8")
    (child / "app.py").write_text("print('ready')\n", encoding="utf-8")

    identity = vault / "Identity" / "Persona.md"
    experience = vault / "Experience" / "Employment.md"
    identity.parent.mkdir(parents=True)
    experience.parent.mkdir(parents=True)
    identity.write_text("# Persona\n\nUser-authored identity.\n", encoding="utf-8")
    experience.write_text("# Employment\n\nVerified work history.\n", encoding="utf-8")
    identity_before = identity.read_bytes()
    experience_before = experience.read_bytes()

    old_path = projects_root / "Legacy"
    old_path.mkdir(parents=True)
    store.upsert_project(
        {
            "id": "project-legacy",
            "name": "Legacy",
            "local_path": str(old_path),
            "classification": "first-party",
            "tracked_file_count": 1,
        }
    )
    store.set_project_presence("project-legacy", present=True)
    evidence_id, _ = store.add_evidence(
        source_type="git",
        source_ref="project-inventory:legacy",
        kind="project_inventory",
        project_id="project-legacy",
        payload={"project_ids": ["project-legacy"], "name": "Legacy"},
    )
    project_observation = store.add_observation(
        {
            "kind": "project_fact",
            "subject": "Legacy",
            "claim": "Legacy project claim.",
            "evidence_refs": [evidence_id],
            "confidence": 0.9,
            "status": "promoted",
        }
    )
    personal_observation = store.add_observation(
        {
            "kind": "experience",
            "subject": "Verified employment",
            "claim": "Verified employment remains preserved.",
            "evidence_refs": [evidence_id],
            "confidence": 0.9,
            "status": "promoted",
        }
    )
    session_evidence, _ = store.add_evidence(
        source_type="codex",
        source_ref="codex:unattributed-session",
        kind="message",
        payload={"role": "user", "text": "Workspace decision"},
    )
    scoped_decision = store.add_observation(
        {
            "kind": "decision",
            "subject": "Workspace branch strategy",
            "claim": "This workspace should use one branch.",
            "evidence_refs": [session_evidence],
            "confidence": 0.9,
            "scope": "project",
            "status": "pending",
        }
    )
    global_decision = store.add_observation(
        {
            "kind": "decision",
            "subject": "Global source preference",
            "claim": "Use the verified source globally.",
            "evidence_refs": [session_evidence],
            "confidence": 0.9,
            "scope": "global",
            "status": "promoted",
        }
    )
    legacy_pending_decision = store.add_observation(
        {
            "kind": "decision",
            "subject": "Legacy workspace strategy",
            "claim": "A legacy unscoped workspace decision.",
            "evidence_refs": [session_evidence],
            "confidence": 0.9,
            "status": "pending",
        }
    )
    _generated_note(
        vault / "Projects" / "legacy.md",
        "Legacy",
        f"- Legacy project claim. ^{project_observation}",
    )

    config = default_runtime_config(paths)
    config["vault_root"] = str(vault)
    config["projects_root"] = str(projects_root)
    config["ignored_project_paths"] = []
    config["project_classification_overrides"] = {}
    monkeypatch.setattr(
        project_rebuild,
        "build_dashboard",
        lambda _paths, _vault: _paths.dashboard / "index.html",
    )

    result = rebuild_project_subsystem(
        paths,
        vault,
        store,
        config=config,
        defaults=load_defaults(),
        collection_names=["Utilities & Automation"],
        confirm=True,
    )

    assert result["status"] == "rebuilt"
    assert result["model_called"] is False
    assert result["catalog_health"]["ok"] is True
    assert identity.read_bytes() == identity_before
    assert experience.read_bytes() == experience_before
    assert store.observation(project_observation) is None
    assert store.observation(personal_observation) is not None
    assert store.observation(scoped_decision) is None
    assert store.observation(global_decision) is not None
    assert store.observation(legacy_pending_decision) is None
    retained = store.evidence_by_ids([evidence_id])[0]
    assert retained["kind"] == "redacted_support"
    assert retained["project_id"] is None
    actual, collections, _folders = catalog_groups(store.present_projects())
    assert {item["name"] for item in actual} == {"HomeDrop", "vault"}
    assert {item["name"] for item in collections} == {"Utilities & Automation"}
    assert not (vault / "Projects" / "legacy.md").exists()


def test_project_rebuild_preview_changes_nothing(tmp_path: Path) -> None:
    paths = setup_runtime(RuntimePaths.from_root(tmp_path / "runtime"))
    store = StateStore(paths.state)
    store.set_bootstrap_state("completed")
    vault = tmp_path / "vault"
    vault.mkdir()
    projects_root = tmp_path / "Projects"
    collection = projects_root / "Utilities & Automation"
    collection.mkdir(parents=True)
    store.upsert_project(
        {
            "id": "project-one",
            "name": "One",
            "local_path": str(projects_root / "One"),
            "classification": "review",
            "tracked_file_count": 1,
        }
    )
    config = default_runtime_config(paths)
    config["projects_root"] = str(projects_root)

    result = rebuild_project_subsystem(
        paths,
        vault,
        store,
        config=config,
        defaults=load_defaults(),
        collection_names=["Utilities & Automation"],
    )

    assert result["status"] == "preview"
    assert len(store.projects()) == 1
    assert not list((paths.runs / "backups").glob("project-rebuild-*"))
