from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .basic_memory_integration import reindex_changed
from .config import RuntimePaths, protocol_root
from .dashboard import build_snapshot, render_dashboard
from .knowledge import dislike_knowledge, like_knowledge, undo_last_dislike
from .question_actions import answer_question
from .state import StateStore


DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 8765
SERVICE_NAME = "second-brain-dashboard"
MAX_REQUEST_BYTES = 8192
SEARCH_REFRESH_RETRY_SECONDS = 30
IndexRefresher = Callable[[RuntimePaths, Path, list[str]], str]


def dashboard_url(port: int = DASHBOARD_PORT) -> str:
    return f"http://{DASHBOARD_HOST}:{port}/"


class DashboardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        paths: RuntimePaths,
        vault: Path,
        *,
        reindexer: IndexRefresher = reindex_changed,
    ):
        super().__init__(server_address, DashboardRequestHandler)
        self.paths = paths
        self.vault = vault
        self.store = StateStore(paths.state)
        self.csrf_token = secrets.token_urlsafe(32)
        self.reindexer = reindexer
        self.action_lock = threading.Lock()
        self.refresh_event = threading.Event()
        self.stop_event = threading.Event()
        self.refresh_thread = threading.Thread(target=self._refresh_loop, daemon=True)
        self.refresh_thread.start()
        if self.store.search_refresh_batch():
            self.refresh_event.set()

    def request_search_refresh(self) -> None:
        self.refresh_event.set()

    def _refresh_loop(self) -> None:
        while not self.stop_event.is_set():
            self.refresh_event.wait()
            if self.stop_event.is_set():
                return
            self.refresh_event.clear()
            if self.stop_event.wait(0.75):
                return
            self.refresh_event.clear()
            batch = self.store.search_refresh_batch()
            if not batch:
                continue
            self.store.set_search_refresh_state("indexing")
            try:
                self.reindexer(
                    self.paths,
                    self.vault,
                    [str(item["path"]) for item in batch],
                )
            except Exception as error:
                self.store.fail_search_refresh(batch, str(error))
                if not self.stop_event.wait(SEARCH_REFRESH_RETRY_SECONDS):
                    self.refresh_event.set()
                continue
            self.store.complete_search_refresh(batch)
            if self.store.search_refresh_batch():
                self.refresh_event.set()

    def server_close(self) -> None:
        self.stop_event.set()
        self.refresh_event.set()
        super().server_close()
        if self.refresh_thread.is_alive():
            self.refresh_thread.join(timeout=2)


