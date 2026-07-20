from __future__ import annotations

import json
import os
import re
import subprocess
import webbrowser
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .codex_account import read_codex_rate_limits
from .config import RuntimePaths, load_defaults, load_runtime_config, protocol_root
from .markdown import slugify
from .model_runner import TOKEN_USAGE_FIELDS, usage_from_receipt
from .notifications import obsidian_uri
from .project_catalog import catalog_groups, project_catalog_health
from .review import build_review_groups, review_summary
from .scheduler import task_details
from .security import sanitize_text
from .state import StateStore


VISIBLE_PROJECT_CLASSES = {
    "first-party",
    "fork",
    "modified-fork",
    "experiment",
    "review",
}
MODEL_PRICING_USD_PER_MTOK = {
    "gpt-5.6-luna": {"input": 1.0, "cached_input": 0.10, "output": 6.0},
    "gpt-5.6-terra": {"input": 2.5, "cached_input": 0.25, "output": 15.0},
    "gpt-5.6-sol": {"input": 5.0, "cached_input": 0.50, "output": 30.0},
}
PRICING_AS_OF = "2026-07-17"
INSIGHT_KINDS = {
    "decision",
    "lesson",
    "personality",
    "preference",
    "project_fact",
    "skill",
    "voice_style",
    "work_style",
}
KIND_LABELS = {
    "decision": "Decision",
    "education": "Education",
    "experience": "Experience",
    "explicit_fact": "Personal fact",
    "goal": "Goal",
    "lesson": "Lesson",
    "military": "Military service",
    "personality": "Personal pattern",
    "preference": "Preference",
    "project_fact": "Project knowledge",
    "skill": "Capability",
    "voice_style": "Voice",
    "work_style": "Work style",
}
KNOWLEDGE_LAYER_ORDER = (
    "about_shai",
    "professional_profile",
    "operating_preferences",
    "project_knowledge",
)
KNOWLEDGE_LAYER_KINDS = {
    "about_shai": {"explicit_fact", "goal", "personality", "voice_style", "work_style"},
    "professional_profile": {"education", "experience", "military", "skill"},
    "operating_preferences": {"preference"},
    "project_knowledge": {"decision", "lesson", "project_fact"},
}
KNOWLEDGE_LAYER_NOTES = {
    "about_shai": "Identity/Persona.md",
    "professional_profile": "Skills/Index.md",
    "operating_preferences": "Identity/Preferences.md",
    "project_knowledge": "Projects/Index.md",
}
PERSONAL_PATTERN_KINDS = {"personality", "preference", "voice_style", "work_style"}
PROJECT_CONTEXT_MARKERS = (
    "this demo",
    "the animated demo",
    "this project",
    "the current project",
    "the referenced repository",
    "the linked figma",
    "dynamicremotion protocol",
    "for remotion workflow research",
    "when stopping local ai work",
    "home-network sharing",
    "native video-input path",
    "lm studio-based solution",
    "slow dequantization",
    "earlier attempt",
)
GLOBAL_PATTERN_MARKERS = (
    "shai prefers",
    "shai explicitly prefers",
    "prefers ",
    "actively rejects",
    "repeatedly asks",
    "asks for a plan",
    "across multiple",
    "every relevant reference",
    "multiple ai coding-agent environments",
    "project-local skill and tooling installations",
)
IMPORTANT_PROJECT_FACT_TERMS = (
    "authorship",
    "ownership",
    "first-party",
    "third-party",
    "deployed",
    "deployment",
    "production release",
    "went live",
    "archived",
    "paused",
    "blocked",
    "incomplete",
    "failed validation",
    "security boundary",
    "privacy boundary",
)
LOW_VALUE_KNOWLEDGE_TERMS = (
    "windows icons",
    "secondary iconography",
    "svgl.app",
    "svg logo",
    "linked figma design",
    "preview server available over the local network",
)
LOW_VALUE_QUESTION_TERMS = (
    "future packets",
    "trajectory excerpts",
    "what user request initiated",
    "master duration",
    "system-prompt size",
    "system prompt size",
    "caption identity count",
    "session timeline",
    "chat recovery outcome",
    "canonical project ids",
    "repository names",
    "folder project boundaries",
    "for each major project",
)
GENERIC_PROJECT_NAMES = {
    "app",
    "apps",
    "client",
    "code",
    "frontend",
    "lib",
    "libs",
    "packages",
    "server",
    "source",
    "src",
}


