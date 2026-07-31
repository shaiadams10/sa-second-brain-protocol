import threading
import time
import urllib.request
from pathlib import Path

from second_brain_protocol.config import RuntimePaths
from second_brain_protocol.dashboard_server import create_dashboard_server


def test_consecutive_dashboard_refreshes_reuse_the_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = RuntimePaths.from_root(tmp_path / "runtime")
    vault = tmp_path / "vault"
    calls = 0

    def slow_snapshot(_paths, _vault):
        nonlocal calls
        calls += 1
        time.sleep(0.15)
        return {"generated_at": f"snapshot-{calls}"}

    monkeypatch.setattr(
        "second_brain_protocol.dashboard_server.build_snapshot",
        slow_snapshot,
    )
    server = create_dashboard_server(
        paths,
        vault,
        port=0,
        reindexer=lambda _paths, _vault, _relative: "ok",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_address[1]}/"

    try:
        with urllib.request.urlopen(endpoint, timeout=3) as response:
            assert response.status == 200
        started = time.perf_counter()
        with urllib.request.urlopen(endpoint, timeout=3) as response:
            assert response.status == 200
        second_refresh_seconds = time.perf_counter() - started

        assert calls == 1
        assert second_refresh_seconds < 0.08

        server.invalidate_snapshot()
        with urllib.request.urlopen(endpoint, timeout=3) as response:
            assert response.status == 200
        assert calls == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_stale_dashboard_snapshot_refreshes_in_background(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = RuntimePaths.from_root(tmp_path / "runtime")
    vault = tmp_path / "vault"
    calls = 0
    refresh_finished = threading.Event()

    def snapshot(_paths, _vault):
        nonlocal calls
        calls += 1
        if calls > 1:
            time.sleep(0.15)
            refresh_finished.set()
        return {"generated_at": f"snapshot-{calls}"}

    monkeypatch.setattr(
        "second_brain_protocol.dashboard_server.SNAPSHOT_CACHE_SECONDS",
        0,
    )
    monkeypatch.setattr(
        "second_brain_protocol.dashboard_server.build_snapshot",
        snapshot,
    )
    server = create_dashboard_server(
        paths,
        vault,
        port=0,
        reindexer=lambda _paths, _vault, _relative: "ok",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_address[1]}/"

    try:
        with urllib.request.urlopen(endpoint, timeout=3) as response:
            assert response.status == 200
        started = time.perf_counter()
        with urllib.request.urlopen(endpoint, timeout=3) as response:
            assert response.status == 200
        stale_refresh_seconds = time.perf_counter() - started

        assert stale_refresh_seconds < 0.08
        assert refresh_finished.wait(timeout=3)
        assert calls == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
