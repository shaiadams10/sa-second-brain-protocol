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
from brain.knowledge import Knowledge, skill_standing
from brain.llm import Model, make_model
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


def _context_file(owner: str, knowledge: Knowledge, pid: str, subprojects: list[str], about: bool = False) -> str:
    project = knowledge.projects.get(pid, {})
    lines = ["# What the brain already knows", "", "## This folder", ""]
    lines.append(f"Existing blurb: {project.get('blurb') or '(none yet)'}")
    if subprojects:
        lines.append(f"Sub-projects: {', '.join(subprojects)}")
    for note in project.get("notes", [])[-40:]:
        lines.append(f"- [{note['kind']}] {note['text']}")
    lines += ["", f"## About {owner} (use these ids in `reinforces`)", ""]
    known = [(i, it) for i, it in knowledge.items.items()
             if it["status"] in ("active", "candidate") and it["kind"] != "skill"]
    known.sort(key=lambda kv: (kv[1]["status"] != "active", -len(kv[1]["evidence"])))
    for item_id, item in known[:80]:
        lines.append(f"- {item_id} [{item['kind']}] {item['text']}")
    if not known:
        lines.append("(nothing yet)")
    skills = [(i, it) for i, it in knowledge.items.items() if it["kind"] == "skill" and it["status"] != "removed"]
    if skills:
        lines += ["", "## Skills (reuse these topic names and ids)", ""]
        lines += [f"- {i} {it['text']}" for i, it in sorted(skills, key=lambda kv: -len(kv[1]["evidence"]))[:150]]
    if about:
        goals = [(i, it) for i, it in knowledge.items.items()
                 if it["kind"] == "goal" and it.get("open") and it["status"] == "active"]
        lines += ["", "## Open goals (ids for `resolved_goals`)", ""]
        lines += [f"- {i} {it['text']}" for i, it in goals] or ["(none)"]
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
    known.sort(key=lambda kv: kv[1]["kind"] == "skill")  # observations first, then skill topics
    candidates += [f"- {i} [{it['kind']}] {it['text']}" for i, it in known[:250]] or ["(none)"]
    return {"projects.md": "\n".join(projects), "candidates.md": "\n".join(candidates)}, alias


# ----- the run

def _ask_project(cfg: Config, model: Model, knowledge: Knowledge, catalog: Catalog,
                 pw: ProjectWeek, digest: Digest) -> dict:
    subprojects = catalog.projects[pw.id].subprojects if pw.id in catalog.projects else []
    about = pw.id in cfg.about_me
    task = prompts.PROJECT_WEEK_TASK + (prompts.ABOUT_ME_RULES if about else "")
    return model.ask(
        task.format(owner=cfg.owner),
        {"week.md": _week_file(cfg.owner, pw, digest),
         "context.md": _context_file(cfg.owner, knowledge, pw.id, subprojects, about)},
        prompts.ABOUT_ME_SCHEMA if about else prompts.PROJECT_WEEK_SCHEMA,
    )


def learn_week(cfg: Config, week: str, model: Model, catalog: Catalog, activity: Activity, log: Log) -> dict:
    """Fill in skills, communication, interests and what the owner stated for a week that is
    already logged, leaving its summaries, notes and other observations as they are."""
    knowledge = knowledge_for(cfg)
    if week not in knowledge.weeks:
        log(f"{week}: not logged yet, skipped (a normal run writes it)")
        return {"projects": 0, "model_calls": 0, "new_observations": 0, "skipped": True}
    digest = build(cfg, week, catalog, activity)
    knowledge.begin_learn(week)
    talked = [pw for pw in digest.projects if pw.exchanges and pw.id in knowledge.projects]
    new_ids: list[str] = []
    for n, pw in enumerate(talked, 1):
        log(f"{week}: learning from {pw.name} ({n}/{len(talked)}, {pw.exchange_count} exchanges)")
        result = _ask_project(cfg, model, knowledge, catalog, pw, digest)
        new_ids += knowledge.apply_project_result(week, pw.id, result, learn_only=True)
        knowledge.save()
    knowledge.promote()
    knowledge.save()
    render_all(cfg.vault, knowledge, cfg.load_decisions(), catalog, cfg.owner)
    return {"projects": len(talked), "model_calls": len(talked), "new_observations": len(new_ids)}


