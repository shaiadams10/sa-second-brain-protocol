import json
from dataclasses import asdict

import pytest

from second_brain_protocol.recall_harness import (
    RecallCandidate,
    RecallHarness,
    RecallRequest,
)


class StaticSearchBackend:
    def __init__(self, candidates: tuple[RecallCandidate, ...]) -> None:
        self.candidates = candidates
        self.queries: list[str] = []

    def search(self, query: str, *, limit: int) -> tuple[RecallCandidate, ...]:
        self.queries.append(query)
        return self.candidates[:limit]


class RecordingGraphBackend:
    def __init__(self, candidates: tuple[RecallCandidate, ...] = ()) -> None:
        self.candidates = candidates
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def expand(
        self,
        query: str,
        *,
        seed_source_ids: tuple[str, ...],
        limit: int,
    ) -> tuple[RecallCandidate, ...]:
        self.calls.append((query, seed_source_ids))
        return self.candidates[:limit]


def candidate(
    source_id: str,
    *,
    score: float,
    snippet: str,
    note_path: str = "Memory/Decisions.md",
    canonical: bool = True,
    title: str | None = None,
    relations: tuple[str, ...] = (),
) -> RecallCandidate:
    return RecallCandidate(
        source_id=source_id,
        note_path=note_path,
        title=title or source_id,
        snippet=snippet,
        score=score,
        canonical=canonical,
        relations=relations,
    )


def test_recall_fuses_duplicate_lexical_and_vector_hits_deterministically() -> None:
    shared = candidate(
        "note-decision-shadow-cutover",
        score=0.9,
        snippet="Keep the scheduler authoritative until shadow evaluation passes.",
    )
    lexical = StaticSearchBackend(
        (
            shared,
            candidate("note-unrelated", score=0.4, snippet="Unrelated secondary item."),
        )
    )
    vector = StaticSearchBackend(
        (
            shared,
            candidate("note-token-budget", score=0.7, snippet="Measure model token use."),
        )
    )

    packet = RecallHarness(lexical=lexical, vector=vector).retrieve(
        RecallRequest(query="shadow scheduler cutover", max_results=3)
    )

    assert [hit.source_id for hit in packet.hits][0] == shared.source_id
    assert packet.hits[0].provenance == ("lexical", "vector")
    assert len([hit for hit in packet.hits if hit.source_id == shared.source_id]) == 1
    assert packet.graph_used is False


def test_recall_keeps_graph_expansion_opt_in() -> None:
    graph = RecordingGraphBackend(
        (candidate("note-graph-neighbor", score=0.99, snippet="A graph neighbor."),)
    )
    harness = RecallHarness(
        lexical=StaticSearchBackend(
            (candidate("note-seed", score=0.8, snippet="The direct result."),)
        ),
        vector=StaticSearchBackend(()),
        graph=graph,
    )

    without_graph = harness.retrieve(RecallRequest(query="direct result"))
    with_graph = harness.retrieve(
        RecallRequest(query="direct result", include_graph=True)
    )

    assert graph.calls == [("direct result", ("note-seed",))]
    assert "note-graph-neighbor" not in {
        hit.source_id for hit in without_graph.hits
    }
    assert "note-graph-neighbor" in {hit.source_id for hit in with_graph.hits}
    assert with_graph.graph_used is True


def test_recall_excludes_noncanonical_and_absolute_path_results() -> None:
    lexical = StaticSearchBackend(
        (
            candidate(
                "raw-session",
                score=1.0,
                snippet="Raw transcript",
                canonical=False,
            ),
            candidate(
                "absolute-path",
                score=0.9,
                snippet="Local source",
                note_path="C:/Users/person/private.md",
            ),
            candidate(
                "C:/Users/person/raw-session.json",
                score=0.85,
                snippet="Canonical flag cannot make a path-shaped ID safe.",
            ),
            candidate(
                "note-sensitive-file",
                score=0.82,
                snippet="Sensitive identity artifact.",
                note_path="Identity/passport.md",
            ),
            candidate(
                "note-uri-path",
                score=0.81,
                snippet="A URI is not a canonical note path.",
                note_path="https://private.example/note.md",
            ),
            candidate(
                "note-raw-evidence",
                score=0.805,
                snippet="Raw evidence is never canonical recall.",
                note_path="Evidence/Raw/session.md",
            ),
            candidate(
                "note-hidden-runtime",
                score=0.803,
                snippet="Dependency docs are not canonical recall.",
                note_path="Protocol/.venv/package/README.md",
            ),
            candidate(
                "note-casefolded-raw",
                score=0.802,
                snippet="Windows path casing cannot bypass raw exclusion.",
                note_path="evidence/raw/session.md",
            ),
            candidate(
                "note-safe",
                score=0.8,
                snippet="Safe canonical context from C:\\Users\\person\\secret.txt",
            ),
        )
    )

    packet = RecallHarness(
        lexical=lexical,
        vector=StaticSearchBackend(()),
    ).retrieve(RecallRequest(query="safe context", max_packet_chars=1000))

    assert [hit.source_id for hit in packet.hits] == ["note-safe"]
    assert "C:\\Users" not in packet.hits[0].snippet
    assert packet.excluded_candidates == 8


