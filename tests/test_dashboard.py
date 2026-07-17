from datetime import UTC, datetime
from pathlib import Path

from second_brain_protocol.config import RuntimePaths
from second_brain_protocol.dashboard import (
    build_snapshot,
    knowledge_layer_for,
    render_dashboard,
)
from second_brain_protocol.security import scan_text
from second_brain_protocol.state import StateStore


def _note(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_dashboard_uses_safe_promoted_knowledge_and_aggregate_review_counts(tmp_path: Path) -> None:
    vault = tmp_path / "Example Person Second Brain"
    paths = RuntimePaths.from_root(tmp_path / "runtime")
    store = StateStore(paths.state)
    store.set_bootstrap_state("completed")

    for folder in (
        "Journal/Daily",
        "Journal/Weekly",
        "Inbox/Review",
        "Projects",
        "Skills",
        "Memory",
        "Goals",
        "System",
    ):
        (vault / folder).mkdir(parents=True, exist_ok=True)

    _note(vault / "Home.md", "# Home\n")
    _note(vault / "Projects" / "Index.md", "# Projects\n")
    _note(vault / "Projects" / "private-project.md", "# Private Project\n")
    _note(vault / "Skills" / "Index.md", "# Skills\n")
    _note(vault / "Skills" / "systems-integration.md", "# Systems integration\n")
    _note(vault / "Memory" / "LongTermMemory.md", "# Memory\n")
    _note(vault / "Memory" / "Patterns.md", "# Patterns\n")
    _note(vault / "Memory" / "Decisions.md", "# Decisions\n")
    _note(vault / "Memory" / "Lessons.md", "# Lessons\n")
    _note(vault / "Goals" / "ActiveGoals.md", """# Goals
<!-- sb:generated goal-suggestions:start -->
- **Build useful systems:** Create calm, reliable products with clear outcomes.
<!-- sb:generated goal-suggestions:end -->
""")
    _note(vault / "Inbox" / "Review" / "Review-2026-07-16.md", "# Review\n")
    _note(vault / "Journal" / "Weekly" / "2026-W29.md", """# Week
<!-- sb:generated weekly:start -->
Connected several project lessons into a reusable workflow.
<!-- sb:generated weekly:end -->
""")
    fake_path = "C:\\" + "Users\\private\\secret.txt"
    fake_secret = "sk-" + "abcdefghijklmnopqrstuvwxyz" + "123456"
    _note(vault / "Journal" / "Daily" / "2026-07-16.md", f"""# Today
<!-- sb:generated daily:start -->
- Shipped the safe dashboard generator.
- Kept {fake_path} and {fake_secret} out of the view.
<!-- sb:generated daily:end -->
""")

    store.upsert_project({"id": "project-1", "name": "Private Project", "classification": "first-party"})
    store.set_project_presence("project-1", present=True)
    store.add_evidence(
        source_type="codex",
        source_ref="session-digest-1",
        kind="session_digest",
        project_id="project-1",
        occurred_at="2026-07-16T14:00:00+00:00",
        payload={"session_id": "session-1", "project_ids": ["project-1"]},
    )
    evidence_id, _ = store.add_evidence(
        source_type="interview",
        source_ref="safe-observation",
        kind="explicit_profile_answer",
        occurred_at="2026-07-16T14:00:00+00:00",
        payload={"answer": "Prefers clear, testable outcomes."},
    )
    store.add_observation(
        {
            "kind": "work_style",
            "subject": "Outcome ownership",
            "claim": "Prefers clear, testable outcomes and owns the result.",
            "evidence_refs": [evidence_id],
            "confidence": 0.94,
            "source_count": 3,
            "project_count": 2,
            "sensitivity": "normal",
            "promotion_tier": "automatic",
            "status": "promoted",
        }
    )
    store.add_observation(
        {
            "kind": "clarification",
            "subject": "Hidden review item",
            "claim": "PENDING-RAW-SECRET should never appear in the dashboard.",
            "evidence_refs": [evidence_id],
            "confidence": 0.4,
            "source_count": 1,
            "project_count": 0,
            "sensitivity": "sensitive",
            "promotion_tier": "review",
            "status": "pending",
            "payload": {"question": "Keep this private?"},
        }
    )
    store.upsert_pattern_signal(
        {
            "pattern_key": "clear-outcomes",
            "kind": "work_style",
            "label": "Clear outcome framing",
            "claim": "Frames work around a concrete result.",
            "evidence_refs": [evidence_id],
            "confidence": 0.8,
            "explicit": False,
            "session_count": 2,
            "date_count": 2,
            "project_count": 1,
            "status": "tracking",
        }
    )
    run_id = store.start_run("daily", "example-model", "medium")
    store.finish_run(run_id, "completed", evidence_count=2)

    snapshot = build_snapshot(
        paths,
        vault,
        now=datetime(2026, 7, 16, 18, 0, tzinfo=UTC),
        schedule={
            "installed": True,
            "state": "Ready",
            "last_run": "2026-07-15T22:30:00-04:00",
            "next_run": "2026-07-16T22:30:00-04:00",
            "last_result": 0,
            "missed_runs": 0,
        },
    )
    rendered = render_dashboard(snapshot)

    assert snapshot["status"]["label"] == "Brain is up to date"
    assert snapshot["activity"]["projects"][0]["name"] == "Private Project"
    assert snapshot["review"]["questions"] == 1
    assert snapshot["insights"][0]["subject"] == "Outcome ownership"
    assert snapshot["knowledge"]["counts"] == {"new": 1, "all": 1, "confirmed": 0, "removed": 0}
    assert snapshot["knowledge"]["cards"][0]["subject"] == "Outcome ownership"
    assert snapshot["knowledge"]["cards"][0]["layer"] == "about_shai"
    assert snapshot["knowledge"]["default_layer"] == "about_shai"
    assert [item["key"] for item in snapshot["knowledge"]["layers"]] == [
        "about_shai",
        "professional_profile",
        "operating_preferences",
        "project_knowledge",
    ]
    assert snapshot["knowledge"]["layers"][0]["label"] == "About Example"
    assert snapshot["knowledge"]["layers"][0]["short_label"] == "About Example"
    assert snapshot["knowledge"]["layers"][0]["count"] == 1
    assert snapshot["actions"] == {"enabled": False, "csrf_token": ""}
    assert "PENDING-RAW-SECRET" not in rendered
    assert fake_path not in rendered
    assert fake_secret not in rendered
    assert "__DASHBOARD_DATA__" not in rendered
    assert "obsidian://open" in rendered
    assert "https://" not in rendered and "http://" not in rendered
    assert "<svg" not in rendered.casefold()
    assert not scan_text(rendered, "dashboard.html", block_paths=True)


def test_knowledge_layers_separate_self_professional_operating_and_project() -> None:
    assert knowledge_layer_for({"kind": "voice_style"}) == "about_shai"
    assert knowledge_layer_for({"kind": "education"}) == "professional_profile"
    assert knowledge_layer_for({"kind": "preference"}) == "operating_preferences"
    assert knowledge_layer_for({"kind": "project_fact"}) == "project_knowledge"
    assert knowledge_layer_for({"kind": "lesson"}) == "project_knowledge"
    assert (
        knowledge_layer_for(
            {
                "kind": "preference",
                "payload": {"knowledge_layer": "about_shai"},
            }
        )
        == "about_shai"
    )


def test_dashboard_output_is_derived_runtime_state_not_vault_content(tmp_path: Path) -> None:
    paths = RuntimePaths.from_root(tmp_path / "runtime")
    vault = tmp_path / "vault"
    assert paths.dashboard == (tmp_path / "runtime" / "dashboard").resolve()
    assert paths.dashboard.is_relative_to(paths.root)
    assert not paths.dashboard.is_relative_to(vault)
