from pathlib import Path

from second_brain_protocol.config import RuntimePaths, load_defaults
from second_brain_protocol.orchestrator import _synthesize
from second_brain_protocol.state import StateStore


def test_empty_daily_makes_no_model_call(tmp_path: Path) -> None:
    paths = RuntimePaths.from_root(tmp_path / "runtime")
    for path in (paths.root, paths.staging, paths.runs):
        path.mkdir(parents=True, exist_ok=True)
    store = StateStore(paths.state)
    result = _synthesize("daily", store=store, paths=paths, defaults=load_defaults())
    assert result == {"status": "empty", "evidence_count": 0, "model_called": False}
    assert store.runs() == []
