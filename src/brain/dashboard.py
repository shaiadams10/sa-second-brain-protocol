"""The dashboard: a local web page for watching the brain and removing what it got wrong.

Serves brain/web/ and a small JSON API on 127.0.0.1 only. Every change the owner
makes is saved, re-rendered into the vault's Markdown, and committed.
"""

from __future__ import annotations

import json
import mimetypes
import re
import sys
import threading
import time
import webbrowser
from datetime import date, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

from brain import config as config_mod
from brain.config import DAYS, Config
from brain.digest import week_label
from brain.install import TIME, register_task
from brain.knowledge import skill_standing
from brain.projects import Catalog, scan
from brain.render import has_page, render_all, slug
from brain.run import Busy, commit, knowledge_for, lock_held, read_run_log, run

WEB = Path(__file__).parent / "web"
IDLE_SHUTDOWN = 3 * 3600  # the server stops itself after 3 idle hours
FOLDER_RULES = {"project", "project+subprojects", "collection", "ignore"}
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,80}$")
_EFFORT = re.compile(r"^[a-z]{1,12}$")


def run_active(cfg: Config) -> bool:
    """A brain update (Run now, the schedule, or a backfill) holds the run lock. Its knowledge is
    in memory and will be saved over any edit made meanwhile, so edits wait until it ends."""
    return lock_held(cfg.work_dir / "run.lock", 24 * 3600)


def run_choice(body: dict) -> dict:
    """The CLI, model and effort picked in Run now; empty values mean the configured default."""
    cli, model, effort = body.get("cli") or None, body.get("model") or None, body.get("effort") or None
    if cli not in (None, "agy", "codex"):
        raise ValueError("unknown CLI")
    if model is not None and (not isinstance(model, str) or not _MODEL_ID.match(model) or model.startswith("manual")):
        raise ValueError("bad model id")
    if effort is not None and (not isinstance(effort, str) or not _EFFORT.match(effort)):
        raise ValueError("bad effort")
    return {"cli": cli, "model_name": model, "effort": effort}


