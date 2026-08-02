import json
from pathlib import Path
from typing import Any

import pytest

from second_brain_protocol.config import RuntimePaths
from second_brain_protocol.extraction_harness import ExtractionRequest
from second_brain_protocol.extraction_model import (
    BudgetedExtractionModel,
    EvidencePacketBudgetExceeded,
    ModelCallBudgetExceeded,
    ProtocolExtractionModel,
)
from second_brain_protocol.model_runner import ModelRole


class CountingModel:
    def __init__(self) -> None:
        self.calls = 0

    def extract(self, _request: ExtractionRequest) -> dict[str, Any]:
        self.calls += 1
        return {"candidates": []}


def test_protocol_extraction_model_uses_dedicated_prompt_schema_and_usage(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_runner(**kwargs: Any) -> tuple[dict[str, Any], Path]:
        calls.append(kwargs)
        receipt = tmp_path / f"{kwargs['run_id']}.json"
        receipt.write_text(
            json.dumps(
                {
                    "usage": {
                        "input_tokens": 120,
                        "output_tokens": 30,
                        "total_tokens": 150,
                        "model_calls": 1,
                    }
                }
            ),
            encoding="utf-8",
        )
        return {"candidates": []}, receipt

    model = ProtocolExtractionModel(
        paths=RuntimePaths.from_root(tmp_path),
        role=ModelRole(name="test-model", reasoning="low"),
        run_id="run-evaluation",
        max_packet_chars=5000,
        run=fake_runner,
    )
    response = model.extract(
        ExtractionRequest(
            run_kind="daily",
            evidence=(
                {
                    "id": "ev-live-adapter",
                    "source_type": "session-episode",
                    "kind": "session_episode",
                    "project_id": None,
                    "payload": {
                        "analysis_lane": "profile_only",
                        "project_ids": [],
                    },
                },
            ),
        )
    )

    assert calls[0]["prompt_name"] == "extraction.md"
    assert calls[0]["system_prompt_name"] == "extraction-system.md"
    assert calls[0]["schema_name"] == "extraction-output.schema.json"
    assert calls[0]["run_id"] == "run-evaluation-call-0001"
    assert response["usage"]["total_tokens"] == 150


def test_budgeted_model_never_calls_delegate_past_explicit_ceiling() -> None:
    delegate = CountingModel()
    model = BudgetedExtractionModel(delegate=delegate, max_model_calls=2)
    request = ExtractionRequest(run_kind="daily", evidence=())

    model.extract(request)
    model.extract(request)
    with pytest.raises(ModelCallBudgetExceeded, match="2"):
        model.extract(request)

    assert delegate.calls == 2


def test_protocol_model_refuses_hidden_item_or_packet_truncation(
    tmp_path: Path,
) -> None:
    called = False

    def forbidden_runner(**_kwargs: Any) -> tuple[dict[str, Any], Path]:
        nonlocal called
        called = True
        raise AssertionError("Truncated evidence must not reach the model runner")

    model = ProtocolExtractionModel(
        paths=RuntimePaths.from_root(tmp_path),
        role=ModelRole(name="test-model", reasoning="low"),
        run_id="run-bounded",
        max_packet_chars=500,
        max_evidence_chars=120,
        run=forbidden_runner,
    )

    with pytest.raises(EvidencePacketBudgetExceeded, match="preserve"):
        model.extract(
            ExtractionRequest(
                run_kind="daily",
                evidence=(
                    {
                        "id": "ev-oversized-live-item",
                        "source_type": "canonical-note",
                        "kind": "canonical_context",
                        "project_id": None,
                        "payload": {"context": "ordinary text " * 200},
                    },
                ),
            )
        )

    assert called is False


def test_default_item_budget_reserves_envelope_around_maximum_episode_text(
    tmp_path: Path,
) -> None:
    calls = 0

    def fake_runner(**kwargs: Any) -> tuple[dict[str, Any], Path]:
        nonlocal calls
        calls += 1
        receipt = tmp_path / "max-episode-receipt.json"
        receipt.write_text(json.dumps({"usage": {"model_calls": 1}}), encoding="utf-8")
        return {"candidates": []}, receipt

    model = ProtocolExtractionModel(
        paths=RuntimePaths.from_root(tmp_path),
        role=ModelRole(name="test-model", reasoning="low"),
        run_id="run-max-episode",
        max_packet_chars=20_000,
        run=fake_runner,
    )
    model.extract(
        ExtractionRequest(
            run_kind="daily",
            evidence=(
                {
                    "id": "ev-max-episode-text",
                    "source_type": "session-episode",
                    "kind": "session_episode",
                    "project_id": None,
                    "payload": {
                        "source": "synthetic",
                        "analysis_lane": "profile_only",
                        "project_ids": [],
                        "user_messages": [
                            {
                                "occurred_at": f"2026-08-01T10:0{index}:00Z",
                                "text": "x" * 1200,
                            }
                            for index in range(5)
                        ],
                        "assistant_results": [],
                    },
                },
            ),
        )
    )

    assert calls == 1
