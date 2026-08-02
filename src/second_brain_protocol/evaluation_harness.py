from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Protocol

from .extraction_harness import ExtractionRequest, ExtractionResult


@dataclass(frozen=True)
class ExpectedCandidate:
    candidate_type: str
    operation: str
    kind: str
    subject: str
    claim: str
    scope: str
    project_id: str | None
    target_memory_id: str | None
    evidence_refs: tuple[str, ...]
    confidence: float
    explicit: bool


@dataclass(frozen=True)
class EvaluationCase:
    name: str
    request: ExtractionRequest
    expected_status: str
    expected_candidates: tuple[ExpectedCandidate, ...]
    expected_rejection_codes: tuple[str, ...]
    max_input_tokens: int | None = None
    expected_failure_codes: tuple[str, ...] = ()
    candidate_match: str = "exact"
    candidate_meaning_terms: tuple[tuple[tuple[str, ...], ...], ...] = ()


@dataclass(frozen=True)
class EvaluationSuite:
    name: str
    cases: tuple[EvaluationCase, ...]


@dataclass(frozen=True)
class EvaluationCaseResult:
    name: str
    passed: bool
    mismatches: tuple[str, ...]
    candidate_true_positives: int
    candidate_false_positives: int
    candidate_false_negatives: int
    policy_rejection_counts: tuple[tuple[str, int], ...]
    extraction_failure_counts: tuple[tuple[str, int], ...]
    source_evidence_count: int
    episode_count: int
    omitted_messages: int
    failed_episodes: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    model_calls: int


@dataclass(frozen=True)
class EvaluationReport:
    suite: str
    passed: bool
    total_cases: int
    passed_cases: int
    failed_cases: int
    cases: tuple[EvaluationCaseResult, ...]
    candidate_true_positives: int
    candidate_false_positives: int
    candidate_false_negatives: int
    candidate_precision: float
    candidate_recall: float
    policy_rejection_counts: tuple[tuple[str, int], ...]
    extraction_failure_counts: tuple[tuple[str, int], ...]
    source_evidence_count: int
    episode_count: int
    omitted_messages: int
    failed_episodes: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    model_calls: int


class Extractor(Protocol):
    def extract(self, request: ExtractionRequest) -> ExtractionResult: ...