def refresh_themes(cfg: Config, model: Model, catalog: Catalog, log: Log) -> None:
    """One call that reads the whole catalog, skill map and stated interests, and names the
    areas the owner keeps coming back to."""
    knowledge = knowledge_for(cfg)
    decisions = cfg.load_decisions()
    rows = []
    for pid, project in knowledge.projects.items():
        if project.get("kind") in ("outside", "group"):
            continue
        history = knowledge.attention(pid)
        touched = sorted(w for w, a in history.items() if a > 0)
        if not touched:
            continue
        state = knowledge.status(pid, decisions)["state"]
        rows.append((sum(history.values()), f"- {project['name']}: {project.get('blurb') or '(no description)'} "
                     f"· attention {sum(history.values()):.0f} over {len(touched)} weeks, {touched[0]} to "
                     f"{touched[-1]} · came back {knowledge.returns(pid)} times after a break · {state}"))
    if len(rows) < 2:
        return
    skills = [f"- {it['text']}: {skill_standing(it)['level']} ({skill_standing(it)['trend']})"
              for it in knowledge.items.values() if it["kind"] == "skill" and it["status"] != "removed"]
    about = [f"- [{it['kind']}] {it['text']}" for it in knowledge.items.values()
             if it["status"] == "active" and it["kind"] in ("interest", "stated")]
    log("Naming the themes across projects")
    result = model.ask(prompts.THEMES_TASK.format(owner=cfg.owner), {
        "projects.md": "# Projects, most attention first\n\n" + "\n".join(r for _, r in sorted(rows, reverse=True)),
        "skills.md": "# Skills\n\n" + ("\n".join(skills) or "(none yet)"),
        "about.md": "# Interests and stated facts\n\n" + ("\n".join(about) or "(none yet)"),
    }, prompts.THEMES_SCHEMA)
    knowledge.set_themes(max(knowledge.weeks), result.get("themes", []))
    knowledge.save()
    render_all(cfg.vault, knowledge, decisions, catalog, cfg.owner)


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
        result = _ask_project(cfg, model, knowledge, catalog, pw, digest)
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
    done = {w for w, e in knowledge.weeks.items() if e.get("ran_at") and not e.get("partial")}
    last = week_label(date.today() - timedelta(days=date.today().isoweekday()))
    weeks, cursor = [], week_bounds(first)[0].date()
    while week_label(cursor) <= last:
        if week_label(cursor) not in done:
            weeks.append(week_label(cursor))
        cursor += timedelta(weeks=1)
    return weeks


def unlearned_weeks(knowledge: Knowledge) -> list[str]:
    """Logged weeks the learning pass has not read yet, oldest first (so it can resume)."""
    return sorted(w for w, e in knowledge.weeks.items() if e.get("ran_at") and not e.get("learned"))


