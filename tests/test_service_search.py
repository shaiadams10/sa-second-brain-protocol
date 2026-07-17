from pathlib import Path

from second_brain_protocol import service
from second_brain_protocol.config import RuntimePaths
from second_brain_protocol.state import StateStore


def test_search_hides_rejected_claim_while_index_refresh_is_pending(
    tmp_path: Path, monkeypatch,
) -> None:
    paths = RuntimePaths.from_root(tmp_path / "runtime")
    store = StateStore(paths.state)
    vault = tmp_path / "vault"
    vault.mkdir()
    observation_id = store.add_observation(
        {
            "kind": "preference",
            "subject": "Removed preference",
            "claim": "This rejected claim must not remain searchable.",
            "evidence_refs": [],
            "confidence": 0.8,
            "source_count": 1,
            "project_count": 1,
            "sensitivity": "normal",
            "promotion_tier": "review",
            "status": "rejected",
        }
    )
    with store.transaction() as connection:
        store.enqueue_search_refresh(connection, ["Identity/Preferences.md"])
    monkeypatch.setattr(
        service,
        "memory_search",
        lambda _paths, _query, limit: [
            {
                "path": "Identity/Preferences.md",
                "excerpt": f"This rejected claim must not remain searchable. ^{observation_id}",
            }
        ],
    )

    assert service.search(paths, vault, "rejected claim") == []
