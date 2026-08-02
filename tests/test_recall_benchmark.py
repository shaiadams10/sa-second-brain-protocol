import pytest

from second_brain_protocol.recall_benchmark import (
    RecallBenchmark,
    RecallBenchmarkCase,
    RecallBenchmarkSuite,
)
from second_brain_protocol.recall_harness import RecallHit, RecallPacket, RecallRequest


class ScriptedRetriever:
    def __init__(self, packets: dict[tuple[object, ...], RecallPacket]) -> None:
        self.packets = packets

    def retrieve(self, request: RecallRequest) -> RecallPacket:
        exact = (
            request.query,
            request.use_lexical,
            request.use_vector,
            request.include_graph,
        )
        if exact in self.packets:
            return self.packets[exact]
        return self.packets[(request.query, request.include_graph)]


def packet(
    *source_ids: str,
    graph_used: bool,
    executed_channels: tuple[str, ...] | None = None,
) -> RecallPacket:
    return RecallPacket(
        query_hash="hash",
        hits=tuple(
            RecallHit(
                source_id=source_id,
                note_path="Memory/Decisions.md",
                title=source_id,
                snippet="bounded",
                score=0.1,
                provenance=("graph",) if graph_used else ("lexical",),
                relations=(),
            )
            for source_id in source_ids
        ),
        total_candidates=len(source_ids),
        excluded_candidates=0,
        used_chars=100 * len(source_ids),
        estimated_tokens=25 * len(source_ids),
        truncated=False,
        graph_used=graph_used,
        executed_channels=(
            executed_channels
            if executed_channels is not None
            else ("lexical", "vector", *(("graph",) if graph_used else ()))
        ),
    )


def test_recall_benchmark_reports_recall_noise_latency_size_and_graph_lift() -> None:
    clocks = iter((1.0, 1.01, 2.0, 2.02))
    benchmark = RecallBenchmark(
        retriever=ScriptedRetriever(
            {
                ("same query", False): packet("note-a", "noise", graph_used=False),
                ("same query", True): packet("note-a", "note-b", graph_used=True),
            }
        ),
        clock=lambda: next(clocks),
    )
    suite = RecallBenchmarkSuite(
        name="recall-ablation",
        cases=(
            RecallBenchmarkCase(
                name="without graph",
                request=RecallRequest(query="same query", include_graph=False),
                expected_source_ids=("note-a", "note-b"),
                comparison_key="same-query",
            ),
            RecallBenchmarkCase(
                name="with graph",
                request=RecallRequest(query="same query", include_graph=True),
                expected_source_ids=("note-a", "note-b"),
                comparison_key="same-query",
            ),
        ),
    )

    report = benchmark.evaluate(suite)

    assert report.source_recall == 0.75
    assert report.irrelevant_context_rate == 0.25
    assert report.average_latency_ms == 15.0
    assert report.average_packet_chars == 200.0
    assert report.graph_recall_lift == 0.5
    assert report.lexical_recall_lift is None
    assert report.vector_recall_lift is None
    assert report.passed is False


def test_recall_benchmark_reports_lexical_and_vector_ablation_lifts() -> None:
    packets = {
        ("lexical query", False): packet("note-a", graph_used=False),
        ("vector query", False): packet("note-a", graph_used=False),
        ("lexical query", False, True, False): packet(
            "note-a", graph_used=False, executed_channels=("vector",)
        ),
        ("lexical query", True, True, False): packet(
            "note-a", "note-b", graph_used=False
        ),
        ("vector query", True, False, False): packet(
            "note-a", graph_used=False, executed_channels=("lexical",)
        ),
        ("vector query", True, True, False): packet(
            "note-a", "note-b", graph_used=False
        ),
    }
    suite = RecallBenchmarkSuite(
        name="channel-ablation",
        cases=(
            RecallBenchmarkCase(
                name="lexical disabled",
                request=RecallRequest(
                    query="lexical query", use_lexical=False, use_vector=True
                ),
                expected_source_ids=("note-a", "note-b"),
                comparison_key="lexical",
                comparison_kind="lexical",
            ),
            RecallBenchmarkCase(
                name="lexical enabled",
                request=RecallRequest(query="lexical query"),
                expected_source_ids=("note-a", "note-b"),
                comparison_key="lexical",
                comparison_kind="lexical",
            ),
            RecallBenchmarkCase(
                name="vector disabled",
                request=RecallRequest(
                    query="vector query", use_lexical=True, use_vector=False
                ),
                expected_source_ids=("note-a", "note-b"),
                comparison_key="vector",
                comparison_kind="vector",
            ),
            RecallBenchmarkCase(
                name="vector enabled",
                request=RecallRequest(query="vector query"),
                expected_source_ids=("note-a", "note-b"),
                comparison_key="vector",
                comparison_kind="vector",
            ),
        ),
    )

    report = RecallBenchmark(retriever=ScriptedRetriever(packets)).evaluate(suite)

    assert report.lexical_recall_lift == 0.5
    assert report.vector_recall_lift == 0.5
    assert report.graph_recall_lift is None


def test_graph_comparison_rejects_different_packet_budgets() -> None:
    benchmark = RecallBenchmark(
        retriever=ScriptedRetriever(
            {
                ("same query", False): packet("note-a", graph_used=False),
                ("same query", True): packet("note-a", graph_used=True),
            }
        )
    )
    suite = RecallBenchmarkSuite(
        name="confounded",
        cases=(
            RecallBenchmarkCase(
                name="direct",
                request=RecallRequest(
                    query="same query", include_graph=False, max_packet_chars=1000
                ),
                expected_source_ids=("note-a",),
                comparison_key="pair",
            ),
            RecallBenchmarkCase(
                name="graph",
                request=RecallRequest(
                    query="same query", include_graph=True, max_packet_chars=2000
                ),
                expected_source_ids=("note-a",),
                comparison_key="pair",
            ),
        ),
    )

    with pytest.raises(ValueError, match="confounded"):
        benchmark.evaluate(suite)


def test_graph_comparison_rejects_direct_packet_marked_graph_used() -> None:
    benchmark = RecallBenchmark(
        retriever=ScriptedRetriever(
            {
                ("same query", False): packet("note-a", graph_used=True),
                ("same query", True): packet("note-a", graph_used=True),
            }
        )
    )
    suite = RecallBenchmarkSuite(
        name="invalid-execution",
        cases=(
            RecallBenchmarkCase(
                name="direct",
                request=RecallRequest(query="same query", include_graph=False),
                expected_source_ids=("note-a",),
                comparison_key="pair",
            ),
            RecallBenchmarkCase(
                name="graph",
                request=RecallRequest(query="same query", include_graph=True),
                expected_source_ids=("note-a",),
                comparison_key="pair",
            ),
        ),
    )

    with pytest.raises(ValueError, match="direct case executed graph recall"):
        benchmark.evaluate(suite)
