from datetime import date
from pathlib import Path

import pytest

from second_brain_protocol.markdown import update_generated_file
from second_brain_protocol.publisher import (
    publish_interview_profile,
    publish_model_output,
    promote_observation_group,
    write_review_artifacts,
)
from second_brain_protocol.state import StateStore


def _note(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\nid: fixture\ntype: memory\naliases: []\nconfidence: 0\nprovenance: []\nfirst_seen: null\nlast_verified: null\ntags: [test]\n---\n\n"
        "# Note\n\n<!-- sb:generated canonical:start -->\n_No generated content yet._\n<!-- sb:generated canonical:end -->\n\nmanual\n",
        encoding="utf-8",
    )


def test_generated_file_retries_a_transient_windows_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = tmp_path / "Note.md"
    _note(note)
    original_replace = Path.replace
    attempts = 0

    def flaky_replace(source: Path, destination: Path) -> Path:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError(13, "transient editor lock", str(destination))
        return original_replace(source, destination)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    monkeypatch.setattr("second_brain_protocol.markdown.time.sleep", lambda _: None)

    update_generated_file(note, "canonical", "Recovered publication.")

    assert attempts == 3
    assert "Recovered publication." in note.read_text(encoding="utf-8")
    assert not note.with_suffix(".md.tmp").exists()


def test_explicit_fact_promotes_but_public_claim_waits(tmp_path: Path) -> None:
    for relative in ("Memory/LongTermMemory.md", "Identity/Persona.md"):
        _note(tmp_path / relative)
    store = StateStore(tmp_path / "state.sqlite")
    ev1, _ = store.add_evidence(source_type="interview", source_ref="interview:a", kind="answer", payload={"session_id": "one"}, project_id="p1", occurred_at="2026-01-01")
    ev2, _ = store.add_evidence(source_type="codex", source_ref="codex:b", kind="message", payload={"session_id": "two"}, project_id="p2", occurred_at="2026-01-02")
    output = {
        "summary": "Summary",
        "observations": [
            {"kind": "explicit_fact", "subject": "name", "claim": "the user prefers the user.", "evidence_refs": [ev1], "confidence": 1.0, "explicit": True, "public_claim": False},
            {"kind": "personality", "subject": "career", "claim": "Public superlative", "evidence_refs": [ev1, ev2], "confidence": 0.8, "explicit": False, "public_claim": True},
        ],
        "project_updates": [],
        "skill_updates": [],
        "voice_samples": [],
        "review_items": [],
    }
    result = publish_model_output(vault=tmp_path, store=store, output=output, run_kind="daily", evidence_ids=[ev1, ev2])
    assert result["promoted"] == 1 and result["pending"] == 1
    assert "the user prefers the user" in (tmp_path / "Memory/LongTermMemory.md").read_text(encoding="utf-8")
    assert "manual" in (tmp_path / "Memory/LongTermMemory.md").read_text(encoding="utf-8")
    assert store.checkpoint("none") is None


