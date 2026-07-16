from pathlib import Path

from second_brain_protocol.evidence_compaction import compact_session_evidence
from second_brain_protocol.model_runner import build_evidence_packet
from second_brain_protocol.state import StateStore


def _event(
    store: StateStore,
    *,
    session: str,
    source: str,
    kind: str,
    payload: dict,
    index: int,
) -> str:
    evidence_id, _ = store.add_evidence(
        source_type=source,
        source_ref=f"{source}:{session}:{index}",
        kind=kind,
        payload={"session_id": session, "source": source, **payload},
        project_id=f"project-{session}",
        occurred_at=f"2026-01-0{index + 1}T10:00:00Z",
    )
    return evidence_id


def test_session_compaction_is_provenance_backed_and_idempotent(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    source_ids = [
        _event(
            store,
            session="one",
            source="codex",
            kind="visible_message",
            payload={"role": "user", "text": "Build the feature."},
            index=0,
        ),
        _event(
            store,
            session="one",
            source="codex",
            kind="visible_message",
            payload={"role": "assistant", "text": "Implemented and tests passed."},
            index=1,
        ),
        _event(
            store,
            session="one",
            source="codex",
            kind="tool_metadata",
            payload={"role": "assistant", "tool": "apply_patch"},
            index=2,
        ),
    ]

    first = compact_session_evidence(store)
    second = compact_session_evidence(store)
    assert first == {"session_digests_created": 1, "source_records_compacted": 3}
    assert second == {"session_digests_created": 0, "source_records_compacted": 0}

    digest = next(item for item in store.evidence(status="new") if item["kind"] == "session_digest")
    assert digest["payload"]["user_messages"][0]["text"] == "Build the feature."
    assert digest["payload"]["tool_usage"] == {"apply_patch": 1}
    assert set(digest["payload"]["source_evidence_ids"]) == set(source_ids)
    assert all(
        item["status"] == "compacted"
        for item in store.evidence()
        if item["id"] in source_ids
    )
    assert set(store.expand_derived_evidence([digest["id"]])) == {
        digest["id"],
        *source_ids,
    }

    packet = build_evidence_packet([digest], max_chars=20000)
    assert "source_evidence_ids" not in str(packet)

    published = store.expand_derived_evidence([digest["id"]])
    store.mark_evidence(published, "processed")
    assert all(
        item["status"] == "processed"
        for item in store.evidence()
        if item["id"] in published
    )


def test_large_derivation_uses_chunked_sql_updates(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    source_ids = []
    for index in range(1200):
        evidence_id, _ = store.add_evidence(
            source_type="codex",
            source_ref=f"codex:large:{index}",
            kind="tool_metadata",
            payload={"session_id": "large", "tool": "shell_command", "index": index},
        )
        source_ids.append(evidence_id)
    derived_id, _ = store.add_derived_evidence(
        source_type="session-digest",
        source_ref="session-digest:codex:large",
        kind="session_digest",
        payload={"session_id": "large", "source_evidence_ids": source_ids},
        source_evidence_ids=source_ids,
    )
    expanded = store.expand_derived_evidence([derived_id])
    assert len(expanded) == 1201
    assert len(store.evidence_by_ids(expanded)) == 1201
    store.publish_checkpoint_candidates(expanded)
    store.mark_evidence(expanded, "processed")
    assert len(store.evidence(status="processed")) == 1201
