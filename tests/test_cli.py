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
    approve = _parser().parse_args(["review", "approve-group", "rvg-private-projects-deadbeef00"])
    answer = _parser().parse_args(
        ["review", "answer", "rvg-questions-technical-deadbeef00", "2", "--answer", "defer"]
    )

    assert approve.action == "approve-group"
    assert approve.id == "rvg-private-projects-deadbeef00"
    assert answer.action == "answer"
    assert answer.item == 2
    assert answer.answer == "defer"


def test_bootstrap_refresh_is_an_explicit_preapproval_action() -> None:
    args = _parser().parse_args(["bootstrap", "--refresh-evidence"])
    assert args.refresh_evidence is True
    assert args.approve is False


def test_dashboard_is_natural_language_friendly_but_has_internal_actions() -> None:
    default = _parser().parse_args(["dashboard"])
    build = _parser().parse_args(["dashboard", "build"])

    assert default.action == "open"
    assert build.action == "build"


def test_protocol_publish_supports_scheduled_change_detection() -> None:
    manual = _parser().parse_args(["protocol", "publish"])
    scheduled = _parser().parse_args(["protocol", "publish", "--if-changed"])

    assert manual.if_changed is False
    assert scheduled.if_changed is True
