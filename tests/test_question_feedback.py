import json
import threading
import urllib.request

from second_brain_protocol.config import RuntimePaths
from second_brain_protocol.dashboard_server import create_dashboard_server
from second_brain_protocol.feedback_learning import (
    knowledge_feedback_profile,
    should_suppress_review_question,
)
from second_brain_protocol.question_actions import (
    dismiss_question,
    undo_last_question_dismissal,
)
from second_brain_protocol.state import StateStore


def _question(store: StateStore, index: int) -> str:
    evidence_id, _ = store.add_evidence(
        source_type="test",
        source_ref=f"question:{index}",
        kind="project_fact",
        payload={"status": "unclear"},
    )
    return store.add_observation(
        {
            "kind": "clarification",
            "subject": f"Preview status {index}",
            "claim": "The current deployment status is unclear.",
            "question": "Is this preview deployed, working locally, or still a prototype?",
            "review_reason": "missing_information",
            "evidence_refs": [evidence_id],
            "confidence": 0.7,
            "status": "pending",
        }
    )


def test_question_dismissal_is_reversible_and_trains_category_relevance(
    tmp_path,
) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    vault = tmp_path / "Example Second Brain"
    first = _question(store, 1)
    second = _question(store, 2)

    dismiss_question(vault, store, first)
    dismiss_question(vault, store, second)

    assert store.observation(first)["status"] == "rejected"
    assert store.review_feedback()[first]["decision"] == "dismissed"
    profile = knowledge_feedback_profile(store)
    assert profile["reviewed_questions"] == 2
    assert any(
        item["feature"] == "question:project-state" for item in profile["avoid"]
    )
    assert should_suppress_review_question(
        {
            "kind": "missing_information",
            "subject": "Demo status",
            "description": "The current state is unclear.",
            "question": "Is the demo deployed or still a prototype?",
        },
        profile,
    )

    restored = undo_last_question_dismissal(vault, store)

    assert restored["id"] == second
    assert store.observation(second)["status"] == "pending"
    assert second not in store.review_feedback()


def test_dashboard_question_dismiss_and_undo_endpoints(tmp_path) -> None:
    paths = RuntimePaths.from_root(tmp_path / "runtime")
    store = StateStore(paths.state)
    vault = tmp_path / "Example Second Brain"
    question_id = _question(store, 1)
    server = create_dashboard_server(
        paths, vault, port=0, reindexer=lambda _p, _v, _r: "ok"
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    def post(route: str) -> dict:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{route}",
            data=b"{}",
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Origin": f"http://127.0.0.1:{port}",
                "X-SB-Token": server.csrf_token,
            },
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            return json.loads(response.read())

    try:
        dismissed = post(f"/api/questions/{question_id}/dismiss")
        assert dismissed["dismissed"] is True
        assert StateStore(paths.state).observation(question_id)["status"] == "rejected"
        restored = post("/api/questions/undo")
        assert restored["restored"] is True
        assert StateStore(paths.state).observation(question_id)["status"] == "pending"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
