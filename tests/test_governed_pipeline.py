from pathlib import Path
import json

from second_brain_protocol.governed_pipeline import GovernedDailyWeeklyPipeline
from second_brain_protocol.governed_publication import GOVERNED_JOURNAL_MARKER
from second_brain_protocol.state import StateStore


class ScriptedModel:
    def __init__(self) -> None:
        self.calls = 0
        self.requests = []

    def extract(self, request):
        self.calls += 1
        self.requests.append(request)
        evidence_id = str(request.evidence[0]["id"])
        return {
            "candidates": [
                {
                    "type": "memory_mutation",
                    "operation": "create",
                    "kind": "preference",
                    "subject": "Freshness boundary",
                    "claim": "Only new or materially changed evidence should resurface an insight.",
                    "scope": "global",
                    "project_id": None,
                    "target_memory_id": None,
                    "evidence_refs": [evidence_id],
                    "confidence": 0.98,
                    "explicit": True,
                }
            ],
            "usage": {
                "input_tokens": 20,
                "output_tokens": 10,
                "total_tokens": 30,
                "model_calls": 1,
            },
        }


def _seed(store: StateStore) -> str:
    evidence_id, _created = store.add_evidence(
        source_type="test",
        source_ref="test:freshness",
        kind="explicit_owner_statement",
        payload={"statement": "Do not repeat old insights without a change."},
        occurred_at="2026-08-02T12:00:00Z",
    )
    store.attach_checkpoint_candidate(
        evidence_id,
        source_key="test:freshness",
        cursor='{"kind":"jsonl","offset":10}',
        fingerprint="freshness-v1",
    )
    return evidence_id


def test_governed_daily_redacts_sensitive_session_data_before_model_call(
    tmp_path: Path,
) -> None:
    authorization = "Authorization: " + "Bearer " + "abcdefghijklmnopqrstuvwxyz123456"
    vault = tmp_path / "vault"
    vault.mkdir()
    store = StateStore(tmp_path / "state.sqlite")
    evidence_id, _ = store.add_evidence(
        source_type="session-digest",
        source_ref="session:test-private",
        kind="session_digest",
        payload={
            "session_id": "raw-private-session-id",
            "source": "codex",
            "analysis_lane": "profile_only",
            "project_ids": [],
            "artifacts": [
                {
                    "filename": "passport-front.png",
                    "content": "identity-document-content",
                }
            ],
            "user_messages": [
                {
                    "occurred_at": "2026-08-02T12:00:00Z",
                    "text": (
                        "Email private@example.com and read C:\\Users\\private\\secret.txt. "
                        f"{authorization}. "
                        "```python\nprint('private')\n```"
                    ),
                }
            ],
            "assistant_results": [],
        },
        occurred_at="2026-08-02T12:00:00Z",
    )
    store.attach_checkpoint_candidate(
        evidence_id,
        source_key="session:test-private",
        cursor='{"kind":"jsonl","offset":10}',
        fingerprint="private-v1",
    )
    model = ScriptedModel()

    GovernedDailyWeeklyPipeline(
        vault=vault,
        staging_root=tmp_path / "staging",
        store=store,
        model_factory=lambda _run_id: model,
        model_contract="scripted-v1",
        max_model_calls=2,
    ).run(run_kind="daily", period="2026-08-02")

    encoded = json.dumps(model.requests, default=str, ensure_ascii=False)
    assert "raw-private-session-id" not in encoded
    assert "identity-document-content" not in encoded
    assert "private@example.com" not in encoded
    assert "C:\\Users\\private" not in encoded
    assert "abcdefghijklmnopqrstuvwxyz123456" not in encoded
    assert "print('private')" not in encoded
    assert "[REDACTED_EMAIL]" in encoded