def run(cfg: Config, weeks: list[str] | None = None, current: bool = False, log: Log = print,
        model_name: str | None = None, backfill: bool = False, cli: str | None = None,
        effort: str | None = None, learn: bool = False) -> list[dict]:
    """Update the brain. With no weeks given, catch up every completed week not yet logged.
    current=True also (re)writes the week in progress. backfill=True walks all history.
    learn=True reads already-logged weeks again for skills and what the owner stated, without
    rewriting their logs (every logged week not yet read this way, unless weeks are given).
    cli, model_name and effort override the vault's [model] settings for this run."""
    results = []
    with RunLock(cfg.work_dir / "run.lock", stale_after=24 * 3600 if backfill or learn else 3 * 3600):
        knowledge = knowledge_for(cfg)
        if learn:
            todo = weeks or unlearned_weeks(knowledge)
        else:
            todo = weeks or (backfill_weeks(cfg, model_name) if backfill else pending_weeks(knowledge))
        this_week = week_label(date.today())
        if current and this_week not in todo and not learn:
            todo.append(this_week)
        if not todo:
            log("Nothing to do: every finished week is already in the brain.")
            return results

        cli = cli or cfg.model_cli
        if cli == cfg.model_cli:  # the configured model and effort belong to the configured CLI
            model_name, effort = model_name or cfg.model_name, effort or cfg.model_effort
        model = make_model(cfg.work_dir, cli, model_name, effort, log)
        log(f"Using {model.cli} · {model.name}" + (f" · {model.effort} effort" if model.effort else ""))
        decisions = cfg.load_decisions()
        catalog = scan(cfg.projects_root, decisions.get("folders", {}), cfg.places)
        activity = Activity(cfg.git_authors)

        for week in todo:
            started = time.time()
            record = {"week": week, "started": datetime.now().astimezone().isoformat(timespec="seconds"),
                      "cli": model.cli, "model": model.name, "effort": model.effort, "partial": week == this_week}
            if learn:
                record["learn"] = True
            usage_before = dict(model.usage)
            try:
                if learn:
                    record.update(learn_week(cfg, week, model, catalog, activity, log))
                else:
                    record.update(run_week(cfg, week, model, catalog, activity, week == this_week, log))
                _mark_learned(cfg, week)
                record["status"] = "ok"
            except Exception as exc:  # noqa: BLE001 - recorded for the dashboard, then re-raised
                record.update(status="failed", error=str(exc)[:1000])
                raise
            finally:
                record["seconds"] = round(time.time() - started)
                record["usage"] = usage_since(usage_before, model.usage)
                _append_run_log(cfg, record)
                results.append(record)
            if not record.get("skipped"):
                label = "Brain learning pass" if learn else "Brain update"
                failed = commit(cfg, f"{label} {week}{' (in progress)' if week == this_week else ''}"
                                     f"{' (backfill)' if backfill else ''}")
                if failed:
                    log(f"{week}: WARNING, the vault commit failed: {failed}")
            log(f"{week}: done in {record['seconds']}s")

        if any(not r.get("skipped") for r in results):
            try:
                refresh_themes(cfg, model, catalog, log)
                failed = commit(cfg, "Brain themes")
                if failed:
                    log(f"WARNING, the vault commit failed: {failed}")
            except Exception as exc:  # noqa: BLE001 - the weeks are already saved; themes wait for the next run
                log(f"WARNING, the themes were not refreshed: {exc}")
    return results


def _mark_learned(cfg: Config, week: str) -> None:
    knowledge = knowledge_for(cfg)
    if week in knowledge.weeks:
        knowledge.weeks[week]["learned"] = True
        knowledge.save()


def usage_since(before: dict, now: dict) -> dict:
    """This week's share of the model's running usage totals (numbers only; flags are kept)."""
    return {k: v - before.get(k, 0) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
            for k, v in now.items()}


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
GENERATED_PATHS = ["log", "me/learned.md", "me/skills.md", "me/themes.md", "me/open-questions.md",
                   "brain/knowledge.json", "brain/projects.json", ":(glob)projects/*.md"]


def commit(cfg: Config, message: str) -> str | None:
    """Commit only the brain's own files, leaving anything else the owner has staged alone.
    Returns git's complaint if the commit failed (for example inside a sandbox that protects .git)."""
    if not cfg.auto_commit or not (cfg.vault / ".git").exists():
        return None
    git = ["git", "-C", str(cfg.vault)]
    added = subprocess.run(git + ["add", "-A", "--", *GENERATED_PATHS], capture_output=True, text=True)
    if added.returncode != 0:
        return (added.stderr or added.stdout).strip()[-500:] or "git add failed"
    changed = subprocess.run(git + ["diff", "--cached", "--quiet", "--", *GENERATED_PATHS], capture_output=True)
    if changed.returncode == 1:
        done = subprocess.run(git + ["commit", "-q", "-m", message, "--", *GENERATED_PATHS],
                              capture_output=True, text=True)
        if done.returncode != 0:
            return (done.stderr or done.stdout).strip()[-500:] or "git commit failed"
    return None
