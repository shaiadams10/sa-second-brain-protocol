from pathlib import Path

import pytest

from second_brain_protocol.config import RuntimePaths
from second_brain_protocol.mcp_gateway import READ_ONLY_TOOLS, TRANSPORT_ENABLED, describe, serve
from second_brain_protocol.service import query_project_graph
from second_brain_protocol.source_integrity import capture, compare


def test_source_integrity_proves_unchanged_and_detects_change(tmp_path: Path) -> None:
    projects = tmp_path / "Projects"
    projects.mkdir()
    source = projects / "file.txt"
    source.write_text("one", encoding="utf-8")
    before = capture(projects, tmp_path / "before.json.gz")
    unchanged = capture(projects, tmp_path / "same.json.gz")
    assert compare(before, unchanged).unchanged
    source.write_text("two words", encoding="utf-8")
    after = capture(projects, tmp_path / "after.json.gz")
    result = compare(before, after)
    assert not result.unchanged and result.modified == ["file.txt"]


def test_mcp_boundary_is_readonly_reserved_and_path_safe(tmp_path: Path) -> None:
    assert not TRANSPORT_ENABLED
    assert set(describe()["tools"]) == READ_ONLY_TOOLS
    assert not any(term in READ_ONLY_TOOLS for term in {"write", "delete", "ingest", "approve"})
    with pytest.raises(RuntimeError):
        serve()
    paths = RuntimePaths.from_root(tmp_path / "runtime")
    with pytest.raises(ValueError):
        query_project_graph(paths, "../../secret", "question")
