from __future__ import annotations

import json
import subprocess

from second_brain_protocol import gitops


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