class EvaluationHarness:
    """Evaluate extraction behavior through its public interface."""

    def __init__(self, *, extractor: Extractor) -> None:
        self._extractor = extractor

    def evaluate(self, suite: EvaluationSuite) -> EvaluationReport:
        case_results = tuple(self._evaluate_case(case) for case in suite.cases)
        passed_cases = sum(result.passed for result in case_results)
        true_positives = sum(
            result.candidate_true_positives for result in case_results
        )
        false_positives = sum(
            result.candidate_false_positives for result in case_results
        )
        false_negatives = sum(
            result.candidate_false_negatives for result in case_results
        )
        policy_rejections: Counter[str] = Counter()
        extraction_failures: Counter[str] = Counter()
        for result in case_results:
            policy_rejections.update(dict(result.policy_rejection_counts))
            extraction_failures.update(dict(result.extraction_failure_counts))
        return EvaluationReport(
            suite=suite.name,
            passed=passed_cases == len(case_results),
            total_cases=len(case_results),
            passed_cases=passed_cases,
            failed_cases=len(case_results) - passed_cases,
            cases=case_results,
            candidate_true_positives=true_positives,
            candidate_false_positives=false_positives,
            candidate_false_negatives=false_negatives,
            candidate_precision=(
                true_positives / (true_positives + false_positives)
                if true_positives + false_positives
                else 1.0
            ),
            candidate_recall=(
                true_positives / (true_positives + false_negatives)
                if true_positives + false_negatives
                else 1.0
            ),
            policy_rejection_counts=tuple(sorted(policy_rejections.items())),
            extraction_failure_counts=tuple(sorted(extraction_failures.items())),
            source_evidence_count=sum(
                result.source_evidence_count for result in case_results
            ),
            episode_count=sum(result.episode_count for result in case_results),
            omitted_messages=sum(
                result.omitted_messages for result in case_results
            ),
            failed_episodes=sum(result.failed_episodes for result in case_results),
            input_tokens=sum(result.input_tokens for result in case_results),
            output_tokens=sum(result.output_tokens for result in case_results),
            total_tokens=sum(result.total_tokens for result in case_results),
            model_calls=sum(result.model_calls for result in case_results),
        )

    def _evaluate_case(self, case: EvaluationCase) -> EvaluationCaseResult:
        result = self._extractor.extract(case.request)
        actual_candidates = tuple(
            ExpectedCandidate(
                candidate_type=candidate.candidate_type,
                operation=candidate.operation,
                kind=candidate.kind,
                subject=candidate.subject,
                claim=candidate.claim,
                scope=candidate.scope,
                project_id=candidate.project_id,
                target_memory_id=candidate.target_memory_id,
                evidence_refs=candidate.evidence_refs,
                confidence=candidate.confidence,
                explicit=candidate.explicit,
            )
            for candidate in result.candidates
        )
        actual_rejection_codes = tuple(
            rejection.code for rejection in result.rejections
        )
        failure_codes = tuple(failure.code for failure in result.failures)
        concept_counts: tuple[int, int, int] | None = None
        if case.candidate_match == "exact":
            actual_identities = actual_candidates
            expected_identities = case.expected_candidates
        elif case.candidate_match == "structural":
            actual_identities = tuple(
                _structural_identity(candidate) for candidate in actual_candidates
            )
            expected_identities = tuple(
                _structural_identity(candidate) for candidate in case.expected_candidates
            )
        elif case.candidate_match == "concept":
            concept_counts = _concept_match_counts(
                actual_candidates,
                case.expected_candidates,
                case.candidate_meaning_terms,
            )
            actual_identities = ()
            expected_identities = ()
        else:
            raise ValueError(f"Unknown candidate match mode: {case.candidate_match}")
        actual_candidate_counts = Counter(actual_identities)
        expected_candidate_counts = Counter(expected_identities)
        mismatches: list[str] = []
        if result.status != case.expected_status:
            mismatches.append(
                f"status: expected {case.expected_status!r}, got {result.status!r}"
            )
        if case.candidate_match == "exact":
            candidates_match = actual_candidates == case.expected_candidates
        elif case.candidate_match == "structural":
            candidates_match = actual_candidate_counts == expected_candidate_counts
        else:
            assert concept_counts is not None
            true_positives, false_positives, false_negatives = concept_counts
            candidates_match = false_positives == 0 and false_negatives == 0
        if not candidates_match:
            mismatches.append(
                f"candidates ({case.candidate_match}): "
                f"expected {case.expected_candidates!r}, got {actual_candidates!r}"
            )
        if actual_rejection_codes != case.expected_rejection_codes:
            mismatches.append(
                "rejections: "
                f"expected {case.expected_rejection_codes!r}, "
                f"got {actual_rejection_codes!r}"
            )
        if failure_codes != case.expected_failure_codes:
            mismatches.append(
                "failures: "
                f"expected {case.expected_failure_codes!r}, got {failure_codes!r}"
            )
        if (
            case.max_input_tokens is not None
            and result.usage.input_tokens > case.max_input_tokens
        ):
            mismatches.append(
                "input_tokens: "
                f"expected <= {case.max_input_tokens}, "
                f"got {result.usage.input_tokens}"
            )
        if concept_counts is None:
            true_positives = sum(
                (actual_candidate_counts & expected_candidate_counts).values()
            )
            false_positives = sum(
                (actual_candidate_counts - expected_candidate_counts).values()
            )
            false_negatives = sum(
                (expected_candidate_counts - actual_candidate_counts).values()
            )
        return EvaluationCaseResult(
            name=case.name,
            passed=not mismatches,
            mismatches=tuple(mismatches),
            candidate_true_positives=true_positives,
            candidate_false_positives=false_positives,
            candidate_false_negatives=false_negatives,
            policy_rejection_counts=tuple(
                sorted(Counter(actual_rejection_codes).items())
            ),
            extraction_failure_counts=tuple(
                sorted(Counter(failure_codes).items())
            ),
            source_evidence_count=result.coverage.source_evidence_count,
            episode_count=result.coverage.episode_count,
            omitted_messages=result.coverage.omitted_messages,
            failed_episodes=result.coverage.failed_episodes,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            total_tokens=result.usage.total_tokens,
            model_calls=result.usage.model_calls,
        )


def _structural_identity(candidate: ExpectedCandidate) -> tuple[object, ...]:
    return (
        candidate.candidate_type,
        candidate.operation,
        candidate.kind,
        candidate.scope,
        candidate.project_id,
        candidate.target_memory_id,
        candidate.evidence_refs,
        candidate.explicit,
    )


def _concept_match_counts(
    actual: tuple[ExpectedCandidate, ...],
    expected: tuple[ExpectedCandidate, ...],
    meaning_terms: tuple[tuple[tuple[str, ...], ...], ...],
) -> tuple[int, int, int]:
    if len(meaning_terms) != len(expected):
        raise ValueError("Concept matching requires one meaning-term set per gold candidate")
    edges: list[list[int]] = []
    for expected_candidate, term_groups in zip(expected, meaning_terms, strict=True):
        candidates: list[int] = []
        for actual_index, actual_candidate in enumerate(actual):
            haystack = (
                f"{actual_candidate.subject} {actual_candidate.claim}".casefold()
            )
            meaning_matches = all(
                any(term.casefold() in haystack for term in alternatives)
                for alternatives in term_groups
            )
            if (
                _structural_identity(actual_candidate)
                == _structural_identity(expected_candidate)
                and meaning_matches
            ):
                candidates.append(actual_index)
        edges.append(candidates)

    actual_to_expected: dict[int, int] = {}

    def augment(expected_index: int, visited_actual: set[int]) -> bool:
        for actual_index in edges[expected_index]:
            if actual_index in visited_actual:
                continue
            visited_actual.add(actual_index)
            prior_expected = actual_to_expected.get(actual_index)
            if prior_expected is None or augment(prior_expected, visited_actual):
                actual_to_expected[actual_index] = expected_index
                return True
        return False

    true_positives = sum(
        augment(expected_index, set()) for expected_index in range(len(expected))
    )
    return (
        true_positives,
        len(actual) - true_positives,
        len(expected) - true_positives,
    )
