from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from second_brain_protocol import gitops
from second_brain_protocol.config import RuntimePaths
from second_brain_protocol.state import StateStore


def test_old_empty_index_lock_is_recovered_when_git_is_idle(
    tmp_path: Path, monkeypatch
) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    lock = git_dir / "index.lock"
    lock.touch()
    old = time.time() - 120
    os.utime(lock, (old, old))
    monkeypatch.setattr(gitops, "_git_process_running", lambda: False)

    assert gitops._recover_stale_index_lock(tmp_path, minimum_age_seconds=60)
    assert not lock.exists()


def test_old_index_lock_is_preserved_while_git_is_running(
    tmp_path: Path, monkeypatch
) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    lock = git_dir / "index.lock"
    lock.touch()
    old = time.time() - 120
    os.utime(lock, (old, old))
    monkeypatch.setattr(gitops, "_git_process_running", lambda: True)

    assert not gitops._recover_stale_index_lock(tmp_path, minimum_age_seconds=60)
    assert lock.exists()


def test_fresh_or_nonempty_index_lock_is_never_auto_removed(
    tmp_path: Path, monkeypatch
) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    lock = git_dir / "index.lock"
    monkeypatch.setattr(gitops, "_git_process_running", lambda: False)

    lock.touch()
    assert not gitops._recover_stale_index_lock(tmp_path, minimum_age_seconds=60)
    assert lock.exists()

    lock.write_text("prospective index data", encoding="utf-8")
    old = time.time() - 120
    os.utime(lock, (old, old))
    assert not gitops._recover_stale_index_lock(tmp_path, minimum_age_seconds=60)
    assert lock.exists()


def test_active_account_falls_back_to_selected_login_on_github_503(monkeypatch) -> None:
    responses = iter(
        [
            subprocess.CompletedProcess(["gh"], 1, "", "invalid character '<'"),
            subprocess.CompletedProcess(
                ["gh"],
                0,
                json.dumps(
                    {
                        "hosts": {
                            "github.com": [
                                {
                                    "active": True,
                                    "login": "YOUR_GITHUB_USER",
                                    "state": "error",
                                    "error": "HTTP 503: 503 Service Unavailable",
                                }
                            ]
                        }
                    }
                ),
                "",
            ),
        ]
    )
    monkeypatch.setattr(gitops, "_run", lambda *args, **kwargs: next(responses))

    assert gitops._active_gh_account() == "YOUR_GITHUB_USER"


def test_active_account_does_not_fallback_for_other_auth_errors(monkeypatch) -> None:
    responses = iter(
        [
            subprocess.CompletedProcess(["gh"], 1, "", "authentication failed"),
            subprocess.CompletedProcess(
                ["gh"],
                0,
                json.dumps(
                    {
                        "hosts": {
                            "github.com": [
                                {
                                    "active": True,
                                    "login": "YOUR_GITHUB_USER",
                                    "state": "error",
                                    "error": "authentication failed",
                                }
                            ]
                        }
                    }
                ),
                "",
            ),
        ]
    )
    monkeypatch.setattr(gitops, "_run", lambda *args, **kwargs: next(responses))

    try:
        gitops._active_gh_account()
    except gitops.GitPolicyError as exc:
        assert "authentication failed" in str(exc)
    else:
        raise AssertionError("Expected non-503 authentication failure to remain blocking")


def test_protocol_commit_message_supports_a_descriptive_override(monkeypatch) -> None:
    monkeypatch.setenv("SB_PROTOCOL_COMMIT_MESSAGE", "Fix public README author-name redaction")

    assert gitops._protocol_commit_message() == "Fix public README author-name redaction"


def test_protocol_sync_publishes_only_changed_sanitized_exports(
    tmp_path: Path, monkeypatch,
) -> None:
    vault = tmp_path / "vault"
    protocol = vault / "Protocol"
    protocol.mkdir(parents=True)
    readme = protocol / "README.md"
    readme.write_text("# Reusable protocol\n", encoding="utf-8")
    paths = RuntimePaths.from_root(tmp_path / "runtime")
    store = StateStore(paths.state)
    calls = []
    monkeypatch.setattr(gitops, "_run_protocol_tests", lambda _vault: calls.append("tests"))
    monkeypatch.setattr(
        gitops,
        "publish_protocol_draft",
        lambda _vault, _paths, _repository: calls.append("publish") or "https://example.test/pr/1",
    )

    first = gitops.sync_protocol_draft(
        vault, paths, "example/protocol", store, if_changed=True
    )
    second = gitops.sync_protocol_draft(
        vault, paths, "example/protocol", store, if_changed=True
    )
    readme.write_text("# Reusable protocol\n\nChanged.\n", encoding="utf-8")
    third = gitops.sync_protocol_draft(
        vault, paths, "example/protocol", store, if_changed=True
    )

    assert first == {"status": "published", "draft_pr": "https://example.test/pr/1"}
    assert second == {"status": "unchanged"}
    assert third == {"status": "published", "draft_pr": "https://example.test/pr/1"}
    assert calls == ["tests", "publish", "tests", "publish"]
