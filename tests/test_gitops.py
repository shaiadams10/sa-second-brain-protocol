from __future__ import annotations

import json
import subprocess
from pathlib import Path

from second_brain_protocol import gitops
from second_brain_protocol.config import RuntimePaths
from second_brain_protocol.state import StateStore


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
