from datetime import date

from second_brain_protocol.orchestrator import _weekly_target_period


def test_weekly_target_uses_current_week_on_weekend_and_previous_afterward() -> None:
    assert _weekly_target_period(date(2026, 7, 18)) == "2026-W29"  # Saturday
    assert _weekly_target_period(date(2026, 7, 19)) == "2026-W29"  # Sunday catch-up
    assert _weekly_target_period(date(2026, 7, 20)) == "2026-W29"  # Monday catch-up
    assert _weekly_target_period(date(2026, 7, 24)) == "2026-W29"  # Friday catch-up

