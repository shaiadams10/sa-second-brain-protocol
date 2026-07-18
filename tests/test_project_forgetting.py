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
    legacy_path = tmp_path / "Projects" / "Angel"
    protected_path = tmp_path / "Projects" / "First Party Project With Existing Brain"
    legacy_path.mkdir(parents=True)
    protected_path.mkdir()
    store.upsert_project(
        {
            "id": "project-angel",
            "name": "Angel",
            "local_path": str(legacy_path),
            "classification": "first-party",
        }
    )
    store.upsert_project(
        {
            "id": "project-angel-v2",
            "name": "First Party Project With Existing Brain",
            "local_path": str(protected_path),
            "classification": "first-party",
        }
    )
    evidence_id, _ = store.add_evidence(
        source_type="codex",
        source_ref="session:legacy-angel",
        kind="visible_message",
        project_id="project-angel",
        payload={"project_ids": ["project-angel"], "text": "legacy Angel details"},
    )
    project_observation_id = store.add_observation(
        {
            "kind": "project_fact",
            "subject": "Angel deployment",
            "claim": "Angel used the legacy deployment.",
            "evidence_refs": [evidence_id],
            "confidence": 0.9,
            "status": "promoted",
        }
    )
    profile_observation_id = store.add_observation(
        {
            "kind": "experience",
            "subject": "Unrelated role",
            "claim": "the user has a verified unrelated role.",
            "evidence_refs": [evidence_id],
            "confidence": 0.9,
            "status": "promoted",
        }
    )
    _generated_note(
        vault / "Projects" / "angel.md",
        "Angel",
        f"- Legacy Angel fact. ^{project_observation_id}",
    )
    _generated_note(
        vault / "Projects" / "angel-version-2.md",
        "First Party Project With Existing Brain",
        "First Party Project With Existing Brain remains protected.",
    )
    _generated_note(
        vault / "Experience" / "Employment.md",
        "Employment",
        f"- the user has a verified unrelated role. ^{profile_observation_id}",
    )
    _generated_note(
        vault / "Projects" / "Index.md",
        "Projects",
        "- [[Projects/angel|Angel]]\n- [[Projects/angel-version-2|First Party Project With Existing Brain]]",
    )
    _generated_note(
        vault / "Journal" / "Daily" / "2026-07-17.md",
        "Daily",
        "- Project activity: Angel\n- Project activity: First Party Project With Existing Brain",
    )
    review = vault / "Inbox" / "Review" / "Review-2026-07-17.md"
    review.parent.mkdir(parents=True)
    review.write_text("generated review\n", encoding="utf-8")
    graph = paths.graphify / "project-angel"
    graph.mkdir(parents=True)
    (graph / "graph.json").write_text("{}\n", encoding="utf-8")

    preview = forget_projects(
        paths,
        vault,
        store,
        identifiers=["Angel"],
        protected_identifiers=["First Party Project With Existing Brain"],
    )
    assert preview["status"] == "preview"
    assert store.observation(project_observation_id) is not None

    result = forget_projects(
        paths,
        vault,
        store,
        identifiers=["Angel"],
        protected_identifiers=["First Party Project With Existing Brain"],
        confirm=True,
        reindexer=lambda _paths, _vault: None,
        dashboard_builder=lambda _paths, _vault: None,
    )

    assert result["status"] == "forgotten"
    assert result["source_projects_untouched"] is True
    assert Path(result["backup"]).is_dir()
    assert store.observation(project_observation_id) is None
    assert store.observation(profile_observation_id) is not None
    retained = store.evidence_by_ids([evidence_id])[0]
    assert retained["project_id"] is None
    assert retained["kind"] == "redacted_support"
    assert {project["name"] for project in store.projects()} == {"First Party Project With Existing Brain"}
    assert not (vault / "Projects" / "angel.md").exists()
    assert (vault / "Projects" / "angel-version-2.md").exists()
    assert "[[Projects/angel|Angel]]" not in (vault / "Projects" / "Index.md").read_text()
    assert "First Party Project With Existing Brain" in (vault / "Projects" / "Index.md").read_text()
    assert not graph.exists()
    config = json.loads(paths.config.read_text(encoding="utf-8"))
    assert str(legacy_path.resolve()) in config["ignored_project_paths"]
