from pathlib import Path

from second_brain_protocol.session_attribution import (
    ProjectSessionResolver,
    normalize_workspace_path,
    seed_project_paths_from_backups,
)
from second_brain_protocol.state import StateStore


def _project(project_id: str, path: Path) -> dict:
    return {
        "id": project_id,
        "name": project_id,
        "classification": "first-party",
        "local_path": str(path),
        "tracked_file_count": 1,
    }


def test_resolver_uses_current_and_historical_paths_without_text_matching(
    tmp_path: Path,
) -> None:
    current = tmp_path / "Projects" / "First Party Project With Existing Brain"
    old = tmp_path / "Old Projects" / "Angel"
    project = _project("project-angel", current)
    resolver = ProjectSessionResolver(
        [project],
        [
            {
                "normalized_path": normalize_workspace_path(old),
                "project_id": "project-angel",
                "confidence": 1.0,
                "is_current": 0,
            }
        ],
    )

    assert resolver.resolve(current / "src").project_id == "project-angel"
    historical = resolver.resolve(old / "app")
    assert historical.project_id == "project-angel"
    assert historical.resolver == "historical_path"
    assert resolver.resolve(tmp_path / "Other" / "Angel").status == "unmatched"


def test_resolver_rejects_one_historical_path_claimed_by_two_projects(
    tmp_path: Path,
) -> None:
    shared = normalize_workspace_path(tmp_path / "Old" / "Workspace")
    projects = [
        _project("project-one", tmp_path / "Current" / "One"),
        _project("project-two", tmp_path / "Current" / "Two"),
    ]
    aliases = [
        {
            "normalized_path": shared,
            "project_id": project_id,
            "confidence": 1.0,
            "is_current": 0,
        }
        for project_id in ("project-one", "project-two")
    ]

    resolution = ProjectSessionResolver(projects, aliases).resolve(shared)

    assert resolution.status == "ambiguous"
    assert resolution.project_id is None


def test_backup_state_recovers_a_moved_project_path(tmp_path: Path) -> None:
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    old_path = tmp_path / "Old" / "Portfolio"
    current_path = tmp_path / "Projects" / "Portfolio"
    backup = StateStore(backup_root / "state-old.sqlite")
    backup.upsert_project(_project("project-portfolio", old_path))
    store = StateStore(tmp_path / "state.sqlite")
    current = _project("project-portfolio", current_path)
    store.upsert_project(current)

    result = seed_project_paths_from_backups(store, backup_root, [current])

    assert result["historical_paths_registered"] == 1
    aliases = store.project_path_aliases(project_ids={"project-portfolio"})
    assert aliases[0]["normalized_path"] == normalize_workspace_path(old_path)
    assert aliases[0]["source"] == "state_backup"


def test_codex_git_header_recovers_moved_path_and_rejects_conflicts(
    tmp_path: Path,
) -> None:
    angel = _project("project-angel", tmp_path / "Current" / "Angel")
    angel["remote_url"] = "git@github.com:owner/angel.git"
    portfolio = _project("project-portfolio", tmp_path / "Current" / "Portfolio")
    portfolio["remote_url"] = "https://github.com/owner/portfolio.git"
    resolver = ProjectSessionResolver([angel, portfolio], [])

    moved = resolver.resolve(
        tmp_path / "Deleted" / "Angel",
        remote_url="https://github.com/owner/angel",
    )
    conflict = resolver.resolve(
        angel["local_path"],
        remote_url="https://github.com/owner/portfolio.git",
    )

    assert moved.project_id == "project-angel"
    assert moved.resolver == "session_git_identity"
    assert conflict.status == "ambiguous"
    assert conflict.project_id is None
