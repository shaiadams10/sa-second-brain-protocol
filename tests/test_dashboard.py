from datetime import UTC, datetime
from pathlib import Path

from second_brain_protocol.config import RuntimePaths
from second_brain_protocol.codex_account import normalize_rate_limits
from second_brain_protocol.dashboard import (
    _knowledge_deck,
    _question_deck,
    _summary_cost,
    _summary_visuals,
    build_snapshot,
    default_knowledge_layer,
    knowledge_layer_for,
    personalize_knowledge_text,
    render_dashboard,
)
from second_brain_protocol.security import scan_text
from second_brain_protocol.state import StateStore


def _note(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_daily_activity_bullets_become_visual_stats_and_chips() -> None:
    visuals = _summary_visuals(
        [
            {
                "title": "Deterministic activity ledger",
                "paragraphs": [],
                "items": [
                    "Project deltas: 10 across Angel, HyperFrames, Portfolio",
                    "Change types: classification_changed (5), files_changed (4), stack_changed (2)",
                    "Agent sessions evaluated: 6 across 6 attributed projects",
                    "Recurring patterns: 1 tracking, 1 promoted, 0 awaiting review",
                ],
            }
        ]
    )

    assert [item["value"] for item in visuals["stats"]] == [10, 6, 6, 1]
    assert [item["title"] for item in visuals["groups"]] == [
        "Projects touched",
        "Change signals",
        "Session coverage",
        "Pattern status",
    ]
    assert visuals["groups"][1]["chips"][0] == {"label": "Classification", "value": 5}


def test_summary_cost_uses_exact_split_or_honest_legacy_range() -> None:
    exact = _summary_cost(
        [
            {
                "model": "gpt-5.6-luna",
                "details_available": True,
                "input_tokens": 100_000,
                "cached_input_tokens": 20_000,
                "cache_write_input_tokens": 0,
                "output_tokens": 10_000,
            }
        ]
    )
    legacy = _summary_cost(
        [
            {
                "model": "gpt-5.6-luna",
                "details_available": False,
                "total_tokens": 58_658,
            }
        ]
    )

    assert exact["exact_split"] is True
    assert exact["estimate_low_usd"] == exact["estimate_high_usd"] == 0.142
    assert legacy["exact_split"] is False
    assert legacy["estimate_low_usd"] == 0.058658
    assert legacy["estimate_high_usd"] == 0.351948


def test_codex_rate_limit_snapshot_keeps_only_display_fields() -> None:
    snapshot = normalize_rate_limits(
        {
            "rateLimitsByLimitId": {
                "codex": {
                    "planType": "plus",
                    "primary": {
                        "usedPercent": 37,
                        "windowDurationMins": 10080,
                        "resetsAt": 1784852749,
                    },
                    "credits": {"balance": "private-detail"},
                }
            }
        },
        {"dailyUsageBuckets": [{"startDate": "2026-07-17", "tokens": 52_180_791}]},
        now=datetime(2026, 7, 17, 15, 0, tzinfo=UTC),
    )

    assert snapshot["available"] is True
    assert snapshot["included_plan"] is True
    assert snapshot["windows"][0]["label"] == "7-day Codex window"
    assert snapshot["windows"][0]["used_percent"] == 37
    assert snapshot["windows"][0]["remaining_percent"] == 63
    assert snapshot["windows"][0]["observed_tokens"] == 52_180_791
    assert "credits" not in snapshot


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
### Deterministic activity ledger

- Shipped the safe dashboard generator.
- Kept {fake_path} and {fake_secret} out of the view.

### Evidence-backed synthesis

The dashboard work was completed and verified.
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
            "claim": "The user prefers clear, testable outcomes and owns the result.",
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
            "subject": "Profile privacy",
            "claim": "PENDING-RAW-SECRET should never appear in the dashboard.",
            "evidence_refs": [evidence_id],
            "confidence": 0.4,
            "source_count": 1,
            "project_count": 0,
            "sensitivity": "sensitive",
            "promotion_tier": "review",
            "status": "pending",
            "payload": {"question": "Should this profile claim remain private or be safe for career use?"},
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
    run_id = store.start_run("daily", "gpt-5.6-luna", "medium")
    usage = {
        "input_tokens": 1200,
        "cached_input_tokens": 800,
        "cache_write_input_tokens": 0,
        "output_tokens": 300,
        "reasoning_output_tokens": 100,
        "total_tokens": 1500,
        "model_calls": 1,
        "cached_result": False,
        "details_available": True,
        "source": "codex-json",
    }
    store.finish_run(run_id, "completed", evidence_count=2, usage=usage)
    store.replace_summary_runs("daily", "2026-07-16", [run_id])

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
        codex_usage_snapshot={
            "available": True,
            "included_plan": True,
            "windows": [
                {
                    "label": "7-day Codex window",
                    "used_percent": 37,
                    "remaining_percent": 63,
                    "starts_at": "2026-07-16T00:00:00+00:00",
                    "resets_at": "2026-07-23T20:25:49+00:00",
                    "observed_tokens": 100_000,
                }
            ],
        },
    )
    rendered = render_dashboard(snapshot)

    assert snapshot["status"]["label"] == "Brain is up to date"
    assert snapshot["activity"]["projects"][0]["name"] == "Private Project"
    assert snapshot["review"]["questions"] == 1
    assert snapshot["insights"][0]["subject"] == "Outcome ownership"
    assert snapshot["knowledge"]["counts"] == {"new": 1, "all": 1, "confirmed": 0, "removed": 0}
    assert snapshot["knowledge"]["cards"][0]["subject"] == "Outcome ownership"
    assert snapshot["knowledge"]["cards"][0]["claim"].startswith("Example prefers")
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
    assert snapshot["briefing"]["daily"]["sections"][0]["title"] == "Deterministic activity ledger"
    assert snapshot["summaries"]["counts"] == {"daily": 1, "weekly": 1}
    assert snapshot["summaries"]["daily"][0]["highlights"][0] == "Shipped the safe dashboard generator."
    assert snapshot["summaries"]["daily"][0]["usage"]["total_tokens"] == 1500
    assert snapshot["summaries"]["daily"][0]["usage"]["cached_input_tokens"] == 800
    assert snapshot["summaries"]["daily"][0]["usage"]["pricing"]["estimate_low_usd"] == 0.00228
    assert snapshot["summaries"]["daily"][0]["usage"]["codex_impact"]["percentage_points"] == 0.555
    assert snapshot["summaries"]["codex_usage"]["windows"][0]["used_percent"] == 37
    assert snapshot["questions"]["cards"][0]["question"] == "Should this profile claim remain private or be safe for career use?"
    assert snapshot["actions"] == {"enabled": False, "csrf_token": ""}
    assert "PENDING-RAW-SECRET" not in rendered
    assert "Should this profile claim remain private or be safe for career use?" in rendered
    assert fake_path not in rendered
    assert fake_secret not in rendered
    assert "__DASHBOARD_DATA__" not in rendered
    assert "summary-collapsed-badges" in rendered
    assert "Narrative synthesis available below" not in rendered
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


