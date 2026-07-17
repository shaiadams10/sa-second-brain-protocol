from __future__ import annotations

import json
import os
import re
import subprocess
import webbrowser
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import RuntimePaths, load_defaults, load_runtime_config, protocol_root
from .markdown import slugify
from .notifications import obsidian_uri
from .review import review_summary
from .scheduler import task_details
from .security import sanitize_text
from .state import StateStore


VISIBLE_PROJECT_CLASSES = {"first-party", "fork", "modified-fork", "experiment", "review"}
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
    text = re.sub(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", lambda m: m.group(2) or m.group(1), text)
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    text = re.sub(r"\^(?:obs|ev)-[a-zA-Z0-9-]+", "", text)
    text = re.sub(r"[`*_>#]", "", text)
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
    candidates = [path for path in folder.glob(pattern) if path.name.casefold() != "index.md"]
    return max(candidates, key=lambda path: path.stem) if candidates else None


def _note_brief(path: Path | None, section: str) -> dict[str, Any]:
    if path is None:
        return {"available": False, "summary": "", "highlights": []}
    raw = _generated_section(path, section)
    if not raw or "no automated run yet" in raw.casefold():
        return {"available": False, "summary": "", "highlights": []}
    lines = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("<!--"):
            continue
        if stripped.startswith(("- ", "* ", "+ ")):
            item = _clean_markdown(stripped[2:], max_chars=180)
            if item:
                lines.append(item)
    summary = _clean_markdown(raw, max_chars=620)
    return {"available": bool(summary), "summary": summary, "highlights": lines[:4]}


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


def knowledge_layer_for(observation: dict[str, Any]) -> str:
    """Place promoted knowledge in one stable human-facing layer."""

    declared = str((observation.get("payload") or {}).get("knowledge_layer") or "")
    if declared in KNOWLEDGE_LAYER_ORDER:
        return declared
    kind = str(observation.get("kind") or "")
    for layer, kinds in KNOWLEDGE_LAYER_KINDS.items():
        if kind in kinds:
            return layer
    return "project_knowledge"


def _knowledge_deck(store: StateStore, vault: Path) -> dict[str, Any]:
    feedback = store.knowledge_feedback()
    rows = sorted(store.observations("promoted"), key=lambda item: item["updated_at"], reverse=True)
    cards = []
    for item in rows:
        if item.get("sensitivity") == "sensitive":
            continue
        claim = _clean_markdown(str(item.get("claim") or ""), max_chars=420)
        if not claim:
            continue
        decision = feedback.get(item["id"], {}).get("decision")
        if decision == "disliked":
            continue
        layer = knowledge_layer_for(item)
        cards.append(
            {
                "id": item["id"],
                "layer": layer,
                "kind": KIND_LABELS.get(item["kind"], item["kind"].replace("_", " ").title()),
                "subject": _clean_markdown(str(item.get("subject") or "Knowledge"), max_chars=96),
                "claim": claim,
                "confidence": round(float(item.get("confidence", 0)) * 100),
                "source_count": int(item.get("source_count") or 0),
                "project_count": int(item.get("project_count") or 0),
                "updated_at": item.get("updated_at"),
                "feedback": decision,
                "url": obsidian_uri(vault, _observation_note(vault, item["kind"])),
            }
        )
    confirmed = sum(1 for card in cards if card["feedback"] == "liked")
    removed = sum(1 for item in feedback.values() if item.get("decision") == "disliked")
    display_name = vault.name.removesuffix(" Second Brain").strip() or "Second Brain"
    first_name = display_name.split()[0]
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
            "description": "Agent behavior, preferred formats, validation rules, design taste, and reusable protocols.",
        },
        "project_knowledge": {
            "label": "Project knowledge",
            "short_label": "Projects",
            "description": "Project-specific facts, architecture, decisions, implementations, and lessons.",
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
        "default_layer": "about_shai",
        "layers": layers,
        "counts": {
            "new": len(cards) - confirmed,
            "all": len(cards),
            "confirmed": confirmed,
            "removed": removed,
        },
        "undo_available": store.last_knowledge_dislike() is not None,
    }