def test_human_review_digest_separates_group_pages_from_machine_ledger(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    evidence_id, _ = store.add_evidence(
        source_type="interview", source_ref="interview:one", kind="answer", payload={}
    )
    records = [
        {
            "kind": "clarification",
            "subject": "Project ownership",
            "claim": "Question: Is this first-party or third-party?",
            "evidence_refs": [evidence_id],
            "confidence": 0.9,
            "status": "pending",
            "question": "Is this first-party or third-party?",
            "review_reason": "ambiguity",
        },
        {
            "kind": "experience",
            "subject": "Public role",
            "claim": "The user led a project.",
            "evidence_refs": [evidence_id],
            "confidence": 0.9,
            "status": "pending",
            "public_claim": True,
            "review_reason": "public-facing claim requires review",
        },
        {
            "kind": "lesson",
            "subject": "Private lesson",
            "claim": "A private implementation lesson.",
            "evidence_refs": [evidence_id],
            "confidence": 0.8,
            "status": "pending",
            "public_claim": False,
            "review_reason": "manual review tier",
        },
    ]
    observation_ids = [store.add_observation(record) for record in records]

    result = write_review_artifacts(tmp_path, store)

    digest = Path(result["digest"]).read_text(encoding="utf-8")
    ledger = Path(result["ledger"]).read_text(encoding="utf-8")
    group_text = "\n".join(Path(path).read_text(encoding="utf-8") for path in result["group_paths"])
    assert result["pending"] == 3
    assert all(observation_id not in digest for observation_id in observation_ids)
    assert evidence_id not in digest
    assert all(observation_id in ledger for observation_id in observation_ids)
    assert evidence_id in ledger
    assert "Technical provenance" in group_text
    assert "sb review approve-group rvg-" in group_text
    assert "sb review answer rvg-" in group_text


def test_group_promotion_updates_notes_and_state_together(tmp_path: Path) -> None:
    _note(tmp_path / "Memory" / "Lessons.md")
    _note(tmp_path / "Goals" / "ActiveGoals.md")
    store = StateStore(tmp_path / "state.sqlite")
    evidence_id, _ = store.add_evidence(
        source_type="interview", source_ref="interview:one", kind="answer", payload={}
    )
    ids = [
        store.add_observation(
            {
                "kind": kind,
                "subject": subject,
                "claim": claim,
                "evidence_refs": [evidence_id],
                "confidence": 0.9,
            }
        )
        for kind, subject, claim in (
            ("lesson", "testing", "A durable testing lesson."),
            ("goal", "shipping", "A durable delivery goal."),
        )
    ]

    promote_observation_group(tmp_path, store, ids)

    assert {store.observation(observation_id)["status"] for observation_id in ids} == {"promoted"}
    assert "A durable testing lesson." in (tmp_path / "Memory" / "Lessons.md").read_text(encoding="utf-8")
    assert "A durable delivery goal." in (tmp_path / "Goals" / "ActiveGoals.md").read_text(encoding="utf-8")


def test_same_output_is_idempotent(tmp_path: Path) -> None:
    _note(tmp_path / "Memory/LongTermMemory.md")
    store = StateStore(tmp_path / "state.sqlite")
    ev, _ = store.add_evidence(source_type="interview", source_ref="interview:a", kind="answer", payload={})
    output = {
        "summary": "Summary",
        "observations": [{"kind": "explicit_fact", "subject": "x", "claim": "One claim", "evidence_refs": [ev], "confidence": 1.0, "explicit": True, "public_claim": False}],
        "project_updates": [], "skill_updates": [], "voice_samples": [], "review_items": [],
    }
    for _ in range(2):
        publish_model_output(vault=tmp_path, store=store, output=output, run_kind="daily", evidence_ids=[ev])
    assert (tmp_path / "Memory/LongTermMemory.md").read_text(encoding="utf-8").count("One claim") == 1
    assert len(store.observations()) == 1


def test_rejected_observation_is_a_tombstone(tmp_path: Path) -> None:
    _note(tmp_path / "Memory/LongTermMemory.md")
    store = StateStore(tmp_path / "state.sqlite")
    ev, _ = store.add_evidence(
        source_type="interview", source_ref="interview:a", kind="answer", payload={}
    )
    output = {
        "summary": "Summary",
        "observations": [
            {
                "kind": "explicit_fact",
                "subject": "x",
                "claim": "Do not repeat this claim",
                "evidence_refs": [ev],
                "confidence": 1.0,
                "explicit": True,
                "public_claim": False,
            }
        ],
        "project_updates": [],
        "skill_updates": [],
        "voice_samples": [],
        "review_items": [],
    }
    first = publish_model_output(
        vault=tmp_path, store=store, output=output, run_kind="daily", evidence_ids=[ev]
    )
    observation_id = store.observations()[0]["id"]
    store.decide_observation(observation_id, "rejected", "Incorrect")
    note = tmp_path / "Memory/LongTermMemory.md"
    before = note.read_text(encoding="utf-8")
    second = publish_model_output(
        vault=tmp_path, store=store, output=output, run_kind="daily", evidence_ids=[ev]
    )
    assert first["promoted"] == 1
    assert second["promoted"] == 0 and second["pending"] == 0
    assert note.read_text(encoding="utf-8") == before
    assert store.observation(observation_id)["status"] == "rejected"


def test_voice_style_promotes_only_after_stable_cross_session_evidence(tmp_path: Path) -> None:
    _note(tmp_path / "Identity/Voice.md")
    store = StateStore(tmp_path / "state.sqlite")
    refs = []
    for index, (session, project, occurred_at) in enumerate(
        [
            ("session-one", "project-one", "2026-01-01T10:00:00Z"),
            ("session-two", "project-two", "2026-01-01T15:00:00Z"),
            ("session-three", "project-one", "2026-01-02T10:00:00Z"),
        ]
    ):
        evidence_id, _ = store.add_evidence(
            source_type="codex",
            source_ref=f"codex:session-{index}",
            kind="message",
            payload={"session_id": session, "role": "user", "text": "Example"},
            project_id=project,
            occurred_at=occurred_at,
        )
        refs.append(evidence_id)
    output = {
        "summary": "Summary",
        "observations": [
            {
                "kind": "voice_style",
                "subject": "directness",
                "claim": "the user prefers direct, practical phrasing.",
                "evidence_refs": refs,
                "confidence": 0.85,
                "explicit": False,
                "public_claim": False,
                "authoritative": False,
            }
        ],
        "project_updates": [],
        "skill_updates": [],
        "voice_samples": [],
        "review_items": [],
    }
    result = publish_model_output(
        vault=tmp_path,
        store=store,
        output=output,
        run_kind="weekly",
        evidence_ids=refs,
    )
    assert result["promoted"] == 1 and result["pending"] == 0
    assert "the user prefers direct, practical phrasing." in (
        tmp_path / "Identity/Voice.md"
    ).read_text(encoding="utf-8")


def test_daily_synthesis_preserves_manual_notes_and_bootstrap_uses_audit_note(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    today = date.today().isoformat()
    daily = tmp_path / "Journal" / "Daily" / f"{today}.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text(
        f"---\ntype: daily\n---\n\n# {today}\n\n"
        "## Personal notes\n\nKeep this manual thought.\n\n"
        "## Automated summary\n\n"
        "<!-- sb:generated daily:start -->\nOld summary.\n<!-- sb:generated daily:end -->\n",
        encoding="utf-8",
    )
    output = {
        "summary": "New evidence-backed summary.",
        "observations": [],
        "project_updates": [],
        "skill_updates": [],
        "voice_samples": [],
        "review_items": [],
    }

    publish_model_output(
        vault=tmp_path, store=store, output=output, run_kind="daily", evidence_ids=[]
    )
    daily_text = daily.read_text(encoding="utf-8")
    assert "Keep this manual thought." in daily_text
    assert "New evidence-backed summary." in daily_text
    assert "Old summary." not in daily_text

    publish_model_output(
        vault=tmp_path, store=store, output=output, run_kind="bootstrap", evidence_ids=[]
    )
    assert daily.read_text(encoding="utf-8") == daily_text
    bootstrap_note = tmp_path / "System" / "Audits" / "Bootstrap" / "Synthesis.md"
    assert "New evidence-backed summary." in bootstrap_note.read_text(encoding="utf-8")


def test_explicit_interview_answer_populates_private_profile(tmp_path: Path) -> None:
    _note(tmp_path / "Identity/Persona.md")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "interview.json").write_text(
        '{"complete": false, "answers": {"preferred_name": "Use the userâ€”or YOUR_NAME."}}',
        encoding="utf-8",
    )
    store = StateStore(tmp_path / "state.sqlite")
    evidence_id, _ = store.add_evidence(
        source_type="interview",
        source_ref="interview:preferred_name:v1",
        kind="explicit_profile_answer",
        payload={"answer": "Use the user—or YOUR_NAME.", "explicit": True},
    )

    result = publish_interview_profile(tmp_path, store, runtime)

    text = (tmp_path / "Identity/Persona.md").read_text(encoding="utf-8")
    assert result["answers_published"] == 1
    assert "**Preferred name:** Use the user—or YOUR_NAME." in text
    assert evidence_id in text


