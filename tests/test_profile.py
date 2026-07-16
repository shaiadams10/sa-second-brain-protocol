import json
from pathlib import Path

from second_brain_protocol.profile import answer_interview, create_interview
from second_brain_protocol.state import StateStore


def test_corrected_interview_answer_supersedes_earlier_evidence(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    vault = tmp_path / "vault"
    store = StateStore(runtime / "state.sqlite")

    answer_interview(store, runtime, "current_work", "Old answer")
    answer_interview(store, runtime, "current_work", "Corrected answer")

    evidence = [
        item
        for item in store.evidence()
        if item["source_ref"] == "interview:current_work:v1"
    ]
    assert len(evidence) == 2
    assert [item["status"] for item in evidence].count("new") == 1
    assert [item["status"] for item in evidence].count("superseded") == 1
    current = next(item for item in evidence if item["status"] == "new")
    assert current["payload"]["answer"] == "Corrected answer"

    create_interview(vault, runtime)
    saved = json.loads((runtime / "interview.json").read_text(encoding="utf-8"))
    assert saved["answers"]["current_work"] == "Corrected answer"
    assert "status: open" in (vault / "Inbox/Review/BootstrapInterview.md").read_text(
        encoding="utf-8"
    )
