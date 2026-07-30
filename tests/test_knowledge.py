import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from second_brain_protocol import dashboard_server
from second_brain_protocol.config import RuntimePaths
from second_brain_protocol.dashboard_server import create_dashboard_server
from second_brain_protocol.knowledge import dislike_knowledge, like_knowledge, undo_last_dislike
from second_brain_protocol.question_actions import answer_question
from second_brain_protocol.state import StateStore


def _answer_evaluator(draft: dict) -> dict:
    project_id = draft["project_ids"][0] if draft.get("project_ids") else None
    scope = "project" if project_id else "global"
    return {
        "normalized_answer": draft["answer"].rstrip(".") + ".",
        "claims": [
            {
                "destination": (
                    "project_knowledge" if project_id else "professional_profile"
                ),
                "kind": "project_fact" if project_id else "explicit_fact",
                "subject": "Evaluated owner answer",
                "claim": draft["answer"].rstrip(".") + ".",
                "scope": scope,
                "project_id": project_id,
                "public_claim": False,
                "confidence": 1.0,
            }
        ],
    }


def _promoted_observation(store: StateStore, vault: Path) -> tuple[str, Path, dict]:
    evidence_id, _ = store.add_evidence(
        source_type="test",
        source_ref="safe-source",
        kind="explicit_profile_answer",
        occurred_at="2026-07-16T12:00:00+00:00",
        payload={"answer": "Prefers evidence-backed outcomes."},
    )
    record = {
        "kind": "work_style",
        "subject": "Outcome ownership",
        "claim": "Prefers evidence-backed outcomes.",
        "evidence_refs": [evidence_id],
        "confidence": 0.95,
        "source_count": 3,
        "project_count": 2,
        "sensitivity": "normal",
        "promotion_tier": "automatic",
        "status": "promoted",
    }
    observation_id = store.add_observation(record)
    note = vault / "Identity" / "WorkStyle.md"
    note.parent.mkdir(parents=True)
    note.write_text(
        "# Work style\n\n"
        "<!-- sb:generated canonical:start -->\n"
        f"- Prefers evidence-backed outcomes. ^{observation_id}\n"
        "<!-- sb:generated canonical:end -->\n\n"
        "## Manual notes\n\nManual prose is always preserved.\n",
        encoding="utf-8",
    )
    return observation_id, note, record


def test_knowledge_like_dislike_and_undo_are_durable_and_safe(tmp_path: Path) -> None:
    paths = RuntimePaths.from_root(tmp_path / "runtime")
    store = StateStore(paths.state)
    vault = tmp_path / "vault"
    observation_id, note, record = _promoted_observation(store, vault)
    assert like_knowledge(store, observation_id)["decision"] == "liked"
    assert store.knowledge_feedback()[observation_id]["decision"] == "liked"

    result = dislike_knowledge(paths, vault, store, observation_id)
    text = note.read_text(encoding="utf-8")
    assert result["removed_occurrences"] == 1
    assert f"^{observation_id}" not in text
    assert "Manual prose is always preserved." in text
    assert store.observation(observation_id)["status"] == "rejected"
    assert store.knowledge_feedback()[observation_id]["decision"] == "disliked"
    assert result["search_refresh_queued"] == 1
    assert [item["path"] for item in store.search_refresh_batch()] == ["Identity/WorkStyle.md"]

    # Re-ingesting the exact same model output cannot reopen the tombstone.
    assert store.add_observation(record) == observation_id
    assert store.observation(observation_id)["status"] == "rejected"

    restored = undo_last_dislike(paths, vault, store)
    text = note.read_text(encoding="utf-8")
    assert restored == {
        "id": observation_id,
        "decision": "liked",
        "restored": True,
        "search_refresh_queued": 1,
    }
    assert f"^{observation_id}" in text
    assert "Manual prose is always preserved." in text
    assert store.observation(observation_id)["status"] == "promoted"
    assert store.knowledge_feedback()[observation_id]["decision"] == "liked"
    assert store.search_refresh_status()["pending"] == 1


def test_failed_background_search_refresh_keeps_authoritative_retraction(tmp_path: Path) -> None:
    paths = RuntimePaths.from_root(tmp_path / "runtime")
    store = StateStore(paths.state)
    vault = tmp_path / "vault"
    observation_id, note, _record = _promoted_observation(store, vault)
    dislike_knowledge(paths, vault, store, observation_id)
    batch = store.search_refresh_batch()
    store.fail_search_refresh(batch, "index unavailable")

    assert f"^{observation_id}" not in note.read_text(encoding="utf-8")
    assert store.observation(observation_id)["status"] == "rejected"
    assert store.knowledge_feedback()[observation_id]["decision"] == "disliked"
    assert store.search_refresh_status()["state"] == "failed"
    assert store.search_refresh_status()["pending"] == 1


