import json
import shutil
import subprocess
from pathlib import Path

from second_brain_protocol.activity import (
    activity_markdown,
    build_project_delta,
    session_recap,
)
from second_brain_protocol.collector import collect_projects
from second_brain_protocol.scanner import relevant_manifest
from second_brain_protocol.state import StateStore


DEFAULTS = {
    "identity": {
        "confirmed_git_owners": ["YOUR_GITHUB_USER"],
        "confirmed_author_names": ["YOUR_NAME"],
        "confirmed_author_emails": ["shai@example.com"],
        "excluded_authors": [],
    }
}


def _repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "YOUR_NAME"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "shai@example.com"], cwd=path, check=True)
    (path / "package.json").write_text(
        json.dumps({"dependencies": {"react": "1"}}), encoding="utf-8"
    )
    subprocess.run(["git", "add", "package.json"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=path, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:YOUR_GITHUB_USER/activity-test.git"],
        cwd=path,
        check=True,
    )
    return path


def _deltas(store: StateStore) -> list[dict]:
    return [item for item in store.evidence() if item["kind"] == "project_delta"]


def test_project_activity_emits_only_real_deltas_and_tracks_moves_and_removal(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "Projects"
    repo = _repo(projects_root / "Original")
    store = StateStore(tmp_path / "state.sqlite")

    first = collect_projects(store, projects_root=projects_root, defaults=DEFAULTS)
    assert first["project_deltas"] == 1
    project_id = first["projects"][0]["id"]
    assert "added" in _deltas(store)[-1]["payload"]["change_types"]

    second = collect_projects(store, projects_root=projects_root, defaults=DEFAULTS)
    assert second["project_deltas"] == 0

    (repo / "package.json").write_text(
        json.dumps({"dependencies": {"react": "2", "express": "1"}}),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "package.json"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add backend"], cwd=repo, check=True)
    changed = collect_projects(store, projects_root=projects_root, defaults=DEFAULTS)
    assert changed["changed_project_ids"] == [project_id]
    change_types = set(_deltas(store)[-1]["payload"]["change_types"])
    assert {"files_changed", "commits_added", "head_changed", "stack_changed"} <= change_types

    moved = projects_root / "Renamed"
    shutil.move(str(repo), str(moved))
    collect_projects(store, projects_root=projects_root, defaults=DEFAULTS)
    moved_delta = _deltas(store)[-1]["payload"]
    assert moved_delta["project_id"] == project_id
    assert {"moved", "renamed"} <= set(moved_delta["change_types"])
    assert "local_path" not in json.dumps(moved_delta)

    shutil.move(str(moved), str(tmp_path / "Outside"))
    removed = collect_projects(store, projects_root=projects_root, defaults=DEFAULTS)
    assert removed["project_deltas"] == 1
    assert _deltas(store)[-1]["payload"]["change_types"] == ["removed"]
    assert collect_projects(
        store, projects_root=projects_root, defaults=DEFAULTS
    )["project_deltas"] == 0


def test_project_classification_override_persists_explicit_user_answer(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "Projects"
    _repo(projects_root / "FrameworkFork")
    store = StateStore(tmp_path / "state.sqlite")

    initial = collect_projects(store, projects_root=projects_root, defaults=DEFAULTS)
    project_id = initial["projects"][0]["id"]
    updated = collect_projects(
        store,
        projects_root=projects_root,
        defaults=DEFAULTS,
        classification_overrides={
            project_id: {
                "classification": "fork",
                "reason": "Explicit user confirmation.",
            }
        },
    )

    assert updated["projects"][0]["classification"] == "fork"
    assert updated["projects"][0]["classification_reasons"] == [
        "Explicit user confirmation."
    ]


def test_remote_migration_keeps_project_identity_at_same_source_directory(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "Projects"
    repo = _repo(projects_root / "First Party Project With Existing Brain")
    store = StateStore(tmp_path / "state.sqlite")
    initial = collect_projects(store, projects_root=projects_root, defaults=DEFAULTS)
    project_id = initial["projects"][0]["id"]
    subprocess.run(["git", "remote", "remove", "origin"], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "remote",
            "add",
            "origin",
            "https://github.com/The-Angel-Way/angel-ai-mvp.git",
        ],
        cwd=repo,
        check=True,
    )

    migrated = collect_projects(store, projects_root=projects_root, defaults=DEFAULTS)

    assert migrated["projects"][0]["id"] == project_id
    assert migrated["projects"][0]["classification"] == "review"
    assert len(store.projects()) == 1


def test_gitlink_directory_stats_do_not_create_false_file_changes(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "Repo"
    gitlink = repo / "nested-gitlink"
    gitlink.mkdir(parents=True)

    manifest = relevant_manifest(repo, ["nested-gitlink"])
    assert manifest == {"nested-gitlink": (0, 0)}

    previous = {
        "id": "project-demo",
        "name": "Demo",
        "classification": "first-party",
        "manifest": {"nested-gitlink": (4096, 123456)},
        "tech_stack": [],
        "second_brain_files": [],
        "git_history": {"working_tree": [], "recent_commits": []},
        "head_commit": "abc",
    }
    current = dict(previous, manifest=manifest)
    assert build_project_delta(previous, current, was_present=True) is None


def test_session_ledger_keeps_connectivity_tests_and_labels_backfill() -> None:
    payload = {
        "source": "antigravity",
        "project_ids": ["project-demo"],
        "started_at": "2026-07-19T23:20:28Z",
        "user_messages": [{"text": "<USER_REQUEST>test</USER_REQUEST>"}],
        "assistant_results": [{"text": "I successfully loaded the workspace."}],
        "tool_usage": {"list_dir": 1},
    }
    assert session_recap(payload) == (
        "Connectivity test completed; the workspace loaded successfully."
    )
    markdown = activity_markdown(
        [
            {
                "id": "ev-session",
                "kind": "session_digest",
                "occurred_at": "2026-07-19T23:20:28Z",
                "payload": payload,
            }
        ],
        project_names_by_id={"project-demo": "Demo"},
        period="2026-07-20",
    )
    assert "[[Projects/demo|Demo]]" in markdown
    assert "Connectivity test completed" in markdown
    assert "backfill from 2026-07-19" in markdown
