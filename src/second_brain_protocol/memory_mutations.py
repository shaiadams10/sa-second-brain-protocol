from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Protocol

from .extraction_harness import ExtractionCandidate


KIND_LAYER = {
    "explicit_fact": "about-person",
    "voice_style": "about-person",
    "personality": "about-person",
    "goal": "about-person",
    "experience": "professional-profile",
    "education": "professional-profile",
    "military": "professional-profile",
    "preference": "operating-preferences",
    "work_style": "operating-preferences",
    "project_fact": "project-knowledge",
    "decision": "project-knowledge",
    "lesson": "project-knowledge",
}
KIND_DESTINATION = {
    "explicit_fact": "Memory/LongTermMemory.md",
    "project_fact": "Memory/LongTermMemory.md",
    "decision": "Memory/Decisions.md",
    "lesson": "Memory/Lessons.md",
    "preference": "Identity/Preferences.md",
    "work_style": "Identity/WorkStyle.md",
    "voice_style": "Identity/Voice.md",
    "personality": "Identity/Persona.md",
    "experience": "Experience/Employment.md",
    "education": "Experience/Education.md",
    "military": "Experience/MilitaryService.md",
    "goal": "Goals/ActiveGoals.md",
}


@dataclass(frozen=True)
class MemoryKey:
    layer: str
    scope: str
    project_id: str | None
    kind: str
    subject_key: str


@dataclass(frozen=True)
class MemoryHead:
    memory_id: str
    key: MemoryKey
    version_id: str
    version: int
    subject: str
    claim: str
    status: str
    reinforcement_count: int


@dataclass(frozen=True)
class PlannedMemoryMutation:
    candidate_index: int
    requested_operation: str
    mutation_id: str
    memory_id: str
    memory_key: MemoryKey
    layer: str
    destination: str
    version_id: str
    version: int
    expected_version_id: str | None
    expected_reinforcement_count: int
    creates_version: bool
    subject: str
    claim: str
    status: str
    disposition: str
    evidence_refs: tuple[str, ...]
    confidence: float
    explicit: bool
    reinforcement_count: int
    supersedes_version_id: str | None
    contradicts_version_id: str | None


@dataclass(frozen=True)
class MemoryPlanRejection:
    candidate_index: int
    code: str
    target_memory_id: str | None


@dataclass(frozen=True)
class MemoryMutationPlan:
    items: tuple[PlannedMemoryMutation, ...]
    rejections: tuple[MemoryPlanRejection, ...]


class MemoryCatalog(Protocol):
    def get(self, memory_id: str) -> MemoryHead | None: ...

    def find_by_key(self, key: MemoryKey) -> MemoryHead | None: ...


class InMemoryMemoryCatalog:
    def __init__(self, heads: tuple[MemoryHead, ...] = ()) -> None:
        self._by_id = {head.memory_id: head for head in heads}
        self._by_key = {head.key: head for head in heads}

    def get(self, memory_id: str) -> MemoryHead | None:
        return self._by_id.get(memory_id)

    def find_by_key(self, key: MemoryKey) -> MemoryHead | None:
        return self._by_key.get(key)