def test_loopback_api_rejects_missing_token_and_accepts_same_origin_like(
    tmp_path: Path,
) -> None:
    paths = RuntimePaths.from_root(tmp_path / "runtime")
    store = StateStore(paths.state)
    vault = tmp_path / "vault"
    observation_id, _note, _record = _promoted_observation(store, vault)
    server = create_dashboard_server(
        paths,
        vault,
        port=0,
        reindexer=lambda _p, _v, _r: "ok",
        answer_evaluator=_answer_evaluator,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    endpoint = f"http://127.0.0.1:{port}/api/knowledge/{observation_id}/like"
    try:
        unauthorized = urllib.request.Request(
            endpoint,
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"},
        )
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(unauthorized, timeout=3)
        assert denied.value.code == 403

        authorized = urllib.request.Request(
            endpoint,
            data=b"{}",
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Origin": f"http://127.0.0.1:{port}",
                "X-SB-Token": server.csrf_token,
            },
        )
        with urllib.request.urlopen(authorized, timeout=3) as response:
            payload = json.loads(response.read())
        assert payload == {"ok": True, "id": observation_id, "decision": "liked"}
        assert StateStore(paths.state).knowledge_feedback()[observation_id]["decision"] == "liked"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_background_worker_batches_durable_refresh_paths(tmp_path: Path) -> None:
    paths = RuntimePaths.from_root(tmp_path / "runtime")
    store = StateStore(paths.state)
    vault = tmp_path / "vault"
    calls: list[list[str]] = []
    with store.transaction() as connection:
        store.enqueue_search_refresh(
            connection, {"Identity/WorkStyle.md", "Memory/Lessons.md"}
        )

    server = create_dashboard_server(
        paths,
        vault,
        port=0,
        reindexer=lambda _paths, _vault, relative: calls.append(relative) or "ok",
    )
    try:
        deadline = time.monotonic() + 4
        while store.search_refresh_status()["pending"] and time.monotonic() < deadline:
            time.sleep(0.05)
        assert calls == [["Identity/WorkStyle.md", "Memory/Lessons.md"]]
        assert store.search_refresh_status()["pending"] == 0
        assert store.search_refresh_status()["state"] == "ready"
    finally:
        server.server_close()


def test_background_worker_retries_failed_refresh(
    tmp_path: Path, monkeypatch,
) -> None:
    paths = RuntimePaths.from_root(tmp_path / "runtime")
    store = StateStore(paths.state)
    vault = tmp_path / "vault"
    attempts = []
    with store.transaction() as connection:
        store.enqueue_search_refresh(connection, ["Memory/Lessons.md"])

    def flaky_refresh(_paths, _vault, relative):
        attempts.append(relative)
        if len(attempts) == 1:
            raise RuntimeError("temporary failure")
        return "ok"

    monkeypatch.setattr(dashboard_server, "SEARCH_REFRESH_RETRY_SECONDS", 0.05)
    server = create_dashboard_server(paths, vault, port=0, reindexer=flaky_refresh)
    try:
        deadline = time.monotonic() + 4
        while store.search_refresh_status()["pending"] and time.monotonic() < deadline:
            time.sleep(0.05)
        assert attempts == [["Memory/Lessons.md"], ["Memory/Lessons.md"]]
        assert store.search_refresh_status()["pending"] == 0
        assert store.search_refresh_status()["state"] == "ready"
    finally:
        server.server_close()