def test_project_decision_uses_an_attribution_stamp(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "Example Person Second Brain"
    (vault / "Projects").mkdir(parents=True)
    _note(vault / "Projects" / "Index.md", "# Projects\n")
    _note(vault / "Projects" / "animated-demo.md", "# Animated Demo\n")
    store = StateStore(tmp_path / "state.sqlite")
    store.upsert_project(
        {"id": "project-demo", "name": "Animated Demo", "classification": "first-party"}
    )
    evidence_id, _ = store.add_evidence(
        source_type="session-digest",
        source_ref="session-digest:codex:demo",
        kind="session_digest",
        project_id="project-demo",
        occurred_at="2026-07-16T14:00:00Z",
        payload={"session_id": "demo", "project_ids": ["project-demo"]},
    )
    observation_id = store.add_observation(
        {
            "kind": "decision",
            "subject": "Animated Demo preview access",
            "claim": "The Animated Demo preview server is available on the local network.",
            "evidence_refs": [evidence_id],
            "confidence": 0.99,
            "source_count": 1,
            "project_count": 1,
            "explicit": True,
            "status": "promoted",
        }
    )

    card = next(item for item in _knowledge_deck(store, vault)["cards"] if item["id"] == observation_id)

    assert card["scope"] == "project"
    assert card["layer"] == "project_knowledge"
    assert card["project_names"] == ["Animated Demo"]
    assert card["project_stamp"] == "Animated Demo"
    assert "animated-demo.md" in card["url"]


def test_one_off_project_preference_is_not_a_curate_card(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "Example Person Second Brain"
    (vault / "Projects").mkdir(parents=True)
    _note(vault / "Projects" / "Index.md", "# Projects\n")
    store = StateStore(tmp_path / "state.sqlite")
    evidence_id, _ = store.add_evidence(
        source_type="session-digest",
        source_ref="session-digest:antigravity:unknown",
        kind="session_digest",
        payload={"session_id": "unknown", "project_ids": []},
    )
    store.add_observation(
        {
            "kind": "preference",
            "subject": "Network preview",
            "claim": "the user requested a network preview for this demo.",
            "evidence_refs": [evidence_id],
            "confidence": 0.99,
            "source_count": 1,
            "project_count": 0,
            "explicit": True,
            "status": "promoted",
        }
    )

    assert _knowledge_deck(store, vault)["cards"] == []


def test_reusable_preference_from_one_project_stays_in_how_i_work(tmp_path: Path) -> None:
    vault = tmp_path / "Example Person Second Brain"
    (vault / "Projects").mkdir(parents=True)
    _note(vault / "Projects" / "Index.md", "# Projects\n")
    store = StateStore(tmp_path / "state.sqlite")
    store.upsert_project(
        {"id": "project-demo", "name": "Animated Demo", "classification": "first-party"}
    )
    evidence_id, _ = store.add_evidence(
        source_type="session-digest",
        source_ref="session-digest:codex:visual-quality",
        kind="session_digest",
        project_id="project-demo",
        payload={"session_id": "visual-quality", "project_ids": ["project-demo"]},
    )
    store.add_observation(
        {
            "kind": "preference",
            "subject": "Visual quality",
            "claim": "the user prefers premium, realistic interfaces and rejects generic AI-looking design.",
            "evidence_refs": [evidence_id],
            "confidence": 0.98,
            "source_count": 1,
            "project_count": 1,
            "explicit": True,
            "status": "promoted",
        }
    )

    card = _knowledge_deck(store, vault)["cards"][0]

    assert card["scope"] == "global"
    assert card["layer"] == "operating_preferences"
    assert card["project_stamp"] == ""


def test_routine_project_inventory_does_not_become_a_curate_card(tmp_path: Path) -> None:
    vault = tmp_path / "Example Person Second Brain"
    (vault / "Projects").mkdir(parents=True)
    _note(vault / "Projects" / "Index.md", "# Projects\n")
    store = StateStore(tmp_path / "state.sqlite")
    evidence_id, _ = store.add_evidence(
        source_type="project-activity",
        source_ref="project:portfolio",
        kind="project_delta",
        payload={},
        project_id="project-portfolio",
    )
    store.add_observation(
        {
            "kind": "project_fact",
            "subject": "Portfolio application",
            "claim": "The portfolio uses Vite, React, TypeScript, and Three.js visual elements.",
            "evidence_refs": [evidence_id],
            "confidence": 0.95,
            "status": "promoted",
        }
    )

    assert _knowledge_deck(store, vault)["cards"] == []


def test_project_question_shows_destination_and_tailored_answer_starter(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "Example Person Second Brain"
    (vault / "Inbox" / "Review" / "Groups").mkdir(parents=True)
    store = StateStore(tmp_path / "state.sqlite")
    store.upsert_project(
        {"id": "project-angel", "name": "First Party Project With Existing Brain", "classification": "first-party"}
    )
    evidence_id, _ = store.add_evidence(
        source_type="project-activity",
        source_ref="project:angel",
        kind="project_delta",
        payload={},
        project_id="project-angel",
    )
    store.add_observation(
        {
            "kind": "clarification",
            "subject": "First Party Project With Existing Brain contribution ownership",
            "claim": "Which First Party Project With Existing Brain components did the user direct or implement?",
            "question": "Which First Party Project With Existing Brain components did the user direct or implement?",
            "evidence_refs": [evidence_id],
            "confidence": 0.8,
            "status": "pending",
        }
    )

    card = _question_deck(store, vault)["cards"][0]

    assert card["scope"] == "project"
    assert card["project_stamp"] == "First Party Project With Existing Brain"
    assert card["destination"] == "Project knowledge"
    assert "not saved as personality" in card["destination_detail"]
    assert "First Party Project With Existing Brain" in card["placeholder"]
    assert card["suggestions"][0]["label"] == "My contribution"


def test_portfolio_word_outweighs_generic_agent_evidence_attribution(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "Example Person Second Brain"
    (vault / "Inbox" / "Review" / "Groups").mkdir(parents=True)
    store = StateStore(tmp_path / "state.sqlite")
    for project_id, name in (
        ("project-agents", "AgentSkillsHub"),
        ("project-remotion", "Remotion"),
        ("project-portfolio", "Contrasting First Party Project"),
    ):
        store.upsert_project(
            {"id": project_id, "name": name, "classification": "first-party"}
        )
    evidence_refs = []
    for project_id in ("project-agents", "project-remotion"):
        evidence_id, _ = store.add_evidence(
            source_type="session-digest",
            source_ref=f"session:{project_id}",
            kind="session_digest",
            project_id=project_id,
            payload={"project_ids": [project_id]},
        )
        evidence_refs.append(evidence_id)
    store.add_observation(
        {
            "kind": "clarification",
            "subject": "Agent-assisted demo status",
            "claim": "Which completed demos are shipped, portfolio-ready, experimental, or archived?",
            "question": "Which completed demos are shipped, portfolio-ready, experimental, or archived?",
            "evidence_refs": evidence_refs,
            "confidence": 0.95,
            "project_count": 2,
            "status": "pending",
        }
    )

    card = _question_deck(store, vault)["cards"][0]

    assert card["project_names"] == ["Contrasting First Party Project"]
    assert card["project_stamp"] == "Contrasting First Party Project"
    assert "Contrasting First Party Project" in card["placeholder"]
    assert card["suggestions"][0]["label"] == "Classify demos"
    assert "shipped demos" in card["placeholder"]
    assert "experimental demos" in card["placeholder"].casefold()


def test_knowledge_deck_batches_project_and_evidence_reads(
    tmp_path: Path, monkeypatch
) -> None:
    vault = tmp_path / "Example Person Second Brain"
    (vault / "Projects").mkdir(parents=True)
    store = StateStore(tmp_path / "state.sqlite")
    store.upsert_project(
        {"id": "project-demo", "name": "Animated Demo", "classification": "first-party"}
    )
    for index in range(4):
        evidence_id, _ = store.add_evidence(
            source_type="session-digest",
            source_ref=f"session:{index}",
            kind="session_digest",
            project_id="project-demo",
            payload={"session_id": str(index), "project_ids": ["project-demo"]},
        )
        store.add_observation(
            {
                "kind": "decision",
                "subject": f"Animated Demo decision {index}",
                "claim": f"Animated Demo decision {index} changes the release boundary.",
                "evidence_refs": [evidence_id],
                "confidence": 0.9,
                "status": "promoted",
            }
        )
    calls = {"projects": 0, "evidence": 0}
    original_projects = store.projects
    original_evidence = store.evidence_by_ids

    def counted_projects():
        calls["projects"] += 1
        return original_projects()

    def counted_evidence(evidence_ids):
        calls["evidence"] += 1
        return original_evidence(evidence_ids)

    monkeypatch.setattr(store, "projects", counted_projects)
    monkeypatch.setattr(store, "evidence_by_ids", counted_evidence)

    assert len(_knowledge_deck(store, vault)["cards"]) == 4
    assert calls == {"projects": 1, "evidence": 1}


def test_generic_src_folder_question_is_deferred_not_project_stamped(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "Example Person Second Brain"
    (vault / "Inbox" / "Review" / "Groups").mkdir(parents=True)
    store = StateStore(tmp_path / "state.sqlite")
    store.upsert_project(
        {"id": "folder-src", "name": "src", "classification": "review"}
    )
    evidence_id, _ = store.add_evidence(
        source_type="filesystem",
        source_ref="folder:src",
        kind="project_inventory",
        project_id="folder-src",
        payload={"project_ids": ["folder-src"]},
    )
    store.add_observation(
        {
            "kind": "clarification",
            "subject": "Folder project boundaries",
            "claim": "Should these source folders be separate projects?",
            "question": "What are their canonical project IDs or repository names?",
            "evidence_refs": [evidence_id],
            "confidence": 0.9,
            "status": "pending",
        }
    )

    deck = _question_deck(store, vault)

    assert deck["cards"] == []
    assert deck["deferred_count"] == 1


def test_curate_starts_on_first_layer_that_needs_attention() -> None:
    layers = [
        {"key": "about_shai", "new": 0},
        {"key": "professional_profile", "new": 3},
        {"key": "operating_preferences", "new": 1},
    ]

    assert default_knowledge_layer(layers) == "professional_profile"
    assert default_knowledge_layer([{**item, "new": 0} for item in layers]) == "about_shai"


def test_knowledge_cards_use_owner_name_instead_of_generic_user_wording() -> None:
    assert personalize_knowledge_text("The user ships work. The user's process is careful.", "the user") == (
        "the user ships work. the user's process is careful."
    )


def test_dashboard_output_is_derived_runtime_state_not_vault_content(tmp_path: Path) -> None:
    paths = RuntimePaths.from_root(tmp_path / "runtime")
    vault = tmp_path / "vault"
    assert paths.dashboard == (tmp_path / "runtime" / "dashboard").resolve()
    assert paths.dashboard.is_relative_to(paths.root)
    assert not paths.dashboard.is_relative_to(vault)