class MemoryMutationPlanner:
    """Turn accepted model proposals into deterministic entity/version mutations."""

    def __init__(self, *, catalog: MemoryCatalog) -> None:
        self._catalog = catalog

    def plan(
        self, candidates: tuple[ExtractionCandidate, ...]
    ) -> MemoryMutationPlan:
        items: list[PlannedMemoryMutation] = []
        rejections: list[MemoryPlanRejection] = []
        reserved_keys: set[MemoryKey] = set()
        reserved_memory_ids: set[str] = set()
        for index, candidate in enumerate(candidates):
            key = memory_key_for(candidate)
            if key in reserved_keys or (
                candidate.target_memory_id in reserved_memory_ids
            ):
                rejections.append(
                    MemoryPlanRejection(
                        candidate_index=index,
                        code="batch-memory-conflict",
                        target_memory_id=candidate.target_memory_id,
                    )
                )
                continue
            if candidate.operation == "create":
                if self._catalog.find_by_key(key) is not None:
                    rejections.append(
                        MemoryPlanRejection(
                            candidate_index=index,
                            code="duplicate-create-requires-target",
                            target_memory_id=None,
                        )
                    )
                    continue
                memory_id = _memory_id(key)
                planned = _planned_version(
                        candidate=candidate,
                        candidate_index=index,
                        key=key,
                        memory_id=memory_id,
                        version=1,
                        expected_version_id=None,
                        expected_reinforcement_count=0,
                        reinforcement_count=0,
                        supersedes_version_id=None,
                        contradicts_version_id=None,
                        status="active",
                    )
                items.append(planned)
                reserved_keys.add(key)
                reserved_memory_ids.add(planned.memory_id)
                continue

            existing = (
                self._catalog.get(candidate.target_memory_id)
                if candidate.target_memory_id
                else None
            )
            if existing is None:
                rejections.append(
                    MemoryPlanRejection(
                        candidate_index=index,
                        code="unknown-target-memory",
                        target_memory_id=candidate.target_memory_id,
                    )
                )
                continue
            if existing.status not in {"active", "contested"}:
                rejections.append(
                    MemoryPlanRejection(
                        candidate_index=index,
                        code="inactive-target-memory",
                        target_memory_id=existing.memory_id,
                    )
                )
                continue
            if existing.key != key:
                rejections.append(
                    MemoryPlanRejection(
                        candidate_index=index,
                        code="target-memory-key-mismatch",
                        target_memory_id=existing.memory_id,
                    )
                )
                continue
            key = existing.key
            same_claim = _normalized_statement(candidate.claim) == _normalized_statement(
                existing.claim
            )
            if candidate.operation == "reinforce":
                if not same_claim:
                    rejections.append(
                        MemoryPlanRejection(
                            candidate_index=index,
                            code="reinforcement-claim-mismatch",
                            target_memory_id=existing.memory_id,
                        )
                    )
                    continue
                planned = _planned_reinforcement(
                        candidate=candidate,
                        candidate_index=index,
                        key=key,
                        existing=existing,
                    )
                items.append(planned)
                reserved_keys.add(key)
                reserved_memory_ids.add(planned.memory_id)
                continue
            if same_claim:
                rejections.append(
                    MemoryPlanRejection(
                        candidate_index=index,
                        code="non-changing-mutation",
                        target_memory_id=existing.memory_id,
                    )
                )
                continue
            contradicts = (
                existing.version_id if candidate.operation == "contradict" else None
            )
            supersedes = (
                existing.version_id
                if candidate.operation in {"update", "supersede"}
                else None
            )
            planned = _planned_version(
                    candidate=candidate,
                    candidate_index=index,
                    key=key,
                    memory_id=existing.memory_id,
                    version=existing.version + 1,
                    expected_version_id=existing.version_id,
                    expected_reinforcement_count=existing.reinforcement_count,
                    reinforcement_count=0,
                    supersedes_version_id=supersedes,
                    contradicts_version_id=contradicts,
                    status="contested" if contradicts else "active",
                )
            items.append(planned)
            reserved_keys.add(key)
            reserved_memory_ids.add(planned.memory_id)
        return MemoryMutationPlan(items=tuple(items), rejections=tuple(rejections))


def memory_key_for(candidate: ExtractionCandidate) -> MemoryKey:
    layer = KIND_LAYER[candidate.kind]
    return MemoryKey(
        layer=layer,
        scope=candidate.scope,
        project_id=candidate.project_id,
        kind=candidate.kind,
        subject_key=_normalized_subject(candidate.subject),
    )


