import json
import shutil
import subprocess
from pathlib import Path

from second_brain_protocol.scanner import (
    ProjectScanner,
    discover_filesystem_roots,
    discover_git_roots,
)


DEFAULTS = {
    "identity": {
        "confirmed_git_owners": ["YOUR_GITHUB_USER", "YOUR_ALTERNATE_GITHUB_OWNER"],
        "confirmed_author_names": ["YOUR_NAME"],
        "confirmed_author_emails": ["YOUR_GITHUB_ID+YOUR_GITHUB_USER@users.noreply.github.com"],
        "excluded_authors": ["Excluded Contributor"],
    }
}


def _repo(path: Path, *, author: str, email: str, remote: str) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", author], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", email], cwd=path, check=True)
    (path / "package.json").write_text(json.dumps({"dependencies": {"react": "1"}}), encoding="utf-8")
    subprocess.run(["git", "add", "package.json"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=path, check=True)
    subprocess.run(["git", "remote", "add", "origin", remote], cwd=path, check=True)
    return path


def test_discovery_and_authorship_exclusion(tmp_path: Path) -> None:
    projects = tmp_path / "Projects"
    first = _repo(
        projects / "Portfolio",
        author="YOUR_NAME",
        email="YOUR_GITHUB_ID+YOUR_GITHUB_USER@users.noreply.github.com",
        remote="git@github.com:YOUR_GITHUB_USER/portfolio.git",
    )
    dify = _repo(
        projects / "Playground" / "dify",
        author="Excluded Contributor",
        email="other@example.com",
        remote="https://github.com/langgenius/dify.git",
    )
    assert discover_git_roots(projects) == sorted([first, dify], key=lambda item: item.as_posix().lower())
    scanned = ProjectScanner(projects, DEFAULTS).scan_all()
    by_name = {item["name"]: item for item in scanned}
    assert by_name["Portfolio"]["classification"] == "first-party"
    assert by_name["dify"]["classification"] == "third-party"
    assert by_name["Playground"]["classification"] == "collection"
    assert "React" in by_name["Portfolio"]["tech_stack"]
    assert by_name["Portfolio"]["language_counts"] == {}
    assert "package.json" in by_name["Portfolio"]["artifact_categories"]["manifests"]
    assert by_name["Portfolio"]["git_history"]["commit_count"] == 1
    assert by_name["Portfolio"]["git_history"]["confirmed_user_commits_in_recent_window"] == 1


def test_moved_repository_keeps_identity_and_duplicate_is_classified(tmp_path: Path) -> None:
    projects = tmp_path / "Projects"
    original = _repo(
        projects / "Original",
        author="YOUR_NAME",
        email="YOUR_GITHUB_ID+YOUR_GITHUB_USER@users.noreply.github.com",
        remote="git@github.com:YOUR_GITHUB_USER/sample.git",
    )
    moved = tmp_path / "Renamed"
    shutil.copytree(original, moved)
    scanner = ProjectScanner(projects, DEFAULTS)
    original_id = scanner.scan_repo(original)["id"]
    assert ProjectScanner(tmp_path, DEFAULTS).scan_repo(moved)["id"] == original_id
    shutil.copytree(original, projects / "Copy")
    scanned = ProjectScanner(projects, DEFAULTS).scan_all()
    assert sum(item["classification"] == "duplicate" for item in scanned) == 1


def test_fork_is_not_first_party_skill_evidence(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path / "Fork",
        author="YOUR_NAME",
        email="YOUR_GITHUB_ID+YOUR_GITHUB_USER@users.noreply.github.com",
        remote="git@github.com:YOUR_GITHUB_USER/fork.git",
    )
    subprocess.run(["git", "remote", "add", "upstream", "https://github.com/vendor/original.git"], cwd=repo, check=True)
    assert ProjectScanner(tmp_path, DEFAULTS).scan_repo(repo)["classification"] == "fork"


def test_explicit_local_project_exclusion(tmp_path: Path) -> None:
    keep = tmp_path / "Keep"
    ignored = tmp_path / "Ignored"
    keep.mkdir()
    ignored.mkdir()
    scanner = ProjectScanner(tmp_path, DEFAULTS, (ignored,))
    names = {item["name"] for item in scanner.scan_all()}
    assert "Keep" in names
    assert "Ignored" not in names


def test_nested_non_git_projects_are_discovered_inside_collection(tmp_path: Path) -> None:
    projects = tmp_path / "Projects"
    group = projects / "Video & Media Tools"
    tts = group / "HiggsAudioV3"
    converter = group / "AudioConverter"
    tts.mkdir(parents=True)
    converter.mkdir()
    (tts / "requirements.txt").write_text("gradio\n", encoding="utf-8")
    (tts / "app.py").write_text("print('tts')\n", encoding="utf-8")
    (converter / "README.md").write_text("# Audio converter\n", encoding="utf-8")
    (converter / "converter.py").write_text("print('convert')\n", encoding="utf-8")
    (group / "Loose Files").mkdir()
    environment = group / "ChatterVenv"
    installed = environment / "Lib" / "site-packages" / "gradio"
    installed.mkdir(parents=True)
    (environment / "pyvenv.cfg").write_text("home = python\n", encoding="utf-8")
    (installed / "pyproject.toml").write_text("[project]\nname='gradio'\n", encoding="utf-8")

    roots = discover_filesystem_roots(projects)
    assert roots == [converter, tts]

    scanned = ProjectScanner(projects, DEFAULTS).scan_all()
    by_name = {item["name"]: item for item in scanned}
    assert by_name["Video & Media Tools"]["classification"] == "collection"
    assert len(by_name["Video & Media Tools"]["child_project_ids"]) == 2
    assert by_name["HiggsAudioV3"]["classification"] == "review"
    assert "Python" in by_name["HiggsAudioV3"]["tech_stack"]


def test_generic_source_directory_is_never_promoted_as_a_project(tmp_path: Path) -> None:
    projects = tmp_path / "Projects"
    source = projects / "src"
    source.mkdir(parents=True)
    (source / "package.json").write_text("{}\n", encoding="utf-8")
    (source / "index.ts").write_text("export {}\n", encoding="utf-8")

    assert source not in discover_filesystem_roots(projects)
    assert "src" not in {item["name"].casefold() for item in ProjectScanner(projects, DEFAULTS).scan_all()}
