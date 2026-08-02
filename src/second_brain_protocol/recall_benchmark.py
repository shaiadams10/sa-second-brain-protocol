from __future__ import annotations

from dataclasses import dataclass, replace
from time import perf_counter
from typing import Callable, Protocol

from .recall_harness import RecallPacket, RecallRequest


@dataclass(frozen=True)
class RecallBenchmarkCase:
    name: str
    request: RecallRequest
    expected_source_ids: tuple[str, ...]
    forbidden_source_ids: tuple[str, ...] = ()
    comparison_key: str | None = None
    comparison_kind: str | None = None


@dataclass(frozen=True)
class RecallBenchmarkSuite:
    name: str
    cases: tuple[RecallBenchmarkCase, ...]


@dataclass(frozen=True)
class RecallBenchmarkCaseResult:
    name: str
    passed: bool
    source_recall: float
    irrelevant_context_rate: float
    latency_ms: float
    packet_chars: int
    estimated_tokens: int
    graph_used: bool
    executed_channels: tuple[str, ...]
    missing_source_ids: tuple[str, ...]
    forbidden_source_ids: tuple[str, ...]


@dataclass(frozen=True)
class RecallBenchmarkReport:
    suite: str
    passed: bool
    cases: tuple[RecallBenchmarkCaseResult, ...]
    source_recall: float
    irrelevant_context_rate: float
    average_latency_ms: float
    average_packet_chars: float
    average_estimated_tokens: float
    lexical_recall_lift: float | None
    vector_recall_lift: float | None
    graph_recall_lift: float | None


class Retriever(Protocol):
    def retrieve(self, request: RecallRequest) -> RecallPacket: ...


class RecallBenchmark:
    """Measure recall quality, context noise, latency, and packet cost."""

    def __init__(
        self,
        *,
        retriever: Retriever,
        clock: Callable[[], float] = perf_counter,
    ) -> None:
        self._retriever = retriever
        self._clock = clock

    def evaluate(self, suite: RecallBenchmarkSuite) -> RecallBenchmarkReport:
        results: list[RecallBenchmarkCaseResult] = []
        expected_total = 0
        relevant_total = 0
        returned_total = 0
        for case in suite.cases:
            started = self._clock()
            packet = self._retriever.retrieve(case.request)
            latency_ms = (self._clock() - started) * 1000
            actual = {hit.source_id for hit in packet.hits}
            expected = set(case.expected_source_ids)
            forbidden = actual & set(case.forbidden_source_ids)
            relevant = actual & expected
            missing = expected - actual
            irrelevant = actual - expected
            expected_total += len(expected)
            relevant_total += len(relevant)
            returned_total += len(actual)
            results.append(
                RecallBenchmarkCaseResult(
                    name=case.name,
                    passed=not missing and not forbidden and not irrelevant,
                    source_recall=(len(relevant) / len(expected) if expected else 1.0),
                    irrelevant_context_rate=(
                        len(irrelevant) / len(actual) if actual else 0.0
                    ),
                    latency_ms=round(latency_ms, 6),
                    packet_chars=packet.used_chars,
                    estimated_tokens=packet.estimated_tokens,
                    graph_used=packet.graph_used,
                    executed_channels=packet.executed_channels,
                    missing_source_ids=tuple(sorted(missing)),
                    forbidden_source_ids=tuple(sorted(forbidden)),
                )
            )
        lifts = _paired_recall_lifts(suite, tuple(results))
        return RecallBenchmarkReport(
            suite=suite.name,
            passed=all(result.passed for result in results),
            cases=tuple(results),
            source_recall=(
                relevant_total / expected_total if expected_total else 1.0
            ),
            irrelevant_context_rate=(
                (returned_total - relevant_total) / returned_total
                if returned_total
                else 0.0
            ),
            average_latency_ms=round(
                sum(result.latency_ms for result in results) / len(results), 6
            )
            if results
            else 0.0,
            average_packet_chars=(
                sum(result.packet_chars for result in results) / len(results)
                if results
                else 0.0
            ),
            average_estimated_tokens=(
                sum(result.estimated_tokens for result in results) / len(results)
                if results
                else 0.0
            ),
            lexical_recall_lift=lifts["lexical"],
            vector_recall_lift=lifts["vector"],
            graph_recall_lift=lifts["graph"],
        )


def _paired_recall_lifts(
    suite: RecallBenchmarkSuite,
    results: tuple[RecallBenchmarkCaseResult, ...],
) -> dict[str, float | None]:
    groups: dict[str, list[tuple[RecallBenchmarkCase, RecallBenchmarkCaseResult]]] = {}
    for case, result in zip(suite.cases, results, strict=True):
        if case.comparison_key:
            groups.setdefault(case.comparison_key, []).append((case, result))
    lifts: dict[str, list[float]] = {
        "lexical": [],
        "vector": [],
        "graph": [],
    }
    for key, pairs in groups.items():
        if len(pairs) != 2:
            raise ValueError(f"Recall comparison {key!r} must contain exactly two cases")
        kinds = {pair[0].comparison_kind or "graph" for pair in pairs}
        if len(kinds) != 1 or next(iter(kinds)) not in lifts:
            raise ValueError(f"Recall comparison {key!r} has an invalid kind")
        kind = next(iter(kinds))
        field = {
            "lexical": "use_lexical",
            "vector": "use_vector",
            "graph": "include_graph",
        }[kind]
        disabled = next(
            (pair for pair in pairs if not getattr(pair[0].request, field)), None
        )
        enabled = next(
            (pair for pair in pairs if getattr(pair[0].request, field)), None
        )
        if disabled is None or enabled is None:
            raise ValueError(
                f"Recall comparison {key!r} needs disabled and enabled {kind} cases"
            )
        disabled_case, disabled_result = disabled
        enabled_case, enabled_result = enabled
        comparable_request = (
            disabled_case.request
            == replace(enabled_case.request, **{field: False})
            and disabled_case.expected_source_ids == enabled_case.expected_source_ids
            and disabled_case.forbidden_source_ids
            == enabled_case.forbidden_source_ids
        )
        if not comparable_request:
            raise ValueError(f"Recall comparison {key!r} is confounded")
        if kind != "graph" and enabled_case.request.include_graph:
            raise ValueError(
                f"Recall comparison {key!r} mixes {kind} and graph recall"
            )
        if kind == "graph" and not enabled_result.graph_used:
            raise ValueError(f"Recall comparison {key!r} did not execute graph recall")
        if kind == "graph" and disabled_result.graph_used:
            raise ValueError(
                f"Recall comparison {key!r} direct case executed graph recall"
            )
        if kind not in enabled_result.executed_channels:
            raise ValueError(
                f"Recall comparison {key!r} did not execute enabled {kind} recall"
            )
        if kind in disabled_result.executed_channels:
            raise ValueError(
                f"Recall comparison {key!r} executed disabled {kind} recall"
            )
        lifts[kind].append(
            enabled_result.source_recall - disabled_result.source_recall
        )
    return {
        kind: round(sum(values) / len(values), 6) if values else None
        for kind, values in lifts.items()
    }