def _recent_activity(
    store: StateStore, vault: Path, *, now: datetime, days: int = 7
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    cutoff = now.astimezone(UTC) - timedelta(days=days)
    projects = {item["id"]: item for item in store.projects()}
    presence: dict[str, bool] = {}
    with store.connect() as connection:
        for row in connection.execute("SELECT project_id,present FROM project_presence").fetchall():
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
            if not project_id.startswith("project-") or not project or not presence.get(project_id, True):
                continue
            if project.get("classification") not in VISIBLE_PROJECT_CLASSES:
                continue
            sessions_by_project[project_id].add(key)
            last_activity[project_id] = max(last_activity.get(project_id, occurred), occurred)

    changes: dict[str, int] = defaultdict(int)
    for row in delta_rows:
        project_id = str(row["project_id"] or "")
        project = projects.get(project_id)
        if not project_id.startswith("project-") or not project or not presence.get(project_id, True):
            continue
        if project.get("classification") not in VISIBLE_PROJECT_CLASSES:
            continue
        changes[project_id] += 1
        occurred = _parse_datetime(row["activity_at"])
        if occurred:
            last_activity[project_id] = max(last_activity.get(project_id, occurred), occurred)

    activity = []
    active_ids = set(sessions_by_project) | set(changes)
    for project_id in active_ids:
        project = projects[project_id]
        sessions = len(sessions_by_project.get(project_id, set()))
        change_count = changes.get(project_id, 0)
        activity.append(
            {
                "name": _clean_markdown(str(project.get("name") or "Untitled project"), max_chars=80),
                "classification": str(project.get("classification") or "review").replace("-", " "),
                "sessions": sessions,
                "changes": change_count,
                "score": sessions * 3 + change_count,
                "last_activity": last_activity.get(project_id).isoformat() if project_id in last_activity else None,
                "url": obsidian_uri(vault, _project_note(vault, str(project.get("name") or ""))),
            }
        )
    activity.sort(key=lambda item: (item["score"], item["last_activity"] or ""), reverse=True)
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
    rows = sorted(store.observations("promoted"), key=lambda item: item["updated_at"], reverse=True)
    insights = []
    for item in rows:
        if item["kind"] not in INSIGHT_KINDS or item.get("sensitivity") == "sensitive":
            continue
        claim = _clean_markdown(str(item.get("claim") or ""), max_chars=240)
        if not claim:
            continue
        insights.append(
            {
                "kind": KIND_LABELS.get(item["kind"], item["kind"].replace("_", " ").title()),
                "subject": _clean_markdown(str(item.get("subject") or "New insight"), max_chars=80),
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
            (
                min(sessions / 3, 1)
                + min(dates / 2, 1)
                + min(projects / 2, 1)
            )
            / 3
            * 100
        )
        patterns.append(
            {
                "label": _clean_markdown(str(item.get("label") or "Emerging pattern"), max_chars=80),
                "claim": _clean_markdown(str(item.get("claim") or ""), max_chars=190),
                "sessions": sessions,
                "dates": dates,
                "projects": projects,
                "progress": progress,
                "last_seen": item.get("last_seen"),
                "url": obsidian_uri(vault, vault / "Memory" / "Patterns.md"),
            }
        )
    patterns.sort(key=lambda item: (item["progress"], item["last_seen"] or ""), reverse=True)
    return patterns[:4]


def _group_runs(store: StateStore, *, limit: int = 7) -> list[dict[str, Any]]:
    grouped: list[dict[str, Any]] = []
    for run in store.runs(limit=30):
        started = _parse_datetime(run.get("started_at"))
        completed = _parse_datetime(run.get("completed_at"))
        status = "completed" if run.get("status") == "validated" else str(run.get("status") or "unknown")
        candidate = {
            "kind": str(run.get("kind") or "operation").replace("-", " ").title(),
            "status": status,
            "started_at": run.get("started_at"),
            "completed_at": run.get("completed_at"),
            "model": run.get("model"),
            "reasoning": run.get("reasoning"),
            "evidence_count": int(run.get("evidence_count") or 0),
            "duration_seconds": max(0, round((completed - started).total_seconds())) if started and completed else None,
            "batch_count": 1,
        }
        previous = grouped[-1] if grouped else None
        previous_started = _parse_datetime(previous.get("started_at")) if previous else None
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
            if previous["duration_seconds"] is not None and candidate["duration_seconds"] is not None:
                previous["duration_seconds"] = max(previous["duration_seconds"], candidate["duration_seconds"])
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
        return {"state": "attention", "detail": f"{changes} local edit{'s' if changes != 1 else ''}"}
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
        "title": _clean_markdown(title_match.group(1), max_chars=80) if title_match else "Current direction",
        "summary": _clean_markdown(raw, max_chars=380),
        "url": obsidian_uri(vault, path),
    }


def _system_status(
    store: StateStore, schedule: dict[str, Any], *, now: datetime
) -> dict[str, str]:
    bootstrap = store.bootstrap_state().get("state")
    runs = [run for run in store.runs(limit=30) if run.get("kind") in {"daily", "weekly"}]
    latest = runs[0] if runs else None
    if bootstrap != "completed":
        return {"tone": "attention", "label": "Setup needs attention", "detail": "Bootstrap is not complete."}
    if not schedule.get("installed"):
        return {"tone": "attention", "label": "Schedule needs attention", "detail": "The daily task is not installed."}
    if latest and latest.get("status") == "failed":
        return {"tone": "danger", "label": "Last run failed safely", "detail": "Evidence is preserved for retry."}
    if latest is None:
        return {
            "tone": "ready",
            "label": "Ready for the first daily run",
            "detail": "The system is installed and waiting for its first real daily briefing.",
        }
    completed = _parse_datetime(latest.get("completed_at") or latest.get("started_at"))
    if completed and now.astimezone(UTC) - completed > timedelta(hours=48):
        return {"tone": "attention", "label": "A refresh is due", "detail": "The latest successful update is more than two days old."}
    return {"tone": "good", "label": "Brain is up to date", "detail": "Recent evidence has been processed successfully."}


def build_snapshot(
    paths: RuntimePaths,
    vault: Path,
    *,
    now: datetime | None = None,
    schedule: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = now or datetime.now().astimezone()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    store = StateStore(paths.state)
    defaults = load_defaults()
    runtime_config = load_runtime_config(paths)
    schedule = schedule if schedule is not None else task_details(str(runtime_config["task_name"]))

    daily_path = _latest_note(vault / "Journal" / "Daily", "20??-??-??.md")
    weekly_path = _latest_note(vault / "Journal" / "Weekly", "*.md")
    daily = _note_brief(daily_path, "daily")
    weekly = _note_brief(weekly_path, "weekly")
    activity, trend, total_sessions = _recent_activity(store, vault, now=now)
    pending = store.observations("pending")
    reviews = review_summary(pending)
    runs = _group_runs(store)
    status = _system_status(store, schedule, now=now)

    present_projects = 0
    first_party_projects = 0
    for project in store.projects():
        presence = store.project_presence(project["id"])
        if presence and not bool(presence["present"]):
            continue
        present_projects += 1
        if project.get("classification") == "first-party":
            first_party_projects += 1

    latest_review = _latest_note(vault / "Inbox" / "Review", "Review-*.md")
    dashboard_links = {
        "home": obsidian_uri(vault, vault / "Home.md"),
        "projects": obsidian_uri(vault, vault / "Projects" / "Index.md"),
        "skills": obsidian_uri(vault, vault / "Skills" / "Index.md"),
        "memory": obsidian_uri(vault, vault / "Memory" / "LongTermMemory.md"),
        "goals": obsidian_uri(vault, vault / "Goals" / "ActiveGoals.md"),
        "review": obsidian_uri(vault, latest_review or vault / "Inbox" / "Review" / "Index.md"),
        "daily": obsidian_uri(vault, daily_path or vault / "Journal" / "Daily" / "Index.md"),
        "weekly": obsidian_uri(vault, weekly_path or vault / "Journal" / "Weekly" / "Index.md"),
        "roadmap": obsidian_uri(vault, vault / "System" / "Roadmap.md"),
    }

    cached_search = _cached_search_health(paths, store)
    git = _git_summary(vault)
    schedule_state = str(schedule.get("state") or "unknown")
    scheduler_good = bool(schedule.get("installed")) and schedule_state.casefold() in {"ready", "running"}
    scheduler_detail = "Installed; first run pending" if not schedule.get("last_run") and scheduler_good else schedule_state.title()
    checks = [
        {"label": "Vault", "state": "good", "detail": "Canonical notes available"},
        {"label": "Scheduler", "state": "good" if scheduler_good else "attention", "detail": scheduler_detail},
        {"label": "Search", **cached_search},
        {"label": "Private Git", **git},
        {
            "label": "Evidence queue",
            "state": "neutral" if store.evidence_count(status="new") else "good",
            "detail": f"{store.evidence_count(status='new')} waiting for the next run"
            if store.evidence_count(status="new")
            else "Nothing waiting",
        },
    ]

    display_name = vault.name.removesuffix(" Second Brain").strip() or "Second Brain"
    first_name = display_name.split()[0]
    schedule_defaults = defaults.get("schedule", {})
    return {
        "schema_version": 1,
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
        "metrics": [
            {"label": "Projects mapped", "value": present_projects, "detail": f"{first_party_projects} first-party"},
            {
                "label": "Skills evidenced",
                "value": len([path for path in (vault / "Skills").glob("*.md") if path.name != "Index.md"]),
                "detail": "Canonical skill notes",
            },
            {"label": "Sessions understood", "value": total_sessions, "detail": "Deduplicated agent sessions"},
            {"label": "Knowledge promoted", "value": len(store.observations("promoted")), "detail": "Evidence-backed observations"},
        ],
        "activity": {"projects": activity, "trend": trend, "days": 7},
        "insights": _recent_insights(store, vault),
        "knowledge": _knowledge_deck(store, vault),
        "patterns": _forming_patterns(store, vault),
        "review": {
            "pending": reviews["pending"],
            "questions": reviews["needs_answers"],
            "public": reviews["public_claims"],
            "private": reviews["private_review"],
            "groups": [
                {"title": group["title"], "count": group["count"], "section": group["section"]}
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
    packaged_template = Path(__file__).resolve().parent / "assets" / "dashboard" / "index.html"
    source_template = protocol_root() / "assets" / "dashboard" / "index.html"
    template_path = packaged_template if packaged_template.is_file() else source_template
    template = template_path.read_text(encoding="utf-8")
    payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    if template.count("__DASHBOARD_DATA__") != 1:
        raise RuntimeError("Dashboard template must contain exactly one data placeholder.")
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
        raise RuntimeError("Desktop shortcut installation is currently supported on Windows only.")
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
        raise RuntimeError(result.stderr or result.stdout or "Dashboard shortcut installation failed")
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        shortcut = Path(payload["StartMenuShortcut"])
    except (IndexError, KeyError, json.JSONDecodeError) as error:
        raise RuntimeError("Dashboard shortcut installer returned an invalid receipt") from error
    if not shortcut.is_file():
        raise RuntimeError("Dashboard shortcut was not created.")
    return shortcut