def _project_is_attributable(project: dict[str, Any]) -> bool:
    name = str(project.get("name") or project.get("logical_name") or "").strip()
    if not name or name.casefold() in GENERIC_PROJECT_NAMES:
        return False
    return str(project.get("classification") or "") not in {"collection", "duplicate"}


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _clean_markdown(text: str, *, max_chars: int = 520) -> str:
    text = sanitize_text(text, max_chars=max_chars * 3)
    text = re.sub(r"^---\s.*?\s---\s*", "", text, flags=re.DOTALL)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(
        r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", lambda m: m.group(2) or m.group(1), text
    )
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    text = re.sub(r"\^(?:obs|ev)-[a-zA-Z0-9-]+", "", text)
    text = re.sub(r"[`*>#]", "", text)
    text = re.sub(r"^\s*[-+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    shortened = text[: max_chars - 1].rsplit(" ", 1)[0].rstrip(" ,.;:")
    return shortened + "…"


def _generated_section(path: Path, section: str) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    pattern = re.compile(
        rf"<!-- sb:generated {re.escape(section)}:start -->\s*(.*?)\s*"
        rf"<!-- sb:generated {re.escape(section)}:end -->",
        re.DOTALL,
    )
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def _latest_note(folder: Path, pattern: str) -> Path | None:
    candidates = [
        path for path in folder.glob(pattern) if path.name.casefold() != "index.md"
    ]
    return max(candidates, key=lambda path: path.stem) if candidates else None


def _summary_sections(raw: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] = {"title": "Summary", "items": [], "paragraphs": []}
    paragraph_lines: list[str] = []

    def flush_paragraph() -> None:
        if not paragraph_lines:
            return
        paragraph = _clean_markdown(" ".join(paragraph_lines), max_chars=4000)
        if paragraph:
            current["paragraphs"].append(paragraph)
        paragraph_lines.clear()

    def flush_section() -> None:
        flush_paragraph()
        if current["items"] or current["paragraphs"]:
            sections.append(
                {
                    "title": current["title"],
                    "items": list(current["items"]),
                    "paragraphs": list(current["paragraphs"]),
                }
            )

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("<!--"):
            flush_paragraph()
            continue
        heading = re.match(r"^#{1,6}\s+(.+)$", stripped)
        if heading:
            flush_section()
            current = {
                "title": _clean_markdown(heading.group(1), max_chars=120),
                "items": [],
                "paragraphs": [],
            }
            continue
        bullet = re.match(r"^[-+*]\s+(.+)$", stripped)
        if bullet:
            flush_paragraph()
            item = _clean_markdown(bullet.group(1), max_chars=1200)
            if item:
                current["items"].append(item)
            continue
        paragraph_lines.append(stripped)
    flush_section()
    return sections


CHANGE_LABELS = {
    "added": "Project added",
    "classification_changed": "Classification",
    "commits_added": "Commits",
    "files_changed": "Files",
    "head_changed": "Git head",
    "removed": "Removed",
    "stack_changed": "Tech stack",
    "project_brain_changed": "Project brain",
    "working_tree_changed": "Working tree",
}


def _count_chips(text: str) -> list[dict[str, Any]]:
    return [
        {
            "label": CHANGE_LABELS.get(name.casefold(), name.replace("_", " ").title()),
            "value": int(value),
        }
        for name, value in re.findall(
            r"([a-z][a-z0-9_]*)\s*\((\d+)\)", text, re.IGNORECASE
        )
    ]


def _status_chips(text: str) -> list[dict[str, Any]]:
    chips = []
    for value, label in re.findall(r"(\d+)\s+([^,]+)", text):
        chips.append(
            {"label": label.strip().replace("_", " ").title(), "value": int(value)}
        )
    return chips


def _summary_visuals(sections: list[dict[str, Any]]) -> dict[str, Any]:
    """Turn deterministic activity bullets into compact visual dashboard data."""

    stats: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    items = [str(item) for section in sections for item in section.get("items", [])]
    for item in items:
        label, separator, detail = item.partition(":")
        normalized = label.strip().casefold()
        detail = detail.strip() if separator else item
        if normalized in {"project deltas", "projects with detected changes"}:
            if normalized == "project deltas":
                match = re.match(r"(\d+)\s+across\s+(.+)", detail, re.IGNORECASE)
            else:
                match = re.match(r"(\d+)(?:\s+-\s+(.+))?$", detail, re.IGNORECASE)
            if not match:
                continue
            count = int(match.group(1))
            names_text = match.group(2) or ""
            names = [name.strip() for name in names_text.split(",") if name.strip()]
            stats.append(
                {
                    "value": count,
                    "label": "Projects changed",
                    "icon": "🚀",
                    "tone": "pink",
                }
            )
            groups.append(
                {
                    "title": "Projects with detected changes",
                    "icon": "🗂️",
                    "tone": "pink",
                    "description": "Projects with new file, Git, stack, lifecycle, or working-tree changes in this run",
                    "chips": [{"label": name} for name in names],
                }
            )
        elif normalized in {"change types", "change signals"}:
            chips = _count_chips(detail)
            if chips:
                groups.append(
                    {
                        "title": "Change signals",
                        "icon": "🧩",
                        "tone": "purple",
                        "description": f"{sum(chip['value'] for chip in chips)} signals across {len(chips)} types",
                        "chips": chips,
                    }
                )
        elif normalized == "agent sessions reviewed":
            match = re.match(
                r"(\d+)\s+total\s+-\s+(\d+)\s+linked\s+to\s+(\d+)\s+projects?;\s+(\d+)\s+not\s+yet\s+linked(?:;\s*(.*))?",
                detail,
                re.IGNORECASE,
            )
            if not match:
                continue
            sessions, linked, projects, unlinked = (
                int(value) for value in match.groups()[:4]
            )
            stats.extend(
                [
                    {
                        "value": sessions,
                        "label": "Sessions reviewed",
                        "icon": "🤖",
                        "tone": "purple",
                    },
                    {
                        "value": projects,
                        "label": "Projects represented in sessions",
                        "icon": "🔗",
                        "tone": "green",
                    },
                ]
            )
            groups.append(
                {
                    "title": "Session-to-project coverage",
                    "icon": "🤖",
                    "tone": "green" if unlinked == 0 else "yellow",
                    "description": "How many reviewed agent sessions could be connected to a known project",
                    "chips": [
                        {"label": "Reviewed sessions", "value": sessions},
                        {"label": "Linked sessions", "value": linked},
                        {"label": "Projects represented", "value": projects},
                        {"label": "Not yet linked", "value": unlinked},
                    ],
                }
            )
        elif normalized == "agent sessions evaluated":
            match = re.match(
                r"(\d+)\s+across\s+(\d+)\s+attributed projects?", detail, re.IGNORECASE
            )
            if not match:
                continue
            sessions, projects = (int(value) for value in match.groups())
            stats.extend(
                [
                    {
                        "value": sessions,
                        "label": "Sessions reviewed",
                        "icon": "🤖",
                        "tone": "purple",
                    },
                    {
                        "value": projects,
                        "label": "Projects represented in sessions",
                        "icon": "🔗",
                        "tone": "green",
                    },
                ]
            )
            groups.append(
                {
                    "title": "Session-to-project coverage",
                    "icon": "🤖",
                    "tone": "green",
                    "description": "How many reviewed agent sessions could be connected to a known project",
                    "chips": [
                        {"label": "Reviewed sessions", "value": sessions},
                        {"label": "Projects represented", "value": projects},
                    ],
                }
            )
        elif normalized in {"recurring patterns", "knowledge signals"}:
            chips = _status_chips(detail)
            if not chips:
                continue
            promoted = next(
                (
                    chip["value"]
                    for chip in chips
                    if chip["label"].casefold() == "promoted"
                ),
                0,
            )
            stats.append(
                {
                    "value": promoted,
                    "label": "Patterns promoted",
                    "icon": "🔁",
                    "tone": "yellow",
                }
            )
            groups.append(
                {
                    "title": "Pattern status",
                    "icon": "🔁",
                    "tone": "yellow",
                    "description": "Recurring signals moving through evidence gates",
                    "chips": chips,
                }
            )

    if not groups and items:
        groups = [
            {
                "title": f"Update {index}",
                "icon": "✦",
                "tone": "purple",
                "description": item,
                "chips": [],
            }
            for index, item in enumerate(items[:6], 1)
        ]
    return {"available": bool(stats or groups), "stats": stats[:4], "groups": groups}


def _period_for_run(run: dict[str, Any], kind: str) -> str | None:
    timestamp = _parse_datetime(run.get("completed_at") or run.get("started_at"))
    if timestamp is None:
        return None
    local = timestamp.astimezone()
    if kind == "daily":
        return local.date().isoformat()
    week = local.isocalendar()
    return f"{week.year}-W{week.week:02d}"


def _summary_cost(iterations: list[dict[str, Any]]) -> dict[str, Any]:
    rates = []
    low = 0.0
    high = 0.0
    exact = True
    for iteration in iterations:
        model = str(iteration.get("model") or "")
        pricing = MODEL_PRICING_USD_PER_MTOK.get(model)
        if pricing is None:
            return {"available": False, "rates": []}
        if not any(item["model"] == model for item in rates):
            rates.append({"model": model, **pricing})
        scale = 1_000_000
        if iteration.get("details_available"):
            input_tokens = int(iteration.get("input_tokens") or 0)
            cached_tokens = min(
                input_tokens, int(iteration.get("cached_input_tokens") or 0)
            )
            uncached_tokens = max(0, input_tokens - cached_tokens)
            cache_write_tokens = int(iteration.get("cache_write_input_tokens") or 0)
            output_tokens = int(iteration.get("output_tokens") or 0)
            long_context = input_tokens > 272_000
            input_multiplier = 2.0 if long_context else 1.0
            output_multiplier = 1.5 if long_context else 1.0
            cost = (
                uncached_tokens * pricing["input"] * input_multiplier
                + cached_tokens * pricing["cached_input"] * input_multiplier
                + cache_write_tokens * pricing["input"] * 1.25 * input_multiplier
                + output_tokens * pricing["output"] * output_multiplier
            ) / scale
            low += cost
            high += cost
        else:
            exact = False
            total = int(iteration.get("total_tokens") or 0)
            low += total * pricing["input"] / scale
            high += total * pricing["output"] / scale
    return {
        "available": bool(rates),
        "exact_split": exact,
        "estimate_low_usd": round(low, 6),
        "estimate_high_usd": round(high, 6),
        "rates": rates,
        "pricing_as_of": PRICING_AS_OF,
        "basis": "api-equivalent",
    }


def _summary_codex_impact(
    usage: dict[str, Any], codex_usage: dict[str, Any]
) -> dict[str, Any]:
    """Estimate one run's percentage-point impact on the active Codex window."""

    if not usage.get("available") or not codex_usage.get("available"):
        return {"available": False}
    windows = codex_usage.get("windows") or []
    if not windows:
        return {"available": False}
    window = windows[0]
    observed_tokens = int(window.get("observed_tokens") or 0)
    used_percent = float(window.get("used_percent") or 0)
    run_tokens = int(usage.get("total_tokens") or 0)
    completed_values = [
        _parse_datetime(item.get("completed_at"))
        for item in usage.get("iterations") or []
        if item.get("completed_at")
    ]
    completed = max(
        (item for item in completed_values if item is not None), default=None
    )
    starts_at = _parse_datetime(window.get("starts_at"))
    resets_at = _parse_datetime(window.get("resets_at"))
    if (
        observed_tokens <= 0
        or used_percent <= 0
        or run_tokens <= 0
        or (completed and starts_at and completed < starts_at)
        or (completed and resets_at and completed > resets_at)
    ):
        return {"available": False}
    token_share_percent = run_tokens / observed_tokens * 100
    percentage_points = run_tokens / observed_tokens * used_percent
    return {
        "available": True,
        "estimated": True,
        "percentage_points": round(percentage_points, 4),
        "token_share_percent": round(token_share_percent, 4),
        "run_tokens": run_tokens,
        "observed_window_tokens": observed_tokens,
        "window_used_percent": used_percent,
        "window_label": window.get("label") or "Codex window",
        "method": "token-share-scaled-by-window-usage",
    }


def _summary_usage(store: StateStore, kind: str, period: str) -> dict[str, Any]:
    run_ids = store.summary_run_ids(kind, period)
    runs = {run["id"]: run for run in store.runs(limit=250)}
    if not run_ids:
        run_ids = [
            run["id"]
            for run in runs.values()
            if run.get("kind") == kind
            and run.get("status") == "completed"
            and _period_for_run(run, kind) == period
        ]
    stored = {item["run_id"]: item for item in store.usage_for_runs(run_ids)}
    iterations = []
    for run_id in sorted(run_ids):
        usage = stored.get(run_id) or usage_from_receipt(
            runs.get(run_id, {}).get("receipt_path")
        )
        if usage is None:
            continue
        iterations.append(
            {field: int(usage.get(field) or 0) for field in TOKEN_USAGE_FIELDS}
            | {
                "model": runs.get(run_id, {}).get("model"),
                "completed_at": runs.get(run_id, {}).get("completed_at"),
                "model_calls": int(usage.get("model_calls") or 0),
                "cached_result": bool(usage.get("cached_result")),
                "details_available": bool(usage.get("details_available")),
            }
        )
    if not iterations:
        return {"available": False, "iterations": []}
    totals = {
        field: sum(int(item.get(field) or 0) for item in iterations)
        for field in TOKEN_USAGE_FIELDS
    }
    result = {
        "available": True,
        **totals,
        "model_calls": sum(item["model_calls"] for item in iterations),
        "cached_results": sum(int(item["cached_result"]) for item in iterations),
        "details_available": all(item["details_available"] for item in iterations),
        "iterations": iterations,
    }
    result["pricing"] = _summary_cost(iterations)
    return result


def _summary_entry(
    vault: Path, path: Path, kind: str, *, usage: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    raw = _generated_section(path, kind)
    if not raw or "no automated run yet" in raw.casefold():
        return None
    sections = _summary_sections(raw)
    if not sections:
        return None

    synthesis = next(
        (
            section
            for section in sections
            if any(
                word in section["title"].casefold()
                for word in ("synthesis", "learn", "reflection")
            )
        ),
        sections[-1],
    )
    summary_candidates = list(synthesis["paragraphs"]) or list(synthesis["items"])
    if not summary_candidates:
        summary_candidates = [
            value
            for section in sections
            for value in (*section["paragraphs"], *section["items"])
        ]
    summary = summary_candidates[0] if summary_candidates else ""
    highlights = [item for section in sections for item in section["items"]]
    if not highlights:
        highlights = [
            paragraph for section in sections for paragraph in section["paragraphs"]
        ]
    period = path.stem
    label = "Daily" if kind == "daily" else "Weekly"
    return {
        "available": True,
        "kind": kind,
        "period": period,
        "title": f"{label} summary — {period}",
        "summary": summary,
        "highlights": highlights[:6],
        "sections": sections,
        "visuals": _summary_visuals(sections),
        "usage": usage or {"available": False, "iterations": []},
        "url": obsidian_uri(vault, path),
    }


def _summary_history(
    vault: Path, kind: str, store: StateStore, *, limit: int = 24
) -> list[dict[str, Any]]:
    folder = vault / "Journal" / ("Daily" if kind == "daily" else "Weekly")
    pattern = "20??-??-??.md" if kind == "daily" else "*.md"
    candidates = sorted(
        (path for path in folder.glob(pattern) if path.name.casefold() != "index.md"),
        key=lambda path: path.stem,
        reverse=True,
    )
    result = []
    for path in candidates:
        entry = _summary_entry(
            vault,
            path,
            kind,
            usage=_summary_usage(store, kind, path.stem),
        )
        if entry:
            result.append(entry)
        if len(result) >= limit:
            break
    return result


def personalize_knowledge_text(text: str, first_name: str) -> str:
    """Use the vault owner's preferred name in cards without rewriting canonical notes."""

    personalized = re.sub(
        r"\bthe user['’]s\b", f"{first_name}'s", text, flags=re.IGNORECASE
    )
    return re.sub(r"\bthe user\b", first_name, personalized, flags=re.IGNORECASE)


def _project_note(vault: Path, name: str) -> Path:
    candidate = vault / "Projects" / f"{slugify(name)}.md"
    return candidate if candidate.is_file() else vault / "Projects" / "Index.md"


def _observation_note(vault: Path, kind: str) -> Path:
    mapping = {
        "explicit_fact": vault / "Memory" / "LongTermMemory.md",
        "decision": vault / "Memory" / "Decisions.md",
        "lesson": vault / "Memory" / "Lessons.md",
        "project_fact": vault / "Memory" / "LongTermMemory.md",
        "skill": vault / "Skills" / "Index.md",
        "voice_style": vault / "Identity" / "Voice.md",
        "work_style": vault / "Identity" / "WorkStyle.md",
        "preference": vault / "Identity" / "Preferences.md",
        "personality": vault / "Identity" / "Persona.md",
        "experience": vault / "Experience" / "Employment.md",
        "education": vault / "Experience" / "Education.md",
        "military": vault / "Experience" / "MilitaryService.md",
        "goal": vault / "Goals" / "ActiveGoals.md",
    }
    return mapping.get(kind, vault / "Home.md")


def knowledge_layer_for(
    observation: dict[str, Any], *, project_scoped: bool = False
) -> str:
    """Place promoted knowledge in one stable human-facing layer."""

    if project_scoped and str(observation.get("kind") or "") in PERSONAL_PATTERN_KINDS:
        return "project_knowledge"
    declared = str((observation.get("payload") or {}).get("knowledge_layer") or "")
    if declared in KNOWLEDGE_LAYER_ORDER:
        return declared
    kind = str(observation.get("kind") or "")
    for layer, kinds in KNOWLEDGE_LAYER_KINDS.items():
        if kind in kinds:
            return layer
    return "project_knowledge"


def _observation_context(
    store: StateStore,
    observation: dict[str, Any],
    *,
    evidence_by_id: dict[str, dict[str, Any]] | None = None,
    projects_by_id: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = (
        observation.get("payload")
        if isinstance(observation.get("payload"), dict)
        else {}
    )
    evidence_refs = list(observation.get("evidence_refs") or [])
    evidence_rows = (
        [
            evidence_by_id[evidence_id]
            for evidence_id in evidence_refs
            if evidence_id in evidence_by_id
        ]
        if evidence_by_id is not None
        else store.evidence_by_ids(evidence_refs)
    )
    override_ids = payload.get("project_ids_override")
    attribution_overridden = isinstance(override_ids, list) and bool(override_ids)
    project_ids = (
        {str(project_id) for project_id in override_ids if project_id}
        if attribution_overridden
        else {
            str(project_id)
            for project_id in (payload.get("project_ids") or [])
            if project_id
        }
    )
    if not attribution_overridden and payload.get("project_id"):
        project_ids.add(str(payload["project_id"]))
    sessions: set[str] = set()
    dates: set[str] = set()
    session_scoped = False
    trusted_profile_evidence = False
    for row in evidence_rows:
        row_payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if not attribution_overridden:
            if row.get("project_id"):
                project_ids.add(str(row["project_id"]))
            project_ids.update(
                str(item) for item in row_payload.get("project_ids", []) if item
            )
        session_id = row_payload.get("session_id")
        if session_id:
            sessions.add(str(session_id))
            session_scoped = True
        if row.get("kind") == "session_digest" or row.get("source_type") in {
            "codex",
            "session-digest",
            "antigravity",
        }:
            session_scoped = True
        occurred = str(row.get("occurred_at") or row.get("created_at") or "")[:10]
        if occurred:
            dates.add(occurred)
        if row.get("source_type") in {"interview", "linkedin"}:
            trusted_profile_evidence = True

    projects = (
        projects_by_id
        if projects_by_id is not None
        else {
            str(item["id"]): item
            for item in store.projects()
            if _project_is_attributable(item)
        }
    )
    project_names = sorted(
        {
            str(
                projects[project_id].get("name")
                or projects[project_id].get("logical_name")
                or project_id
            )
            for project_id in project_ids
            if project_id in projects
        },
        key=str.casefold,
    )
    return {
        "project_ids": sorted(project_ids),
        "project_names": project_names,
        "sessions": sessions,
        "dates": dates,
        "session_scoped": session_scoped,
        "trusted_profile_evidence": trusted_profile_evidence,
        "attribution_overridden": attribution_overridden,
    }


def _focused_project_names(
    store: StateStore,
    candidates: list[str],
    text: str,
    *,
    all_project_names: list[str] | None = None,
) -> list[str]:
    """Prefer a project named by the card over noisy multi-project evidence context."""

    def normalized(value: str) -> str:
        expanded = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
        return " ".join(re.findall(r"[a-z0-9]+", expanded.casefold()))

    folded = normalized(text)
    all_names = (
        all_project_names
        if all_project_names is not None
        else sorted(
            {
                str(item.get("name") or item.get("logical_name") or "").strip()
                for item in store.projects()
                if _project_is_attributable(item)
                if item.get("name") or item.get("logical_name")
            },
            key=str.casefold,
        )
    )
    direct = [
        name for name in all_names if len(name) >= 5 and normalized(name) in folded
    ]
    if direct:
        return [max(direct, key=len)]
    stopwords = {
        "project",
        "version",
        "status",
        "current",
        "ownership",
        "repository",
        "application",
        "system",
        "shai",
        "adams",
        "agent",
        "agents",
        "assisted",
        "demo",
        "demos",
    }
    text_tokens = set(folded.split()) - stopwords
    scored: list[tuple[int, str]] = []
    for name in all_names:
        tokens = {
            token
            for token in normalized(name).split()
            if len(token) >= 4 and token not in stopwords
        }
        scored.append((len(tokens & text_tokens), name))
    best = max((score for score, _name in scored), default=0)
    winners = [name for score, name in scored if score == best and score > 0]
    if len(winners) == 1:
        return winners
    return candidates


def _knowledge_scope(
    store: StateStore,
    observation: dict[str, Any],
    *,
    context: dict[str, Any] | None = None,
) -> tuple[str, list[str]]:
    """Separate durable personal patterns from contextual project instructions."""

    payload = (
        observation.get("payload")
        if isinstance(observation.get("payload"), dict)
        else {}
    )
    declared = str(
        payload.get("scope") or payload.get("knowledge_scope") or ""
    ).casefold()
    context = context or _observation_context(store, observation)
    project_names = list(context["project_names"])
    kind = str(observation.get("kind") or "")
    if kind not in PERSONAL_PATTERN_KINDS:
        return (
            "project" if kind in {"decision", "lesson", "project_fact"} else "global"
        ), project_names
    if declared == "project":
        return "project", project_names
    if declared == "global" or context["trusted_profile_evidence"]:
        return "global", project_names

    observed_project_count = (
        len(context["project_ids"])
        if context["attribution_overridden"]
        else max(
            len(context["project_ids"]), int(observation.get("project_count") or 0)
        )
    )
    stable_cross_project = (
        len(context["sessions"]) >= 3
        and len(context["dates"]) >= 2
        and observed_project_count >= 2
    )
    if stable_cross_project:
        return "global", project_names
    pattern_text = " ".join(
        str(value)
        for value in (
            observation.get("subject"),
            observation.get("claim"),
        )
        if value
    ).casefold()
    explicitly_contextual = any(
        marker in pattern_text for marker in PROJECT_CONTEXT_MARKERS
    )
    explicitly_reusable = any(
        marker in pattern_text for marker in GLOBAL_PATTERN_MARKERS
    )
    if explicitly_reusable and not explicitly_contextual:
        return "global", project_names
    if context["session_scoped"] or observed_project_count > 0:
        return "project", project_names
    return "global", project_names


def _knowledge_card_is_useful(
    observation: dict[str, Any], *, scope: str, subject: str, claim: str
) -> bool:
    """Keep Curate focused on durable owner judgment instead of routine inventory."""

    kind = str(observation.get("kind") or "")
    text = f"{subject} {claim}".casefold()
    if any(term in text for term in LOW_VALUE_KNOWLEDGE_TERMS):
        return False
    if scope == "project" and kind in PERSONAL_PATTERN_KINDS:
        return False
    if kind != "project_fact":
        return True
    return any(term in text for term in IMPORTANT_PROJECT_FACT_TERMS)


def default_knowledge_layer(layers: list[dict[str, Any]]) -> str:
    """Open Curate on the first layer that has unreviewed knowledge."""
    return next(
        (str(layer["key"]) for layer in layers if int(layer.get("new") or 0) > 0),
        "about_shai",
    )


def _knowledge_deck(store: StateStore, vault: Path) -> dict[str, Any]:
    feedback = store.knowledge_feedback()
    rows = sorted(
        store.observations("promoted"),
        key=lambda item: item["updated_at"],
        reverse=True,
    )
    projects = [item for item in store.projects() if _project_is_attributable(item)]
    projects_by_id = {str(item["id"]): item for item in projects}
    all_project_names = sorted(
        {
            str(item.get("name") or item.get("logical_name") or "").strip()
            for item in projects
            if item.get("name") or item.get("logical_name")
        },
        key=str.casefold,
    )
    evidence_by_id = {
        str(item["id"]): item
        for item in store.evidence_by_ids(
            sorted(
                {
                    str(evidence_id)
                    for row in rows
                    for evidence_id in (row.get("evidence_refs") or [])
                    if evidence_id
                }
            )
        )
    }
    display_name = vault.name.removesuffix(" Second Brain").strip() or "Second Brain"
    first_name = display_name.split()[0]
    cards = []
    for item in rows:
        if item.get("sensitivity") == "sensitive":
            continue
        claim = _clean_markdown(str(item.get("claim") or ""), max_chars=420)
        if not claim:
            continue
        claim = personalize_knowledge_text(claim, first_name)
        decision = feedback.get(item["id"], {}).get("decision")
        if decision == "disliked":
            continue
        context = _observation_context(
            store,
            item,
            evidence_by_id=evidence_by_id,
            projects_by_id=projects_by_id,
        )
        scope, project_names = _knowledge_scope(store, item, context=context)
        project_names = _focused_project_names(
            store,
            project_names,
            f"{item.get('subject', '')} {item.get('claim', '')}",
            all_project_names=all_project_names,
        )
        layer = knowledge_layer_for(item, project_scoped=scope == "project")
        subject = _clean_markdown(str(item.get("subject") or "Knowledge"), max_chars=96)
        if not _knowledge_card_is_useful(
            item, scope=scope, subject=subject, claim=claim
        ):
            continue
        if scope == "project":
            project_stamp = (
                " + ".join(project_names) if project_names else "Project not attributed"
            )
            note = (
                _project_note(vault, project_names[0])
                if len(project_names) == 1
                else vault / "Projects" / "Index.md"
            )
        else:
            project_stamp = ""
            note = _observation_note(vault, item["kind"])
        cards.append(
            {
                "id": item["id"],
                "layer": layer,
                "kind": KIND_LABELS.get(
                    item["kind"], item["kind"].replace("_", " ").title()
                ),
                "subject": subject,
                "claim": claim,
                "confidence": round(float(item.get("confidence", 0)) * 100),
                "source_count": int(item.get("source_count") or 0),
                "project_count": int(item.get("project_count") or 0),
                "scope": scope,
                "project_names": project_names,
                "project_stamp": project_stamp,
                "updated_at": item.get("updated_at"),
                "feedback": decision,
                "url": obsidian_uri(vault, note),
            }
        )
    confirmed = sum(1 for card in cards if card["feedback"] == "liked")
    removed = sum(1 for item in feedback.values() if item.get("decision") == "disliked")
    layer_content = {
        "about_shai": {
            "label": f"About {first_name}",
            "short_label": f"About {first_name}",
            "description": "Identity, voice, work style, personality, values, and goals.",
        },
        "professional_profile": {
            "label": "Professional profile",
            "short_label": "Professional",
            "description": "Capabilities, skills, experience, education, service, and verified technical range.",
        },
        "operating_preferences": {
            "label": "Operating preferences",
            "short_label": "How I work",
            "description": "Reusable cross-project behavior, preferred formats, validation rules, design taste, and protocols.",
        },
        "project_knowledge": {
            "label": "Project knowledge",
            "short_label": "Projects",
            "description": "Project-specific facts, architecture, decisions, implementations, lessons, and contextual instructions—not personality.",
        },
    }
    layers = []
    for key in KNOWLEDGE_LAYER_ORDER:
        layer_cards = [card for card in cards if card["layer"] == key]
        layer_confirmed = sum(1 for card in layer_cards if card["feedback"] == "liked")
        layers.append(
            {
                "key": key,
                **layer_content[key],
                "count": len(layer_cards),
                "new": len(layer_cards) - layer_confirmed,
                "confirmed": layer_confirmed,
                "url": obsidian_uri(vault, vault / KNOWLEDGE_LAYER_NOTES[key]),
            }
        )
    return {
        "cards": cards,
        "default_layer": default_knowledge_layer(layers),
        "layers": layers,
        "counts": {
            "new": len(cards) - confirmed,
            "all": len(cards),
            "confirmed": confirmed,
            "removed": removed,
        },
        "undo_available": store.last_knowledge_dislike() is not None,
    }


def _question_is_owner_worthy(
    group_key: str, item: dict[str, Any], question: str
) -> bool:
    """Surface questions that need the user's judgment, not agent-resolvable trivia."""

    text = f"{item.get('subject', '')} {question}".casefold()
    if group_key == "questions-technical":
        return False
    if any(term in text for term in LOW_VALUE_QUESTION_TERMS):
        return False
    if group_key == "questions-profile-privacy":
        return True
    if group_key == "questions-attribution":
        return any(
            term in text
            for term in (
                "owner",
                "ownership",
                "author",
                "attribution",
                "contribution",
                "original",
                "first-party",
                "third-party",
                "fork",
                "collaboration",
                "boundaries",
                "belong",
                "separate projects",
            )
        )
    if group_key == "questions-project-state":
        return any(
            term in text
            for term in (
                "status",
                "current state",
                "deployed",
                "deployment",
                "working",
                "prototype",
                "paused",
                "archived",
                "release",
                "timeline",
                "public status",
                "validation status",
            )
        )
    return False


def _question_suggestions(
    group_key: str,
    *,
    subject: str,
    question: str,
    project_names: list[str],
) -> list[dict[str, str]]:
    """Create editable, question-specific answer starters."""

    project = project_names[0] if len(project_names) == 1 else "[project name]"
    text = f"{subject} {question}".casefold()
    if group_key == "questions-profile-privacy":
        return [
            {
                "label": "Career-safe",
                "text": f"Safe for career use: [verified capability]. Supporting project: {project}. Date or period: [month/year]. Keep [private detail] private.",
            },
            {
                "label": "Private only",
                "text": "Private only. Remember the corrected fact as: [correction]. Do not use it in resumes, LinkedIn, or public bios.",
            },
            {
                "label": "Public-safe",
                "text": f"Safe to share publicly: [approved wording]. Supporting project: {project}. Remove or generalize [sensitive detail].",
            },
        ]
    if "boundar" in text or "belong" in text or "separate" in text:
        return [
            {
                "label": "Map workstreams",
                "text": "[Workstream A] belongs to [canonical project]. [Workstream B] should be tracked separately as [project name] because [reason].",
            },
            {
                "label": "Keep together",
                "text": f"Keep these workstreams together under {project}. They are parts of the same project because [reason].",
            },
        ]
    if any(
        term in text for term in ("contribution", "direct", "implement", "components")
    ):
        return [
            {
                "label": "My contribution",
                "text": f"For {project}, I directed [features/decisions] and implemented [components]. [Person/tool] handled [other parts].",
            },
            {
                "label": "Collaboration",
                "text": f"{project} was collaborative. My responsibility was [scope]; collaborators or generated code covered [scope].",
            },
            {
                "label": "Not my work",
                "text": f"{project} is a third-party reference or experiment. Do not credit me as the original author; only retain [specific adaptation, if any].",
            },
        ]
    if group_key == "questions-attribution":
        return [
            {
                "label": "First-party",
                "text": f"{project} is first-party work. I directed [scope] and implemented [parts]. Public attribution is [allowed/not allowed].",
            },
            {
                "label": "Modified fork",
                "text": f"{project} is a modified fork of [upstream]. Credit me only for [changes]; retain upstream attribution for the rest.",
            },
            {
                "label": "Third-party",
                "text": f"{project} is a third-party reference. Do not treat it as evidence of my authorship or original work.",
            },
        ]
    if group_key == "questions-project-state" and any(
        term in text for term in ("portfolio-ready", "shipped", "experimental")
    ):
        return [
            {
                "label": "Classify demos",
                "text": f"For {project}: shipped demos: [names]. Portfolio-ready demos: [names]. Experimental demos: [names]. Archived demos: [names].",
            },
            {
                "label": "Portfolio-ready only",
                "text": f"For {project}, the demos ready to include are [names] because [reason]. Keep [names] experimental, and archive [names].",
            },
            {
                "label": "Needs verification",
                "text": f"For {project}, no demo classification is final yet. Verify [demos/checks], then classify each as shipped, portfolio-ready, experimental, or archived.",
            },
        ]
    if group_key == "questions-project-state" and any(
        term in text for term in ("architecture", "milestone", "phase")
    ):
        return [
            {
                "label": "Describe current state",
                "text": f"{project} phase: [phase]. Canonical architecture: [short description]. Production status: [local/deployed/prototype]. Next milestone: [milestone].",
            },
            {
                "label": "Still a prototype",
                "text": f"{project} is still a prototype. Working now: [parts]. Architecture still changing: [parts]. The next milestone is [milestone].",
            },
            {
                "label": "Production-ready",
                "text": f"{project} is production-ready as of [month/year]. Canonical architecture: [description]. Next milestone: [milestone].",
            },
        ]
    if group_key == "questions-project-state" and "timeline" in text:
        return [
            {
                "label": "Add timeline",
                "text": f"{project} started around [month/year]. Major milestone: [event/date]. Current status: [status]. Last meaningful work: [month/year].",
            },
            {
                "label": "Dates uncertain",
                "text": f"{project} is [current status]. I do not remember the exact dates; use repository evidence to reconstruct the timeline.",
            },
        ]
    if group_key == "questions-project-state" and "validation" in text:
        return [
            {
                "label": "Validated",
                "text": f"{project} was validated in [environment] on [month/year]. Verified: [checks]. Not yet verified: [remaining checks].",
            },
            {
                "label": "Not validated",
                "text": f"{project} has not been fully validated. Last known working state: [state]. Required verification: [checks].",
            },
        ]
    if group_key == "questions-project-state":
        return [
            {
                "label": "Working locally",
                "text": f"{project} is working locally as of [month/year]. Verified: [what works]. Still incomplete: [remaining work].",
            },
            {
                "label": "Deployed",
                "text": f"{project} is deployed as of [month/year] at [private/public location]. Verified: [checks]. Current limitation: [limitation].",
            },
            {
                "label": "Prototype / paused",
                "text": f"{project} is currently a [prototype/paused/archived] project. Last meaningful state: [state] in [month/year].",
            },
        ]
    return [
        {
            "label": "Answer with context",
            "text": f"For {project}: [direct answer]. Evidence or date: [context]. Anything still uncertain: [unknowns].",
        }
    ]


def _question_deck(store: StateStore, vault: Path) -> dict[str, Any]:
    pending = store.observations("pending")
    groups = build_review_groups(pending)
    projects = [item for item in store.projects() if _project_is_attributable(item)]
    projects_by_id = {str(item["id"]): item for item in projects}
    all_project_names = sorted(
        {
            str(item.get("name") or item.get("logical_name") or "").strip()
            for item in projects
            if item.get("name") or item.get("logical_name")
        },
        key=str.casefold,
    )
    evidence_by_id = {
        str(item["id"]): item
        for item in store.evidence_by_ids(
            sorted(
                {
                    str(evidence_id)
                    for row in pending
                    for evidence_id in (row.get("evidence_refs") or [])
                    if evidence_id
                }
            )
        )
    }
    cards: list[dict[str, Any]] = []
    category_counts: dict[str, int] = defaultdict(int)
    total_questions = 0
    for group in groups:
        if group["section"] != "questions":
            continue
        total_questions += int(group["count"])
        group_path = vault / "Inbox" / "Review" / "Groups" / group["filename"]
        for item in group["items"]:
            payload = item.get("payload") or {}
            nested_payload = (
                payload.get("payload")
                if isinstance(payload.get("payload"), dict)
                else {}
            )
            question = _clean_markdown(
                str(
                    payload.get("question")
                    or nested_payload.get("question")
                    or item.get("claim")
                    or ""
                ),
                max_chars=1600,
            )
            if not question:
                continue
            if not _question_is_owner_worthy(group["key"], item, question):
                continue
            context = _observation_context(
                store,
                item,
                evidence_by_id=evidence_by_id,
                projects_by_id=projects_by_id,
            )
            subject = _clean_markdown(
                str(item.get("subject") or "Open question"), max_chars=140
            )
            project_names = _focused_project_names(
                store,
                list(context["project_names"]),
                f"{subject} {question}",
                all_project_names=all_project_names,
            )
            scope = (
                "profile" if group["key"] == "questions-profile-privacy" else "project"
            )
            if scope == "project":
                project_stamp = (
                    " + ".join(project_names[:2])
                    + (
                        f" + {len(project_names) - 2} more"
                        if len(project_names) > 2
                        else ""
                    )
                    if project_names
                    else "Project not attributed"
                )
                destination = "Project knowledge"
                destination_detail = "This answer stays with project knowledge and is not saved as personality."
            else:
                project_stamp = "Professional profile"
                destination = "Professional profile"
                destination_detail = "This answer supports verified profile facts; it does not become a personality trait."
            suggestions = _question_suggestions(
                group["key"],
                subject=subject,
                question=question,
                project_names=project_names,
            )
            category_counts[group["title"]] += 1
            cards.append(
                {
                    "id": item["id"],
                    "category": group["title"],
                    "subject": subject,
                    "question": question,
                    "explanation": group["description"],
                    "guidance": group["answer_template"],
                    "scope": scope,
                    "project_names": project_names,
                    "project_stamp": project_stamp,
                    "destination": destination,
                    "destination_detail": destination_detail,
                    "suggestions": suggestions,
                    "placeholder": suggestions[0]["text"],
                    "url": obsidian_uri(vault, group_path),
                }
            )
    cards.sort(
        key=lambda card: (
            0 if card["scope"] == "project" and len(card["project_names"]) == 1 else 1,
            0 if card["scope"] == "profile" else 1,
            card["subject"].casefold(),
        )
    )
    categories = [
        {"title": title, "count": count} for title, count in category_counts.items()
    ]
    return {
        "cards": cards,
        "count": len(cards),
        "deferred_count": max(0, total_questions - len(cards)),
        "categories": categories,
        "undo_available": store.last_question_dismissal() is not None,
    }


def _recent_activity(
    store: StateStore, vault: Path, *, now: datetime, days: int = 7
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    cutoff = now.astimezone(UTC) - timedelta(days=days)
    projects = {
        item["id"]: item for item in store.projects() if _project_is_attributable(item)
    }
    presence: dict[str, bool] = {}
    with store.connect() as connection:
        for row in connection.execute(
            "SELECT project_id,present FROM project_presence"
        ).fetchall():
            presence[str(row["project_id"])] = bool(row["present"])
        digest_rows = connection.execute(
            """SELECT source_type,project_id,occurred_at,payload_json FROM evidence
            WHERE kind='session_digest' AND COALESCE(occurred_at,created_at) >= ?""",
            (cutoff.isoformat(),),
        ).fetchall()
        delta_rows = connection.execute(
            """SELECT project_id,COALESCE(occurred_at,created_at) AS activity_at FROM evidence
            WHERE kind='project_delta' AND COALESCE(occurred_at,created_at) >= ?""",
            (cutoff.isoformat(),),
        ).fetchall()
        all_digests = connection.execute(
            "SELECT source_type,payload_json FROM evidence WHERE kind='session_digest'"
        ).fetchall()

    sessions_by_day: dict[str, set[str]] = defaultdict(set)
    sessions_by_project: dict[str, set[str]] = defaultdict(set)
    last_activity: dict[str, datetime] = {}
    for row in digest_rows:
        try:
            payload = json.loads(row["payload_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        session_id = str(payload.get("session_id") or "")
        if not session_id:
            continue
        key = f"{row['source_type']}:{session_id}"
        occurred = _parse_datetime(row["occurred_at"]) or cutoff
        local_day = occurred.astimezone(now.tzinfo).date().isoformat()
        sessions_by_day[local_day].add(key)
        project_ids = set(str(item) for item in payload.get("project_ids", []) if item)
        if row["project_id"]:
            project_ids.add(str(row["project_id"]))
        for project_id in project_ids:
            project = projects.get(project_id)
            if not project or not presence.get(project_id, True):
                continue
            if project.get("classification") not in VISIBLE_PROJECT_CLASSES:
                continue
            sessions_by_project[project_id].add(key)
            last_activity[project_id] = max(
                last_activity.get(project_id, occurred), occurred
            )

    changes: dict[str, int] = defaultdict(int)
    for row in delta_rows:
        project_id = str(row["project_id"] or "")
        project = projects.get(project_id)
        if not project or not presence.get(project_id, True):
            continue
        if project.get("classification") not in VISIBLE_PROJECT_CLASSES:
            continue
        changes[project_id] += 1
        occurred = _parse_datetime(row["activity_at"])
        if occurred:
            last_activity[project_id] = max(
                last_activity.get(project_id, occurred), occurred
            )

    activity = []
    active_ids = set(sessions_by_project) | set(changes)
    for project_id in active_ids:
        project = projects[project_id]
        sessions = len(sessions_by_project.get(project_id, set()))
        change_count = changes.get(project_id, 0)
        activity.append(
            {
                "name": _clean_markdown(
                    str(project.get("name") or "Untitled project"), max_chars=80
                ),
                "classification": str(
                    project.get("classification") or "review"
                ).replace("-", " "),
                "sessions": sessions,
                "changes": change_count,
                "score": sessions * 3 + change_count,
                "last_activity": last_activity.get(project_id).isoformat()
                if project_id in last_activity
                else None,
                "url": obsidian_uri(
                    vault, _project_note(vault, str(project.get("name") or ""))
                ),
            }
        )
    activity.sort(
        key=lambda item: (item["score"], item["last_activity"] or ""), reverse=True
    )
    activity = activity[:6]
    max_score = max((item["score"] for item in activity), default=1)
    for item in activity:
        item["relative"] = max(8, round(item["score"] / max_score * 100))

    trend = []
    for offset in range(days - 1, -1, -1):
        day = (now.date() - timedelta(days=offset)).isoformat()
        trend.append({"date": day, "sessions": len(sessions_by_day.get(day, set()))})

    all_session_keys: set[str] = set()
    for row in all_digests:
        try:
            payload = json.loads(row["payload_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        session_id = payload.get("session_id")
        if session_id:
            all_session_keys.add(f"{row['source_type']}:{session_id}")
    return activity, trend, len(all_session_keys)


def _recent_insights(store: StateStore, vault: Path) -> list[dict[str, Any]]:
    rows = sorted(
        store.observations("promoted"),
        key=lambda item: item["updated_at"],
        reverse=True,
    )
    insights = []
    for item in rows:
        if item["kind"] not in INSIGHT_KINDS or item.get("sensitivity") == "sensitive":
            continue
        claim = _clean_markdown(str(item.get("claim") or ""), max_chars=240)
        if not claim:
            continue
        insights.append(
            {
                "kind": KIND_LABELS.get(
                    item["kind"], item["kind"].replace("_", " ").title()
                ),
                "subject": _clean_markdown(
                    str(item.get("subject") or "New insight"), max_chars=80
                ),
                "claim": claim,
                "confidence": round(float(item.get("confidence", 0)) * 100),
                "updated_at": item.get("updated_at"),
                "url": obsidian_uri(vault, _observation_note(vault, item["kind"])),
            }
        )
        if len(insights) == 5:
            break
    return insights


def _forming_patterns(store: StateStore, vault: Path) -> list[dict[str, Any]]:
    rows = store.pattern_signals("tracking")
    patterns = []
    for item in rows:
        sessions = int(item.get("session_count", 0))
        dates = int(item.get("date_count", 0))
        projects = int(item.get("project_count", 0))
        progress = round(
            (min(sessions / 3, 1) + min(dates / 2, 1) + min(projects / 2, 1)) / 3 * 100
        )
        patterns.append(
            {
                "label": _clean_markdown(
                    str(item.get("label") or "Emerging pattern"), max_chars=80
                ),
                "claim": _clean_markdown(str(item.get("claim") or ""), max_chars=190),
                "sessions": sessions,
                "dates": dates,
                "projects": projects,
                "progress": progress,
                "last_seen": item.get("last_seen"),
                "url": obsidian_uri(vault, vault / "Memory" / "Patterns.md"),
            }
        )
    patterns.sort(
        key=lambda item: (item["progress"], item["last_seen"] or ""), reverse=True
    )
    return patterns[:4]


def _group_runs(store: StateStore, *, limit: int = 7) -> list[dict[str, Any]]:
    pipeline_rows = store.pipeline_runs(limit=30)
    grouped: list[dict[str, Any]] = []
    if pipeline_rows:
        oldest_pipeline = min(str(row["started_at"]) for row in pipeline_rows)
        source_rows = [
            {
                **row,
                "pipeline": True,
                "model": None,
                "reasoning": None,
                "evidence_count": 0,
            }
            for row in pipeline_rows
        ]
        source_rows.extend(
            {**row, "pipeline": False}
            for row in store.runs(limit=30)
            if str(row.get("started_at") or "") < oldest_pipeline
        )
        source_rows.sort(key=lambda row: str(row.get("started_at") or ""), reverse=True)
    else:
        source_rows = [{**row, "pipeline": False} for row in store.runs(limit=30)]

    for run in source_rows:
        started = _parse_datetime(run.get("started_at"))
        completed = _parse_datetime(run.get("completed_at"))
        status = (
            "completed"
            if run.get("status") == "validated"
            else str(run.get("status") or "unknown")
        )
        candidate = {
            "kind": str(run.get("kind") or "operation").replace("-", " ").title(),
            "status": status,
            "started_at": run.get("started_at"),
            "completed_at": run.get("completed_at"),
            "model": run.get("model"),
            "reasoning": run.get("reasoning"),
            "evidence_count": int(run.get("evidence_count") or 0),
            "duration_seconds": max(0, round((completed - started).total_seconds()))
            if started and completed
            else None,
            "batch_count": 1,
            "stage": str(run.get("stage") or "").replace("_", " ").title(),
            "error": sanitize_text(str(run.get("error") or ""), max_chars=240),
            "trigger": str(run.get("trigger") or ""),
        }
        previous = grouped[-1] if grouped else None
        previous_started = (
            _parse_datetime(previous.get("started_at")) if previous else None
        )
        if (
            previous
            and previous["kind"] == candidate["kind"]
            and previous["status"] == candidate["status"]
            and started
            and previous_started
            and abs((previous_started - started).total_seconds()) <= 8
        ):
            previous["batch_count"] += 1
            previous["evidence_count"] += candidate["evidence_count"]
            if (
                previous["duration_seconds"] is not None
                and candidate["duration_seconds"] is not None
            ):
                previous["duration_seconds"] = max(
                    previous["duration_seconds"], candidate["duration_seconds"]
                )
            continue
        grouped.append(candidate)
        if len(grouped) == limit:
            break
    return grouped


def _git_summary(vault: Path) -> dict[str, Any]:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=vault,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        return {"state": "unknown", "detail": "Git status unavailable"}
    changes = len([line for line in result.stdout.splitlines() if line.strip()])
    if changes:
        return {
            "state": "attention",
            "detail": f"{changes} local edit{'s' if changes != 1 else ''}",
        }
    return {"state": "good", "detail": "Working tree clean"}


def _cached_search_health(paths: RuntimePaths, store: StateStore) -> dict[str, Any]:
    refresh = store.search_refresh_status()
    pending = int(refresh.get("pending") or 0)
    if pending:
        if refresh.get("state") == "failed":
            return {
                "state": "attention",
                "detail": f"{pending} note update{'s' if pending != 1 else ''} waiting for search repair",
            }
        return {
            "state": "neutral",
            "detail": f"Updating {pending} changed note{'s' if pending != 1 else ''} in the background",
        }
    report_path = paths.runs / "health-report.json"
    if report_path.is_file():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            if bool(report.get("basic_memory", {}).get("ok")):
                return {"state": "good", "detail": "Local semantic index ready"}
        except (json.JSONDecodeError, OSError, TypeError):
            pass
    if (paths.basic_memory / "vault-mirror").is_dir():
        return {"state": "neutral", "detail": "Local index available"}
    return {"state": "attention", "detail": "Index needs a health check"}


def _focus(vault: Path) -> dict[str, str]:
    path = vault / "Goals" / "ActiveGoals.md"
    raw = _generated_section(path, "goal-suggestions")
    title_match = re.search(r"\*\*([^:*]+):\*\*", raw)
    return {
        "title": _clean_markdown(title_match.group(1), max_chars=80)
        if title_match
        else "Current direction",
        "summary": _clean_markdown(raw, max_chars=380),
        "url": obsidian_uri(vault, path),
    }


def _system_status(
    store: StateStore, schedule: dict[str, Any], *, now: datetime
) -> dict[str, str]:
    bootstrap = store.bootstrap_state().get("state")
    pipeline_runs = store.pipeline_runs(limit=30)
    latest_pipeline = pipeline_runs[0] if pipeline_runs else None
    runs = [
        run for run in store.runs(limit=30) if run.get("kind") in {"daily", "weekly"}
    ]
    latest = runs[0] if runs else None
    if bootstrap != "completed":
        return {
            "tone": "attention",
            "label": "Setup needs attention",
            "detail": "Bootstrap is not complete.",
        }
    if not schedule.get("installed"):
        return {
            "tone": "attention",
            "label": "Schedule needs attention",
            "detail": "The daily task is not installed.",
        }
    if latest_pipeline and latest_pipeline.get("status") == "failed":
        stage = str(latest_pipeline.get("stage") or "unknown stage").replace("_", " ")
        error = sanitize_text(str(latest_pipeline.get("error") or ""), max_chars=180)
        detail = f"Failed during {stage}. Evidence is preserved for retry."
        if error:
            detail += f" {error}"
        return {"tone": "danger", "label": "Last daily run failed", "detail": detail}
    schedule_result = int(schedule.get("last_result") or 0)
    scheduled_at = _parse_datetime(schedule.get("last_run"))
    successful_at = _parse_datetime(
        (latest_pipeline or {}).get("completed_at")
        if latest_pipeline and latest_pipeline.get("status") == "completed"
        else (latest or {}).get("completed_at")
    )
    if schedule_result != 0 and (
        scheduled_at is None or successful_at is None or successful_at < scheduled_at
    ):
        return {
            "tone": "danger",
            "label": "Last scheduled run failed",
            "detail": f"Scheduler exit code {schedule_result}. Evidence is preserved for retry.",
        }
    if latest and latest.get("status") == "failed":
        return {
            "tone": "danger",
            "label": "Last run failed safely",
            "detail": "Evidence is preserved for retry.",
        }
    if latest is None:
        return {
            "tone": "ready",
            "label": "Ready for the first daily run",
            "detail": "The system is installed and waiting for its first real daily briefing.",
        }
    completed = _parse_datetime(latest.get("completed_at") or latest.get("started_at"))
    if completed and now.astimezone(UTC) - completed > timedelta(hours=48):
        return {
            "tone": "attention",
            "label": "A refresh is due",
            "detail": "The latest successful update is more than two days old.",
        }
    return {
        "tone": "good",
        "label": "Brain is up to date",
        "detail": "Recent evidence has been processed successfully.",
    }


def build_snapshot(
    paths: RuntimePaths,
    vault: Path,
    *,
    now: datetime | None = None,
    schedule: dict[str, Any] | None = None,
    codex_usage_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = now or datetime.now().astimezone()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    store = StateStore(paths.state)
    defaults = load_defaults()
    runtime_config = load_runtime_config(paths)
    if schedule is None and codex_usage_snapshot is None:
        with ThreadPoolExecutor(max_workers=2) as executor:
            schedule_future = executor.submit(
                task_details, str(runtime_config["task_name"])
            )
            codex_usage_future = executor.submit(read_codex_rate_limits, paths)
            schedule = schedule_future.result()
            codex_usage_snapshot = codex_usage_future.result()
    else:
        schedule = (
            schedule
            if schedule is not None
            else task_details(str(runtime_config["task_name"]))
        )
        codex_usage_snapshot = (
            codex_usage_snapshot
            if codex_usage_snapshot is not None
            else read_codex_rate_limits(paths)
        )

    daily_path = _latest_note(vault / "Journal" / "Daily", "20??-??-??.md")
    weekly_path = _latest_note(vault / "Journal" / "Weekly", "*.md")
    daily_history = _summary_history(vault, "daily", store)
    weekly_history = _summary_history(vault, "weekly", store)
    for entry in (*daily_history, *weekly_history):
        entry["usage"]["codex_impact"] = _summary_codex_impact(
            entry["usage"], codex_usage_snapshot
        )
    empty_summary = {
        "available": False,
        "summary": "",
        "highlights": [],
        "sections": [],
        "url": "",
    }
    daily = daily_history[0] if daily_history else dict(empty_summary)
    weekly = weekly_history[0] if weekly_history else dict(empty_summary)
    activity, trend, _historical_sessions = _recent_activity(store, vault, now=now)
    session_coverage = store.session_coverage()
    total_sessions = int(session_coverage["total"])
    pending = store.observations("pending")
    reviews = review_summary(pending)
    runs = _group_runs(store)
    status = _system_status(store, schedule, now=now)

    runtime_projects = store.present_projects()
    catalog_projects, _catalog_collections, _catalog_folders = catalog_groups(
        runtime_projects
    )
    catalog_health = project_catalog_health(vault, runtime_projects)
    present_projects = len(catalog_projects)
    first_party_projects = len(
        [
            project
            for project in catalog_projects
            if project.get("classification") == "first-party"
        ]
    )

    latest_review = _latest_note(vault / "Inbox" / "Review", "Review-*.md")
    dashboard_links = {
        "home": obsidian_uri(vault, vault / "Home.md"),
        "projects": obsidian_uri(vault, vault / "Projects" / "Index.md"),
        "skills": obsidian_uri(vault, vault / "Skills" / "Index.md"),
        "memory": obsidian_uri(vault, vault / "Memory" / "LongTermMemory.md"),
        "goals": obsidian_uri(vault, vault / "Goals" / "ActiveGoals.md"),
        "review": obsidian_uri(
            vault, latest_review or vault / "Inbox" / "Review" / "Index.md"
        ),
        "daily": obsidian_uri(
            vault, daily_path or vault / "Journal" / "Daily" / "Index.md"
        ),
        "weekly": obsidian_uri(
            vault, weekly_path or vault / "Journal" / "Weekly" / "Index.md"
        ),
        "roadmap": obsidian_uri(vault, vault / "System" / "Roadmap.md"),
    }

    cached_search = _cached_search_health(paths, store)
    git = _git_summary(vault)
    schedule_state = str(schedule.get("state") or "unknown")
    scheduler_failed = (
        status.get("tone") == "danger"
        and "run failed" in str(status.get("label", "")).casefold()
    )
    scheduler_good = (
        bool(schedule.get("installed"))
        and schedule_state.casefold() in {"ready", "running"}
        and not scheduler_failed
    )
    scheduler_detail = (
        "Installed; first run pending"
        if not schedule.get("last_run") and scheduler_good
        else schedule_state.title()
    )
    if scheduler_failed:
        scheduler_detail = status["detail"]
    checks = [
        {"label": "Vault", "state": "good", "detail": "Canonical notes available"},
        {
            "label": "Scheduler",
            "state": "good" if scheduler_good else "attention",
            "detail": scheduler_detail,
        },
        {"label": "Search", **cached_search},
        {"label": "Private Git", **git},
        {
            "label": "Evidence queue",
            "state": "neutral" if store.evidence_count(status="new") else "good",
            "detail": f"{store.evidence_count(status='new')} waiting for the next run"
            if store.evidence_count(status="new")
            else "Nothing waiting",
        },
        {
            "label": "Session attribution",
            "state": "attention" if session_coverage["unattributed"] else "good",
            "detail": (
                f"{session_coverage['attributed']} of {session_coverage['total']} sessions linked to projects; "
                f"{session_coverage['unattributed']} still need context"
                if session_coverage["total"]
                else "No session evidence collected yet"
            ),
        },
        {
            "label": "Project catalog",
            "state": "good" if catalog_health["ok"] else "attention",
            "detail": (
                f"{catalog_health['projects']} projects; "
                f"{catalog_health['collections']} collections kept separate"
                if catalog_health["ok"]
                else str(catalog_health["reason"])
            ),
        },
    ]

    display_name = vault.name.removesuffix(" Second Brain").strip() or "Second Brain"
    first_name = display_name.split()[0]
    schedule_defaults = defaults.get("schedule", {})
    return {
        "schema_version": 2,
        "generated_at": now.isoformat(),
        "display_name": display_name,
        "first_name": first_name,
        "status": status,
        "briefing": {
            "date": now.date().isoformat(),
            "daily": daily,
            "weekly": weekly,
            "pending_evidence": store.evidence_count(status="new"),
        },
        "summaries": {
            "daily": daily_history,
            "weekly": weekly_history,
            "counts": {"daily": len(daily_history), "weekly": len(weekly_history)},
            "codex_usage": codex_usage_snapshot,
        },
        "metrics": [
            {
                "label": "Projects mapped",
                "value": present_projects,
                "detail": f"{first_party_projects} first-party",
            },
            {
                "label": "Skills evidenced",
                "value": len(
                    [
                        path
                        for path in (vault / "Skills").glob("*.md")
                        if path.name != "Index.md"
                    ]
                ),
                "detail": "Canonical skill notes",
            },
            {
                "label": "Sessions understood",
                "value": total_sessions,
                "detail": (
                    f"{session_coverage['attributed']} linked to projects, "
                    f"{session_coverage['unattributed']} not yet linked"
                ),
            },
            {
                "label": "Knowledge promoted",
                "value": len(store.observations("promoted")),
                "detail": "Evidence-backed observations",
            },
        ],
        "activity": {"projects": activity, "trend": trend, "days": 7},
        "project_catalog": catalog_health,
        "session_coverage": session_coverage,
        "insights": _recent_insights(store, vault),
        "knowledge": _knowledge_deck(store, vault),
        "questions": _question_deck(store, vault),
        "patterns": _forming_patterns(store, vault),
        "review": {
            "pending": reviews["pending"],
            "questions": reviews["needs_answers"],
            "public": reviews["public_claims"],
            "private": reviews["private_review"],
            "groups": [
                {
                    "title": group["title"],
                    "count": group["count"],
                    "section": group["section"],
                }
                for group in reviews["groups"]
            ],
            "url": dashboard_links["review"],
        },
        "runs": runs,
        "schedule": {
            "daily_time": schedule_defaults.get("daily_time", "22:30"),
            "weekly_day": schedule_defaults.get("weekly_day", "Saturday"),
            "last_run": schedule.get("last_run"),
            "next_run": schedule.get("next_run"),
            "state": schedule_state,
            "missed_runs": int(schedule.get("missed_runs") or 0),
        },
        "health": {"checks": checks},
        "focus": _focus(vault),
        "links": dashboard_links,
        "actions": {"enabled": False, "csrf_token": ""},
        "privacy": "Canonical knowledge and safe operational summaries only. Raw evidence stays out of this view.",
    }


def render_dashboard(snapshot: dict[str, Any]) -> str:
    packaged_template = (
        Path(__file__).resolve().parent / "assets" / "dashboard" / "index.html"
    )
    source_template = protocol_root() / "assets" / "dashboard" / "index.html"
    template_path = (
        packaged_template if packaged_template.is_file() else source_template
    )
    template = template_path.read_text(encoding="utf-8")
    payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).replace(
        "</", "<\\/"
    )
    if template.count("__DASHBOARD_DATA__") != 1:
        raise RuntimeError(
            "Dashboard template must contain exactly one data placeholder."
        )
    return template.replace("__DASHBOARD_DATA__", payload)


def build_dashboard(paths: RuntimePaths, vault: Path) -> Path:
    paths.dashboard.mkdir(parents=True, exist_ok=True)
    rendered = render_dashboard(build_snapshot(paths, vault))
    destination = paths.dashboard / "index.html"
    temporary = destination.with_suffix(".html.tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(destination)
    return destination


def open_dashboard(paths: RuntimePaths, vault: Path) -> str:
    from .dashboard_server import ensure_dashboard_server

    url = ensure_dashboard_server(paths)
    webbrowser.open(url)
    return url


def install_dashboard_shortcut(paths: RuntimePaths, vault: Path) -> Path:
    if os.name != "nt":
        raise RuntimeError(
            "Desktop shortcut installation is currently supported on Windows only."
        )
    _ = paths
    owner = vault.name.removesuffix(" Second Brain").strip()
    words = re.findall(r"[A-Za-z0-9]+", owner)
    initials = "".join(word[0] for word in words[:2]).upper() or "SB"
    shortcut_name = f"{initials} Second Brain"
    installer = protocol_root() / "scripts" / "install-dashboard-launcher.ps1"
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(installer),
            "-VaultRoot",
            str(vault),
            "-LauncherName",
            shortcut_name,
            "-Initials",
            initials,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr or result.stdout or "Dashboard shortcut installation failed"
        )
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        shortcut = Path(payload["StartMenuShortcut"])
    except (IndexError, KeyError, json.JSONDecodeError) as error:
        raise RuntimeError(
            "Dashboard shortcut installer returned an invalid receipt"
        ) from error
    if not shortcut.is_file():
        raise RuntimeError("Dashboard shortcut was not created.")
    return shortcut
