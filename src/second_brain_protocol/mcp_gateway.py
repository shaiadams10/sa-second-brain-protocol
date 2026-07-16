from __future__ import annotations

from typing import Any


TRANSPORT_ENABLED = False
READ_ONLY_TOOLS = {
    "search",
    "read_note",
    "build_context",
    "recent_activity",
    "query_project_graph",
    "get_project_neighbors",
    "trace_project_path",
}


def describe() -> dict[str, Any]:
    return {
        "transport_enabled": TRANSPORT_ENABLED,
        "tools": sorted(READ_ONLY_TOOLS),
        "exposes_raw_evidence": False,
        "exposes_ingestion_state": False,
        "exposes_basic_memory_writes": False,
    }


def serve() -> None:
    raise RuntimeError("MCP transport is intentionally disabled in v1. The read-only service boundary is reserved only.")
