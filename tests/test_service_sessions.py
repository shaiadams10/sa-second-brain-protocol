import json

from second_brain_protocol.config import RuntimePaths, setup_runtime
from second_brain_protocol.service import (
    configure_session_project_link,
    latest_sessions,
    remember_explicit_skill,
    session_index_summary,
)
from second_brain_protocol.state import StateStore


def _digest(
    store: StateStore,
    *,
    session_id: str,
    surface: str,
    started_at: str,
    ended_at: str,
    message: str,
    project_id: str | None = None,
    source_suffix: str = "",
) -> None:
    store.add_evidence(
        source_type="session-digest",
        source_ref=f"session-digest:{surface}:{session_id}{source_suffix}",
        kind="session_digest",
        project_id=project_id,
        occurred_at=started_at,
        payload={
            "source": surface,
            "session_id": session_id,
            "started_at": started_at,
            "ended_at": ended_at,
            "project_ids": [project_id] if project_id else [],
            "user_messages": [{"occurred_at": ended_at, "text": message}],
        },
    )


def test_latest_sessions_filters_projects_and_deduplicates_legacy_exports(
    tmp_path,
) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.upsert_project(
        {
            "id": "folder-homedrop",
            "name": "HomeDrop",
            "classification": "review",
        }
    )
    message = "<USER_REQUEST>leave this for the daily test</USER_REQUEST>"
    _digest(
        store,
        session_id="12345678-1234-1234-1234-123456789012",
        surface="antigravity",
        started_at="2026-07-17T14:00:00Z",
        ended_at="2026-07-17T14:01:00Z",
        message=message,
        project_id="folder-homedrop",
    )
    _digest(
        store,
        session_id="transcript_full.jsonl",
        surface="antigravity",
        started_at="2026-07-17T14:00:00Z",
        ended_at="2026-07-17T14:01:00Z",
        message=message,
    )
    _digest(
        store,
        session_id="codex-one",
        surface="codex",
        started_at="2026-07-17T15:00:00Z",
        ended_at="2026-07-17T15:05:00Z",
        message="newer codex message",
    )

    antigravity = latest_sessions(store, surface="antigravity", project="HomeDrop")

    assert len(antigravity) == 1
    assert antigravity[0]["projects"] == ["HomeDrop"]
    assert antigravity[0]["last_user_message"] == "leave this for the daily test"
    assert antigravity[0]["attributed"] is True
    assert all("session_id" not in item for item in latest_sessions(store))
    assert (
        latest_sessions(store, surface="codex")[0]["last_user_message"]
        == "newer codex message"
    )
    assert latest_sessions(store, project="Not Collected Yet") == []
    assert store.session_coverage() == {
        "total": 2,
        "attributed": 1,
        "unattributed": 1,
        "projects_represented": 1,
        "by_surface": {
            "antigravity": {
                "total": 1,
                "attributed": 1,
                "unattributed": 0,
                "latest_at": "2026-07-17T14:01:00Z",
            },
            "codex": {
                "total": 1,
                "attributed": 0,
                "unattributed": 1,
                "latest_at": "2026-07-17T15:05:00Z",
            },
        },
    }


