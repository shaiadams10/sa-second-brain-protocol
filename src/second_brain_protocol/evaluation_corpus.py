from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from .config import protocol_root
from .evaluation_harness import (
    EvaluationCase,
    EvaluationSuite,
    ExpectedCandidate,
)
from .extraction_harness import (
    ExtractionLimits,
    ExtractionRequest,
    _candidate_schema_validator,
    _model_requests,
)


@dataclass(frozen=True)
class _ReplayCase:
    root_evidence_id: str
    route_evidence_ids: frozenset[str]
    responses: tuple[Mapping[str, Any], ...]


class ReplayExtractionModel:
    """Replay versioned model responses without external calls."""

    def __init__(self, cases: tuple[_ReplayCase, ...]) -> None:
        self._cases = cases
        self._responses = {case.root_evidence_id: case.responses for case in cases}
        self._positions = {case.root_evidence_id: 0 for case in cases}

    def extract(self, request: ExtractionRequest) -> Mapping[str, Any]:
        roots: set[str] = set()
        for evidence in request.evidence:
            payload = evidence.get("payload")
            source_ids = (
                payload.get("source_evidence_ids")
                if isinstance(payload, Mapping)
                else None
            )
            if isinstance(source_ids, list):
                roots.update(str(value) for value in source_ids if value)
            roots.add(str(evidence["id"]))
        matching_cases = [
            case for case in self._cases if roots & case.route_evidence_ids
        ]
        if len(matching_cases) != 1:
            raise RuntimeError("Replay request did not match exactly one corpus case")
        root = matching_cases[0].root_evidence_id
        position = self._positions[root]
        responses = self._responses[root]
        if position >= len(responses):
            raise RuntimeError("Replay corpus has no response for this model call")
        self._positions[root] = position + 1
        response = json.loads(json.dumps(responses[position]))
        if "return_value" in response:
            return response["return_value"]
        error_type = response.pop("raises", None)
        if error_type:
            raise RuntimeError(f"replayed {error_type}")
        return response


@dataclass(frozen=True)
class EvaluationCorpus:
    name: str
    suite: EvaluationSuite
    _replay_cases: tuple[_ReplayCase, ...]
    _live_case_names: frozenset[str]

    @property
    def live_case_count(self) -> int:
        return len(self._live_case_names)

    @property
    def live_suite(self) -> EvaluationSuite:
        return EvaluationSuite(
            name=f"{self.name}-live",
            cases=tuple(
                replace(case, candidate_match="concept")
                for case in self.suite.cases
                if case.name in self._live_case_names
            ),
        )

    def replay_model(self) -> ReplayExtractionModel:
        return ReplayExtractionModel(self._replay_cases)


def load_evaluation_corpus(path: Path) -> EvaluationCorpus:
    payload = json.loads(path.read_text(encoding="utf-8"))
    validator = _corpus_validator()
    validator.validate(payload)
    cases: list[EvaluationCase] = []
    replay_cases: list[_ReplayCase] = []
    live_case_names: set[str] = set()
    for raw_case in payload["cases"]:
        limits = raw_case.get("limits") or {}
        expected = raw_case["expected"]
        request = ExtractionRequest(
            run_kind=raw_case["run_kind"],
            evidence=tuple(raw_case["evidence"]),
            limits=ExtractionLimits(**limits),
        )
        expected_candidates: list[ExpectedCandidate] = []
        for candidate in expected["candidates"]:
            _candidate_schema_validator().validate(candidate)
            expected_candidates.append(
                ExpectedCandidate(
                    candidate_type=candidate["type"],
                    operation=candidate["operation"],
                    kind=candidate["kind"],
                    subject=candidate["subject"],
                    claim=candidate["claim"],
                    scope=candidate["scope"],
                    project_id=candidate.get("project_id"),
                    target_memory_id=candidate.get("target_memory_id"),
                    evidence_refs=tuple(candidate["evidence_refs"]),
                    confidence=float(candidate["confidence"]),
                    explicit=bool(candidate["explicit"]),
                )
            )
        meaning_terms = tuple(
            tuple(tuple(str(term) for term in alternatives) for alternatives in groups)
            for groups in raw_case.get("meaning_terms", [])
        )
        if raw_case.get("live_enabled") and len(meaning_terms) != len(
            expected_candidates
        ):
            raise ValueError(
                "Every live gold candidate requires deterministic meaning terms"
            )
        cases.append(
            EvaluationCase(
                name=raw_case["name"],
                request=request,
                expected_status=expected["status"],
                expected_candidates=tuple(expected_candidates),
                expected_rejection_codes=tuple(expected["rejection_codes"]),
                max_input_tokens=raw_case.get("max_input_tokens"),
                expected_failure_codes=tuple(expected.get("failure_codes", [])),
                candidate_meaning_terms=meaning_terms,
            )
        )
        root_evidence_id = str(raw_case["evidence"][0]["id"])
        model_requests, _coverage = _model_requests(request)
        route_evidence_ids = frozenset(
            str(item["id"])
            for model_request in model_requests
            for item in model_request.evidence
        )
        replay_cases.append(
            _ReplayCase(
                root_evidence_id=root_evidence_id,
                route_evidence_ids=route_evidence_ids | {root_evidence_id},
                responses=tuple(raw_case["scripted_responses"]),
            )
        )
        if raw_case.get("live_enabled"):
            live_case_names.add(raw_case["name"])
    return EvaluationCorpus(
        name=payload["name"],
        suite=EvaluationSuite(name=payload["name"], cases=tuple(cases)),
        _replay_cases=tuple(replay_cases),
        _live_case_names=frozenset(live_case_names),
    )


def _corpus_validator() -> Draft202012Validator:
    schema_path = protocol_root() / "schemas" / "evaluation-corpus.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)
