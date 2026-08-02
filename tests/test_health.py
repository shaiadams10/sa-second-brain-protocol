from __future__ import annotations

import json

from second_brain_protocol.health import _current_run_history, _current_schedule
from second_brain_protocol.state import StateStore


def test_health_hides_pre_cutover_daily_and_weekly_history(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    daily = store.start_run("daily", "retired-model", "medium")
    store.finish_run(daily, "failed", error="retired Daily failure")
    project = store.start_run("project-history", "current-model", "medium")
    store.finish_run(project, "completed")
    pipeline = store.start_pipeline_run(
        "weekly", trigger="scheduled", stage="retired_stage"
    )
    store.finish_pipeline_run(pipeline, "failed", error="retired Weekly failure")
    store.set_meta(
        "daily-weekly-governed-cutover-baseline-v1",
        json.dumps({"completed_at": "2099-01-01T00:00:00+00:00"}),
    )

    runs, pipelines = _current_run_history(store)

    assert [item["kind"] for item in runs] == ["project-history"]
    assert pipelines == []


def test_health_hides_pre_cutover_scheduler_receipt(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.set_meta(
        "daily-weekly-governed-cutover-baseline-v1",
        json.dumps({"completed_at": "2026-08-02T11:33:37+00:00"}),
    )

    schedule = _current_schedule(
        store,
        {
            "installed": True,
            "state": "Ready",
            "last_run": "2026-08-01T22:30:00-04:00",
            "last_result": 1,
            "missed_runs": 0,
            "next_run": "2026-08-02T22:30:00-04:00",
        },
    )

    assert schedule["last_run"] is None
    assert schedule["last_result"] is None
    assert schedule["next_run"] == "2026-08-02T22:30:00-04:00"
