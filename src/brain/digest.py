"""The weekly digest: everything the model needs about one week, built without a model.

For each project it records how much attention it got (conversations, active days,
file changes, commits) and a bounded selection of exchanges: the owner's message and
the assistant's final reply. Exchanges where the owner praised, corrected, or
interrupted the assistant are always kept, because that is where preferences show.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from brain.activity import Activity, in_range
from brain.config import Config
from brain.projects import Catalog, scan
from brain.sources import load_sources_for
from brain.text import clip, redact

OUTSIDE = "(outside projects)"
USER_LIMIT = 1200
REPLY_LIMIT = 900
SAMPLE_PER_PROJECT = 20
MAX_PER_PROJECT = 60

_PRAISE = re.compile(
    r"(?i)\b(gj|good job|great job|nice work|well done|perfect(ly)?|love (it|this|that)|amazing|awesome|"
    r"exactly what i (wanted|meant)|works (great|perfectly|now)|looks (great|amazing|good|perfect))\b"
)
# Pushback shows up at the start of a message; later in the text these words are usually instructions.
_CORRECTION = re.compile(
    r"(?i)(^\s*(no|nope|nah)\b|\bnot what i\b|\bthat'?s (not|wrong)\b|\bwrong\b|\bwhy did (you|u)\b|"
    r"\brevert\b|\bundo (that|this|it)\b|\bstill (broken|not|black|the same|doesn'?t|isn'?t)\b|"
    r"\b(doesn'?t|didn'?t|isn'?t|not) work(ing)?\b|\b(you|u) (made|broke|forgot|missed|ignored|didn'?t)\b|"
    r"\bi (already )?(said|asked|told)\b|\bi didn'?t ask\b|\bdisappointed\b|\bnot good\b)"
)
SIGNAL_WINDOW = 300


def week_bounds(label: str) -> tuple[datetime, datetime]:
    """'2026-W39' -> local Monday 00:00 through the next Monday 00:00."""
    year, week = label.split("-W")
    monday = date.fromisocalendar(int(year), int(week), 1)
    start = datetime(monday.year, monday.month, monday.day).astimezone()
    return start, start + timedelta(days=7)


def week_label(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def resolve_week(spec: str) -> str:
    today = date.today()
    if spec == "this":
        return week_label(today)
    if spec == "last":
        return week_label(today - timedelta(days=7))
    return spec


def signal(text: str) -> str | None:
    if text == "(interrupted the assistant)":
        return "interrupt"
    head = text[:SIGNAL_WINDOW]
    if _CORRECTION.search(head):
        return "correction"
    if _PRAISE.search(head):
        return "praise"
    return None


@dataclass
class DigestExchange:
    at: str
    tool: str
    session: str
    user: str
    reply: str
    tool_calls: int
    signal: str | None = None
    sub: str | None = None  # sub-project, for projects tracked with sub-projects


@dataclass
class ProjectWeek:
    id: str
    name: str
    group: str | None
    kind: str
    sessions: dict[str, int] = field(default_factory=dict)  # tool -> session count
    exchange_count: int = 0
    active_days: list[str] = field(default_factory=list)
    files_changed: int = 0
    file_days: int = 0
    commits: int = 0
    attention: float = 0.0
    exchanges: list[DigestExchange] = field(default_factory=list)


@dataclass
class Digest:
    week: str
    start: str
    end: str
    projects: list[ProjectWeek]
    needs_review: dict[str, str]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=1, ensure_ascii=False)


def _attention(pw: ProjectWeek) -> float:
    """Days of work count most; raw volume is capped so one long session can't dominate."""
    return round(
        2.0 * len(pw.active_days)
        + min(pw.exchange_count, 40) / 4
        + min(pw.commits, 20) / 2
        + min(pw.file_days, 7) / 2,
        1,
    )


def _spread(indices: list[int], room: int) -> list[int]:
    """Pick up to `room` indices spread evenly across the list."""
    if room <= 0 or not indices:
        return []
    if len(indices) <= room:
        return indices
    step = len(indices) / room
    return [indices[int(k * step)] for k in range(room)]