def test_recall_can_ablate_lexical_and_vector_independently() -> None:
    lexical = StaticSearchBackend(
        (candidate("note-lexical", score=0.9, snippet="lexical"),)
    )
    vector = StaticSearchBackend(
        (candidate("note-vector", score=0.9, snippet="vector"),)
    )
    harness = RecallHarness(lexical=lexical, vector=vector)

    packet = harness.retrieve(
        RecallRequest(query="ablation", use_lexical=True, use_vector=False)
    )

    assert lexical.queries == ["ablation"]
    assert vector.queries == []
    assert [hit.source_id for hit in packet.hits] == ["note-lexical"]


def test_duplicate_source_within_one_channel_contributes_only_once() -> None:
    shared = candidate("note-shared", score=0.9, snippet="shared")
    single = RecallHarness(
        lexical=StaticSearchBackend((shared,)),
        vector=StaticSearchBackend(()),
    ).retrieve(RecallRequest(query="single"))
    duplicated = RecallHarness(
        lexical=StaticSearchBackend((shared, shared)),
        vector=StaticSearchBackend(()),
    ).retrieve(RecallRequest(query="duplicate"))

    assert duplicated.hits[0].score == single.hits[0].score


def test_conflicting_source_identity_across_channels_is_excluded() -> None:
    packet = RecallHarness(
        lexical=StaticSearchBackend(
            (
                candidate(
                    "note-conflict",
                    score=0.9,
                    snippet="lexical view",
                    note_path="Memory/One.md",
                ),
            )
        ),
        vector=StaticSearchBackend(
            (
                candidate(
                    "note-conflict",
                    score=0.8,
                    snippet="vector view",
                    note_path="Memory/Two.md",
                ),
            )
        ),
    ).retrieve(RecallRequest(query="conflict"))

    assert packet.hits == ()
    assert packet.excluded_candidates == 2


def test_case_only_note_path_variants_share_one_source_identity() -> None:
    packet = RecallHarness(
        lexical=StaticSearchBackend(
            (
                candidate(
                    "note-case-identity",
                    score=0.9,
                    snippet="lexical view",
                    note_path="Memory/Decisions.md",
                ),
            )
        ),
        vector=StaticSearchBackend(
            (
                candidate(
                    "note-case-identity",
                    score=0.8,
                    snippet="vector view",
                    note_path="memory/decisions.md",
                ),
            )
        ),
    ).retrieve(RecallRequest(query="same windows note"))

    assert len(packet.hits) == 1
    assert packet.hits[0].provenance == ("lexical", "vector")


def test_graph_does_not_execute_without_safe_direct_seeds() -> None:
    graph = RecordingGraphBackend(
        (candidate("note-neighbor", score=0.8, snippet="neighbor"),)
    )
    packet = RecallHarness(
        lexical=StaticSearchBackend(()),
        vector=StaticSearchBackend(()),
        graph=graph,
    ).retrieve(RecallRequest(query="no seeds", include_graph=True))

    assert graph.calls == []
    assert packet.graph_used is False


def test_graph_only_request_is_rejected() -> None:
    with pytest.raises(ValueError, match="direct recall channel"):
        RecallRequest(
            query="graph only",
            use_lexical=False,
            use_vector=False,
            include_graph=True,
        )


def test_recall_packet_size_includes_complete_serialized_envelope() -> None:
    packet = RecallHarness(
        lexical=StaticSearchBackend(
            (candidate("note-sized", score=0.9, snippet="bounded packet"),)
        ),
        vector=StaticSearchBackend(()),
    ).retrieve(RecallRequest(query="size", max_packet_chars=1000))

    encoded = json.dumps(asdict(packet), ensure_ascii=False, separators=(",", ":"))
    assert packet.used_chars == len(encoded)
    assert packet.used_chars <= 1000
    assert packet.estimated_tokens == (packet.used_chars + 3) // 4


def test_packet_budget_truncates_top_hit_instead_of_skipping_it() -> None:
    packet = RecallHarness(
        lexical=StaticSearchBackend(
            (
                candidate("note-top-ranked", score=1.0, snippet="top " * 1000),
                candidate("note-lower-ranked", score=0.5, snippet="small"),
            )
        ),
        vector=StaticSearchBackend(()),
    ).retrieve(
        RecallRequest(
            query="bounded priority",
            max_results=2,
            max_packet_chars=700,
            max_packet_tokens=175,
        )
    )

    assert packet.hits[0].source_id == "note-top-ranked"
    assert len(packet.hits[0].snippet) < 1200
    assert packet.used_chars <= 700
    assert packet.truncated is True


def test_packet_budget_discards_optional_metadata_before_top_hit() -> None:
    packet = RecallHarness(
        lexical=StaticSearchBackend(
            (
                candidate(
                    "note-top-metadata",
                    score=1.0,
                    snippet="important",
                    title="large title " * 30,
                    relations=tuple("relation " * 40 for _ in range(8)),
                ),
                candidate("note-small-lower", score=0.5, snippet="small"),
            )
        ),
        vector=StaticSearchBackend(()),
    ).retrieve(
        RecallRequest(
            query="metadata priority",
            max_results=2,
            max_packet_chars=650,
            max_packet_tokens=163,
        )
    )

    assert packet.hits[0].source_id == "note-top-metadata"
    assert packet.used_chars <= 650
    assert packet.truncated is True
