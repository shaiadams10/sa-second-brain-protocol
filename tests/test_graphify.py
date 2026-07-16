from pathlib import Path

from second_brain_protocol.graphify_integration import graph_summary, update_project_graph


def test_graphify_code_only_writes_outside_source(tmp_path: Path) -> None:
    source = Path(__file__).parent / "fixtures" / "graph_project"
    manifest = {
        path.relative_to(source).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in source.rglob("*")
        if path.is_file()
    }
    before = {path.relative_to(source): (path.stat().st_size, path.stat().st_mtime_ns) for path in source.rglob("*") if path.is_file()}
    graph = update_project_graph({"id": "fixture", "local_path": str(source), "manifest": manifest}, tmp_path)
    after = {path.relative_to(source): (path.stat().st_size, path.stat().st_mtime_ns) for path in source.rglob("*") if path.is_file()}
    assert before == after
    assert graph.is_relative_to(tmp_path)
    assert not (tmp_path / "fixture" / "source-snapshot" / "README.md").exists()
    assert graph_summary(graph)["node_count"] >= 2
