import json
import sqlite3
from pathlib import Path

from second_brain_protocol.collector import (
    collect_sessions,
    reconcile_existing_session_attribution,
)
from second_brain_protocol.session_attribution import register_current_project_paths
from second_brain_protocol.state import StateStore


def _write_codex_session(path: Path, session_id: str, cwd: Path, message: str) -> None:
    rows = [
        {
            "type": "session_meta",
            "payload": {"id": session_id, "cwd": str(cwd)},
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": message}],
            },
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _varint(value: int) -> bytes:
    result = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        result.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(result)


def _protobuf_field(number: int, value: bytes) -> bytes:
    return _varint((number << 3) | 2) + _varint(len(value)) + value


def _write_antigravity_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE steps(
            idx INTEGER PRIMARY KEY,step_type INTEGER,status INTEGER,
            has_subtrajectory INTEGER,metadata BLOB,error_details BLOB,permissions BLOB,
            task_details BLOB,render_info BLOB,step_payload BLOB,step_format INTEGER)"""
        )
        connection.execute(
            "CREATE TABLE trajectory_metadata_blob(id TEXT PRIMARY KEY,data BLOB)"
        )
        connection.execute(
            "INSERT INTO trajectory_metadata_blob VALUES('meta',?)",
            (_protobuf_field(18, b"outside-of-project"),),
        )
        user = _protobuf_field(19, _protobuf_field(2, b"Please implement this"))
        assistant = _protobuf_field(
            20, _protobuf_field(1, b"Implemented and tests passed")
        )
        for index, step_type, payload in ((0, 14, user), (1, 15, assistant)):
            connection.execute(
                "INSERT INTO steps VALUES(?,?,3,0,NULL,NULL,NULL,NULL,NULL,?,0)",
                (index, step_type, payload),
            )


def _config(tmp_path: Path) -> dict:
    return {
        "codex_sessions": str(tmp_path / "codex" / "sessions"),
        "codex_archived_sessions": str(tmp_path / "codex" / "archived"),
        "antigravity_brain": str(tmp_path / "antigravity" / "brain"),
        "antigravity_conversations": str(tmp_path / "antigravity" / "conversations"),
        "runtime_root": str(tmp_path / "runtime"),
    }


def test_collection_reads_full_content_only_for_uniquely_matched_sessions(
    tmp_path: Path,
) -> None:
    sessions = tmp_path / "codex" / "sessions"
    archived = tmp_path / "codex" / "archived"
    sessions.mkdir(parents=True)
    archived.mkdir(parents=True)
    project_path = tmp_path / "Projects" / "First Party Project With Existing Brain"
    unrelated_path = tmp_path / "Scratch"
    project_path.mkdir(parents=True)
    unrelated_path.mkdir()
    _write_codex_session(
        sessions / "matched.jsonl",
        "matched-session",
        project_path,
        "matched content",
    )
    _write_codex_session(
        sessions / "unrelated.jsonl",
        "unrelated-session",
        unrelated_path,
        "must not be ingested",
    )
    project = {
        "id": "project-angel",
        "name": "First Party Project With Existing Brain",
        "classification": "first-party",
        "local_path": str(project_path),
        "tracked_file_count": 1,
    }
    store = StateStore(tmp_path / "state.sqlite")
    store.upsert_project(project)
    store.set_project_presence(project["id"], present=True)
    register_current_project_paths(store, [project])
    config = {
        "codex_sessions": str(sessions),
        "codex_archived_sessions": str(archived),
        "antigravity_brain": str(tmp_path / "antigravity" / "brain"),
        "antigravity_conversations": str(tmp_path / "antigravity" / "conversations"),
        "runtime_root": str(tmp_path / "runtime"),
    }

    result = collect_sessions(store, config)

    assert result["sources_indexed"] == 2
    assert result["sources_matched"] == 1
    assert result["sources_unmatched"] == 1
    assert result["sources_skipped_without_project"] == 1
    messages = [row for row in store.evidence() if row["kind"] == "visible_message"]
    assert [row["payload"]["text"] for row in messages] == ["matched content"]
    assert messages[0]["project_id"] == "project-angel"
    index = {row["session_id"]: row for row in store.session_project_index()}
    assert index["matched-session"]["status"] == "matched"
    assert index["unrelated-session"]["status"] == "unmatched"

    repeated = collect_sessions(store, config)
    assert repeated["codex"] == 0
    assert (
        len([row for row in store.evidence() if row["kind"] == "visible_message"]) == 1
    )


def test_conflicting_session_metadata_combines_no_projects(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    for project_id, name in (
        ("project-angel", "First Party Project With Existing Brain"),
        ("project-ernie", "Ernie LoRA"),
    ):
        path = tmp_path / name
        project = {
            "id": project_id,
            "name": name,
            "classification": "first-party",
            "local_path": str(path),
            "tracked_file_count": 1,
        }
        store.upsert_project(project)
        store.set_project_presence(project_id, present=True)
    for source_key, project_id in (
        ("codex-file:angel", "project-angel"),
        ("codex-file:ernie", "project-ernie"),
    ):
        store.upsert_session_source(
            source_key=source_key,
            surface="codex",
            session_id="conflicted-session",
            fingerprint=source_key,
            workspace_hash=source_key,
            project_id=project_id,
            status="matched",
            resolver="current_path",
            confidence=1.0,
            ingested=True,
        )
    evidence_id, _ = store.add_evidence(
        source_type="codex",
        source_ref="codex:conflicted-session:1",
        kind="visible_message",
        payload={"session_id": "conflicted-session", "text": "do work"},
        project_id="project-angel",
    )

    result = reconcile_existing_session_attribution(store)

    assert result["ambiguous"] == 1
    assert store.session_project_index()[0]["project_id"] is None
    assert store.evidence_by_ids([evidence_id])[0]["project_id"] is None


def test_antigravity_hub_metadata_recovers_moved_third_party_session(
    tmp_path: Path,
) -> None:
    session_id = "11111111-2222-3333-4444-555555555555"
    project_path = tmp_path / "Projects" / "External Tool"
    project_path.mkdir(parents=True)
    project = {
        "id": "project-external",
        "name": "External Tool",
        "classification": "third-party",
        "local_path": str(project_path),
        "remote_url": "https://github.com/example/external.git",
        "tracked_file_count": 1,
    }
    store = StateStore(tmp_path / "state.sqlite")
    store.upsert_project(project)
    store.set_project_presence(project["id"], present=True)
    register_current_project_paths(store, [project])
    database = tmp_path / "antigravity" / "conversations" / f"{session_id}.db"
    _write_antigravity_database(database)
    repository = _protobuf_field(
        3,
        _protobuf_field(2, b"https://github.com/example/external.git"),
    )
    summary = _protobuf_field(
        1,
        session_id.encode(),
    ) + _protobuf_field(
        2,
        _protobuf_field(9, _protobuf_field(1, b"file:///D:/Old/External") + repository),
    )
    (tmp_path / "antigravity" / "agyhub_summaries_proto.pb").write_bytes(
        _protobuf_field(1, summary)
    )

    result = collect_sessions(store, _config(tmp_path))

    assert result["sources_matched"] == 1
    messages = [row for row in store.evidence() if row["kind"] == "visible_message"]
    assert {row["project_id"] for row in messages} == {"project-external"}


def test_explicit_session_override_recovers_outside_project_conversation(
    tmp_path: Path,
) -> None:
    session_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    project_path = tmp_path / "Projects" / "PortManager"
    project_path.mkdir(parents=True)
    project = {
        "id": "folder-portmanager",
        "name": "PortManager",
        "classification": "first-party",
        "local_path": str(project_path),
        "tracked_file_count": 1,
    }
    store = StateStore(tmp_path / "state.sqlite")
    store.upsert_project(project)
    store.set_project_presence(project["id"], present=True)
    register_current_project_paths(store, [project])
    database = tmp_path / "antigravity" / "conversations" / f"{session_id}.db"
    _write_antigravity_database(database)
    config = _config(tmp_path)
    config["session_project_overrides"] = {f"antigravity:{session_id}": "PortManager"}

    collect_sessions(store, config)

    indexed = store.session_project_index()[0]
    assert indexed["project_id"] == "folder-portmanager"
    assert indexed["resolver"] == "explicit_user_session_mapping"


def test_antigravity_tool_path_recovers_untagged_session(tmp_path: Path) -> None:
    session_id = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
    project_path = tmp_path / "Projects" / "Angel"
    project_path.mkdir(parents=True)
    project = {
        "id": "project-angel",
        "name": "Angel",
        "classification": "first-party",
        "local_path": str(project_path),
        "tracked_file_count": 1,
    }
    store = StateStore(tmp_path / "state.sqlite")
    store.upsert_project(project)
    store.set_project_presence(project["id"], present=True)
    register_current_project_paths(store, [project])
    database = tmp_path / "antigravity" / "conversations" / f"{session_id}.db"
    _write_antigravity_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO steps VALUES(2,8,3,0,NULL,NULL,NULL,NULL,NULL,?,0)",
            (_protobuf_field(1, str(project_path / "src" / "app.ts").encode()),),
        )

    collect_sessions(store, _config(tmp_path))

    indexed = store.session_project_index()[0]
    assert indexed["project_id"] == "project-angel"
    assert indexed["resolver"] == "antigravity_tool_path"
    messages = [row for row in store.evidence() if row["kind"] == "visible_message"]
    assert {row["project_id"] for row in messages} == {"project-angel"}
