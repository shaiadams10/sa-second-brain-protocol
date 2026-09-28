"""One brain update: digest -> model -> knowledge.json -> Markdown -> git commit.

Nothing waits for approval. Every run is committed to the vault's git history,
and the owner removes anything they don't want from the dashboard.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path

from brain import prompts
from brain.activity import Activity
from brain.config import Config
from brain.digest import OUTSIDE, Digest, ProjectWeek, build, week_bounds, week_label
from brain.knowledge import Knowledge
from brain.llm import Model, find_agy, update
from brain.projects import Catalog, scan
from brain.render import render_all

MAX_CATCH_UP_WEEKS = 8
Log = Callable[[str], None]


class Busy(RuntimeError):
    pass


class RunLock:
    def __init__(self, path: Path, stale_after: float = 3 * 3600) -> None:
        self.path, self.stale_after = path, stale_after

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and time.time() - self.path.stat().st_mtime < self.stale_after:
            raise Busy("Another brain update is already running.")
        self.path.write_text(str(os.getpid()), encoding="utf-8")
        return self

    def __exit__(self, *exc) -> None:
        self.path.unlink(missing_ok=True)


def knowledge_for(cfg: Config) -> Knowledge:
    return Knowledge(cfg.vault / "brain" / "knowledge.json")


def pending_weeks(knowledge: Knowledge, today: date | None = None) -> list[str]:
    """Completed weeks that have no finished log yet, oldest first."""
    today = today or date.today()
    done = {w for w, e in knowledge.weeks.items() if not e.get("partial") and e.get("ran_at")}
    last_complete = today - timedelta(days=today.isoweekday())  # the Sunday that ended last week
    weeks = []
    for back in range(MAX_CATCH_UP_WEEKS):
        label = week_label(last_complete - timedelta(weeks=back))
        if label in done:
            break
        weeks.append(label)
    if not done:
        weeks = weeks[:1]  # first run: last week only; history comes from the backfill
    return sorted(weeks)


# ----- model input

def _week_file(owner: str, pw: ProjectWeek, digest: Digest) -> str:
    who = owner.upper()
    lines = [f"# {pw.name}: week {digest.week}", ""]
    if pw.group:
        lines.append(f"Folder group: {pw.group}")
    lines.append(f"Conversations: {', '.join(f'{t} {n}' for t, n in pw.sessions.items())}; "
                 f"{pw.exchange_count} exchanges in total, {len(pw.exchanges)} shown below; "
                 f"active on {len(pw.active_days)} days; {pw.commits} commits.")
    lines.append("")
    for n, e in enumerate(pw.exchanges, 1):
        meta = [e.at.replace("T", " "), e.tool]
        if e.sub:
            meta.append(f"sub-project: {e.sub}")
        if e.signal:
            meta.append(f"signal: {e.signal}")
        lines += [f"## Exchange {n} · {' · '.join(meta)}", f"{who}: {e.user}"]
        if e.reply:
            lines.append(f"ASSISTANT (final reply): {e.reply}")
        lines.append("")
    return "\n".join(lines)


def _context_file(owner: str, knowledge: Knowledge, pid: str, subprojects: list[str]) -> str:
    project = knowledge.projects.get(pid, {})
    lines = ["# What the brain already knows", "", "## This folder", ""]
    lines.append(f"Existing blurb: {project.get('blurb') or '(none yet)'}")
    if subprojects:
        lines.append(f"Sub-projects: {', '.join(subprojects)}")
    for note in project.get("notes", [])[-40:]:
        lines.append(f"- [{note['kind']}] {note['text']}")
    lines += ["", f"## About {owner} (use these ids in `reinforces`)", ""]
    known = [(i, it) for i, it in knowledge.items.items() if it["status"] in ("active", "candidate")]
    known.sort(key=lambda kv: (kv[1]["status"] != "active", -len(kv[1]["evidence"])))
    for item_id, item in known[:80]:
        lines.append(f"- {item_id} [{item['kind']}] {item['text']}")
    if not known:
        lines.append("(nothing yet)")
    removed = [it["text"] for it in knowledge.items.values() if it["status"] == "removed"]
    removed += [n["text"] for n in project.get("removed_notes", [])]
    if removed:
        lines += ["", f"## REMOVED by {owner}: never propose these or anything similar", ""]
        lines += [f"- {text}" for text in removed[-80:]]
    return "\n".join(lines)


def _synthesis_files(knowledge: Knowledge, week: str, new_ids: list[str]) -> tuple[dict, dict[str, str]]:
    entry = knowledge.weeks[week]["projects"]
    rows = sorted(entry.items(), key=lambda kv: kv[1].get("attention", 0), reverse=True)
    projects = ["# The week, project by project", ""]
    for pid, p in rows:
        name = knowledge.projects.get(pid, {}).get("name", pid)
        summary = p.get("summary") or "(files changed, no conversations)"
        projects.append(f"- {name} (attention {p.get('attention', 0)}): {summary}")
    alias = {f"new-{n}": item_id for n, item_id in enumerate(new_ids, 1)}
    candidates = ["# Observations", "", "## Proposed this week", ""]
    candidates += [f"- {a} [{knowledge.items[i]['kind']}] {knowledge.items[i]['text']}" for a, i in alias.items()]
    candidates += ["", "## Already known", ""]
    known = [(i, it) for i, it in knowledge.items.items()
             if it["status"] in ("active", "candidate") and i not in new_ids]
    candidates += [f"- {i} [{it['kind']}] {it['text']}" for i, it in known[:120]] or ["(none)"]
    return {"projects.md": "\n".join(projects), "candidates.md": "\n".join(candidates)}, alias


# ----- the run

def run_week(cfg: Config, week: str, model: Model, catalog: Catalog, activity: Activity,
             partial: bool, log: Log) -> dict:
    decisions = cfg.load_decisions()
    digest = build(cfg, week, catalog, activity)
    if not digest.projects:
        log(f"{week}: no activity, nothing to log")
        return {"projects": 0, "model_calls": 0, "new_observations": 0, "skipped": True}
    knowledge = knowledge_for(cfg)
    knowledge.begin_week(week)

    for pw in digest.projects:
        subprojects = catalog.projects[pw.id].subprojects if pw.id in catalog.projects else []
        name = "Outside the projects folder" if pw.id == OUTSIDE else pw.name
        knowledge.record_project(week, pw.id, name, pw.group, pw.kind, subprojects, {
            "attention": pw.attention, "active_days": pw.active_days,
            "conversations": sum(pw.sessions.values()), "exchanges": pw.exchange_count,
            "commits": pw.commits, "files": pw.files_changed,
        })

    new_ids: list[str] = []
    talked = [pw for pw in digest.projects if pw.exchanges]
    for n, pw in enumerate(talked, 1):
        log(f"{week}: reading {pw.name} ({n}/{len(talked)}, {pw.exchange_count} exchanges)")
        subprojects = catalog.projects[pw.id].subprojects if pw.id in catalog.projects else []
        result = model.ask(
            prompts.PROJECT_WEEK_TASK.format(owner=cfg.owner),
            {"week.md": _week_file(cfg.owner, pw, digest),
             "context.md": _context_file(cfg.owner, knowledge, pw.id, subprojects)},
            prompts.PROJECT_WEEK_SCHEMA,
        )
        new_ids += knowledge.apply_project_result(week, pw.id, result)
        knowledge.save()

    log(f"{week}: writing the weekly summary")
    files, alias = _synthesis_files(knowledge, week, new_ids)
    synthesis = model.ask(prompts.WEEK_SYNTHESIS_TASK.format(owner=cfg.owner), files,
                          prompts.WEEK_SYNTHESIS_SCHEMA)
    knowledge.apply_synthesis(week, synthesis, model.name, partial, alias)
    knowledge.save()
    render_all(cfg.vault, knowledge, decisions, catalog, cfg.owner)
    return {"projects": len(digest.projects), "model_calls": len(talked) + 1, "new_observations": len(new_ids)}


def first_activity_week(cfg: Config) -> str | None:
    """The week of the oldest conversation file any source holds."""
    from brain.sources import PARSERS
    oldest = None
    for name, root in cfg.sources.items():
        if not root.exists():
            continue
        for path in PARSERS[name].session_files(root, datetime(2000, 1, 1).astimezone()):
            mtime = path.stat().st_mtime
            oldest = mtime if oldest is None or mtime < oldest else oldest
    return week_label(date.fromtimestamp(oldest)) if oldest else None


def backfill_weeks(cfg: Config, model_name: str | None) -> list[str]:
    """Every finished week from the first conversation on, oldest first, skipping weeks a
    previous backfill with the same model already finished (so it can resume)."""
    first = first_activity_week(cfg)
    if not first:
        return []
    knowledge = knowledge_for(cfg)
    done = {w for w, e in knowledge.weeks.items()
            if e.get("ran_at") and not e.get("partial") and e.get("model") == model_name}
    last = week_label(date.today() - timedelta(days=date.today().isoweekday()))
    weeks, cursor = [], week_bounds(first)[0].date()
    while week_label(cursor) <= last:
        if week_label(cursor) not in done:
            weeks.append(week_label(cursor))
        cursor += timedelta(weeks=1)
    return weeks


def run(cfg: Config, weeks: list[str] | None = None, current: bool = False, log: Log = print,
        model_name: str | None = None, backfill: bool = False) -> list[dict]:
    """Update the brain. With no weeks given, catch up every completed week not yet logged.
    current=True also (re)writes the week in progress. backfill=True walks all history."""
    results = []
    with RunLock(cfg.work_dir / "run.lock", stale_after=24 * 3600 if backfill else 3 * 3600):
        knowledge = knowledge_for(cfg)
        todo = weeks or (backfill_weeks(cfg, model_name) if backfill else pending_weeks(knowledge))
        this_week = week_label(date.today())
        if current and this_week not in todo:
            todo.append(this_week)
        if not todo:
            log("Nothing to do: every finished week is already in the brain.")
            return results

        agy = find_agy()
        log("Updating the Antigravity CLI")
        update(agy)
        model = Model(cfg.work_dir / "agy", agy=agy, name=model_name)
        log(f"Using {model.name}")
        decisions = cfg.load_decisions()
        catalog = scan(cfg.projects_root, decisions.get("folders", {}), cfg.places)
        activity = Activity(cfg.git_authors)

        for week in todo:
            started = time.time()
            record = {"week": week, "started": datetime.now().astimezone().isoformat(timespec="seconds"),
                      "model": model.name, "partial": week == this_week}
            try:
                record.update(run_week(cfg, week, model, catalog, activity, week == this_week, log))
                record["status"] = "ok"
            except Exception as exc:  # noqa: BLE001 - recorded for the dashboard, then re-raised
                record.update(status="failed", error=str(exc)[:1000])
                raise
            finally:
                record["seconds"] = round(time.time() - started)
                record["usage"] = dict(model.usage)
                _append_run_log(cfg, record)
                results.append(record)
            if not record.get("skipped"):
                commit(cfg, f"Brain update {week}{' (in progress)' if week == this_week else ''}"
                            f"{' (backfill)' if backfill else ''}")
            log(f"{week}: done in {record['seconds']}s")
    return results


def _append_run_log(cfg: Config, record: dict) -> None:
    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    with (cfg.work_dir / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_run_log(cfg: Config, limit: int = 30) -> list[dict]:
    path = cfg.work_dir / "runs.jsonl"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    return [json.loads(line) for line in reversed(lines) if line.strip()]


# Only the brain's own files; `projects/*.md` does not reach into subfolders.
GENERATED_PATHS = ["log", "me/learned.md", "brain/knowledge.json", "brain/projects.json", ":(glob)projects/*.md"]


def commit(cfg: Config, message: str) -> None:
    """Commit only the brain's own files, leaving anything else the owner has staged alone."""
    if not cfg.auto_commit or not (cfg.vault / ".git").exists():
        return
    git = ["git", "-C", str(cfg.vault)]
    subprocess.run(git + ["add", "-A", "--", *GENERATED_PATHS], capture_output=True)
    changed = subprocess.run(git + ["diff", "--cached", "--quiet", "--", *GENERATED_PATHS], capture_output=True)
    if changed.returncode == 1:
        subprocess.run(git + ["commit", "-q", "-m", message, "--", *GENERATED_PATHS], capture_output=True)
