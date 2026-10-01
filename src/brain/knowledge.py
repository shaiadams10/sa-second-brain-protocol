"""What the brain has learned, stored in <vault>/brain/knowledge.json.

Everything the model contributes lands here first, and the Markdown notes are
rendered from it. Changes apply automatically; the owner removes what they
don't like from the dashboard, and removed items are shown to the model as
things never to propose again.

An observation about the owner starts as a candidate and becomes part of their
profile only after it recurs: in two different projects, or in two different
weeks. One-off remarks never reach the profile. What the owner states directly
about themself (a fact, a correction, a goal, advice they took or turned down)
counts at once. A correction also strikes the belief it rejects.

Skills are items too: one per topic, each piece of evidence carrying the level the
owner showed that week (directs, learning, struggled). Their standing is computed
from that history, never stored.

Project status is computed from weekly attention, never stored:
  ongoing    marked by the owner, or real attention in 3 of the last 8 weeks
             and active in the last 3
  on-hold    was ongoing, but quiet for 3 weeks or more
  exploring  touched in the last 4 weeks
  earlier    everything else
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from brain.digest import week_bounds, week_label

ACTIVE_ATTENTION = 3.0  # a week counts as real work on a project at this attention or above
ONGOING_WEEKS = 3
WINDOW_WEEKS = 8
QUIET_WEEKS = 3
EXPLORING_WEEKS = 4
RETURN_GAP_WEEKS = 4  # a project touched again after this many weeks counts as a return

SAID_KINDS = ("stated", "goal", "advice")  # stated directly by the owner: confirmed at once
# What the learning pass (`run --learn`) fills in for weeks logged before it existed.
LEARN_KINDS = ("skill", "communication", "interest", *SAID_KINDS)
LEVEL_RANK = {"directs": 0, "learning": 1, "struggled": 2}


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Knowledge:
    def __init__(self, path: Path) -> None:
        self.path = path
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))
        else:
            self.data = {"version": 1, "weeks": {}, "projects": {}, "items": {}}

    # ----- persistence

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    @property
    def weeks(self) -> dict:
        return self.data["weeks"]

    @property
    def projects(self) -> dict:
        return self.data["projects"]

    @property
    def items(self) -> dict:
        return self.data["items"]

    @property
    def themes(self) -> dict:
        return self.data.get("themes", {})

    # ----- applying a week

    def begin_week(self, week: str) -> None:
        """Forget what an earlier run of this week contributed, so re-runs don't double count."""
        self.weeks.pop(week, None)
        for project in self.projects.values():
            project["notes"] = [n for n in project.get("notes", []) if n.get("week") != week]
        self._forget_week(week, None)

    def begin_learn(self, week: str) -> None:
        """Like begin_week, but only for what the learning pass adds: summaries, project notes,
        and the other observations of an earlier run stay as they are."""
        self._forget_week(week, LEARN_KINDS)

    def _forget_week(self, week: str, kinds: tuple[str, ...] | None) -> None:
        for item_id in list(self.items):
            item = self.items[item_id]
            if kinds is not None and item["kind"] not in kinds:
                continue
            item["evidence"] = [e for e in item["evidence"] if e["week"] != week]
            if item.get("resolved") == week:
                item["open"] = True
                item.pop("resolved")
            if item["status"] == "removed":
                continue  # kept so the model is told never to propose it again
            if not item["evidence"]:
                del self.items[item_id]
            elif item["status"] == "active" and item.get("promoted") == week:
                item["status"] = "candidate"  # re-earned (or not) by this week's re-run
                item.pop("promoted", None)

    def record_project(self, week: str, pid: str, name: str, group: str | None, kind: str,
                       subprojects: list[str], stats: dict) -> None:
        project = self.projects.setdefault(pid, {"name": name, "blurb": "", "notes": []})
        project.update({"name": name, "group": group, "kind": kind, "subprojects": subprojects})
        week_entry = self.weeks.setdefault(week, {"projects": {}})
        week_entry["projects"][pid] = {**week_entry["projects"].get(pid, {}), **stats}

    def apply_project_result(self, week: str, pid: str, result: dict, learn_only: bool = False) -> list[str]:
        """Apply the model's answer for one project. Returns ids of new candidates.
        learn_only applies only what the learning pass adds (see LEARN_KINDS)."""
        project = self.projects[pid]
        known = {n["text"].lower() for n in project["notes"]}
        removed = {n["text"].lower() for n in project.get("removed_notes", [])}
        if not learn_only:
            if result.get("blurb", "").strip():
                project["blurb"] = result["blurb"].strip()
            entry = self.weeks[week]["projects"][pid]
            entry["summary"] = result.get("week_summary", "").strip()
            known_subs = set(project.get("subprojects", []))
            entry["subprojects_touched"] = [s for s in result.get("subprojects_touched", []) if s in known_subs]
            for note in result.get("project_notes", [])[:5]:
                text = note["text"].strip()
                if text and text.lower() not in known | removed:
                    project["notes"].append({"id": _id("n"), "kind": note["kind"], "text": text, "week": week})
                    known.add(text.lower())

        new_ids = []
        for obs in result.get("observations", [])[:5]:
            text = obs["text"].strip()
            if not text or obs["scope"] == "ephemeral" or (learn_only and obs["kind"] not in LEARN_KINDS):
                continue
            if obs["scope"] == "project":
                if learn_only:
                    continue
                if text.lower() not in known | removed:
                    project["notes"].append({"id": _id("n"), "kind": "preference", "text": text, "week": week})
                    known.add(text.lower())
                continue
            evidence = {"week": week, "project": pid, "quote": obs.get("evidence", "").strip()[:300]}
            target = self.items.get(obs.get("reinforces", ""))
            if target and target["status"] == "removed":
                continue  # the model matched it to something the owner removed
            if target and learn_only and target["kind"] not in LEARN_KINDS:
                continue  # the earlier run already counted that kind for this week
            if target:
                target["evidence"].append(evidence)
                continue
            if self._is_removed_text(text):
                continue
            item_id = _id("i")
            self.items[item_id] = {"kind": obs["kind"], "text": text, "status": "candidate",
                                   "evidence": [evidence], "created": week}
            new_ids.append(item_id)

        for skill in result.get("skills", [])[:6]:
            new_id = self._apply_skill(week, pid, skill)
            if new_id:
                new_ids.append(new_id)
        for said in result.get("said", [])[:10]:
            self._apply_said(week, pid, said)
        for goal_id in result.get("resolved_goals", []):
            goal = self.items.get(goal_id)
            if goal and goal["kind"] == "goal" and goal.get("open"):
                goal["open"] = False
                goal["resolved"] = week
        return new_ids

    def _apply_skill(self, week: str, pid: str, skill: dict) -> str | None:
        """Add one week's evidence to a skill topic. Returns the id if the topic is new."""
        topic = skill["topic"].strip()
        if not topic or skill.get("level") not in LEVEL_RANK:
            return None
        evidence = {"week": week, "project": pid, "level": skill["level"],
                    "quote": skill.get("evidence", "").strip()[:300]}
        target = self.items.get(skill.get("reinforces", ""))
        if not (target and target["kind"] == "skill"):
            target = next((i for i in self.items.values()
                           if i["kind"] == "skill" and i["text"].lower() == topic.lower()), None)
        if target:
            if target["status"] != "removed":
                target["evidence"].append(evidence)
            return None
        item_id = _id("i")
        self.items[item_id] = {"kind": "skill", "text": topic, "status": "candidate",
                               "evidence": [evidence], "created": week}
        return item_id

    def _apply_said(self, week: str, pid: str, said: dict) -> None:
        """Something the owner stated directly. It counts at once; a correction also strikes
        the belief it rejects, so the model is told never to propose it again."""
        text = said["text"].strip()
        if not text or said.get("kind") not in (*SAID_KINDS, "correction"):
            return
        kind = "stated" if said["kind"] == "correction" else said["kind"]
        evidence = {"week": week, "project": pid, "quote": said.get("evidence", "").strip()[:300]}
        wrong = said.get("wrong", "").strip() if said["kind"] == "correction" else ""
        if wrong:
            match = next((i for i in self.items.values() if i["text"].lower() == wrong.lower()), None)
            if match and match["status"] != "removed":
                self.remove_item(next(k for k, v in self.items.items() if v is match))
            elif not match:
                self.items[_id("i")] = {"kind": kind, "text": wrong, "status": "removed", "removed_at": _now(),
                                        "evidence": [evidence], "created": week, "corrected": True}
        if self._is_removed_text(text):
            return
        target = next((i for i in self.items.values() if i["kind"] == kind and i["text"].lower() == text.lower()),
                      None)
        if target:
            target["evidence"].append(evidence)
            return
        item = {"kind": kind, "text": text, "status": "active", "promoted": week,
                "evidence": [evidence], "created": week}
        if kind == "goal":
            item["open"] = True
        self.items[_id("i")] = item

    def apply_synthesis(self, week: str, result: dict, model: str, partial: bool, alias: dict[str, str]) -> None:
        """alias maps the temporary "new-N" ids shown to the model to real item ids."""
        entry = self.weeks[week]
        entry.update({
            "headline": result.get("headline", "").strip(),
            "summary": result.get("summary", "").strip(),
            "highlights": [h.strip() for h in result.get("highlights", []) if h.strip()][:4],
            "model": model, "ran_at": _now(), "partial": partial,
        })
        for merge in result.get("merges", []):
            source = alias.get(merge["id"], merge["id"])
            target = alias.get(merge["same_as"], merge["same_as"])
            if source == target or source not in self.items or target not in self.items:
                continue
            if self.items[source]["status"] != "candidate" or self.items[target]["status"] == "removed":
                continue
            if (self.items[source]["kind"] == "skill") != (self.items[target]["kind"] == "skill"):
                continue  # a skill topic and an observation are never the same thing
            self.items[target]["evidence"].extend(self.items[source]["evidence"])
            del self.items[source]
        self.promote()

    def promote(self) -> None:
        for item in self.items.values():
            if item["status"] != "candidate":
                continue
            projects = {e["project"] for e in item["evidence"]}
            weeks = {e["week"] for e in item["evidence"]}
            if item["kind"] in SAID_KINDS or len(projects) >= 2 or len(weeks) >= 2:
                item["status"] = "active"
                item["promoted"] = max(weeks)

    def set_themes(self, week: str, themes: list[dict]) -> None:
        """Replace the themes; only project names the brain knows are kept."""
        names = {p["name"].lower(): p["name"] for p in self.projects.values()}
        kept = []
        for theme in themes[:7]:
            projects = [names[n.strip().lower()] for n in theme.get("projects", []) if n.strip().lower() in names]
            if theme.get("name", "").strip() and len(set(projects)) >= 2:
                kept.append({"name": theme["name"].strip(), "summary": theme.get("summary", "").strip(),
                             "projects": list(dict.fromkeys(projects))})
        if kept:
            self.data["themes"] = {"week": week, "updated": _now(), "items": kept}

    def _is_removed_text(self, text: str) -> bool:
        lowered = text.lower()
        return any(i["status"] == "removed" and i["text"].lower() == lowered for i in self.items.values())

    # ----- owner edits from the dashboard

    def remove_item(self, item_id: str) -> None:
        item = self.items[item_id]
        item["previous_status"] = item["status"]
        item["status"] = "removed"
        item["removed_at"] = _now()

    def restore_item(self, item_id: str) -> None:
        item = self.items[item_id]
        item["status"] = item.pop("previous_status", "active")
        item.pop("removed_at", None)

    def remove_note(self, pid: str, note_id: str) -> None:
        project = self.projects[pid]
        for note in project["notes"]:
            if note["id"] == note_id:
                project["notes"].remove(note)
                project.setdefault("removed_notes", []).append({**note, "removed_at": _now()})
                return

    def restore_note(self, pid: str, note_id: str) -> None:
        project = self.projects[pid]
        for note in project.get("removed_notes", []):
            if note["id"] == note_id:
                project["removed_notes"].remove(note)
                note.pop("removed_at", None)
                project["notes"].append(note)
                return

    # ----- views

    def attention(self, pid: str) -> dict[str, float]:
        return {w: e["projects"][pid].get("attention", 0.0)
                for w, e in self.weeks.items() if pid in e.get("projects", {})}

    def status(self, pid: str, decisions: dict, today: date | None = None) -> dict:
        today = today or date.today()
        ref = week_label(today)
        history = self.attention(pid)
        marked = pid in decisions.get("marked", [])
        unmarked = pid in decisions.get("unmarked", [])

        def weeks_ago(label: str) -> int:
            return (week_bounds(ref)[0] - week_bounds(label)[0]).days // 7

        active_weeks = sorted(w for w, a in history.items() if a >= ACTIVE_ATTENTION)
        touched = sorted(w for w, a in history.items() if a > 0)
        last = touched[-1] if touched else None
        recent_active = [w for w in active_weeks if weeks_ago(w) < WINDOW_WEEKS]
        quiet_for = weeks_ago(last) if last else None
        ever_ongoing = marked or _ever_ongoing(active_weeks)

        recent = quiet_for is not None and quiet_for < QUIET_WEEKS
        if (marked and (recent or last is None)) or (len(recent_active) >= ONGOING_WEEKS and recent and not unmarked):
            state = "ongoing"
        elif ever_ongoing and not unmarked:
            state = "on-hold"
        elif quiet_for is not None and quiet_for < EXPLORING_WEEKS:
            state = "exploring"
        else:
            state = "earlier"
        last_day = None
        if last:
            days = self.weeks[last]["projects"][pid].get("active_days") or []
            last_day = days[-1] if days else week_bounds(last)[0].date().isoformat()
        return {"state": state, "marked": marked, "unmarked": unmarked, "last_week": last,
                "last_day": last_day, "active_weeks": len(active_weeks), "weeks_touched": len(touched)}

    def returns(self, pid: str) -> int:
        """How many times the owner came back to a project after RETURN_GAP_WEEKS or more away."""
        starts = sorted(week_bounds(w)[0] for w, a in self.attention(pid).items() if a > 0)
        return sum(1 for a, b in zip(starts, starts[1:]) if b - a >= timedelta(weeks=RETURN_GAP_WEEKS))


