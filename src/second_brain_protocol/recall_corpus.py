from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError

from .canonical_paths import is_indexable_markdown
from .config import protocol_root
from .recall_benchmark import RecallBenchmarkCase, RecallBenchmarkSuite
from .recall_harness import RecallCandidate, RecallHarness, RecallRequest


class _CorpusSearchBackend:
    def __init__(self, results: dict[str, tuple[RecallCandidate, ...]]) -> None:
        self._results = results

    def search(self, query: str, *, limit: int) -> tuple[RecallCandidate, ...]:
        return self._results.get(query, ())[:limit]


class _CorpusGraphBackend:
    def __init__(
        self,
        results: dict[
            str, tuple[tuple[str, ...], tuple[RecallCandidate, ...]]
        ],
    ) -> None:
        self._results = results

    def expand(
        self,
        query: str,
        *,
        seed_source_ids: tuple[str, ...],
        limit: int,
    ) -> tuple[RecallCandidate, ...]:
        fixture = self._results.get(query)
        if fixture is None:
            return ()
        expected_seeds, candidates = fixture
        if (
            len(seed_source_ids) != len(expected_seeds)
            or frozenset(seed_source_ids) != frozenset(expected_seeds)
        ):
            raise ValueError("Recall corpus graph seed mismatch")
        return candidates[:limit]


@dataclass(frozen=True)
class RecallCorpus:
    name: str
    suite: RecallBenchmarkSuite
    _lexical: dict[str, tuple[RecallCandidate, ...]]
    _vector: dict[str, tuple[RecallCandidate, ...]]
    _graph: dict[str, tuple[tuple[str, ...], tuple[RecallCandidate, ...]]]

    def harness(self) -> RecallHarness:
        return RecallHarness(
            lexical=_CorpusSearchBackend(self._lexical),
            vector=_CorpusSearchBackend(self._vector),
            graph=_CorpusGraphBackend(self._graph),
        )


def load_recall_corpus(path: Path) -> RecallCorpus:
    payload = json.loads(path.read_text(encoding="utf-8"))
    _corpus_validator().validate(payload)
    cases: list[RecallBenchmarkCase] = []
    channels: dict[str, dict[str, tuple[RecallCandidate, ...]]] = {
        "lexical": {},
        "vector": {},
    }
    graph_fixtures: dict[
        str, tuple[tuple[str, ...], tuple[RecallCandidate, ...]]
    ] = {}
    case_names: set[str] = set()
    for raw_case in payload["cases"]:
        name = raw_case["name"]
        if name in case_names:
            raise ValueError(f"Duplicate recall case name: {name}")
        case_names.add(name)
        query = raw_case["query"]
        expected_source_ids = tuple(raw_case["expected_source_ids"])
        forbidden_source_ids = tuple(raw_case.get("forbidden_source_ids", []))
        if set(expected_source_ids) & set(forbidden_source_ids):
            raise ValueError(
                f"Recall case {name!r} has expected and forbidden source overlap"
            )
        request = RecallRequest(
            query=query,
            max_results=raw_case.get("max_results", 8),
            max_packet_chars=raw_case.get("max_packet_chars", 6000),
            max_packet_tokens=raw_case.get("max_packet_tokens", 1500),
            include_graph=raw_case["include_graph"],
            use_lexical=raw_case.get("use_lexical", True),
            use_vector=raw_case.get("use_vector", True),
        )
        cases.append(
            RecallBenchmarkCase(
                name=name,
                request=request,
                expected_source_ids=expected_source_ids,
                forbidden_source_ids=forbidden_source_ids,
                comparison_key=raw_case.get("comparison_key"),
                comparison_kind=raw_case.get("comparison_kind"),
            )
        )
        for channel in channels:
            fixture = tuple(
                _candidate(candidate) for candidate in raw_case[channel]
            )
            existing = channels[channel].get(query)
            if existing is not None and existing != fixture:
                raise ValueError(
                    f"Recall query {query!r} has conflicting {channel} fixtures"
                )
            channels[channel][query] = fixture
        graph_candidates = tuple(
            _candidate(candidate) for candidate in raw_case["graph"]
        )
        graph_fixture = (
            tuple(raw_case.get("graph_seed_source_ids", [])),
            graph_candidates,
        )
        existing_graph = graph_fixtures.get(query)
        if existing_graph is not None and existing_graph != graph_fixture:
            raise ValueError(
                f"Recall query {query!r} has conflicting graph fixtures"
            )
        graph_fixtures[query] = graph_fixture
    return RecallCorpus(
        name=payload["name"],
        suite=RecallBenchmarkSuite(name=payload["name"], cases=tuple(cases)),
        _lexical=channels["lexical"],
        _vector=channels["vector"],
        _graph=graph_fixtures,
    )


def _candidate(raw: dict[str, object]) -> RecallCandidate:
    if not is_indexable_markdown(raw["note_path"]):
        raise ValidationError("Recall corpus candidate path is not canonical")
    score = raw["score"]
    if not math.isfinite(score):
        raise ValueError("Recall corpus candidate score must be finite")
    return RecallCandidate(
        source_id=raw["source_id"],
        note_path=raw["note_path"],
        title=raw["title"],
        snippet=raw["snippet"],
        score=score,
        canonical=raw.get("canonical", True),
        relations=tuple(raw.get("relations", [])),
    )


def _corpus_validator() -> Draft202012Validator:
    schema_path = protocol_root() / "schemas" / "recall-corpus.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)
