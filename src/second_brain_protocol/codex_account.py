from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from .config import RuntimePaths
from .model_runner import find_codex_executable


_CACHE_LOCK = threading.Lock()
_CACHE_VALUE: dict[str, Any] | None = None
_CACHE_AT = 0.0


def _window_label(duration_minutes: int | None) -> str:
    if not duration_minutes:
        return "Codex window"
    if duration_minutes % 10080 == 0:
        weeks = duration_minutes // 10080
        return f"{weeks}-week Codex window" if weeks != 1 else "7-day Codex window"
    if duration_minutes % 1440 == 0:
        days = duration_minutes // 1440
        return f"{days}-day Codex window"
    if duration_minutes % 60 == 0:
        hours = duration_minutes // 60
        return f"{hours}-hour Codex window"
    return f"{duration_minutes}-minute Codex window"


def normalize_rate_limits(
    payload: dict[str, Any],
    usage_payload: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Keep only safe, display-ready Codex quota fields from app-server output."""

    buckets = payload.get("rateLimitsByLimitId")
    snapshot = buckets.get("codex") if isinstance(buckets, dict) else None
    if not isinstance(snapshot, dict):
        snapshot = payload.get("rateLimits")
    if not isinstance(snapshot, dict):
        return {"available": False, "windows": []}

    now = now or datetime.now(UTC)
    usage_buckets = (usage_payload or {}).get("dailyUsageBuckets") or []
    windows = []
    for key in ("primary", "secondary"):
        window = snapshot.get(key)
        if not isinstance(window, dict) or not isinstance(
            window.get("usedPercent"), int
        ):
            continue
        used = max(0, min(100, int(window["usedPercent"])))
        duration = window.get("windowDurationMins")
        resets_at = window.get("resetsAt")
        reset_time = (
            datetime.fromtimestamp(int(resets_at), tz=UTC)
            if resets_at is not None
            else None
        )
        start_time = (
            reset_time - timedelta(minutes=int(duration))
            if reset_time is not None and duration is not None
            else None
        )
        observed_tokens = 0
        if start_time is not None:
            for bucket in usage_buckets:
                try:
                    bucket_date = datetime.fromisoformat(
                        str(bucket["startDate"])
                    ).date()
                    bucket_tokens = int(bucket["tokens"])
                except (KeyError, TypeError, ValueError):
                    continue
                if start_time.date() <= bucket_date <= min(now, reset_time).date():
                    observed_tokens += max(0, bucket_tokens)
        windows.append(
            {
                "key": key,
                "label": _window_label(int(duration) if duration is not None else None),
                "used_percent": used,
                "remaining_percent": 100 - used,
                "duration_minutes": int(duration) if duration is not None else None,
                "starts_at": start_time.isoformat() if start_time is not None else None,
                "resets_at": reset_time.isoformat() if reset_time is not None else None,
                "observed_tokens": observed_tokens,
            }
        )
    if not windows:
        return {"available": False, "windows": []}
    return {
        "available": True,
        "windows": windows,
        "included_plan": bool(snapshot.get("planType")),
        "source": "codex-app-server",
        "checked_at": datetime.now(UTC).isoformat(),
    }


def _query_rate_limits(
    paths: RuntimePaths, *, timeout_seconds: float
) -> dict[str, Any]:
    executable = find_codex_executable(paths)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    env = os.environ.copy()
    env["CODEX_HOME"] = str(paths.codex_home)
    process = subprocess.Popen(
        [str(executable), "app-server", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=creationflags,
        env=env,
    )
    messages: queue.Queue[dict[str, Any]] = queue.Queue()

    def collect() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            try:
                value = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(value, dict):
                messages.put(value)

    reader = threading.Thread(target=collect, daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout_seconds

    def send(payload: dict[str, Any]) -> None:
        if process.stdin is None:
            raise RuntimeError("Codex account channel is unavailable")
        process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
        process.stdin.flush()

    def receive(request_id: int) -> dict[str, Any]:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Codex account usage timed out")
            message = messages.get(timeout=remaining)
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError("Codex account usage is unavailable")
                return message.get("result") or {}

    try:
        send(
            {
                "method": "initialize",
                "id": 1,
                "params": {
                    "clientInfo": {"name": "second-brain-dashboard", "version": "1.0"},
                    "capabilities": {"experimentalApi": True},
                },
            }
        )
        receive(1)
        send({"method": "account/rateLimits/read", "id": 2})
        rate_limits = receive(2)
        send({"method": "account/usage/read", "id": 3})
        return normalize_rate_limits(rate_limits, receive(3))
    finally:
        if process.stdin is not None:
            process.stdin.close()
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()


def read_codex_rate_limits(
    paths: RuntimePaths, *, timeout_seconds: float = 4.0, cache_seconds: float = 60.0
) -> dict[str, Any]:
    """Read current authenticated Codex quota percentages, failing closed."""

    global _CACHE_AT, _CACHE_VALUE
    with _CACHE_LOCK:
        now = time.monotonic()
        if _CACHE_VALUE is not None and now - _CACHE_AT < cache_seconds:
            return dict(_CACHE_VALUE)
        try:
            value = _query_rate_limits(paths, timeout_seconds=timeout_seconds)
        except Exception:
            value = {"available": False, "windows": []}
        _CACHE_VALUE = value
        _CACHE_AT = now
        return dict(value)