class DashboardRequestHandler(BaseHTTPRequestHandler):
    server: DashboardHTTPServer

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _security_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'none'",
        )

    def _send_bytes(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send_bytes(status, "application/json; charset=utf-8", body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/health":
            self._send_json(HTTPStatus.OK, {"ok": True, "service": SERVICE_NAME})
            return
        if path not in {"/", "/index.html"}:
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
            return
        snapshot = build_snapshot(self.server.paths, self.server.vault)
        snapshot["actions"] = {
            "enabled": True,
            "csrf_token": self.server.csrf_token,
        }
        body = render_dashboard(snapshot).encode("utf-8")
        self._send_bytes(HTTPStatus.OK, "text/html; charset=utf-8", body)

    def _authorized(self) -> bool:
        port = self.server.server_address[1]
        allowed_origin = f"http://{DASHBOARD_HOST}:{port}"
        origin = self.headers.get("Origin")
        fetch_site = self.headers.get("Sec-Fetch-Site")
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().casefold()
        return (
            origin == allowed_origin
            and fetch_site in {None, "same-origin"}
            and content_type == "application/json"
            and secrets.compare_digest(
                self.headers.get("X-SB-Token", ""), self.server.csrf_token
            )
        )

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            self._send_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "Action denied"})
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = -1
        if content_length < 0 or content_length > MAX_REQUEST_BYTES:
            self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": "Invalid request"})
            return
        try:
            payload = json.loads(self.rfile.read(content_length) or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid request"})
            return
        route = self.path.split("?", 1)[0]
        parts = route.strip("/").split("/")
        knowledge_route = route == "/api/knowledge/undo" or (
            len(parts) == 4 and parts[:2] == ["api", "knowledge"]
        )
        question_route = len(parts) == 4 and parts[:2] == ["api", "questions"] and parts[3] == "answer"
        if not knowledge_route and not question_route:
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
            return
        if knowledge_route and payload not in ({}, None):
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Unexpected request data"})
            return
        if question_route and (
            not isinstance(payload, dict)
            or set(payload) != {"answer"}
            or not isinstance(payload.get("answer"), str)
        ):
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "A written answer is required"})
            return

        try:
            with self.server.action_lock:
                if question_route:
                    result = answer_question(
                        self.server.vault,
                        self.server.store,
                        parts[2],
                        payload["answer"],
                    )
                elif route == "/api/knowledge/undo":
                    result = undo_last_dislike(
                        self.server.paths,
                        self.server.vault,
                        self.server.store,
                    )
                else:
                    observation_id, action = parts[2], parts[3]
                    if action == "like":
                        result = like_knowledge(self.server.store, observation_id)
                    elif action == "dislike":
                        result = dislike_knowledge(
                            self.server.paths,
                            self.server.vault,
                            self.server.store,
                            observation_id,
                        )
                    else:
                        raise LookupError("Not found")
        except LookupError:
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
            return
        except (KeyError, ValueError):
            error = "Invalid question or answer" if question_route else "Invalid knowledge item"
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": error})
            return
        except Exception:
            self._send_json(
                HTTPStatus.CONFLICT,
                {"ok": False, "error": "The action failed safely. No partial decision was kept."},
            )
            return
        if route == "/api/knowledge/undo" or result.get("decision") == "disliked":
            self.server.request_search_refresh()
        self._send_json(HTTPStatus.OK, {"ok": True, **result})


def create_dashboard_server(
    paths: RuntimePaths,
    vault: Path,
    *,
    port: int = DASHBOARD_PORT,
    reindexer: IndexRefresher = reindex_changed,
) -> DashboardHTTPServer:
    return DashboardHTTPServer((DASHBOARD_HOST, port), paths, vault, reindexer=reindexer)


def server_is_running(port: int = DASHBOARD_PORT) -> bool:
    try:
        with urllib.request.urlopen(
            dashboard_url(port) + "api/health", timeout=0.6
        ) as response:
            payload = json.loads(response.read())
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
        return False
    return response.status == HTTPStatus.OK and payload.get("service") == SERVICE_NAME


def serve_dashboard(
    paths: RuntimePaths,
    vault: Path,
    *,
    open_browser: bool = True,
    port: int = DASHBOARD_PORT,
) -> None:
    if server_is_running(port):
        if open_browser:
            webbrowser.open(dashboard_url(port))
        print("The private dashboard is already running.")
        return
    try:
        server = create_dashboard_server(paths, vault, port=port)
    except OSError as error:
        raise RuntimeError("The dashboard port is already in use by another application") from error
    if open_browser:
        timer = threading.Timer(0.35, webbrowser.open, args=(dashboard_url(port),))
        timer.daemon = True
        timer.start()
    print(f"Private dashboard ready at {dashboard_url(port)}")
    print("Keep this window open. Press Ctrl+C to stop the dashboard.")
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def ensure_dashboard_server(
    paths: RuntimePaths,
    *,
    port: int = DASHBOARD_PORT,
) -> str:
    if server_is_running(port):
        return dashboard_url(port)
    command = [
        sys.executable,
        "-m",
        "second_brain_protocol.cli",
        "dashboard",
        "serve",
        "--no-browser",
    ]
    kwargs: dict[str, Any] = {
        "cwd": protocol_root(),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NO_WINDOW
        )
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(command, **kwargs)
    for _ in range(50):
        if server_is_running(port):
            return dashboard_url(port)
        time.sleep(0.1)
    raise RuntimeError("The private dashboard server did not start")
