from pathlib import Path

from second_brain_protocol import config
from second_brain_protocol.config import RuntimePaths, dashboard_runtime


def test_dashboard_runtime_reuses_existing_hardened_runtime(
    tmp_path: Path, monkeypatch,
) -> None:
    paths = RuntimePaths.from_root(tmp_path / "runtime")
    paths.root.mkdir(parents=True)
    paths.config.write_text("{}\n", encoding="utf-8")
    paths.state.touch()

    def fail_setup(_paths):
        raise AssertionError("full runtime setup should not run")

    monkeypatch.setattr(config, "setup_runtime", fail_setup)
    assert dashboard_runtime(paths) == paths
    assert paths.dashboard.is_dir()
    assert paths.locks.is_dir()


def test_dashboard_runtime_bootstraps_when_runtime_is_missing(
    tmp_path: Path, monkeypatch,
) -> None:
    paths = RuntimePaths.from_root(tmp_path / "runtime")
    calls = []

    def fake_setup(received):
        calls.append(received)
        return received

    monkeypatch.setattr(config, "setup_runtime", fake_setup)
    assert dashboard_runtime(paths) == paths
    assert calls == [paths]