def _planned_version(
    *,
    candidate: ExtractionCandidate,
    candidate_index: int,
    key: MemoryKey,
    memory_id: str,
    version: int,
    expected_version_id: str | None,
    expected_reinforcement_count: int,
    reinforcement_count: int,
    supersedes_version_id: str | None,
    contradicts_version_id: str | None,
    status: str,
) -> PlannedMemoryMutation:
    version_id = _version_id(
        memory_id=memory_id,
        version=version,
        claim=candidate.claim,
        evidence_refs=candidate.evidence_refs,
        operation=candidate.operation,
    )
    return PlannedMemoryMutation(
        candidate_index=candidate_index,
        requested_operation=candidate.operation,
        mutation_id=_mutation_id(
            memory_id=memory_id,
            operation=candidate.operation,
            claim=candidate.claim,
            evidence_refs=candidate.evidence_refs,
            expected_version_id=expected_version_id,
            expected_reinforcement_count=expected_reinforcement_count,
        ),
        memory_id=memory_id,
        memory_key=key,
        layer=key.layer,
        destination=KIND_DESTINATION[candidate.kind],
        version_id=version_id,
        version=version,
        expected_version_id=expected_version_id,
        expected_reinforcement_count=expected_reinforcement_count,
        creates_version=True,
        subject=candidate.subject,
        claim=candidate.claim,
        status=status,
        disposition="review",
        evidence_refs=tuple(sorted(candidate.evidence_refs)),
        confidence=candidate.confidence,
        explicit=candidate.explicit,
        reinforcement_count=reinforcement_count,
        supersedes_version_id=supersedes_version_id,
        contradicts_version_id=contradicts_version_id,
    )


def _planned_reinforcement(
    *,
    candidate: ExtractionCandidate,
    candidate_index: int,
    key: MemoryKey,
    existing: MemoryHead,
) -> PlannedMemoryMutation:
    return PlannedMemoryMutation(
        candidate_index=candidate_index,
        requested_operation="reinforce",
        mutation_id=_mutation_id(
            memory_id=existing.memory_id,
            operation="reinforce",
            claim=candidate.claim,
            evidence_refs=candidate.evidence_refs,
            expected_version_id=existing.version_id,
            expected_reinforcement_count=existing.reinforcement_count,
        ),
        memory_id=existing.memory_id,
        memory_key=key,
        layer=key.layer,
        destination=KIND_DESTINATION[candidate.kind],
        version_id=existing.version_id,
        version=existing.version,
        expected_version_id=existing.version_id,
        expected_reinforcement_count=existing.reinforcement_count,
        creates_version=False,
        subject=candidate.subject,
        claim=candidate.claim,
        status=existing.status,
        disposition="review",
        evidence_refs=tuple(sorted(candidate.evidence_refs)),
        confidence=candidate.confidence,
        explicit=candidate.explicit,
        reinforcement_count=existing.reinforcement_count + 1,
        supersedes_version_id=None,
        contradicts_version_id=None,
    )


def _memory_id(key: MemoryKey) -> str:
    return "memory-" + _digest(
        {
            "layer": key.layer,
            "scope": key.scope,
            "project_id": key.project_id,
            "kind": key.kind,
            "subject_key": key.subject_key,
        }
    )


def _version_id(
    *,
    memory_id: str,
    version: int,
    claim: str,
    evidence_refs: tuple[str, ...],
    operation: str,
) -> str:
    return "memory-version-" + _digest(
        {
            "memory_id": memory_id,
            "version": version,
            "claim": _normalized_statement(claim),
            "evidence_refs": sorted(evidence_refs),
            "operation": operation,
        }
    )


def _mutation_id(
    *,
    memory_id: str,
    operation: str,
    claim: str,
    evidence_refs: tuple[str, ...],
    expected_version_id: str | None,
    expected_reinforcement_count: int,
) -> str:
    return "memory-mutation-" + _digest(
        {
            "memory_id": memory_id,
            "operation": operation,
            "claim": _normalized_statement(claim),
            "evidence_refs": sorted(evidence_refs),
            "expected_version_id": expected_version_id,
            "expected_reinforcement_count": expected_reinforcement_count,
        }
    )


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()[:24]


def _normalized_subject(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"[^\w]+", "-", normalized, flags=re.UNICODE).strip("-")


def _normalized_statement(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())
