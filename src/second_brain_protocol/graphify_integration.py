from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


BACKEND_ENV_PREFIXES = (
    "ANTHROPIC_",
    "OPENAI_",
    "GEMINI_",
    "GOOGLE_",
    "MOONSHOT_",
    "DEEPSEEK_",
    "AZURE_OPENAI_",
    "OLLAMA_",
    "AWS_",
)

IGNORED_CONTROL_FILES = {"CLAUDE.md", "claude_desktop_config.json"}

CODE_EXTENSIONS = {
    ".c", ".cc", ".cpp", ".cs", ".css", ".dart", ".go", ".h", ".hpp", ".html",
    ".java", ".js", ".jsx", ".kt", ".kts", ".lua", ".php", ".py", ".r", ".rb",
    ".rs", ".scss", ".sh", ".sql", ".svelte", ".swift", ".tsx", ".ts", ".vue",
    ".xml", ".yaml", ".yml", ".toml",
}


def _code_snapshot(project: dict[str, Any], output: Path) -> Path:
    source = Path(project["local_path"]).resolve()
    snapshot = output / "source-snapshot"
    if snapshot.exists():
        shutil.rmtree(snapshot)
    snapshot.mkdir(parents=True)
    for relative in project.get("manifest", {}):
        rel = Path(relative)
        lowered = {part.lower() for part in rel.parts}
        if lowered & {".claude", "node_modules", "vendor", ".git"}:
            continue
        if rel.name in IGNORED_CONTROL_FILES or rel.suffix.lower() not in CODE_EXTENSIONS:
            continue
        source_path = source / rel
        if not source_path.is_file():
            continue
        target = snapshot / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target)
    return snapshot


class GraphifyError(RuntimeError):
    pass


def graphify_executable() -> str | None:
    found = shutil.which("graphify")
    sibling = Path(sys.executable).with_name("graphify.exe")
    return found or (str(sibling) if sibling.exists() else None)


def _clean_environment(output_dir: Path) -> dict[str, str]:
    allowed = {
        "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP",
        "LOCALAPPDATA", "APPDATA", "USERPROFILE", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env["GRAPHIFY_OUT"] = str(output_dir)
    env["PYTHONHASHSEED"] = "0"
    return env


def update_project_graph(project: dict[str, Any], runtime_graph_root: Path, *, timeout: int = 900) -> Path:
    executable = graphify_executable()
    if not executable:
        raise GraphifyError("Graphify is not installed")
    source = Path(project["local_path"]).resolve()
    output = (runtime_graph_root / project["id"]).resolve()
    if output == source or source in output.parents:
        raise GraphifyError("Graphify output must be outside the source repository")
    output.mkdir(parents=True, exist_ok=True)
    snapshot = _code_snapshot(project, output)
    command = [
        executable,
        "extract",
        str(snapshot),
        "--out",
        str(output),
        "--no-cluster",
        "--code-only",
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_clean_environment(output),
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise GraphifyError(result.stderr[-4000:] or result.stdout[-4000:] or "Graphify failed")
    graph = output / "graph.json"
    if not graph.exists():
        graph = output / "graphify-out" / "graph.json"
    if not graph.exists():
        raise GraphifyError(f"Graphify completed without {graph}")
    return graph


def graph_summary(graph_path: Path) -> dict[str, Any]:
    try:
        data = json.loads(graph_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return {"error": str(error)}
    nodes = data.get("nodes", [])
    links = data.get("links", data.get("edges", []))
    relations: dict[str, int] = {}
    for link in links:
        relation = str(link.get("relation") or link.get("type") or "related")
        relations[relation] = relations.get(relation, 0) + 1
    return {
        "node_count": len(nodes),
        "edge_count": len(links),
        "top_relations": sorted(relations.items(), key=lambda item: (-item[1], item[0]))[:20],
    }


def query_graph(graph_path: Path, question: str, *, timeout: int = 60) -> str:
    executable = graphify_executable()
    if not executable:
        raise GraphifyError("Graphify is not installed")
    result = subprocess.run(
        [executable, "query", question, "--graph", str(graph_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=_clean_environment(graph_path.parent),
        check=False,
    )
    if result.returncode != 0:
        raise GraphifyError(result.stderr[-4000:] or "Graphify query failed")
    return result.stdout.strip()


def build_cross_project_graph(projects: list[dict[str, Any]], runtime_graph_root: Path) -> Path:
    output = runtime_graph_root / "cross-project"
    output.mkdir(parents=True, exist_ok=True)
    nodes: list[dict[str, Any]] = []
    links: list[dict[str, str]] = []
    seen_tech: set[str] = set()
    for project in projects:
        if project.get("classification") != "first-party":
            continue
        nodes.append({"id": project["id"], "label": project["name"], "type": "project"})
        for technology in project.get("tech_stack", []):
            technology_id = "tech:" + technology.lower().replace(" ", "-")
            if technology_id not in seen_tech:
                nodes.append({"id": technology_id, "label": technology, "type": "technology"})
                seen_tech.add(technology_id)
            links.append({"source": project["id"], "target": technology_id, "relation": "uses"})
    graph = output / "graph.json"
    graph.write_text(json.dumps({"nodes": nodes, "links": links}, indent=2) + "\n", encoding="utf-8")
    report = output / "GRAPH_REPORT.md"
    report.write_text(
        "# Cross-project technical graph\n\n"
        f"Projects: {sum(1 for item in nodes if item['type'] == 'project')}\n\n"
        f"Technologies: {sum(1 for item in nodes if item['type'] == 'technology')}\n",
        encoding="utf-8",
    )
    return graph
