import io
import sys

from second_brain_protocol.cli import _json, _parser


def test_json_output_is_safe_on_legacy_windows_console(monkeypatch) -> None:
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stream)

    _json({"hebrew": "חפשש"})
    stream.flush()

    rendered = buffer.getvalue().decode("cp1252")
    assert "\\u05d7" in rendered


def test_review_group_commands_are_explicit_and_snapshot_addressed() -> None:
    approve = _parser().parse_args(
        ["review", "approve-group", "rvg-private-projects-deadbeef00"]
    )
    answer = _parser().parse_args(
        [
            "review",
            "answer",
            "rvg-questions-technical-deadbeef00",
            "2",
            "--answer",
            "defer",
        ]
    )

    assert approve.action == "approve-group"
    assert approve.id == "rvg-private-projects-deadbeef00"
    assert answer.action == "answer"
    assert answer.item == 2
    assert answer.answer == "defer"


def test_question_attribution_command_requires_explicit_ids() -> None:
    args = _parser().parse_args(
        ["review", "attribute-question", "obs-question", "project-portfolio"]
    )

    assert args.action == "attribute-question"
    assert args.id == "obs-question"
    assert args.project_id == "project-portfolio"


def test_forget_project_requires_explicit_confirmation_and_protection() -> None:
    preview = _parser().parse_args(["forget-project", "Angel"])
    confirmed = _parser().parse_args(
        [
            "forget-project",
            "Angel",
            "--include",
            "AngelRefrence",
            "--protect",
            "First Party Project With Existing Brain",
            "--confirm",
        ]
    )

    assert preview.confirm is False
    assert confirmed.confirm is True
    assert confirmed.include == ["AngelRefrence"]
    assert confirmed.protect == ["First Party Project With Existing Brain"]


def test_session_link_requires_explicit_confirmation() -> None:
    preview = _parser().parse_args(
        ["sessions", "link", "antigravity", "session-1", "Vane"]
    )
    confirmed = _parser().parse_args(
        ["sessions", "link", "antigravity", "session-1", "Vane", "--confirm"]
    )

    assert preview.confirm is False
    assert confirmed.confirm is True
    assert confirmed.project == "Vane"


def test_bootstrap_refresh_is_an_explicit_preapproval_action() -> None:
    args = _parser().parse_args(["bootstrap", "--refresh-evidence"])
    assert args.refresh_evidence is True
    assert args.approve is False


def test_dashboard_is_natural_language_friendly_but_has_internal_actions() -> None:
    default = _parser().parse_args(["dashboard"])
    build = _parser().parse_args(["dashboard", "build"])

    assert default.action == "open"
    assert build.action == "build"


def test_latest_sessions_accepts_surface_and_project_filters() -> None:
    parsed = _parser().parse_args(
        ["sessions", "latest", "--surface", "antigravity", "--project", "HomeDrop"]
    )
    assert parsed.command == "sessions"
    assert parsed.surface == "antigravity"
    assert parsed.project == "HomeDrop"


def test_session_reconciliation_and_index_commands_are_model_free_actions() -> None:
    reconcile = _parser().parse_args(["sessions", "reconcile"])
    index = _parser().parse_args(["sessions", "index"])

    assert reconcile.action == "reconcile"
    assert index.action == "index"


def test_session_analysis_is_an_explicit_project_isolated_action() -> None:
    analyze = _parser().parse_args(["sessions", "analyze"])
    forced = _parser().parse_args(["sessions", "analyze", "--force"])

    assert analyze.action == "analyze"
    assert analyze.force is False
    assert forced.force is True


def test_project_catalog_sync_is_a_model_free_maintenance_command() -> None:
    parsed = _parser().parse_args(["sync-project-index"])
    assert parsed.command == "sync-project-index"


def test_project_rebuild_requires_confirmation_and_accepts_explicit_collections() -> (
    None
):
    preview = _parser().parse_args(
        ["rebuild-projects", "--collection", "Utilities & Automation"]
    )
    confirmed = _parser().parse_args(
        [
            "rebuild-projects",
            "--collection",
            "Utilities & Automation",
            "--collection",
            "Playground",
            "--confirm",
        ]
    )

    assert preview.confirm is False
    assert preview.collection == ["Utilities & Automation"]
    assert confirmed.confirm is True
    assert confirmed.collection == ["Utilities & Automation", "Playground"]


def test_review_question_dismissal_commands_are_explicit() -> None:
    dismiss = _parser().parse_args(["review", "dismiss", "obs-question"])
    undo = _parser().parse_args(["review", "undo-dismiss"])
    assert dismiss.action == "dismiss"
    assert dismiss.id == "obs-question"
    assert undo.action == "undo-dismiss"


def test_protocol_publish_supports_scheduled_change_detection() -> None:
    manual = _parser().parse_args(["protocol", "publish"])
    scheduled = _parser().parse_args(["protocol", "publish", "--if-changed"])

    assert manual.if_changed is False
    assert scheduled.if_changed is True
