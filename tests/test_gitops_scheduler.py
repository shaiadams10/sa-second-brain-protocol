import subprocess
from pathlib import Path

from second_brain_protocol.gitops import (
    GITHUB_ED25519_KNOWN_HOST,
    _ensure_github_known_hosts,
    commit_if_changed,
    export_public_protocol,
)
from second_brain_protocol.config import RuntimePaths
from second_brain_protocol.scheduler import task_xml


def test_public_export_is_allowlisted_and_generic(tmp_path: Path) -> None:
    protocol = tmp_path / "Protocol"
    (protocol / "src").mkdir(parents=True)
    (protocol / "docs").mkdir()
    (protocol / "config").mkdir()
    (protocol / "src" / "tool.py").write_text("NAME='Personal Second Brain'", encoding="utf-8")
    (protocol / "docs" / "ReviewFlow.md").write_text("# Review flow", encoding="utf-8")
    (protocol / "README.md").write_text("Personal Second Brain", encoding="utf-8")
    (protocol / "CONTRIBUTING.md").write_text("# Contributing", encoding="utf-8")
    (protocol / "config" / "defaults.json").write_text(
        '{"runtime_directory_name":"PrivateName","identity":{"confirmed_author_names":["Private Person"]}}',
        encoding="utf-8",
    )
    (protocol / "private").mkdir()
    (protocol / "private" / "evidence.md").write_text("private", encoding="utf-8")
    destination = tmp_path / "public"
    export_public_protocol(protocol, destination)
    assert "Personal Second Brain" in (destination / "README.md").read_text(encoding="utf-8")
    assert (destination / "docs" / "ReviewFlow.md").is_file()
    assert (destination / "CONTRIBUTING.md").is_file()
    exported_defaults = (destination / "config" / "defaults.json").read_text(encoding="utf-8")
    assert "Private Person" not in exported_defaults
    assert '"YOUR_NAME"' in exported_defaults
    assert not (destination / "private").exists()


def test_public_export_excludes_personal_project_name(tmp_path: Path) -> None:
    protocol = tmp_path / "Protocol"
    protocol.mkdir()
    system = tmp_path / "System"
    system.mkdir()
    (system / "PublicExportRedactions.json").write_text(
        '{"replacements":{"Private Project Alpha":"Project A"},"forbidden_terms":["Private Project Alpha"],"preserve_in_authorship_files":[]}',
        encoding="utf-8",
    )
    (protocol / "README.md").write_text("Private Project Alpha", encoding="utf-8")
    destination = tmp_path / "public"
    export_public_protocol(protocol, destination)
    exported = (destination / "README.md").read_text(encoding="utf-8")
    assert "Private Project Alpha" not in exported
    assert "Project A" in exported


def test_scheduler_is_interactive_missed_run_safe_and_single_instance() -> None:
    xml = task_xml(task_name="Personal Brain", script_path=Path("C:/safe/scheduled-run.ps1"), username="DOMAIN\\user")
    assert "InteractiveToken" in xml
    assert "StartWhenAvailable>true" in xml
    assert "MultipleInstancesPolicy>IgnoreNew" in xml
    assert "22:30:00" in xml


def test_automatic_private_commit_excludes_obsidian_ui_state(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    (tmp_path / "Journal").mkdir()
    (tmp_path / "Journal" / "daily.md").write_text("safe\n", encoding="utf-8")
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian" / "app.json").write_text("{}\n", encoding="utf-8")
    assert commit_if_changed(tmp_path, "safe generated update")
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.splitlines()
    assert "Journal/daily.md" in tracked
    assert ".obsidian/app.json" not in tracked


def test_automatic_private_commit_includes_private_certificates(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    certificates = tmp_path / "Evidence" / "Certificates"
    certificates.mkdir(parents=True)
    (certificates / "course.pdf").write_bytes(b"%PDF-1.4\nprivate certificate fixture\n")

    assert commit_if_changed(tmp_path, "store private certificate")
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.splitlines()

    assert "Evidence/Certificates/course.pdf" in tracked


def test_github_known_hosts_is_pinned_in_runtime(tmp_path: Path) -> None:
    paths = RuntimePaths.from_root(tmp_path / "runtime")

    known_hosts = _ensure_github_known_hosts(paths)

    assert known_hosts.read_text(encoding="utf-8") == GITHUB_ED25519_KNOWN_HOST + "\n"
    assert GITHUB_ED25519_KNOWN_HOST.startswith("github.com ssh-ed25519 ")
