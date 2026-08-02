from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, replace
from pathlib import PurePosixPath
from typing import Protocol

from .canonical_paths import is_indexable_markdown
from .security import (
    assert_model_packet_safe,
    sanitize_external_text,
)


CHANNEL_ORDER = ("lexical", "vector", "graph")
RRF_OFFSET = 60
CANONICAL_SOURCE_ID = re.compile(r"^note-[a-z0-9][a-z0-9-]{2,95}$")
CANONICAL_SEGMENT_ID = re.compile(r"^segment-[a-f0-9]{24}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True)
class RecallRequest:
    query: str
    max_results: int = 8
    max_packet_chars: int = 6000
    max_packet_tokens: int = 1500
    use_lexical: bool = True
    use_vector: bool = True
    include_graph: bool = False

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("Recall query must not be empty")
        if min(self.max_results, self.max_packet_chars, self.max_packet_tokens) <= 0:
            raise ValueError("Recall bounds must be positive")
        if not (self.use_lexical or self.use_vector or self.include_graph):
            raise ValueError("At least one recall channel must be enabled")
        if self.include_graph and not (self.use_lexical or self.use_vector):
            raise ValueError("Graph recall requires a direct recall channel")


@dataclass(frozen=True)
class RecallCandidate:
    source_id: str
    note_path: str
    title: str
    snippet: str
    score: float
    canonical: bool
    relations: tuple[str, ...]
    segment_id: str | None = None
    content_hash: str | None = None


@dataclass(frozen=True)
class RecallHit:
    source_id: str
    note_path: str
    title: str
    snippet: str
    score: float
    provenance: tuple[str, ...]
    relations: tuple[str, ...]
    segment_id: str | None = None
    content_hash: str | None = None


@dataclass(frozen=True)
class RecallPacket:
    query_hash: str
    hits: tuple[RecallHit, ...]
    total_candidates: int
    excluded_candidates: int
    used_chars: int
    estimated_tokens: int
    truncated: bool
    graph_used: bool
    executed_channels: tuple[str, ...]


class SearchBackend(Protocol):
    def search(self, query: str, *, limit: int) -> tuple[RecallCandidate, ...]: ...


class GraphBackend(Protocol):
    def expand(
        self,
        query: str,
        *,
        seed_source_ids: tuple[str, ...],
        limit: int,
    ) -> tuple[RecallCandidate, ...]: ...


@dataclass
class _FusedCandidate:
    candidate: RecallCandidate
    fused_score: float
    provenance: set[str]
    relations: set[str]
    raw_count: int


class RecallHarness:
    """Fuse bounded, canonical-only retrieval behind a read-only seam."""

    def __init__(
        self,
        *,
        lexical: SearchBackend,
        vector: SearchBackend,
        graph: GraphBackend | None = None,
    ) -> None:
        self._lexical = lexical
        self._vector = vector
        self._graph = graph

    def retrieve(self, request: RecallRequest) -> RecallPacket:
        channel_results: list[tuple[str, tuple[RecallCandidate, ...]]] = []
        executed_channels: list[str] = []
        if request.use_lexical:
            executed_channels.append("lexical")
            channel_results.append(
                (
                    "lexical",
                    self._lexical.search(
                        request.query, limit=request.max_results * 3
                    ),
                )
            )
        if request.use_vector:
            executed_channels.append("vector")
            channel_results.append(
                (
                    "vector",
                    self._vector.search(
                        request.query, limit=request.max_results * 3
                    ),
                )
            )
        fused: dict[str, _FusedCandidate] = {}
        poisoned_identities: set[str] = set()
        excluded = 0
        total = 0

        def add_channel(
            channel: str, candidates: tuple[RecallCandidate, ...]
        ) -> None:
            nonlocal excluded, total
            total += len(candidates)
            normalized, channel_excluded, raw_counts = _normalize_channel(candidates)
            excluded += channel_excluded
            for rank, candidate in enumerate(normalized, 1):
                identity = _candidate_key(candidate)
                raw_count = raw_counts[identity]
                if identity in poisoned_identities:
                    excluded += raw_count
                    continue
                contribution = 1.0 / (RRF_OFFSET + rank)
                existing = fused.get(identity)
                if existing is None:
                    fused[identity] = _FusedCandidate(
                        candidate=candidate,
                        fused_score=contribution,
                        provenance={channel},
                        relations=set(candidate.relations),
                        raw_count=raw_count,
                    )
                else:
                    if _candidate_binding(existing.candidate) != _candidate_binding(
                        candidate
                    ):
                        excluded += existing.raw_count + raw_count
                        del fused[identity]
                        poisoned_identities.add(identity)
                        continue
                    existing.fused_score += contribution
                    existing.provenance.add(channel)
                    existing.relations.update(candidate.relations)
                    existing.raw_count += raw_count

        for channel, candidates in channel_results:
            add_channel(channel, candidates)

        if request.include_graph and self._graph is None:
            raise ValueError("Graph recall was requested but no graph backend exists")
        graph_used = False
        if request.include_graph:
            seeds = tuple(
                item.candidate.source_id
                for item in _ranked(fused)[: request.max_results]
            )
            if seeds:
                graph_candidates = self._graph.expand(
                    request.query,
                    seed_source_ids=seeds,
                    limit=request.max_results * 3,
                )
                graph_used = True
                executed_channels.append("graph")
                add_channel("graph", graph_candidates)

        char_budget = min(request.max_packet_chars, request.max_packet_tokens * 4)
        hits: list[RecallHit] = []
        truncated = False
        query_hash = hashlib.sha256(request.query.strip().encode()).hexdigest()
        empty_packet = _build_packet(
            query_hash=query_hash,
            hits=(),
            total_candidates=total,
            excluded_candidates=excluded,
            truncated=False,
            graph_used=graph_used,
            executed_channels=tuple(executed_channels),
        )
        if empty_packet.used_chars > char_budget:
            raise ValueError("Recall packet budget is too small for its envelope")
        for item in _ranked(fused):
            if len(hits) >= request.max_results:
                truncated = True
                break
            candidate = item.candidate
            hit = RecallHit(
                source_id=candidate.source_id,
                note_path=_normalized_note_path(candidate.note_path),
                title=sanitize_external_text(candidate.title, max_chars=200),
                snippet=sanitize_external_text(candidate.snippet, max_chars=1200),
                score=round(item.fused_score, 8),
                provenance=tuple(
                    channel for channel in CHANNEL_ORDER if channel in item.provenance
                ),
                relations=tuple(
                    sorted(
                        sanitize_external_text(value, max_chars=200)
                        for value in item.relations
                    )
                )[:8],
                segment_id=candidate.segment_id,
                content_hash=candidate.content_hash,
            )
            fitted = _fit_hit_to_budget(
                hit=hit,
                existing_hits=tuple(hits),
                char_budget=char_budget,
                query_hash=query_hash,
                total_candidates=total,
                excluded_candidates=excluded,
                graph_used=graph_used,
                executed_channels=tuple(executed_channels),
            )
            if fitted is None:
                truncated = True
                continue
            if fitted != hit:
                truncated = True
            hits.append(fitted)
        packet = _build_packet(
            query_hash=query_hash,
            hits=tuple(hits),
            total_candidates=total,
            excluded_candidates=excluded,
            truncated=truncated,
            graph_used=graph_used,
            executed_channels=tuple(executed_channels),
        )
        if packet.used_chars > char_budget:
            raise AssertionError("Recall packet exceeded its validated budget")
        assert_model_packet_safe(asdict(packet))
        return packet


def _ranked(fused: dict[str, _FusedCandidate]) -> list[_FusedCandidate]:
    return sorted(
        fused.values(),
        key=lambda item: (-item.fused_score, item.candidate.source_id),
    )


def _candidate_is_safe(candidate: RecallCandidate) -> bool:
    segment_binding_valid = (
        candidate.segment_id is None
        and candidate.content_hash is None
    ) or (
        candidate.segment_id is not None
        and candidate.content_hash is not None
        and CANONICAL_SEGMENT_ID.fullmatch(candidate.segment_id) is not None
        and SHA256.fullmatch(candidate.content_hash) is not None
    )
    return bool(
        candidate.canonical
        and CANONICAL_SOURCE_ID.fullmatch(candidate.source_id)
        and candidate.snippet
        and isinstance(candidate.score, (int, float))
        and not isinstance(candidate.score, bool)
        and math.isfinite(float(candidate.score))
        and _note_path_is_relative(candidate.note_path)
        and segment_binding_valid
    )


def _note_path_is_relative(value: str) -> bool:
    return is_indexable_markdown(value)


def _normalized_note_path(value: str) -> str:
    return str(PurePosixPath(value.replace("\\", "/")))


def _normalize_channel(
    candidates: tuple[RecallCandidate, ...],
) -> tuple[list[RecallCandidate], int, dict[str, int]]:
    grouped: dict[str, list[RecallCandidate]] = {}
    for candidate in candidates:
        grouped.setdefault(_candidate_key(candidate), []).append(candidate)
    normalized: list[RecallCandidate] = []
    excluded = 0
    raw_counts: dict[str, int] = {}
    for identity, group in grouped.items():
        if not all(_candidate_is_safe(candidate) for candidate in group):
            excluded += len(group)
            continue
        identities = {
            (
                _normalized_note_path(candidate.note_path).casefold(),
                candidate.title,
                candidate.snippet,
                candidate.canonical,
                candidate.segment_id,
                candidate.content_hash,
            )
            for candidate in group
        }
        if len(identities) != 1:
            excluded += len(group)
            continue
        best = max(group, key=lambda candidate: candidate.score)
        normalized.append(
            replace(
                best,
                relations=tuple(
                    sorted(
                        {
                            relation
                            for candidate in group
                            for relation in candidate.relations
                        }
                    )
                ),
            )
        )
        raw_counts[identity] = len(group)
    normalized.sort(key=lambda candidate: (-candidate.score, candidate.source_id))
    return normalized, excluded, raw_counts


def _candidate_key(candidate: RecallCandidate) -> str:
    return candidate.segment_id or candidate.source_id


def _candidate_binding(candidate: RecallCandidate) -> tuple[str, str | None, str | None]:
    return (
        _normalized_note_path(candidate.note_path).casefold(),
        candidate.segment_id,
        candidate.content_hash,
    )


def _build_packet(
    *,
    query_hash: str,
    hits: tuple[RecallHit, ...],
    total_candidates: int,
    excluded_candidates: int,
    truncated: bool,
    graph_used: bool,
    executed_channels: tuple[str, ...],
) -> RecallPacket:
    packet = RecallPacket(
        query_hash=query_hash,
        hits=hits,
        total_candidates=total_candidates,
        excluded_candidates=excluded_candidates,
        used_chars=0,
        estimated_tokens=0,
        truncated=truncated,
        graph_used=graph_used,
        executed_channels=executed_channels,
    )
    for _ in range(12):
        serialized_chars = len(
            json.dumps(asdict(packet), ensure_ascii=False, separators=(",", ":"))
        )
        estimated_tokens = (serialized_chars + 3) // 4
        if (
            packet.used_chars == serialized_chars
            and packet.estimated_tokens == estimated_tokens
        ):
            return packet
        packet = replace(
            packet,
            used_chars=serialized_chars,
            estimated_tokens=estimated_tokens,
        )
    raise AssertionError("Recall packet size metadata did not converge")


def _fit_hit_to_budget(
    *,
    hit: RecallHit,
    existing_hits: tuple[RecallHit, ...],
    char_budget: int,
    query_hash: str,
    total_candidates: int,
    excluded_candidates: int,
    graph_used: bool,
    executed_channels: tuple[str, ...],
) -> RecallHit | None:
    def fits(candidate: RecallHit) -> bool:
        return (
            _build_packet(
                query_hash=query_hash,
                hits=(*existing_hits, candidate),
                total_candidates=total_candidates,
                excluded_candidates=excluded_candidates,
                truncated=False,
                graph_used=graph_used,
                executed_channels=executed_channels,
            ).used_chars
            <= char_budget
        )

    def fit_snippet(base: RecallHit) -> RecallHit | None:
        if fits(base):
            return base
        low = 0
        high = len(base.snippet)
        best: RecallHit | None = None
        while low <= high:
            midpoint = (low + high) // 2
            shortened = base.snippet[:midpoint].rstrip()
            shortened = (
                shortened + "…" if midpoint < len(base.snippet) else shortened
            )
            candidate = replace(base, snippet=shortened)
            if fits(candidate):
                best = candidate
                low = midpoint + 1
            else:
                high = midpoint - 1
        return best

    for relation_count in range(len(hit.relations), -1, -1):
        fitted = fit_snippet(
            replace(hit, relations=hit.relations[:relation_count])
        )
        if fitted is not None:
            return fitted

    low = 0
    high = len(hit.title)
    best: RecallHit | None = None
    while low <= high:
        midpoint = (low + high) // 2
        shortened = hit.title[:midpoint].rstrip()
        shortened = shortened + "…" if midpoint < len(hit.title) else shortened
        fitted = fit_snippet(replace(hit, title=shortened, relations=()))
        if fitted is not None:
            best = fitted
            low = midpoint + 1
        else:
            high = midpoint - 1
    return best
