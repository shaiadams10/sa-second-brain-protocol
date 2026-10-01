"""Render knowledge.json into readable Markdown in the vault.

Generated files start with GENERATED_MARK so the renderer knows it may overwrite
or delete them. Notes the owner writes by hand are never touched.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from brain.digest import week_bounds
from brain.knowledge import Knowledge, skill_standing
from brain.projects import Catalog

GENERATED_MARK = "<!-- Written by the second brain. Change it from the dashboard; edits here are overwritten. -->"
# Sections of me/learned.md. Skills and goals have pages of their own.
KIND_TITLES = [
    ("stated", "Stated by {owner}"),
    ("interest", "Interests"),
    ("preference", "Preferences"),
    ("dislike", "Dislikes"),
    ("work-style", "How {owner} works"),
    ("communication", "How {owner} communicates"),
    ("advice", "Advice {owner} took or turned down"),
]
SKILL_TITLES = [
    ("strong", "Strong", "Directing it confidently in two or more weeks or projects."),
    ("growing", "Growing", "Asked about it earlier, directing it now."),
    ("shown", "Shown once", "Directed confidently, so far in one week and one project."),
    ("learning", "Learning", "Still asking how it works, across two or more weeks."),
    ("struggled", "Struggled", "It kept failing the last time it came up."),
    ("asked", "Asked about once", "A question in one week; it becomes Learning or Growing if it comes up again."),
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
    written.append(_write(vault / "me" / "skills.md", render_skills(knowledge, owner)))
    written.append(_write(vault / "me" / "themes.md", render_themes(knowledge, states, owner)))
    written.append(_write(vault / "me" / "open-questions.md", render_open_questions(knowledge, owner)))
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

    skills: dict[str, list[str]] = {}
    for item in knowledge.items.values():
        if item["kind"] == "skill" and item["status"] != "removed":
            levels = {e.get("level") for e in item["evidence"] if e["week"] == week}
            for level in ("struggled", "learning", "directs"):
                if level in levels:
                    skills.setdefault(level, []).append(item["text"])
                    break
    if skills:
        lines += ["## Skills this week", ""]
        for level, label in (("directs", "Directed"), ("learning", "Asked about"), ("struggled", "Struggled with")):
            if skills.get(level):
                lines.append(f"- **{label}:** {', '.join(sorted(skills[level], key=str.lower))}")
        lines.append("")

    promoted = [i for i in knowledge.items.values() if i.get("promoted") == week and i["status"] == "active"
                and i["kind"] != "skill"]
    noticed = [i for i in knowledge.items.values() if i["status"] == "candidate" and i["kind"] != "skill"
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
             f"different projects or two different weeks; what {owner} states directly counts at once. "
             f"Skills are in `skills.md`, recurring areas in `themes.md`, and goals in `open-questions.md`. "
             "Hand-written notes in this folder take priority.", ""]
    active = [i for i in knowledge.items.values() if i["status"] == "active"]
    shown = False
    for kind, title in KIND_TITLES:
        items = sorted((i for i in active if i["kind"] == kind), key=lambda i: -len(i["evidence"]))
        if not items:
            continue
        shown = True
        lines += [f"## {title.format(owner=owner)}", ""]
        for item in items:
            verb = "said in" if kind in ("stated", "advice") else "seen in"
            lines.append(f"- {item['text']} *({verb} {_where(knowledge, item)})*")
        lines.append("")
    if not shown:
        lines += ["Nothing confirmed yet.", ""]
    return "\n".join(lines)


def _project_names(knowledge: Knowledge, pids) -> list[str]:
    return sorted({knowledge.projects.get(pid, {}).get("name", pid) for pid in pids}, key=str.lower)


def _where(knowledge: Knowledge, item: dict) -> str:
    projects = _project_names(knowledge, (e["project"] for e in item["evidence"]))
    weeks = sorted({e["week"] for e in item["evidence"]})
    return f"{', '.join(projects)}; {weeks[0]}{' to ' + weeks[-1] if len(weeks) > 1 else ''}"


def render_skills(knowledge: Knowledge, owner: str) -> str:
    lines = [f"# {owner}'s skills", "",
             f"Every week the brain notes which technologies and domains {owner} directed with confidence, "
             "asked about, or struggled with, judged from their own words. Standing comes from that history: "
             "what they stopped asking about and now direct is what they have learned.", ""]
    skills = [i for i in knowledge.items.values() if i["kind"] == "skill" and i["status"] != "removed"]
    if not skills:
        return "\n".join(lines + ["Nothing recorded yet.", ""])
    by_level: dict[str, list[tuple[dict, dict]]] = {}
    for item in skills:
        standing = skill_standing(item)
        level = standing["level"]
        if level == "learning" and len({e["week"] for e in item["evidence"]}) == 1:
            level = "asked"
        by_level.setdefault(level, []).append((item, standing))
    for level, title, meaning in SKILL_TITLES:
        rows = by_level.get(level)
        if not rows:
            continue
        rows.sort(key=lambda r: (-len({e["week"] for e in r[0]["evidence"]}), r[0]["text"].lower()))
        lines += [f"## {title}", "", f"*{meaning}*", ""]
        for item, standing in rows:
            lines.append(f"- **{item['text']}**: {standing['trend']} "
                         f"*({', '.join(_project_names(knowledge, standing['projects']))})*")
        lines.append("")
    return "\n".join(lines)


def render_themes(knowledge: Knowledge, states: dict, owner: str) -> str:
    lines = [f"# Themes in {owner}'s work", "",
             f"The areas {owner} keeps coming back to across projects, named once per run from the project "
             "catalog, the skill map, and what they have said about themself.", ""]
    names = {p["name"]: pid for pid, p in knowledge.projects.items()}

    def link(name: str) -> str:
        pid = names.get(name)
        return f"[{name}](../projects/{slug(pid)}.md)" if pid in states and has_page(states[pid]) else name

    themes = knowledge.themes.get("items", [])
    if themes:
        for theme in themes:
            lines += [f"## {theme['name']}", "", theme["summary"], "",
                      "Projects: " + ", ".join(link(n) for n in theme["projects"]), ""]
    else:
        lines += ["No themes named yet. They are written at the end of the next run.", ""]

    totals = []
    for pid in states:
        history = knowledge.attention(pid)
        if history:
            totals.append((sum(history.values()), len([a for a in history.values() if a > 0]),
                           knowledge.returns(pid), knowledge.projects[pid]["name"]))
    returns = sorted((t for t in totals if t[2]), key=lambda t: (-t[2], -t[0]))
    if returns:
        lines += ["## Came back to after a break", "",
                  "Projects picked up again after four or more weeks away.", ""]
        lines += [f"- {link(name)}: {n} time{'s' if n != 1 else ''} · {weeks} weeks in all"
                  for _, weeks, n, name in returns[:15]]
        lines.append("")
    if totals:
        lines += ["## Most attention overall", ""]
        lines += [f"- {link(name)}: attention {total:.0f} over {weeks} week{'s' if weeks != 1 else ''}"
                  for total, weeks, _, name in sorted(totals, reverse=True)[:12]]
        lines.append("")
    return "\n".join(lines)


def render_open_questions(knowledge: Knowledge, owner: str) -> str:
    lines = [f"# What {owner} is working out", "",
             f"Goals and decisions {owner} has described in conversations about themself, beyond any single "
             "task. A goal moves to Settled when a later conversation shows it reached or decided.", ""]
    goals = [i for i in knowledge.items.values() if i["kind"] == "goal" and i["status"] == "active"]
    open_goals = sorted((g for g in goals if g.get("open", True)), key=lambda g: g["created"], reverse=True)
    settled = sorted((g for g in goals if not g.get("open", True)), key=lambda g: g.get("resolved", ""), reverse=True)
    if not goals:
        return "\n".join(lines + ["Nothing recorded yet.", ""])
    if open_goals:
        lines += ["## Open", ""] + [f"- {g['text']} *(since {g['created']})*" for g in open_goals] + [""]
    if settled:
        lines += ["## Settled", ""]
        lines += [f"- {g['text']} *({g['created']} to {g.get('resolved', '?')})*" for g in settled] + [""]
    return "\n".join(lines)
