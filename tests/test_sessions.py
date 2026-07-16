import json
import sqlite3
from pathlib import Path

from second_brain_protocol.collector import _project_for_record
from second_brain_protocol.sessions import (
    inspect_antigravity_database,
    parse_antigravity_database,
    parse_antigravity_artifact,
    parse_antigravity_transcript,
    parse_codex_jsonl,
    validated_antigravity_database_resume_index,
    validated_jsonl_resume_offset,
)


def _varint(value: int) -> bytes:
    result = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        result.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(result)


def _protobuf_field(number: int, value: int | bytes) -> bytes:
    if isinstance(value, int):
        return _varint(number << 3) + _varint(value)
    return _varint((number << 3) | 2) + _varint(len(value)) + value


def _step_payload(step_type: int, visible_field: bytes = b"") -> bytes:
    return _protobuf_field(1, step_type) + _protobuf_field(4, 3) + visible_field


def test_project_text_fallback_requires_a_real_name_boundary() -> None:
    projects = [
        {"id": "project-dify", "name": "dify"},
        {"id": "project-angel", "name": "Angel"},
    ]
    assert _project_for_record({"text": "modify the workflow"}, projects) is None
    assert _project_for_record({"text": "work in Angel today"}, projects) == "project-angel"


def test_codex_parser_keeps_visible_messages_and_metadata_only(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    rows = [
        {"type": "session_meta", "payload": {"id": "session-1", "cwd": "C:/work/project"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Build it"}]}},
        {"type": "response_item", "payload": {"type": "reasoning", "summary": "hidden"}},
        {"type": "response_item", "payload": {"type": "function_call", "name": "shell_command", "arguments": "secret raw output"}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Implemented and tests passed"}]}},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n{corrupt", encoding="utf-8")
    records = parse_codex_jsonl(path)
    assert [record["kind"] for record in records] == ["visible_message", "tool_metadata", "visible_message"]
    assert all("hidden" not in json.dumps(record) for record in records)
    assert "arguments" not in json.dumps(records)
    assert all(record["source_ref"].startswith("codex:") for record in records)


def test_codex_jsonl_cursor_reads_only_appended_events_and_validates_prefix(
    tmp_path: Path,
) -> None:
    path = tmp_path / "session.jsonl"
    rows = [
        {"type": "session_meta", "payload": {"id": "session-1", "cwd": "C:/work/project"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "First"}},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    first = parse_codex_jsonl(path)
    cursor = first[-1]["_cursor"]
    offset = validated_jsonl_resume_offset(path, cursor)
    assert offset == path.stat().st_size

    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": "Second"}}
            )
            + "\n"
        )
    appended = parse_codex_jsonl(path, start_offset=validated_jsonl_resume_offset(path, cursor))
    assert [item["text"] for item in appended] == ["Second"]
    assert appended[0]["session_id"] == "session-1"
    assert appended[0]["cwd"] == "C:/work/project"

    content = path.read_text(encoding="utf-8")
    path.write_text(content.replace("First", "Changed"), encoding="utf-8")
    assert validated_jsonl_resume_offset(path, cursor) == 0


def test_antigravity_parser_uses_visible_sources_and_ignores_tool_output(tmp_path: Path) -> None:
    path = tmp_path / "12345678-1234-1234-1234-123456789012" / "logs" / "transcript.jsonl"
    path.parent.mkdir(parents=True)
    rows = [
        {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "Please implement"},
        {"step_index": 1, "source": "SYSTEM", "type": "SYSTEM_MESSAGE", "content": "hidden system"},
        {"step_index": 2, "source": "MODEL", "type": "RUN_COMMAND", "content": "raw command output"},
        {"step_index": 3, "source": "MODEL", "type": "GENERIC", "content": "Finished successfully"},
        {"step_index": 4, "source": "MODEL", "type": "PLANNER_RESPONSE", "tool_calls": [{"name": "run_command", "args": "private"}]},
        {"step_index": 5, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": "Implemented and tests passed"},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    records = parse_antigravity_transcript(path)
    assert [(item["role"], item["kind"]) for item in records] == [
        ("user", "visible_message"),
        ("assistant", "visible_message"),
        ("assistant", "tool_metadata"),
        ("assistant", "visible_message"),
    ]
    assert "raw command output" not in json.dumps(records)
    assert "private" not in json.dumps(records)


def test_antigravity_artifacts_and_readonly_database_inventory(tmp_path: Path) -> None:
    artifact = tmp_path / "session" / "walkthrough.md"
    artifact.parent.mkdir()
    artifact.write_text("Tests passed", encoding="utf-8")
    assert parse_antigravity_artifact(artifact)["kind"] == "artifact"
    database = tmp_path / "conversation.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE steps(id INTEGER PRIMARY KEY, content TEXT)")
        connection.execute("INSERT INTO steps(content) VALUES('visible')")
    before = database.stat().st_mtime_ns
    inspected = inspect_antigravity_database(database)
    assert inspected["tables"] == [{"name": "steps", "columns": ["id", "content"], "row_count": 1}]
    assert database.stat().st_mtime_ns == before


def test_antigravity_database_parser_reads_only_visible_protobuf_fields(
    tmp_path: Path,
) -> None:
    database = tmp_path / "11111111-2222-3333-4444-555555555555.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """CREATE TABLE steps(
            idx INTEGER PRIMARY KEY,step_type INTEGER,status INTEGER,
            has_subtrajectory INTEGER,metadata BLOB,error_details BLOB,permissions BLOB,
            task_details BLOB,render_info BLOB,step_payload BLOB,step_format INTEGER)"""
        )
        connection.execute("CREATE TABLE trajectory_metadata_blob(id TEXT PRIMARY KEY,data BLOB)")
        workspace = _protobuf_field(1, b"file:///C:/work/VisibleProject")
        connection.execute("INSERT INTO trajectory_metadata_blob VALUES('meta',?)", (workspace,))
        user = _step_payload(14, _protobuf_field(19, _protobuf_field(2, b"Please implement")))
        assistant = _step_payload(15, _protobuf_field(20, _protobuf_field(1, b"Implemented and tests passed")))
        hidden = _step_payload(101, _protobuf_field(106, _protobuf_field(1, b"hidden system")))
        tool = _step_payload(21, _protobuf_field(26, b"raw command output"))
        for index, step_type, payload in (
            (0, 14, user),
            (1, 15, assistant),
            (2, 101, hidden),
            (3, 21, tool),
        ):
            connection.execute(
                "INSERT INTO steps VALUES(?,?,3,0,NULL,NULL,NULL,NULL,NULL,?,0)",
                (index, step_type, payload),
            )

    before = database.stat().st_mtime_ns
    records = parse_antigravity_database(database)
    assert [(item["role"], item["kind"]) for item in records] == [
        ("user", "visible_message"),
        ("assistant", "visible_message"),
        ("assistant", "tool_metadata"),
    ]
    encoded = json.dumps(records)
    assert "Please implement" in encoded
    assert "Implemented and tests passed" in encoded
    assert "hidden system" not in encoded
    assert "raw command output" not in encoded
    assert all(item["cwd"] == "C:\\work\\VisibleProject" for item in records)
    cursor = records[-1]["_cursor"]
    assert validated_antigravity_database_resume_index(database, cursor) == 4
    assert parse_antigravity_database(database, start_idx=4) == []
    assert database.stat().st_mtime_ns == before