def test_unknown_model_project_id_is_quarantined(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    evidence_id, _ = store.add_evidence(
        source_type="codex",
        source_ref="codex:one",
        kind="artifact",
        payload={"text": "project mention"},
    )
    output = {
        "summary": "Summary",
        "observations": [],
        "project_updates": [
            {
                "project_id": "unassigned-project",
                "name": "Invented Project",
                "summary": "Should not publish.",
                "evidence_refs": [evidence_id],
            }
        ],
        "skill_updates": [],
        "voice_samples": [],
        "review_items": [],
    }

    result = publish_model_output(
        vault=tmp_path,
        store=store,
        output=output,
        run_kind="bootstrap",
        evidence_ids=[evidence_id],
    )

    assert result["projects_written"] == 0
    assert result["pending"] == 1
    assert not (tmp_path / "Projects" / "invented-project.md").exists()


def test_recurring_pattern_registry_accumulates_before_safe_promotion(
    tmp_path: Path,
) -> None:
    _note(tmp_path / "Identity" / "Preferences.md")
    store = StateStore(tmp_path / "state.sqlite")
    results = []
    for index, (session_id, project_id, occurred_at) in enumerate(
        (
            ("session-one", "project-one", "2026-01-01T10:00:00Z"),
            ("session-two", "project-two", "2026-01-02T10:00:00Z"),
            ("session-three", "project-three", "2026-01-03T10:00:00Z"),
        ),
        1,
    ):
        evidence_id, _ = store.add_evidence(
            source_type="session-digest",
            source_ref=f"session-digest:codex:{session_id}",
            kind="session_digest",
            payload={
                "session_id": session_id,
                "project_ids": [project_id],
                "user_messages": [{"text": "Please give me choices and examples."}],
            },
            project_id=project_id,
            occurred_at=occurred_at,
        )
        output = {
            "summary": f"Day {index}",
            "observations": [],
            "pattern_signals": [
                {
                    "pattern_key": "guided-choices-for-abstract-questions",
                    "kind": "protocol_preference",
                    "label": "Guided choices",
                    "claim": "the user prefers concrete choices and examples when a question is abstract.",
                    "evidence_refs": [evidence_id],
                    "confidence": 0.8,
                    "explicit": False,
                }
            ],
            "project_updates": [],
            "skill_updates": [],
            "voice_samples": [],
            "review_items": [],
        }
        results.append(
            publish_model_output(
                vault=tmp_path,
                store=store,
                output=output,
                run_kind="daily",
                evidence_ids=[evidence_id],
            )
        )

    assert [item["patterns_tracking"] for item in results] == [1, 1, 0]
    assert results[-1]["patterns_promoted"] == 1
    pattern = store.pattern_signal("guided-choices-for-abstract-questions")
    assert pattern is not None
    assert pattern["status"] == "promoted"
    assert pattern["session_count"] == 3
    assert pattern["date_count"] == 3
    assert pattern["project_count"] == 3
    assert "concrete choices and examples" in (
        tmp_path / "Identity" / "Preferences.md"
    ).read_text(encoding="utf-8")
    daily = tmp_path / "Journal" / "Daily" / f"{date.today().isoformat()}.md"
    assert "Deterministic activity ledger" in daily.read_text(encoding="utf-8")


def test_weekly_resolves_objective_question_from_authoritative_delta(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    evidence_id, _ = store.add_evidence(
        source_type="project-activity",
        source_ref="project-activity:portfolio:deployed",
        kind="project_delta",
        payload={"changes": [{"type": "lifecycle", "after": "deployed"}]},
        project_id="portfolio",
    )
    question_id = store.add_observation(
        {
            "kind": "clarification",
            "subject": "Portfolio deployment status",
            "claim": "Question: Was the portfolio deployed and verified?",
            "question": "Was the portfolio deployed and verified?",
            "evidence_refs": [evidence_id],
            "confidence": 0.8,
            "status": "pending",
        }
    )
    output = {
        "summary": "The portfolio deployment state is now verified.",
        "observations": [],
        "pattern_signals": [],
        "project_updates": [],
        "skill_updates": [],
        "voice_samples": [],
        "review_items": [],
        "question_resolutions": [
            {
                "question_id": question_id,
                "answer": "The portfolio is deployed.",
                "evidence_refs": [evidence_id],
                "confidence": 0.96,
                "authoritative": True,
            }
        ],
    }

    result = publish_model_output(
        vault=tmp_path,
        store=store,
        output=output,
        run_kind="weekly",
        evidence_ids=[evidence_id],
        question_ids=[question_id],
    )

    resolved = store.observation(question_id)
    assert result["questions_resolved"] == 1
    assert resolved is not None and resolved["status"] == "resolved"
    assert resolved["rejection_reason"] == "The portfolio is deployed."
    assert resolved["payload"]["automatic_resolution"]["evidence_refs"] == [
        evidence_id
    ]


def test_weekly_cannot_resolve_human_disclosure_question(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    evidence_id, _ = store.add_evidence(
        source_type="project-activity",
        source_ref="project-activity:one",
        kind="project_delta",
        payload={"changes": []},
    )
    question_id = store.add_observation(
        {
            "kind": "clarification",
            "subject": "Military public disclosure",
            "claim": "Question: Which military details are approved for public use?",
            "question": "Which military details are approved for public use?",
            "evidence_refs": [evidence_id],
            "confidence": 0.8,
            "status": "pending",
        }
    )
    output = {
        "summary": "No human decision may be inferred.",
        "observations": [],
        "pattern_signals": [],
        "project_updates": [],
        "skill_updates": [],
        "voice_samples": [],
        "review_items": [],
        "question_resolutions": [
            {
                "question_id": question_id,
                "answer": "Public.",
                "evidence_refs": [evidence_id],
                "confidence": 1.0,
                "authoritative": True,
            }
        ],
    }

    with pytest.raises(ValueError, match="ineligible or unsupplied"):
        publish_model_output(
            vault=tmp_path,
            store=store,
            output=output,
            run_kind="weekly",
            evidence_ids=[evidence_id],
            question_ids=[question_id],
        )
    assert store.observation(question_id)["status"] == "pending"
