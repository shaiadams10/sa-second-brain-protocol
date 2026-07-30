from pathlib import Path

from second_brain_protocol.config import RuntimePaths
from second_brain_protocol import orchestrator
from second_brain_protocol.state import StateStore


def test_project_index_sync_never_snapshots_commits_or_calls_a_model(
    tmp_path: Path, monkeypatch
) -> None:
    paths = RuntimePaths.from_root(tmp_path / "runtime")
    paths.root.mkdir(parents=True)
    paths.locks.mkdir(parents=True)
    store = StateStore(paths.state)
    store.set_bootstrap_state("completed")
    vault = tmp_path / "vault"
    vault.mkdir()
    project = {
        "id": "folder-home",
        "name": "HomeDrop",
        "classification": "review",
        "tracked_file_count": 8,
        "fingerprint": "home",
    }
    monkeypatch.setattr(
        orchestrator,
        "context",
        lambda: (
            paths,
            {
                "projects_root": str(tmp_path / "Projects"),
                "ignored_project_paths": [],
                "project_classification_overrides": {},
            },
            {},
            store,
        ),
    )
    monkeypatch.setattr(orchestrator, "vault_root", lambda: vault)
    monkeypatch.setattr(
        orchestrator,
        "snapshot_manual_markdown",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("catalog sync must not touch Git")
        ),
    )
    monkeypatch.setattr(
        orchestrator,
        "collect_projects",
        lambda *_args, **_kwargs: {
            "projects": [project],
            "changed_project_ids": [project["id"]],
        },
    )
    monkeypatch.setattr(
        orchestrator,
        "publish_project_catalog",
        lambda *_args, **_kwargs: {
            "path": str(vault / "Projects" / "Index.md"),
            "changed": True,
            "projects": 1,
            "collections": 0,
            "other_folders": 0,
        },
    )
    monkeypatch.setattr(orchestrator, "reindex", lambda *_args, **_kwargs: None)

    result = orchestrator.sync_project_index()

    assert result["status"] == "completed"
    assert result["model_called"] is False
    assert result["git_published"] is False
