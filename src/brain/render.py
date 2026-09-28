"""Render knowledge.json into readable Markdown in the vault.

Generated files start with GENERATED_MARK so the renderer knows it may overwrite
or delete them. Notes the owner writes by hand are never touched.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from brain.digest import week_bounds
from brain.knowledge import Knowledge
from brain.projects import Catalog

GENERATED_MARK = "<!-- Written by the second brain. Change it from the dashboard; edits here are overwritten. -->"
KIND_TITLES = [
    ("preference", "Preferences"),
    ("dislike", "Dislikes"),
    ("work-style", "How {owner} works"),
    ("skill", "Skills shown in recent work"),
    ("interest", "Interests"),
]
NOTE_TITLES = [
    ("goal", "Goals"),
    ("decision", "Decisions"),
    ("milestone", "Milestones"),
    ("stack", "Stack"),
    ("preference", "Preferences in this project"),
    ("open-problem", "Open problems"),
]
STATE_TITLES = [("ongoing", "Ongoing"), ("exploring", "Exploring"), ("on-hold", "On hold"), ("earlier", "Earlier")]


def slug(pid: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", pid.lower()).strip("-") or "project"


def week_title(week: str) -> str:
    start, end = week_bounds(week)
    last = end.date().fromordinal(end.date().toordinal() - 1)
    span = f"{start:%b} {start.day} – {last:%b} {last.day}, {last.year}"
    return f"Week {int(week.split('-W')[1])} · {span}"


def _day(iso: str | None) -> str:
    if not iso:
        return "unknown"
    d = date.fromisoformat(iso[:10])
    return f"{d:%b} {d.day}, {d.year}"


def has_page(state: dict) -> bool:
    return state["marked"] or state["state"] in ("ongoing", "on-hold")


def render_all(vault: Path, knowledge: Knowledge, decisions: dict, catalog: Catalog, owner: str) -> list[Path]:
    written: list[Path] = []
    states = {pid: knowledge.status(pid, decisions) for pid, p in knowledge.projects.items()
              if p.get("kind") not in ("outside", "group")}
    for week in knowledge.weeks:
        written.append(_write(vault / "log" / f"{week}.md", render_week(week, knowledge, states, owner)))
    written.append(_write(vault / "projects" / "catalog.md", render_catalog(knowledge, states, catalog)))
    pages = {slug(pid) + ".md" for pid, s in states.items() if has_page(s)}
    for pid, state in states.items():
        if has_page(state):
            written.append(_write(vault / "projects" / f"{slug(pid)}.md",
                                  render_project(pid, knowledge, state)))
    for stale in (vault / "projects").glob("*.md"):
        if stale.name not in pages | {"catalog.md"} and _is_generated(stale):
            stale.unlink()
    written.append(_write(vault / "me" / "learned.md", render_learned(knowledge, owner)))
    return written


def _is_generated(path: Path) -> bool:
    try:
        return path.read_text(encoding="utf-8").startswith(GENERATED_MARK)
    except OSError:
        return False


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = GENERATED_MARK + "\n\n" + body.rstrip() + "\n"
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")
    return path


def render_week(week: str, knowledge: Knowledge, states: dict, owner: str) -> str:
    entry = knowledge.weeks[week]
    lines = [f"# {week_title(week)}", ""]
    if entry.get("partial"):
        lines += ["*Week in progress. This note is rewritten when the week ends.*", ""]
    if entry.get("headline"):
        lines += [f"**{entry['headline']}**", ""]
    if entry.get("summary"):
        lines += [entry["summary"], ""]
    if entry.get("highlights"):
        lines += ["## Highlights", ""] + [f"- {h}" for h in entry["highlights"]] + [""]

    projects = sorted(entry["projects"].items(), key=lambda kv: kv[1].get("attention", 0), reverse=True)
    main = [(pid, p) for pid, p in projects if p.get("summary")]
    quiet = [(pid, p) for pid, p in projects if not p.get("summary")]
    if main:
        lines += ["## Projects", ""]
        for pid, p in main:
            name = knowledge.projects.get(pid, {}).get("name", pid)
            link = f"[{name}](../projects/{slug(pid)}.md)" if pid in states and has_page(states[pid]) else name
            lines += [f"### {link}", "", p["summary"], "", f"*{_stats(p)}*", ""]
    if quiet:
        names = ", ".join(knowledge.projects.get(pid, {}).get("name", pid) for pid, _ in quiet)
        lines += ["## Also touched", "", f"Files changed without AI conversations: {names}.", ""]

    promoted = [i for i in knowledge.items.values() if i.get("promoted") == week and i["status"] == "active"]
    noticed = [i for i in knowledge.items.values() if i["status"] == "candidate"
               and any(e["week"] == week for e in i["evidence"])]
    if promoted or noticed:
        lines += [f"## Learned about {owner}", ""]
        lines += [f"- {i['text']}" for i in promoted]
        if noticed:
            lines += ["", "Noticed once, kept as a candidate until it shows up again:", ""]
            lines += [f"- {i['text']}" for i in noticed]
        lines.append("")
    return "\n".join(lines)


def _stats(p: dict) -> str:
    parts = [f"{len(p.get('active_days', []))} active days"]
    if p.get("conversations"):
        parts.append(f"{p['conversations']} conversations")
    if p.get("commits"):
        parts.append(f"{p['commits']} commits")
    if p.get("files"):
        parts.append(f"{p['files']} files changed")
    return " · ".join(parts)


def render_catalog(knowledge: Knowledge, states: dict, catalog: Catalog) -> str:
    lines = ["# Projects", "",
             f"Every folder in the projects directory. Updated {_day(date.today().isoformat())}.", ""]
    for state_key, title in STATE_TITLES:
        rows = [(pid, s) for pid, s in states.items() if s["state"] == state_key and s["last_week"]]
        if not rows:
            continue
        rows.sort(key=lambda r: r[1]["last_day"] or "", reverse=True)
        lines += [f"## {title}", ""]
        for pid, s in rows:
            project = knowledge.projects[pid]
            name = f"[{project['name']}]({slug(pid)}.md)" if has_page(s) else project["name"]
            where = f" · in {project['group']}" if project.get("group") else ""
            when = (f"on hold since {_day(s['last_day'])}" if state_key == "on-hold"
                    else f"last active {_day(s['last_day'])}")
            blurb = f": {project['blurb']}" if project.get("blurb") else ""
            lines.append(f"- **{name}**{blurb} · {when}{where}")
        lines.append("")

    quiet = sorted((p for p in catalog.projects.values()
                    if not states.get(p.id, {}).get("last_week")), key=lambda p: (p.group or "", p.name.lower()))
    if quiet:
        lines += ["## No recorded activity", ""]
        by_group: dict[str, list[str]] = {}
        for p in quiet:
            by_group.setdefault(p.group or "Top level", []).append(p.name)
        for group, names in by_group.items():
            lines.append(f"- **{group}:** {', '.join(names)}")
        lines.append("")
    return "\n".join(lines)


def render_project(pid: str, knowledge: Knowledge, state: dict) -> str:
    project = knowledge.projects[pid]
    status = {"ongoing": "Ongoing", "on-hold": f"On hold since {_day(state['last_day'])}",
              "exploring": "Exploring", "earlier": "Earlier"}[state["state"]]
    if state["marked"]:
        status += " · marked as a project"
    lines = [f"# {project['name']}", ""]
    if project.get("blurb"):
        lines += [project["blurb"], ""]
    lines += [f"**Status:** {status}. Active in {state['active_weeks']} "
              f"week{'s' if state['active_weeks'] != 1 else ''}; last active {_day(state['last_day'])}.", ""]
    if project.get("subprojects"):
        lines += ["**Sub-projects:** " + ", ".join(project["subprojects"]), ""]
    for kind, title in NOTE_TITLES:
        notes = [n for n in project.get("notes", []) if n["kind"] == kind]
        if notes:
            lines += [f"## {title}", ""] + [f"- {n['text']}" for n in notes] + [""]
    timeline = [(w, e["projects"][pid]) for w, e in sorted(knowledge.weeks.items(), reverse=True)
                if pid in e["projects"] and e["projects"][pid].get("summary")]
    if timeline:
        lines += ["## Timeline", ""]
        for week, p in timeline:
            lines += [f"**[{week_title(week)}](../log/{week}.md)**", "", p["summary"], ""]
    return "\n".join(lines)


def render_learned(knowledge: Knowledge, owner: str) -> str:
    lines = [f"# What the brain has learned about {owner}", "",
             "Learned from AI coding conversations. An observation is added once it shows up in two "
             "different projects or two different weeks. Hand-written notes in this folder take priority.", ""]
    active = [i for i in knowledge.items.values() if i["status"] == "active"]
    for kind, title in KIND_TITLES:
        items = sorted((i for i in active if i["kind"] == kind), key=lambda i: -len(i["evidence"]))
        if not items:
            continue
        lines += [f"## {title.format(owner=owner)}", ""]
        for item in items:
            projects = sorted({knowledge.projects.get(e["project"], {}).get("name", e["project"])
                               for e in item["evidence"]})
            weeks = sorted({e["week"] for e in item["evidence"]})
            lines.append(f"- {item['text']} *(seen in {', '.join(projects)}; {weeks[0]}"
                         f"{' to ' + weeks[-1] if len(weeks) > 1 else ''})*")
        lines.append("")
    if not active:
        lines += ["Nothing confirmed yet.", ""]
    return "\n".join(lines)