def test_governed_daily_applies_once_and_leaves_only_weekly_feeder_new(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    store = StateStore(tmp_path / "state.sqlite")
    evidence_id = _seed(store)
    model = ScriptedModel()
    pipeline = GovernedDailyWeeklyPipeline(
        vault=vault,
        staging_root=tmp_path / "staging",
        store=store,
        model_factory=lambda _run_id: model,
        model_contract="scripted-v1",
        max_model_calls=2,
    )

    first = pipeline.run(run_kind="daily", period="2026-08-02")
    second = pipeline.run(run_kind="daily", period="2026-08-02")

    daily = vault / "Journal" / "Daily" / "2026-08-02.md"
    assert first["status"] == "completed"
    assert first["engine"] == "governed-extraction-v3"
    assert first["candidate_count"] == 1
    assert first["checkpoint_advanced"] is True
    assert second == {
        "status": "empty",
        "engine": "governed-extraction-v3",
        "evidence_count": 0,
        "model_called": False,
    }
    assert store.checkpoint("test:freshness")["cursor"] == (
        '{"kind":"jsonl","offset":10}'
    )
    statuses = {item["id"]: item["status"] for item in store.evidence()}
    assert statuses[evidence_id] == "processed"
    assert [item["kind"] for item in store.evidence(status="new")] == [
        "daily_run_summary"
    ]
    assert model.calls == 1
    assert (
        daily.read_text(encoding="utf-8").count(
            "Only new or materially changed evidence should resurface an insight."
        )
        == 1
    )
    assert GOVERNED_JOURNAL_MARKER in daily.read_text(encoding="utf-8")
    pending = store.observations("pending")
    assert len(pending) == 1
    assert pending[0]["payload"]["memory_mutation_id"].startswith("memory-mutation-")


def test_weekly_consumes_daily_summaries_without_reopening_daily_evidence(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    store = StateStore(tmp_path / "state.sqlite")
    _seed(store)
    daily_model = ScriptedModel()
    GovernedDailyWeeklyPipeline(
        vault=vault,
        staging_root=tmp_path / "staging",
        store=store,
        model_factory=lambda _run_id: daily_model,
        model_contract="scripted-daily-v1",
        max_model_calls=2,
    ).run(run_kind="daily", period="2026-08-02")
    weekly_model = ScriptedModel()

    weekly = GovernedDailyWeeklyPipeline(
        vault=vault,
        staging_root=tmp_path / "staging",
        store=store,
        model_factory=lambda _run_id: weekly_model,
        model_contract="scripted-weekly-v1",
        max_model_calls=2,
    ).run(run_kind="weekly", period="2026-W31")

    assert weekly["status"] == "completed"
    assert weekly["evidence_count"] == 1
    assert weekly_model.calls == 1
    assert not store.evidence(status="new")
    assert (vault / "Journal" / "Weekly" / "2026-W31.md").is_file()


def test_completed_daily_apply_repairs_a_missing_weekly_feeder(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    store = StateStore(tmp_path / "state.sqlite")
    _seed(store)
    model = ScriptedModel()
    pipeline = GovernedDailyWeeklyPipeline(
        vault=vault,
        staging_root=tmp_path / "staging",
        store=store,
        model_factory=lambda _run_id: model,
        model_contract="scripted-v1",
        max_model_calls=2,
    )
    pipeline.run(run_kind="daily", period="2026-08-02")
    feeder = next(
        item
        for item in store.evidence(status="new")
        if item["kind"] == "daily_run_summary"
    )
    with store.connect() as connection:
        connection.execute("DELETE FROM evidence WHERE id=?", (feeder["id"],))

    replay = pipeline.run(run_kind="daily", period="2026-08-02")

    assert replay["status"] == "empty"
    repaired = store.evidence(status="new")
    assert len(repaired) == 1
    assert repaired[0]["kind"] == "daily_run_summary"


def test_pending_memory_proposal_repairs_a_missing_review_observation(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    store = StateStore(tmp_path / "state.sqlite")
    _seed(store)
    model = ScriptedModel()
    pipeline = GovernedDailyWeeklyPipeline(
        vault=vault,
        staging_root=tmp_path / "staging",
        store=store,
        model_factory=lambda _run_id: model,
        model_contract="scripted-v1",
        max_model_calls=2,
    )
    pipeline.run(run_kind="daily", period="2026-08-02")
    observation_id = store.observations("pending")[0]["id"]
    with store.connect() as connection:
        connection.execute("DELETE FROM observations WHERE id=?", (observation_id,))

    pipeline.run(run_kind="daily", period="2026-08-02")

    repaired = store.observations("pending")
    assert len(repaired) == 1
    assert repaired[0]["payload"]["memory_mutation_id"].startswith("memory-mutation-")
