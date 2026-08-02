from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from .config import RuntimePaths
from .extraction_harness import (
    ExtractionModel,
    ExtractionRequest,
    ModelCallBudgetExceeded,
)
from .model_runner import (
    ModelRole,
    build_evidence_packet,
    run_model,
    usage_from_receipt,
)
from .security import sanitize_model_evidence_item


EXTRACTION_ITEM_ENVELOPE_CHARS = 3000


class EvidencePacketBudgetExceeded(RuntimeError):
    pass


class BudgetedExtractionModel:
    """Hard-stop external model delegation at an explicit packet-call ceiling."""

    def __init__(self, *, delegate: ExtractionModel, max_model_calls: int) -> None:
        if max_model_calls <= 0:
            raise ValueError("The model-call budget must be positive")
        self._delegate = delegate
        self._max_model_calls = max_model_calls
        self._calls = 0

    @property
    def calls(self) -> int:
        return self._calls

    def extract(self, request: ExtractionRequest) -> Mapping[str, Any]:
        if self._calls >= self._max_model_calls:
            raise ModelCallBudgetExceeded(
                f"Live extraction exceeded the {self._max_model_calls}-call budget"
            )
        self._calls += 1
        return self._delegate.extract(request)


class ProtocolExtractionModel:
    """Adapt the protocol's structured model runner to the extraction seam."""

    def __init__(
        self,
        *,
        paths: RuntimePaths,
        role: ModelRole,
        run_id: str,
        max_packet_chars: int,
        max_evidence_chars: int = 6000 + EXTRACTION_ITEM_ENVELOPE_CHARS,
        use_cache: bool = True,
        run: Callable[..., tuple[dict[str, Any], Path]] = run_model,
    ) -> None:
        self._paths = paths
        self._role = role
        self._run_id = run_id
        self._max_packet_chars = max_packet_chars
        self._max_evidence_chars = max_evidence_chars
        self._use_cache = use_cache
        self._run = run
        self._call_index = 0

    def extract(self, request: ExtractionRequest) -> Mapping[str, Any]:
        self._call_index += 1
        evidence = [dict(item) for item in request.evidence]
        packet = build_evidence_packet(
            evidence,
            max_chars=self._max_packet_chars,
            max_item_chars=self._max_evidence_chars,
        )
        transported = {
            str(item["id"]): item for item in packet.get("evidence", [])
        }
        expected = {
            str(item["id"]): sanitize_model_evidence_item(item)
            for item in evidence
        }
        if transported != expected:
            raise EvidencePacketBudgetExceeded(
                "Live extraction transport must preserve every authorized packet "
                "and evidence item without hidden truncation or omission"
            )
        result, receipt = self._run(
            paths=self._paths,
            role=self._role,
            prompt_name="extraction.md",
            system_prompt_name="extraction-system.md",
            evidence=evidence,
            run_id=f"{self._run_id}-call-{self._call_index:04d}",
            max_packet_chars=self._max_packet_chars,
            max_evidence_chars=self._max_evidence_chars,
            schema_name="extraction-output.schema.json",
            use_cache=self._use_cache,
        )
        return {
            "candidates": list(result.get("candidates", [])),
            "usage": usage_from_receipt(receipt) or {},
        }
