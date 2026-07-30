from pathlib import Path

import pytest

from second_brain_protocol.config import RuntimePaths, load_defaults
from second_brain_protocol.orchestrator import _synthesize, incremental
from second_brain_protocol.state import StateStore


def test_empty_daily_makes_no_model_call(tmp_path: Path) -> None:
    paths = RuntimePaths.from_root(tmp_path / "runtime")
    for path in (paths.root, paths.staging, paths.runs):
        path.mkdir(parents=True, exist_ok=True)
    store = StateStore(paths.state)
    result = _synthesize("daily", store=store, paths=paths, defaults=load_defaults())
    assert result == {"status": "empty", "evidence_count": 0, "model_called": False}
    assert store.runs() == []


def test_manual_daily_requires_explicit_owner_authorization() -> None:
    with pytest.raises(RuntimeError, match="explicit owner request"):
        incremental("daily", trigger="manual")


def test_local_daily_test_requires_explicit_owner_authorization() -> None:
    with pytest.raises(RuntimeError, match="explicit owner request"):
        incremental("daily", trigger="manual", publish_git=False)