def test_remove_api_returns_before_slow_background_refresh(tmp_path: Path) -> None:
    paths = RuntimePaths.from_root(tmp_path / "runtime")
    store = StateStore(paths.state)
    vault = tmp_path / "vault"
    observation_id, note, _record = _promoted_observation(store, vault)
    refresh_started = threading.Event()

    def slow_refresh(_paths, _vault, _relative):
        refresh_started.set()
        time.sleep(0.5)
        return "ok"

    server = create_dashboard_server(paths, vault, port=0, reindexer=slow_refresh)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    endpoint = f"http://127.0.0.1:{port}/api/knowledge/{observation_id}/dislike"
    request = urllib.request.Request(
        endpoint,
        data=b"{}",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{port}",
            "X-SB-Token": server.csrf_token,
        },
    )
    try:
        started = time.monotonic()
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.loads(response.read())
        elapsed = time.monotonic() - started

        assert payload["ok"] is True
        assert elapsed < 0.5
        assert f"^{observation_id}" not in note.read_text(encoding="utf-8")
        assert store.search_refresh_status()["pending"] == 1
        assert refresh_started.wait(timeout=3)
        deadline = time.monotonic() + 3
        while store.search_refresh_status()["pending"] and time.monotonic() < deadline:
            time.sleep(0.05)
        assert store.search_refresh_status()["pending"] == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_dashboard_question_answer_records_explicit_evidence_and_resolves_item(tmp_path: Path) -> None:
    paths = RuntimePaths.from_root(tmp_path / "runtime")
    store = StateStore(paths.state)
    vault = tmp_path / "Example Person Second Brain"
    evidence_id, _ = store.add_evidence(
        source_type="test",
        source_ref="question-source",
        kind="project_fact",
        payload={"status": "unclear"},
        project_id="project-portfolio",
    )
    question_id = store.add_observation(
        {
            "kind": "clarification",
            "subject": "Portfolio status",
            "claim": "What is the current portfolio deployment status?",
            "question": "Is the portfolio working locally, deployed, or still a prototype?",
            "evidence_refs": [evidence_id],
            "confidence": 0.5,
            "source_count": 1,
            "project_count": 1,
            "sensitivity": "normal",
            "promotion_tier": "clarification",
            "status": "pending",
        }
    )

    server = create_dashboard_server(
        paths,
        vault,
        port=0,
        reindexer=lambda _p, _v, _r: "ok",
        answer_evaluator=_answer_evaluator,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/questions/{question_id}/answer",
        data=json.dumps({"answer": "Deployed; the current public version went live in July 2026."}).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{port}",
            "X-SB-Token": server.csrf_token,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.loads(response.read())
        resolved = StateStore(paths.state).observation(question_id)
        saved_evidence = next(
            item
            for item in StateStore(paths.state).evidence()
            if item["source_ref"] == f"review-resolution:{question_id}"
        )
        assert payload == {
            "ok": True,
            "id": question_id,
            "status": "resolved",
            "answer_evaluated": True,
            "claims_saved": 1,
        }
        assert resolved is not None and resolved["status"] == "resolved"
        assert resolved["rejection_reason"].startswith("Deployed;")
        assert saved_evidence["kind"] == "evaluated_project_answer"
        assert saved_evidence["project_id"] == "project-portfolio"
        assert saved_evidence["payload"]["destinations"] == ["project_knowledge"]
        assert saved_evidence["payload"]["scope"] == "project"
        assert "answer" not in saved_evidence["payload"]
        review_files = list((vault / "Inbox" / "Review").glob("Review-*.md"))
        assert review_files
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_question_answer_honors_explicit_project_attribution_override(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    vault = tmp_path / "Example Person Second Brain"
    for project_id, name in (
        ("project-agents", "AgentSkillsHub"),
        ("project-portfolio", "Contrasting First Party Project"),
    ):
        store.upsert_project(
            {"id": project_id, "name": name, "classification": "first-party"}
        )
    evidence_id, _ = store.add_evidence(
        source_type="session-digest",
        source_ref="question-source",
        kind="session_digest",
        project_id="project-agents",
        payload={"project_ids": ["project-agents"]},
    )
    question_id = store.add_observation(
        {
            "kind": "clarification",
            "subject": "Agent-assisted demo status",
            "claim": "Which demos are portfolio-ready?",
            "question": "Which demos are portfolio-ready?",
            "evidence_refs": [evidence_id],
            "confidence": 0.8,
            "status": "pending",
        }
    )
    store.set_observation_project_override(question_id, ["project-portfolio"])

    answer_question(
        vault,
        store,
        question_id,
        "The approved demos belong in the portfolio.",
        evaluator=_answer_evaluator,
    )

    saved = next(
        item
        for item in store.evidence()
        if item["source_ref"] == f"review-resolution:{question_id}"
    )
    assert saved["project_id"] == "project-portfolio"
    assert saved["payload"]["project_ids"] == ["project-portfolio"]


def test_owner_can_reclassify_pending_observation_without_reopening_old_id(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    evidence_id, _ = store.add_evidence(
        source_type="session-digest",
        source_ref="luna",
        kind="session_digest",
        project_id="project-luna",
        payload={"project_ids": ["project-luna"]},
    )
    original_id = store.add_observation(
        {
            "kind": "explicit_fact",
            "subject": "the user",
            "claim": "the user is building Luna.",
            "scope": "project",
            "evidence_refs": [evidence_id],
            "confidence": 0.95,
            "status": "pending",
        }
    )

    replacement_id = store.reclassify_observation(
        original_id,
        kind="project_fact",
        reason="This is project knowledge.",
    )

    assert replacement_id != original_id
    assert store.observation(original_id)["status"] == "rejected"
    replacement = store.observation(replacement_id)
    assert replacement is not None
    assert replacement["kind"] == "project_fact"
    assert replacement["payload"]["reclassified_from"] == original_id


def test_learning_topic_suppression_expires_only_after_newer_evidence(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    topic = {
        "topic_key": "wake-on-lan",
        "label": "Wake-on-LAN",
        "current_state": "demonstrated",
        "assessment": "Historical troubleshooting.",
        "evidence_refs": ["ev-old"],
        "confidence": 0.9,
        "session_count": 1,
        "date_count": 1,
        "project_count": 0,
        "context_count": 1,
        "signal_counts": {"demonstrated_understanding": 1},
        "open_learning_edge": False,
        "first_seen": "2026-04-01T10:00:00Z",
        "last_seen": "2026-04-01T10:00:00Z",
        "last_progress_at": "2026-04-01T10:00:00Z",
    }
    store.upsert_learning_topic(topic)
    store.suppress_learning_topic_until_new("wake-on-lan")

    assert store.visible_learning_topics() == []

    store.upsert_learning_topic(
        {
            **topic,
            "evidence_refs": ["ev-old", "ev-new"],
            "last_seen": "2026-08-01T10:00:00Z",
            "last_progress_at": "2026-08-01T10:00:00Z",
        }
    )

    assert [item["topic_key"] for item in store.visible_learning_topics()] == [
        "wake-on-lan"
    ]