def skill_standing(item: dict) -> dict:
    """Where a skill topic stands, from the level shown each week (a week's worst level counts):
      strong     directing it in 2+ weeks or projects since last asking about it
      growing    asked about it before, directing it in the latest week
      shown      directed once, in one week and one project
      learning   still asking about it in the latest week
      struggled  it kept failing in the latest week
    """
    by_week: dict[str, str] = {}
    for e in item["evidence"]:
        level = e.get("level", "directs")
        if LEVEL_RANK.get(level, 0) >= LEVEL_RANK.get(by_week.get(e["week"], "directs"), 0):
            by_week[e["week"]] = level
    weeks = sorted(by_week)
    if not weeks:
        return {"level": "shown", "trend": "", "projects": []}
    projects = sorted({e["project"] for e in item["evidence"]})
    asked = [w for w in weeks if by_week[w] != "directs"]
    latest = by_week[weeks[-1]]
    if latest != "directs":
        trend = f"asking about it since {asked[0]}" if len(asked) > 1 else f"asked about it in {weeks[-1]}"
        return {"level": latest if latest == "struggled" else "learning", "trend": trend, "projects": projects}
    since = [w for w in weeks if not asked or w > asked[-1]]
    since_projects = {e["project"] for e in item["evidence"] if e["week"] in since}
    if asked:
        trend = f"asked about it in {asked[0]}, directing it by {since[0]}"
        level = "strong" if len(since) >= 2 or len(since_projects) >= 2 else "growing"
    else:
        trend = f"directing it since {weeks[0]}" if len(weeks) > 1 else f"directed it in {weeks[0]}"
        level = "strong" if len(weeks) >= 2 or len(projects) >= 2 else "shown"
    return {"level": level, "trend": trend, "projects": projects}


def _ever_ongoing(active_weeks: list[str]) -> bool:
    """True if any 8-week window held 3 or more weeks of real work."""
    starts = [week_bounds(w)[0] for w in active_weeks]
    for i in range(len(starts)):
        window = [s for s in starts[i:] if s - starts[i] < timedelta(weeks=WINDOW_WEEKS)]
        if len(window) >= ONGOING_WEEKS:
            return True
    return False
