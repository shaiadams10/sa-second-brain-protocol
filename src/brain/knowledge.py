"""What the brain has learned, stored in <vault>/brain/knowledge.json.

Everything the model contributes lands here first, and the Markdown notes are
rendered from it. Changes apply automatically; the owner removes what they
don't like from the dashboard, and removed items are shown to the model as
things never to propose again.

An observation about the owner starts as a candidate and becomes part of their
profile only after it recurs: in two different projects, or in two different
weeks. One-off remarks never reach the profile.

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

    # ----- applying a week

    def begin_week(self, week: str) -> None:
        """Forget what an earlier run of this week contributed, so re-runs don't double count."""
        self.weeks.pop(week, None)
        for project in self.projects.values():
            project["notes"] = [n for n in project.get("notes", []) if n.get("week") != week]
        for item_id in list(self.items):
            item = self.items[item_id]
            item["evidence"] = [e for e in item["evidence"] if e["week"] != week]
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

    def apply_project_result(self, week: str, pid: str, result: dict) -> list[str]:
        """Apply the model's answer for one project. Returns ids of new candidates."""
        project = self.projects[pid]
        if result.get("blurb", "").strip():
            project["blurb"] = result["blurb"].strip()
        entry = self.weeks[week]["projects"][pid]
        entry["summary"] = result.get("week_summary", "").strip()
        known_subs = set(project.get("subprojects", []))
        entry["subprojects_touched"] = [s for s in result.get("subprojects_touched", []) if s in known_subs]

        known = {n["text"].lower() for n in project["notes"]}
        removed = {n["text"].lower() for n in project.get("removed_notes", [])}
        for note in result.get("project_notes", [])[:5]:
            text = note["text"].strip()
            if text and text.lower() not in known | removed:
                project["notes"].append({"id": _id("n"), "kind": note["kind"], "text": text, "week": week})
                known.add(text.lower())

        new_ids = []
        for obs in result.get("observations", [])[:5]:
            text = obs["text"].strip()
            if not text or obs["scope"] == "ephemeral":
                continue
            if obs["scope"] == "project":
                if text.lower() not in known | removed:
                    project["notes"].append({"id": _id("n"), "kind": "preference", "text": text, "week": week})
                    known.add(text.lower())
                continue
            evidence = {"week": week, "project": pid, "quote": obs.get("evidence", "").strip()[:300]}
            target = self.items.get(obs.get("reinforces", ""))
            if target and target["status"] == "removed":
                continue  # the model matched it to something the owner removed
            if target:
                target["evidence"].append(evidence)
                continue
            if self._is_removed_text(text):
                continue
            item_id = _id("i")
            self.items[item_id] = {"kind": obs["kind"], "text": text, "status": "candidate",
                                   "evidence": [evidence], "created": week}
            new_ids.append(item_id)
        return new_ids

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
            self.items[target]["evidence"].extend(self.items[source]["evidence"])
            del self.items[source]
        self.promote()

    def promote(self) -> None:
        for item in self.items.values():
            if item["status"] != "candidate":
                continue
            projects = {e["project"] for e in item["evidence"]}
            weeks = {e["week"] for e in item["evidence"]}
            if len(projects) >= 2 or len(weeks) >= 2:
                item["status"] = "active"
                item["promoted"] = max(weeks)

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


def _ever_ongoing(active_weeks: list[str]) -> bool:
    """True if any 8-week window held 3 or more weeks of real work."""
    starts = [week_bounds(w)[0] for w in active_weeks]
    for i in range(len(starts)):
        window = [s for s in starts[i:] if s - starts[i] < timedelta(weeks=WINDOW_WEEKS)]
        if len(window) >= ONGOING_WEEKS:
            return True
    return False
