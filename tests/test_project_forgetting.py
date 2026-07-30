import json
from pathlib import Path

from second_brain_protocol.config import RuntimePaths, setup_runtime
from second_brain_protocol.project_forgetting import forget_projects
from second_brain_protocol.state import StateStore


def _generated_note(path: Path, title: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"# {title}\n\n<!-- sb:generated canonical:start -->\n{body}\n<!-- sb:generated canonical:end -->\n",
        encoding="utf-8",
    )


def test_forget_project_is_previewed_backed_up_and_preserves_protected_project(
    tmp_path: Path,
) -> None:
    paths = setup_runtime(RuntimePaths.from_root(tmp_path / "runtime"))
    vault = tmp_path / "vault"
    store = StateStore(paths.state)
    legacy_path = tmp_path / "Projects" / "Legacy App"
    protected_path = tmp_path / "Projects" / "Legacy App Rebuild"
    legacy_path.mkdir(parents=True)
    protected_path.mkdir()
    store.upsert_project(
        {
            "id": "project-legacy",
            "name": "Legacy App",
            "local_path": str(legacy_path),
            "classification": "first-party",
        }
    )
    store.upsert_project(
        {
            "id": "project-legacy-rebuild",
            "name": "Legacy App Rebuild",
            "local_path": str(protected_path),
            "classification": "first-party",
        }
    )
    evidence_id, _ = store.add_evidence(
        source_type="codex",
        source_ref="session:legacy-app",
        kind="visible_message",
        project_id="project-legacy",
        payload={"project_ids": ["project-legacy"], "text": "legacy app details"},
    )
    project_observation_id = store.add_observation(
        {
            "kind": "project_fact",
            "subject": "Legacy App deployment",
            "claim": "Legacy App used the old deployment.",
            "evidence_refs": [evidence_id],
            "confidence": 0.9,
            "status": "promoted",
        }
    )
    profile_observation_id = store.add_observation(
        {
            "kind": "experience",
            "subject": "Unrelated role",
            "claim": "The owner has a verified unrelated role.",
            "evidence_refs": [evidence_id],
            "confidence": 0.9,
            "status": "promoted",
        }
    )
    unrelated_evidence_id, _ = store.add_evidence(
        source_type="interview",
        source_ref="interview:unrelated",
        kind="explicit_fact",
        payload={"role": "user", "text": "Unrelated evidence"},
    )
    orphaned_question_id = store.add_observation(
        {
            "kind": "clarification",
            "subject": "Legacy App status",
            "claim": "Is Legacy App still an active project?",
            "evidence_refs": [unrelated_evidence_id],
            "confidence": 0.9,
            "status": "pending",
        }
    )
    _generated_note(
        vault / "Projects" / "legacy-app.md",
        "Legacy App",
        f"- Legacy App fact. ^{project_observation_id}",
    )
    _generated_note(
        vault / "Projects" / "legacy-app-rebuild.md",
        "Legacy App Rebuild",
        "Legacy App Rebuild remains protected.",
    )
    _generated_note(
        vault / "Experience" / "Employment.md",
        "Employment",
        f"- The owner has a verified unrelated role. ^{profile_observation_id}",
    )
    _generated_note(
        vault / "Projects" / "Index.md",
        "Projects",
        "- [[Projects/legacy-app|Legacy App]]\n"
        "- [[Projects/legacy-app-rebuild|Legacy App Rebuild]]",
    )
    _generated_note(
        vault / "Journal" / "Daily" / "2026-07-17.md",
        "Daily",
        "- Project activity: Legacy App\n- Project activity: Legacy App Rebuild",
    )
    review = vault / "Inbox" / "Review" / "Review-2026-07-17.md"
    review.parent.mkdir(parents=True)
    review.write_text("generated review\n", encoding="utf-8")
    graph = paths.graphify / "project-legacy"
    graph.mkdir(parents=True)
    (graph / "graph.json").write_text("{}\n", encoding="utf-8")

    preview = forget_projects(
        paths,
        vault,
        store,
        identifiers=["Legacy App"],
        protected_identifiers=["Legacy App Rebuild"],
    )
    assert preview["status"] == "preview"
    assert preview["project_observations"] == 2
    assert store.observation(project_observation_id) is not None

    result = forget_projects(
        paths,
        vault,
        store,
        identifiers=["Legacy App"],
        protected_identifiers=["Legacy App Rebuild"],
        confirm=True,
        reindexer=lambda _paths, _vault: None,
        dashboard_builder=lambda _paths, _vault: None,
    )

    assert result["status"] == "forgotten"
    assert result["source_projects_untouched"] is True
    assert Path(result["backup"]).is_dir()
    assert store.observation(project_observation_id) is None
    assert store.observation(orphaned_question_id) is None
    assert store.observation(profile_observation_id) is not None
    retained = store.evidence_by_ids([evidence_id])[0]
    assert retained["project_id"] is None
    assert retained["kind"] == "redacted_support"
    assert {project["name"] for project in store.projects()} == {"Legacy App Rebuild"}
    assert not (vault / "Projects" / "legacy-app.md").exists()
    assert (vault / "Projects" / "legacy-app-rebuild.md").exists()
    assert (
        "[[Projects/legacy-app|Legacy App]]"
        not in (vault / "Projects" / "Index.md").read_text()
    )
    assert "Legacy App Rebuild" in (vault / "Projects" / "Index.md").read_text()
    assert not graph.exists()
    config = json.loads(paths.config.read_text(encoding="utf-8"))
    assert str(legacy_path.resolve()) in config["ignored_project_paths"]
