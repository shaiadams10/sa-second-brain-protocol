from pathlib import Path

import pytest

from second_brain_protocol.curate import (
    add_curate_candidate,
    correct_project_knowledge_attribution,
    require_main_vault_context,
)
from second_brain_protocol.dashboard import _knowledge_deck
from second_brain_protocol.state import StateStore


def _note(path: Path, *, section: str = "canonical") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        "id: fixture\n"
        "type: identity\n"
        "confidence: 0\n"
        "provenance: []\n"
        "first_seen: null\n"
        "last_verified: null\n"
        "---\n\n"
        "# Note\n\n"
        f"<!-- sb:generated {section}:start -->\n"
        "_No generated content yet._\n"
        f"<!-- sb:generated {section}:end -->\n",
        encoding="utf-8",
    )


def test_implied_preference_becomes_an_unconfirmed_curate_card(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "Example Person Second Brain"
    note = vault / "Identity" / "Preferences.md"
    _note(note, section="preferences")
    store = StateStore(tmp_path / "state.sqlite")

    result = add_curate_candidate(
        vault,
        store,
        layer="operating_preferences",
        kind="preference",
        subject="Workflow explanations",
        claim="the user prefers a clean step-by-step flow when a workflow becomes complicated.",
    )

    observation = store.observation(result["id"])
    assert result["status"] == "new"
    assert observation is not None
    assert observation["status"] == "promoted"
    assert observation["payload"]["explicit"] is False
    assert observation["payload"]["curate_candidate"] is True
    assert observation["payload"]["knowledge_layer"] == "operating_preferences"
    assert result["id"] in note.read_text(encoding="utf-8")

    card = next(
        item
        for item in _knowledge_deck(store, vault)["cards"]
        if item["id"] == result["id"]
    )
    assert card["layer"] == "operating_preferences"
    assert card["feedback"] is None


def test_curate_capture_is_idempotent(tmp_path: Path) -> None:
    vault = tmp_path / "Example Person Second Brain"
    note = vault / "Identity" / "WorkStyle.md"
    _note(note)
    store = StateStore(tmp_path / "state.sqlite")
    arguments = {
        "layer": "about_shai",
        "kind": "work_style",
        "subject": "Debugging approach",
        "claim": "the user starts by making a failing system understandable.",
    }

    first = add_curate_candidate(vault, store, **arguments)
    second = add_curate_candidate(vault, store, **arguments)

    assert first["id"] == second["id"]
    assert first["created"] is True
    assert second["created"] is False
    assert note.read_text(encoding="utf-8").count(first["id"]) == 1


def test_high_certainty_capture_skips_the_new_queue(tmp_path: Path) -> None:
    vault = tmp_path / "Example Person Second Brain"
    _note(vault / "Identity" / "Preferences.md", section="preferences")
    store = StateStore(tmp_path / "state.sqlite")

    result = add_curate_candidate(
        vault,
        store,
        layer="operating_preferences",
        kind="preference",
        subject="Response ordering",
        claim="the user wants brain-capture confirmations at the top of the response.",
        explicit=True,
        confirmed=True,
        confidence=1.0,
    )

    assert result["status"] == "confirmed"
    card = next(
        item
        for item in _knowledge_deck(store, vault)["cards"]
        if item["id"] == result["id"]
    )
    assert card["feedback"] == "liked"
    assert _knowledge_deck(store, vault)["counts"]["new"] == 0


def test_curate_rejects_layer_kind_mismatches(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")

    with pytest.raises(ValueError, match="does not belong"):
        add_curate_candidate(
            tmp_path,
            store,
            layer="professional_profile",
            kind="preference",
            subject="Format",
            claim="the user prefers concise status notes.",
        )

    assert store.observations() == []


def test_curate_rejects_conflicts_before_creating_evidence(tmp_path: Path) -> None:
    vault = tmp_path / "Example Person Second Brain"
    _note(vault / "Identity" / "Preferences.md", section="preferences")
    store = StateStore(tmp_path / "state.sqlite")
    add_curate_candidate(
        vault,
        store,
        layer="operating_preferences",
        kind="preference",
        subject="Status format",
        claim="the user prefers short status updates.",
    )
    evidence_count = len(store.evidence())

    with pytest.raises(ValueError, match="conflicts with existing canonical"):
        add_curate_candidate(
            vault,
            store,
            layer="operating_preferences",
            kind="preference",
            subject="Status format",
            claim="the user prefers long status updates.",
        )

    assert len(store.evidence()) == evidence_count


def test_project_knowledge_requires_exact_attribution(tmp_path: Path) -> None:
    vault = tmp_path / "Example Person Second Brain"
    _note(vault / "Memory" / "Decisions.md")
    store = StateStore(tmp_path / "state.sqlite")
    store.upsert_project(
        {"id": "project-demo", "name": "Demo", "classification": "first-party"}
    )
    store.set_project_presence("project-demo", present=True)

    with pytest.raises(ValueError, match="requires one exact project"):
        add_curate_candidate(
            vault,
            store,
            layer="project_knowledge",
            kind="decision",
            subject="Demo renderer",
            claim="The Demo uses one renderer.",
        )

    result = add_curate_candidate(
        vault,
        store,
        layer="project_knowledge",
        kind="decision",
        subject="Demo renderer",
        claim="The Demo uses one renderer.",
        project="Demo",
        allow_cross_project=True,
    )
    evidence = store.evidence_by_ids(store.observation(result["id"])["evidence_refs"])
    assert evidence[0]["project_id"] == "project-demo"


def test_project_capture_from_vault_requires_explicit_cross_project_authorization(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "Example Person Second Brain"
    _note(vault / "Memory" / "Decisions.md")
    store = StateStore(tmp_path / "state.sqlite")
    for project in (
        {
            "id": "project-vault",
            "name": "Example Person Second Brain",
            "classification": "first-party",
            "managed_vault": True,
        },
        {
            "id": "project-public-protocol",
            "name": "External Protocol Project",
            "classification": "first-party",
        },
    ):
        store.upsert_project(project)
        store.set_project_presence(project["id"], present=True)

    with pytest.raises(RuntimeError, match="explicit cross-project authorization"):
        add_curate_candidate(
            vault,
            store,
            layer="project_knowledge",
            kind="decision",
            subject="Daily engine",
            claim="The public protocol uses a separate Daily engine.",
            project="External Protocol Project",
        )

    result = add_curate_candidate(
        vault,
        store,
        layer="project_knowledge",
        kind="decision",
        subject="Daily engine",
        claim="The public protocol uses a separate Daily engine.",
        project="External Protocol Project",
        allow_cross_project=True,
    )

    assert result["project"] == "External Protocol Project"


def test_owner_correction_reattributes_promoted_project_knowledge_and_canonical_line(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "Example Person Second Brain"
    note = vault / "Memory" / "Decisions.md"
    _note(note)
    store = StateStore(tmp_path / "state.sqlite")
    for project in (
        {
            "id": "project-vault",
            "name": "Example Person Second Brain",
            "classification": "first-party",
            "managed_vault": True,
        },
        {
            "id": "project-public-protocol",
            "name": "External Protocol Project",
            "classification": "first-party",
        },
    ):
        store.upsert_project(project)
        store.set_project_presence(project["id"], present=True)
    observation_id = store.add_observation(
        {
            "kind": "decision",
            "subject": "Daily engine",
            "claim": "External Protocol Project uses governed extraction.",
            "evidence_refs": [],
            "confidence": 1.0,
            "status": "promoted",
            "project_id": "project-public-protocol",
            "project_ids": ["project-public-protocol"],
        }
    )
    text = note.read_text(encoding="utf-8").replace(
        "_No generated content yet._",
        f"- External Protocol Project uses governed extraction. ^{observation_id}",
    )
    note.write_text(text, encoding="utf-8")

    result = correct_project_knowledge_attribution(
        vault,
        store,
        observation_id=observation_id,
        project="Example Person Second Brain",
        replace_project_name="External Protocol Project",
        reason="Owner confirmed these are separate projects.",
    )

    corrected = store.observation(observation_id)
    assert result["status"] == "corrected"
    assert corrected["status"] == "promoted"
    assert corrected["claim"] == (
        "Example Person Second Brain uses governed extraction."
    )
    assert corrected["payload"]["project_id"] == "project-vault"
    assert corrected["payload"]["project_attribution_source"] == (
        "explicit_owner_correction"
    )
    rendered = note.read_text(encoding="utf-8")
    assert "Example Person Second Brain uses governed extraction." in rendered
    assert "External Protocol Project uses governed extraction." not in rendered


def test_curate_route_is_blocked_outside_main_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    outside = tmp_path / "another-project"
    vault.mkdir()
    outside.mkdir()

    require_main_vault_context(vault, current_directory=vault / "nested")
    with pytest.raises(RuntimeError, match="main second-brain vault"):
        require_main_vault_context(vault, current_directory=outside)