def _select(exchanges: list[DigestExchange]) -> list[DigestExchange]:
    """Within MAX_PER_PROJECT: signals first, then how each session started and ended,
    then an even sample of the rest."""
    keep: set[int] = set()
    signals = [i for i, e in enumerate(exchanges) if e.signal]
    keep.update(_spread(signals, MAX_PER_PROJECT * 2 // 3))
    by_session: dict[str, list[int]] = {}
    for i, e in enumerate(exchanges):
        by_session.setdefault(e.session, []).append(i)
    edges = [i for idx in by_session.values() for i in (idx[0], idx[-1]) if i not in keep]
    keep.update(_spread(sorted(set(edges)), MAX_PER_PROJECT - len(keep)))
    rest = [i for i in range(len(exchanges)) if i not in keep]
    keep.update(_spread(rest, min(SAMPLE_PER_PROJECT, MAX_PER_PROJECT - len(keep))))
    return [exchanges[i] for i in sorted(keep)]


def build(config: Config, week: str, catalog: Catalog | None = None,
          activity: Activity | None = None) -> Digest:
    start, end = week_bounds(week)
    decisions = config.load_decisions()
    catalog = catalog or scan(config.projects_root, decisions.get("folders", {}), config.places)
    activity = activity or Activity(config.git_authors)
    weeks: dict[str, ProjectWeek] = {}

    def entry(pid: str) -> ProjectWeek:
        if pid not in weeks:
            project = catalog.projects.get(pid)
            if project:
                weeks[pid] = ProjectWeek(pid, project.name, project.group, project.kind)
            else:
                weeks[pid] = ProjectWeek(pid, Path(pid).name or pid, None,
                                         "group" if pid in catalog.groups else "outside")
        return weeks[pid]

    raw: dict[str, list[DigestExchange]] = {}
    for session in load_sources_for(config.sources, start, end):
        location = session.cwd or (session.path_hints.most_common(1)[0][0] if session.path_hints else None)
        pid = catalog.resolve(location) or OUTSIDE
        sub = catalog.subproject(location, pid)
        pw = entry(pid)
        pw.sessions[session.tool] = pw.sessions.get(session.tool, 0) + 1
        for ex in session.exchanges:
            raw.setdefault(pid, []).append(DigestExchange(
                at=ex.at.isoformat(timespec="minutes"), tool=session.tool, session=session.id,
                user=clip(redact(ex.user), USER_LIMIT), reply=clip(redact(ex.reply), REPLY_LIMIT),
                tool_calls=ex.tool_calls, signal=signal(ex.user), sub=sub,
            ))

    for pid, project in catalog.projects.items():
        files = in_range(activity.file_days(project), start, end)
        commits = in_range(activity.commit_days(project), start, end)
        if files or commits or pid in weeks:
            pw = entry(pid)
            pw.files_changed = sum(files.values())
            pw.file_days = len(files)
            pw.commits = sum(commits.values())
            days = set(files) | set(commits)
            days |= {datetime.fromisoformat(e.at).date() for e in raw.get(pid, [])}
            pw.active_days = sorted(d.isoformat() for d in days)

    for pid, exchanges in raw.items():
        pw = entry(pid)
        exchanges.sort(key=lambda e: e.at)
        pw.exchange_count = len(exchanges)
        pw.exchanges = _select(exchanges)
        if not pw.active_days:
            pw.active_days = sorted({e.at[:10] for e in exchanges})

    for pw in weeks.values():
        pw.attention = _attention(pw)

    ranked = sorted(weeks.values(), key=lambda p: p.attention, reverse=True)
    review = {p.id: p.needs_review for p in catalog.projects.values()
              if p.needs_review and p.id not in decisions.get("folders", {})}
    return Digest(week, start.isoformat(), end.isoformat(), ranked, review)


def render_markdown(digest: Digest) -> str:
    lines = [f"# Digest {digest.week}", "", f"{digest.start[:10]} to {digest.end[:10]}", ""]
    lines += ["| Project | Attention | Days | Conversations | Exchanges | Files | Commits |",
              "| --- | ---: | ---: | --- | ---: | ---: | ---: |"]
    for p in digest.projects:
        convs = ", ".join(f"{t} {n}" for t, n in p.sessions.items()) or "-"
        lines.append(f"| {p.id} | {p.attention} | {len(p.active_days)} | {convs} | {p.exchange_count} "
                     f"| {p.files_changed} | {p.commits} |")
    for p in digest.projects:
        if not p.exchanges:
            continue
        lines += ["", f"## {p.id}", ""]
        for e in p.exchanges:
            tag = f" **[{e.signal}]**" if e.signal else ""
            lines += [f"**{e.at} · {e.tool}**{tag} (tools used: {e.tool_calls})", "",
                      "> " + e.user.replace("\n", "\n> "), ""]
            if e.reply:
                lines += ["Reply: " + e.reply.replace("\n", " ")[:400], ""]
    if digest.needs_review:
        lines += ["", "## Folders to confirm", ""]
        lines += [f"- **{fid}**: {why}" for fid, why in digest.needs_review.items()]
    return "\n".join(lines) + "\n"


def write(config: Config, digest: Digest) -> tuple[Path, Path]:
    out = config.work_dir / "digests"
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / f"{digest.week}.json"
    md_path = out / f"{digest.week}.md"
    json_path.write_text(digest.to_json(), encoding="utf-8")
    md_path.write_text(render_markdown(digest), encoding="utf-8")
    return json_path, md_path