class RunState:
    """The Run now job, shared by all requests."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.lines: list[str] = []
        self.started: str | None = None
        self.finished: str | None = None
        self.error: str | None = None
        self.choice: dict = {}

    def snapshot(self) -> dict:
        with self.lock:
            return {"running": bool(self.thread and self.thread.is_alive()), "lines": self.lines[-60:],
                    "started": self.started, "finished": self.finished, "error": self.error,
                    "choice": self.choice}

    def start(self, cfg: Config, on_done, choice: dict | None = None) -> bool:
        with self.lock:
            if self.thread and self.thread.is_alive():
                return False
            self.lines, self.error, self.finished = [], None, None
            self.choice = {k: v for k, v in (choice or {}).items() if v}
            self.started = datetime.now().astimezone().isoformat(timespec="seconds")

            def log(line: str) -> None:
                with self.lock:
                    self.lines.append(f"{datetime.now():%H:%M:%S}  {line}")

            def work() -> None:
                try:
                    run(cfg, current=True, log=log, **(choice or {}))
                except Busy as exc:
                    self.error = str(exc)
                except Exception as exc:  # noqa: BLE001 - shown on the dashboard
                    self.error = str(exc)[:1500]
                finally:
                    self.finished = datetime.now().astimezone().isoformat(timespec="seconds")
                    on_done()

            self.thread = threading.Thread(target=work, daemon=True)
            self.thread.start()
            return True


class App:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.run_state = RunState()
        self.write_lock = threading.Lock()
        self._catalog: tuple[float, Catalog] | None = None
        self.last_request = time.time()

    def catalog(self, fresh: bool = False) -> Catalog:
        if fresh or not self._catalog or time.time() - self._catalog[0] > 120:
            decisions = self.cfg.load_decisions()
            self._catalog = (time.time(), scan(self.cfg.projects_root, decisions.get("folders", {}), self.cfg.places))
        return self._catalog[1]

    # ----- read

    def schedule(self) -> dict | None:
        if not hasattr(self, "_schedule") or time.time() - self._schedule[0] > 300:
            from brain.install import schedule_info
            self._schedule = (time.time(), schedule_info())
        return self._schedule[1]

    def state(self) -> dict:
        cfg = self.cfg
        knowledge = knowledge_for(cfg)
        decisions = cfg.load_decisions()
        catalog = self.catalog()
        weeks = []
        for label in sorted(knowledge.weeks, reverse=True):
            entry = knowledge.weeks[label]
            weeks.append({"week": label, **{k: entry.get(k) for k in
                                            ("headline", "summary", "highlights", "partial", "model", "ran_at")},
                          "projects": entry.get("projects", {})})
        projects = []
        for pid, p in knowledge.projects.items():
            if p.get("kind") in ("outside", "group"):
                continue
            status = knowledge.status(pid, decisions)
            projects.append({"id": pid, "name": p["name"], "group": p.get("group"), "kind": p.get("kind"),
                             "blurb": p.get("blurb", ""), "subprojects": p.get("subprojects", []),
                             "notes": p.get("notes", []), "removed_notes": p.get("removed_notes", []),
                             "history": knowledge.attention(pid), "status": status,
                             "page": f"projects/{slug(pid)}.md" if has_page(status) else None})
        tracked = {p["id"] for p in projects}
        folders = [{"id": p.id, "name": p.name, "group": p.group, "kind": p.kind,
                    "subprojects": p.subprojects, "rule": decisions.get("folders", {}).get(p.id),
                    "marked": p.id in decisions.get("marked", []), "tracked": p.id in tracked}
                   for p in sorted(catalog.projects.values(), key=lambda p: ((p.group or ""), p.name.lower()))]
        review = {p.id: p.needs_review for p in catalog.projects.values()
                  if p.needs_review and p.id not in decisions.get("folders", {})}
        items = [{"id": i, **it, **({"standing": skill_standing(it)} if it["kind"] == "skill" else {}),
                  "projects": sorted({knowledge.projects.get(e["project"], {}).get("name", e["project"])
                                                      for e in it["evidence"]})}
                 for i, it in knowledge.items.items()]
        return {
            "owner": cfg.owner, "today": date.today().isoformat(), "thisWeek": week_label(date.today()),
            "weeks": weeks, "projects": projects, "folders": folders, "groups": sorted(catalog.groups),
            "items": items, "review": review, "runs": read_run_log(cfg), "run": self.run_state.snapshot(),
            "names": {pid: p["name"] for pid, p in knowledge.projects.items()},
            "schedule": self.schedule(),
            "settings": {"cli": cfg.model_cli, "model": cfg.model_name or "", "effort": cfg.model_effort or "",
                         "day": cfg.schedule_day, "time": cfg.schedule_time},
            "vault": str(cfg.vault),
        }

    def models(self) -> dict:
        """CLIs, models and effort levels for Run now. Listing agy models takes a few seconds."""
        if not hasattr(self, "_models") or time.time() - self._models[0] > 600:
            from brain.llm import available_models
            self._models = (time.time(), available_models())
        cfg = self.cfg
        return {"clis": self._models[1],
                "config": {"cli": cfg.model_cli, "model": cfg.model_name, "effort": cfg.model_effort,
                           "day": cfg.schedule_day, "time": cfg.schedule_time}}

    def settings(self, body: dict) -> str:
        """Save the weekly run's defaults (CLI, model, effort, day, time) to config.toml, re-register
        the scheduled task, and commit. Returns a line for the toast."""
        choice = run_choice(body)
        day, at = body.get("day"), body.get("time")
        if day not in DAYS or not isinstance(at, str) or not TIME.match(at):
            raise ValueError("pick a weekday and a time as HH:MM")
        with self.write_lock:
            config_mod.save_settings(self.cfg.vault, {
                "model": {"cli": choice["cli"] or "agy", "name": choice["model_name"] or "",
                          "effort": choice["effort"] or ""},
                "schedule": {"day": day, "time": at},
            })
            self.cfg = config_mod.load(self.cfg.vault)
            self.__dict__.pop("_schedule", None)
            try:
                summary = register_task(self.cfg)
            except SystemExit as exc:
                summary = f"Saved, but the scheduled task was not updated: {exc}"
            commit(self.cfg, f"Dashboard: weekly run {day} {at} with {self.cfg.model_cli}"
                             f"{' ' + self.cfg.model_name if self.cfg.model_name else ''}"
                             f"{' ' + self.cfg.model_effort if self.cfg.model_effort else ''}",
                   paths=["brain/config.toml"])
            return summary

    # ----- write

    def guard(self) -> None:
        if run_active(self.cfg):
            raise Busy("A brain update is running. Try again when it has finished, "
                       "otherwise the update would overwrite this change.")

    def _finish_edit(self, knowledge, message: str, rescan: bool = False) -> None:
        knowledge.save()
        render_all(self.cfg.vault, knowledge, self.cfg.load_decisions(), self.catalog(fresh=rescan), self.cfg.owner)
        commit(self.cfg, message)

    def item(self, item_id: str, action: str) -> None:
        with self.write_lock:
            self.guard()
            knowledge = knowledge_for(self.cfg)
            if item_id not in knowledge.items:
                raise KeyError(item_id)
            text = knowledge.items[item_id]["text"]
            (knowledge.remove_item if action == "remove" else knowledge.restore_item)(item_id)
            self._finish_edit(knowledge, f"Dashboard: {action}d \"{text[:60]}\"")

    def note(self, pid: str, note_id: str, action: str) -> None:
        with self.write_lock:
            self.guard()
            knowledge = knowledge_for(self.cfg)
            if pid not in knowledge.projects:
                raise KeyError(pid)
            (knowledge.remove_note if action == "remove" else knowledge.restore_note)(pid, note_id)
            self._finish_edit(knowledge, f"Dashboard: {action}d a note on {knowledge.projects[pid]['name']}")

    def mark(self, pid: str, value: str) -> None:
        with self.write_lock:
            self.guard()
            decisions = self.cfg.load_decisions()
            marked, unmarked = set(decisions.get("marked", [])), set(decisions.get("unmarked", []))
            marked.discard(pid)
            unmarked.discard(pid)
            if value == "mark":
                marked.add(pid)
            elif value == "unmark":
                unmarked.add(pid)
            decisions["marked"], decisions["unmarked"] = sorted(marked), sorted(unmarked)
            self.cfg.save_decisions(decisions)
            label = {"mark": "marked", "unmark": "unmarked", "auto": "reset"}[value]
            self._finish_edit(knowledge_for(self.cfg), f"Dashboard: {label} {pid} as a project")

    def folder(self, fid: str, rule: str | None) -> None:
        with self.write_lock:
            self.guard()
            decisions = self.cfg.load_decisions()
            folders = decisions.setdefault("folders", {})
            if rule:
                folders[fid] = rule
            else:
                folders.pop(fid, None)
            self.cfg.save_decisions(decisions)
            self._finish_edit(knowledge_for(self.cfg), f"Dashboard: {fid} is {rule or 'automatic'}", rescan=True)


def _handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "brain"

        def log_message(self, *args) -> None:  # keep the console quiet
            pass

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, status: int = 200) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self) -> None:
            app.last_request = time.time()
            path = urlparse(self.path).path
            if path == "/api/state":
                return self._json(app.state())
            if path == "/api/run":
                return self._json(app.run_state.snapshot())
            if path == "/api/models":
                return self._json(app.models())
            target = (WEB / (path.lstrip("/") or "index.html")).resolve()
            if not target.is_file() or WEB.resolve() not in target.parents:
                target = WEB / "index.html"
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self._send(200, target.read_bytes(), ctype)

        def do_POST(self) -> None:
            app.last_request = time.time()
            # Only this page may write: a custom header forces a CORS preflight that other sites cannot pass.
            if self.headers.get("X-Brain") != "1":
                return self._json({"error": "forbidden"}, HTTPStatus.FORBIDDEN)
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._json({"error": "bad json"}, HTTPStatus.BAD_REQUEST)
            parts = urlparse(self.path).path.strip("/").split("/")
            try:
                if parts[:2] == ["api", "item"] and body.get("action") in ("remove", "restore"):
                    app.item(body["id"], body["action"])
                elif parts[:2] == ["api", "note"] and body.get("action") in ("remove", "restore"):
                    app.note(body["project"], body["id"], body["action"])
                elif parts[:2] == ["api", "mark"] and body.get("value") in ("mark", "unmark", "auto"):
                    app.mark(body["id"], body["value"])
                elif parts[:2] == ["api", "folder"] and (body.get("rule") in FOLDER_RULES or body.get("rule") is None):
                    app.folder(body["id"], body.get("rule"))
                elif parts[:2] == ["api", "run"]:
                    try:
                        choice = run_choice(body)
                    except ValueError as exc:
                        return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                    started = app.run_state.start(app.cfg, on_done=lambda: None, choice=choice)
                    return self._json({"started": started, **app.run_state.snapshot()})
                elif parts[:2] == ["api", "settings"]:
                    try:
                        summary = app.settings(body)
                    except ValueError as exc:
                        return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                    return self._json({**app.state(), "message": summary, "models": app.models()})
                else:
                    return self._json({"error": "unknown request"}, HTTPStatus.BAD_REQUEST)
            except KeyError as exc:
                return self._json({"error": f"not found: {exc}"}, HTTPStatus.NOT_FOUND)
            except Busy as exc:
                return self._json({"error": str(exc)}, HTTPStatus.CONFLICT)
            self._json(app.state())

    return Handler


def _already_running(url: str) -> bool:
    try:
        with urlopen(url + "api/run", timeout=1.5) as res:
            return res.status == 200
    except (URLError, OSError):
        return False


def _set_window_title(title: str) -> None:
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW(title)
        except (AttributeError, OSError):
            pass


def serve(cfg: Config, port: int = 8765, open_browser: bool = True) -> int:
    """In a terminal window (the desktop shortcut), the dashboard runs until that window is closed.
    Without one, it stops itself after IDLE_SHUTDOWN so it never lingers unseen."""
    url = f"http://127.0.0.1:{port}/"
    in_window = bool(sys.stdout and sys.stdout.isatty())
    if _already_running(url):
        if open_browser:
            webbrowser.open(url)
        print(f"The dashboard is already open in another window: {url}")
        if in_window:
            time.sleep(3)  # long enough to read before this window closes
        return 0
    app = App(cfg)
    server = ThreadingHTTPServer(("127.0.0.1", port), _handler(app))
    if in_window:
        _set_window_title("Second Brain dashboard")
        print(f"Second Brain dashboard: {url}\n\nClose this window (or press Ctrl+C) to stop the dashboard.\n"
              "A Run now in progress stops with it; the next run picks up where it left off.")
    else:
        print(f"Second Brain dashboard: {url}")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    def idle_watch() -> None:
        while True:
            time.sleep(60)
            busy = app.run_state.snapshot()["running"]
            if not busy and time.time() - app.last_request > IDLE_SHUTDOWN:
                server.shutdown()
                return

    if not in_window:
        threading.Thread(target=idle_watch, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