def test_active_session_index_hides_unmatched_digests(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.upsert_project(
        {
            "id": "project-angel",
            "name": "First Party Project With Existing Brain",
            "classification": "first-party",
        }
    )
    _digest(
        store,
        session_id="matched",
        surface="codex",
        started_at="2026-07-18T10:00:00Z",
        ended_at="2026-07-18T10:01:00Z",
        message="matched message",
        project_id="project-angel",
    )
    _digest(
        store,
        session_id="matched",
        surface="codex",
        started_at="2026-07-18T10:00:00Z",
        ended_at="2026-07-18T10:01:00Z",
        message="",
        project_id="project-angel",
        source_suffix=":recompacted",
    )
    _digest(
        store,
        session_id="unmatched",
        surface="codex",
        started_at="2026-07-18T11:00:00Z",
        ended_at="2026-07-18T11:01:00Z",
        message="unmatched message",
    )
    store.replace_session_project_index(
        [
            {
                "surface": "codex",
                "session_id": session_id,
                "project_id": project_id,
                "status": status,
                "resolver": resolver,
                "confidence": confidence,
                "workspace_count": 1,
                "source_record_count": 1,
            }
            for session_id, project_id, status, resolver, confidence in (
                ("matched", "project-angel", "matched", "current_path", 1.0),
                ("unmatched", None, "unmatched", "no_authoritative_match", 0.0),
            )
        ]
    )

    latest = latest_sessions(store)
    summary = session_index_summary(store)

    assert [item["last_user_message"] for item in latest] == ["matched message"]
    assert summary["sessions_indexed"] == 2
    assert summary["matched"] == 1
    assert summary["unmatched"] == 1
    assert summary["matched_projects"][0]["project"] == "First Party Project With Existing Brain"
    assert store.session_coverage() == {
        "total": 2,
        "attributed": 1,
        "unattributed": 1,
        "projects_represented": 1,
        "by_surface": {
            "codex": {
                "total": 2,
                "attributed": 1,
                "unattributed": 1,
                "latest_at": "2026-07-18T11:01:00Z",
            }
        },
    }


def test_session_link_is_previewed_and_stored_by_stable_project_id(tmp_path) -> None:
    paths = setup_runtime(RuntimePaths.from_root(tmp_path / "runtime"))
    store = StateStore(paths.state)
    store.upsert_project(
        {
            "id": "project-vane",
            "name": "Vane",
            "classification": "third-party",
            "local_path": str(tmp_path / "Projects" / "Vane"),
        }
    )
    store.set_project_presence("project-vane", present=True)
    store.replace_session_project_index(
        [
            {
                "surface": "antigravity",
                "session_id": "session-1",
                "project_id": None,
                "status": "unmatched",
                "resolver": "missing_workspace",
                "confidence": 0.0,
                "workspace_count": 0,
                "source_record_count": 1,
            }
        ]
    )

    preview = configure_session_project_link(
        paths,
        store,
        surface="antigravity",
        session_id="session-1",
        project="Vane",
    )
    assert preview["status"] == "preview"
    assert preview["target_project"] == "Vane"

    result = configure_session_project_link(
        paths,
        store,
        surface="antigravity",
        session_id="session-1",
        project="Vane",
        confirm=True,
    )
    config = json.loads(paths.config.read_text(encoding="utf-8"))
    assert result["status"] == "linked"
    assert config["session_project_overrides"] == {
        "antigravity:session-1": "project-vane"
    }


def test_explicit_skill_replaces_project_specific_index_entry(tmp_path) -> None:
    vault = tmp_path / "vault"
    skills_index = vault / "Skills" / "Index.md"
    capabilities = vault / "Identity" / "Capabilities.md"
    skills_index.parent.mkdir(parents=True)
    capabilities.parent.mkdir(parents=True)
    skills_index.write_text(
        "# Skills\n\n<!-- sb:generated skills-index:start -->\n"
        "- [[Skills/local-tts|Local TTS]] — candidate\n"
        "<!-- sb:generated skills-index:end -->\n",
        encoding="utf-8",
    )
    capabilities.write_text(
        "---\nid: capabilities\ntype: identity\nconfidence: 0\nprovenance: []\n"
        "first_seen: null\nlast_verified: null\n---\n\n"
        "# Capabilities\n\n<!-- sb:generated capabilities:start -->\n"
        "Verified capabilities will appear here.\n"
        "<!-- sb:generated capabilities:end -->\n",
        encoding="utf-8",
    )
    store = StateStore(tmp_path / "state.sqlite")
    evidence_id, _ = store.add_evidence(
        source_type="session-digest",
        source_ref="session-digest:tts",
        kind="session_digest",
        payload={
            "user_messages": [{"text": "Configure local TTS"}],
            "assistant_results": [{"text": "Implemented and working"}],
            "artifacts": [],
        },
    )

    result = remember_explicit_skill(
        vault,
        store,
        skill_id="local-tts",
        name="Local TTS",
        claim="the user can configure local AI TTS and voice-cloning workflows.",
        supporting_evidence=[evidence_id],
        successful_implementation=True,
    )

    assert result["status"] == "verified"
    index_text = skills_index.read_text(encoding="utf-8")
    assert index_text.count("[[Skills/local-tts|Local TTS]]") == 1
    assert "— verified" in index_text
    assert "voice-cloning workflows" in capabilities.read_text(encoding="utf-8")
