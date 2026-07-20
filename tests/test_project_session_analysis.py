from pathlib import Path

import pytest

from second_brain_protocol.orchestrator import _validated_project_history_update
from second_brain_protocol.publisher import publish_model_output
from second_brain_protocol.state import StateStore


def _output(project_id: str, evidence_refs: list[str]) -> dict:
    return {
        "project_id": project_id,
        "summary": (
            "## Implemented work\n\n"
            "- Built the first verified workflow.\n"
            "- Added deterministic validation for its outputs.\n\n"
            "## Verified outcomes\n\n"
            "- The supplied session records report passing validation.\n\n"
            "## Open threads\n\n"
            "- Later deployment state is not established by this historical evidence."
        ),
        "evidence_refs": evidence_refs,
    }


def test_project_history_validation_enforces_one_project_and_session_evidence() -> None:
    project = {"id": "project-angel", "name": "First Party Project With Existing Brain"}
    validated = _validated_project_history_update(
        _output("project-angel", ["inventory", "session"]),
        project=project,
        allowed_evidence_ids={"inventory", "session"},
        session_evidence_ids={"session"},
    )

    assert validated["project_id"] == "project-angel"
    assert validated["name"] == "First Party Project With Existing Brain"
    with pytest.raises(RuntimeError, match="cross-project"):
        _validated_project_history_update(
            _output("project-ernie", ["session"]),
            project=project,
            allowed_evidence_ids={"session"},
            session_evidence_ids={"session"},
        )
    with pytest.raises(RuntimeError, match="session digest"):
        _validated_project_history_update(
            _output("project-angel", ["inventory"]),
            project=project,
            allowed_evidence_ids={"inventory"},
            session_evidence_ids={"session"},
        )


def test_project_history_validation_rejects_shallow_protocol_boilerplate() -> None:
    project = {"id": "project-angel", "name": "First Party Project With Existing Brain"}
    output = _output("project-angel", ["session"])
    output["summary"] = (
        "## Summary\n\n- Indexed session history synthesized.\n"
        "- Nothing else.\n\n## Status\n\n- Complete."
    )

    with pytest.raises(RuntimeError, match="boilerplate"):
        _validated_project_history_update(
            output,
            project=project,
            allowed_evidence_ids={"session"},
            session_evidence_ids={"session"},
        )


def test_project_history_publication_writes_only_project_scoped_files(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    project = {
        "id": "project-angel",
        "name": "First Party Project With Existing Brain",
        "classification": "first-party",
        "tracked_file_count": 1,
    }
    store.upsert_project(project)
    evidence_id, _ = store.add_evidence(
        source_type="session-digest",
        source_ref="session-digest:codex:angel",
        kind="session_digest",
        payload={
            "source": "codex",
            "session_id": "angel",
            "project_ids": ["project-angel"],
        },
        project_id="project-angel",
    )
    output = {
        "summary": "- First Party Project With Existing Brain: history synthesized.",
        "observations": [],
        "pattern_signals": [],
        "project_updates": [
            {
                "project_id": "project-angel",
                "name": "First Party Project With Existing Brain",
                "summary": "Verified project history.",
                "evidence_refs": [evidence_id],
            }
        ],
        "skill_updates": [],
        "voice_samples": [],
        "review_items": [],
        "question_resolutions": [],
    }

    result = publish_model_output(
        vault=tmp_path,
        store=store,
        output=output,
        run_kind="project-history:project-angel",
        evidence_ids=[evidence_id],
    )

    assert result["projects_written"] == 1
    assert "Verified project history" in (
        tmp_path / "Projects" / "angel-version-2.md"
    ).read_text(encoding="utf-8")
    assert Path(result["synthesis_path"]).parent.name == "ProjectSessions"
    assert not (tmp_path / "Identity").exists()
    assert not (tmp_path / "Experience").exists()
