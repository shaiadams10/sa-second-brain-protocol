from typing import Any

from second_brain_protocol.evaluation_harness import (
    EvaluationCase,
    EvaluationHarness,
    EvaluationSuite,
    ExpectedCandidate,
)
from second_brain_protocol.extraction_harness import (
    ExtractionHarness,
    ExtractionRequest,
)


class ScriptedExtractionModel:
    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response

    def extract(self, _request: ExtractionRequest) -> dict[str, Any]:
        return self._response


class FailingExtractionModel:
    def extract(self, _request: ExtractionRequest) -> dict[str, Any]:
        raise RuntimeError("provider unavailable")


def test_evaluate_reports_exact_candidate_and_rejection_agreement() -> None:
    evidence_id = "ev-profile-episode"
    extractor = ExtractionHarness(
        model=ScriptedExtractionModel(
            {
                "candidates": [
                    {
                        "type": "memory_mutation",
                        "operation": "create",
                        "kind": "work_style",
                        "subject": "Review first",
                        "claim": "the user asks for review before uncertain changes.",
                        "scope": "global",
                        "project_id": None,
                        "evidence_refs": [evidence_id],
                        "confidence": 0.94,
                        "explicit": True,
                    },
                    {
                        "type": "memory_mutation",
                        "operation": "create",
                        "kind": "decision",
                        "subject": "Unattributed decision",
                        "claim": "This must not survive policy validation.",
                        "scope": "context",
                        "project_id": None,
                        "evidence_refs": [evidence_id],
                        "confidence": 0.90,
                        "explicit": False,
                    },
                ]
            }
        )
    )
    suite = EvaluationSuite(
        name="profile-lane-regressions",
        cases=(
            EvaluationCase(
                name="valid profile candidate survives invalid project sibling",
                request=ExtractionRequest(
                    run_kind="daily",
                    evidence=(
                        {
                            "id": evidence_id,
                            "source_type": "session-episode",
                            "kind": "session_episode",
                            "project_id": None,
                            "payload": {
                                "analysis_lane": "profile_only",
                                "project_ids": [],
                            },
                        },
                    ),
                ),
                expected_status="partial",
                expected_candidates=(
                    ExpectedCandidate(
                        candidate_type="memory_mutation",
                        operation="create",
                        kind="work_style",
                        subject="Review first",
                        claim="the user asks for review before uncertain changes.",
                        scope="global",
                        project_id=None,
                        target_memory_id=None,
                        evidence_refs=(evidence_id,),
                        confidence=0.94,
                        explicit=True,
                    ),
                ),
                expected_rejection_codes=("profile-only-project-knowledge",),
            ),
        ),
    )

    report = EvaluationHarness(extractor=extractor).evaluate(suite)

    assert report.suite == "profile-lane-regressions"
    assert report.passed is True
    assert report.total_cases == 1
    assert report.passed_cases == 1
    assert report.failed_cases == 0
    assert report.cases[0].passed is True
    assert report.cases[0].mismatches == ()


def test_evaluate_fails_case_that_exceeds_input_token_budget() -> None:
    extractor = ExtractionHarness(
        model=ScriptedExtractionModel(
            {
                "candidates": [],
                "usage": {
                    "input_tokens": 6200,
                    "output_tokens": 120,
                    "total_tokens": 6320,
                },
            }
        )
    )
    suite = EvaluationSuite(
        name="token-efficiency",
        cases=(
            EvaluationCase(
                name="small episode stays under budget",
                request=ExtractionRequest(
                    run_kind="daily",
                    evidence=(
                        {
                            "id": "ev-small-episode",
                            "source_type": "session-episode",
                            "kind": "session_episode",
                            "project_id": None,
                            "payload": {
                                "analysis_lane": "profile_only",
                                "project_ids": [],
                            },
                        },
                    ),
                ),
                expected_status="empty",
                expected_candidates=(),
                expected_rejection_codes=(),
                max_input_tokens=5000,
            ),
        ),
    )

    report = EvaluationHarness(extractor=extractor).evaluate(suite)

    assert report.passed is False
    assert report.failed_cases == 1
    assert report.cases[0].mismatches == (
        "input_tokens: expected <= 5000, got 6200",
    )


def test_evaluate_reports_candidate_precision_and_recall() -> None:
    evidence_id = "ev-quality-episode"
    extractor = ExtractionHarness(
        model=ScriptedExtractionModel(
            {
                "candidates": [
                    {
                        "type": "memory_mutation",
                        "operation": "create",
                        "kind": "preference",
                        "subject": "Expected preference",
                        "claim": "Expected candidate.",
                        "scope": "global",
                        "project_id": None,
                        "evidence_refs": [evidence_id],
                        "confidence": 0.95,
                        "explicit": True,
                    },
                    {
                        "type": "memory_mutation",
                        "operation": "create",
                        "kind": "work_style",
                        "subject": "Unexpected work style",
                        "claim": "False positive candidate.",
                        "scope": "global",
                        "project_id": None,
                        "evidence_refs": [evidence_id],
                        "confidence": 0.80,
                        "explicit": False,
                    },
                ]
            }
        )
    )
    suite = EvaluationSuite(
        name="quality-metrics",
        cases=(
            EvaluationCase(
                name="one hit, one false positive, one miss",
                request=ExtractionRequest(
                    run_kind="daily",
                    evidence=(
                        {
                            "id": evidence_id,
                            "source_type": "session-episode",
                            "kind": "session_episode",
                            "project_id": None,
                            "payload": {
                                "analysis_lane": "profile_only",
                                "project_ids": [],
                            },
                        },
                    ),
                ),
                expected_status="accepted",
                expected_candidates=(
                    ExpectedCandidate(
                        candidate_type="memory_mutation",
                        operation="create",
                        kind="preference",
                        subject="Expected preference",
                        claim="Expected candidate.",
                        scope="global",
                        project_id=None,
                        target_memory_id=None,
                        evidence_refs=(evidence_id,),
                        confidence=0.95,
                        explicit=True,
                    ),
                    ExpectedCandidate(
                        candidate_type="memory_mutation",
                        operation="create",
                        kind="goal",
                        subject="Missing goal",
                        claim="Expected but absent candidate.",
                        scope="global",
                        project_id=None,
                        target_memory_id=None,
                        evidence_refs=(evidence_id,),
                        confidence=0.95,
                        explicit=True,
                    ),
                ),
                expected_rejection_codes=(),
            ),
        ),
    )

    report = EvaluationHarness(extractor=extractor).evaluate(suite)

    assert report.candidate_true_positives == 1
    assert report.candidate_false_positives == 1
    assert report.candidate_false_negatives == 1
    assert report.candidate_precision == 0.5
    assert report.candidate_recall == 0.5


def test_evaluate_does_not_match_distinct_claims_with_same_kind_and_scope() -> None:
    evidence_id = "ev-distinct-claims"
    extractor = ExtractionHarness(
        model=ScriptedExtractionModel(
            {
                "candidates": [
                    {
                        "type": "memory_mutation",
                        "operation": "create",
                        "kind": "preference",
                        "subject": "Wrong preference",
                        "claim": "This is not the expected claim.",
                        "scope": "global",
                        "project_id": None,
                        "evidence_refs": [evidence_id],
                        "confidence": 0.95,
                        "explicit": True,
                    }
                ]
            }
        )
    )
    suite = EvaluationSuite(
        name="candidate-identity",
        cases=(
            EvaluationCase(
                name="same kind and scope but different claim",
                request=ExtractionRequest(
                    run_kind="daily",
                    evidence=(
                        {
                            "id": evidence_id,
                            "source_type": "session-episode",
                            "kind": "session_episode",
                            "project_id": None,
                            "payload": {
                                "analysis_lane": "profile_only",
                                "project_ids": [],
                            },
                        },
                    ),
                ),
                expected_status="accepted",
                expected_candidates=(
                    ExpectedCandidate(
                        candidate_type="memory_mutation",
                        operation="create",
                        kind="preference",
                        subject="Expected preference",
                        claim="This is the expected claim.",
                        scope="global",
                        project_id=None,
                        target_memory_id=None,
                        evidence_refs=(evidence_id,),
                        confidence=0.95,
                        explicit=True,
                    ),
                ),
                expected_rejection_codes=(),
            ),
        ),
    )

    report = EvaluationHarness(extractor=extractor).evaluate(suite)

    assert report.candidate_true_positives == 0
    assert report.candidate_false_positives == 1
    assert report.candidate_false_negatives == 1


def test_evaluate_uses_mutation_target_confidence_and_explicitness_in_identity() -> None:
    evidence_id = "ev-full-identity"
    extractor = ExtractionHarness(
        model=ScriptedExtractionModel(
            {
                "candidates": [
                    {
                        "type": "memory_mutation",
                        "operation": "update",
                        "kind": "preference",
                        "subject": "Stable subject",
                        "claim": "Stable claim.",
                        "scope": "global",
                        "project_id": None,
                        "target_memory_id": "mem-actual",
                        "evidence_refs": [evidence_id],
                        "confidence": 0.75,
                        "explicit": False,
                    }
                ]
            }
        )
    )
    suite = EvaluationSuite(
        name="full-candidate-identity",
        cases=(
            EvaluationCase(
                name="identity includes mutation metadata",
                request=ExtractionRequest(
                    run_kind="daily",
                    evidence=(
                        {
                            "id": evidence_id,
                            "source_type": "session-episode",
                            "kind": "session_episode",
                            "project_id": None,
                            "payload": {
                                "analysis_lane": "profile_only",
                                "project_ids": [],
                            },
                        },
                    ),
                ),
                expected_status="accepted",
                expected_candidates=(
                    ExpectedCandidate(
                        candidate_type="memory_mutation",
                        operation="update",
                        kind="preference",
                        subject="Stable subject",
                        claim="Stable claim.",
                        scope="global",
                        project_id=None,
                        target_memory_id="mem-expected",
                        evidence_refs=(evidence_id,),
                        confidence=0.95,
                        explicit=True,
                    ),
                ),
                expected_rejection_codes=(),
            ),
        ),
    )

    report = EvaluationHarness(extractor=extractor).evaluate(suite)

    assert report.candidate_true_positives == 0
    assert report.candidate_false_positives == 1
    assert report.candidate_false_negatives == 1


def test_evaluate_reports_policy_coverage_and_measured_usage() -> None:
    evidence_id = "ev-observability"
    extractor = ExtractionHarness(
        model=ScriptedExtractionModel(
            {
                "candidates": [
                    {
                        "type": "memory_mutation",
                        "operation": "create",
                        "kind": "decision",
                        "subject": "Blocked project claim",
                        "claim": "Profile-only evidence cannot establish project knowledge.",
                        "scope": "context",
                        "project_id": None,
                        "evidence_refs": [evidence_id],
                        "confidence": 0.88,
                        "explicit": False,
                    }
                ],
                "usage": {
                    "input_tokens": 321,
                    "output_tokens": 45,
                    "total_tokens": 366,
                    "model_calls": 1,
                },
            }
        )
    )
    report = EvaluationHarness(extractor=extractor).evaluate(
        EvaluationSuite(
            name="observable-evaluation",
            cases=(
                EvaluationCase(
                    name="policy rejection remains visible",
                    request=ExtractionRequest(
                        run_kind="daily",
                        evidence=(
                            {
                                "id": evidence_id,
                                "source_type": "session-episode",
                                "kind": "session_episode",
                                "project_id": None,
                                "payload": {
                                    "analysis_lane": "profile_only",
                                    "project_ids": [],
                                },
                            },
                        ),
                    ),
                    expected_status="rejected",
                    expected_candidates=(),
                    expected_rejection_codes=(
                        "profile-only-project-knowledge",
                    ),
                ),
            ),
        )
    )

    assert report.policy_rejection_counts == (
        ("profile-only-project-knowledge", 1),
    )
    assert report.extraction_failure_counts == ()
    assert report.source_evidence_count == 1
    assert report.episode_count == 1
    assert report.omitted_messages == 0
    assert report.failed_episodes == 0
    assert report.input_tokens == 321
    assert report.output_tokens == 45
    assert report.total_tokens == 366
    assert report.model_calls == 1


def test_evaluate_fails_case_with_unexpected_extraction_failure() -> None:
    report = EvaluationHarness(
        extractor=ExtractionHarness(model=FailingExtractionModel())
    ).evaluate(
        EvaluationSuite(
            name="failure-awareness",
            cases=(
                EvaluationCase(
                    name="unexpected provider failure",
                    request=ExtractionRequest(
                        run_kind="daily",
                        evidence=(
                            {
                                "id": "ev-unexpected-failure",
                                "source_type": "session-episode",
                                "kind": "session_episode",
                                "project_id": None,
                                "payload": {
                                    "analysis_lane": "profile_only",
                                    "project_ids": [],
                                },
                            },
                        ),
                    ),
                    expected_status="failed",
                    expected_candidates=(),
                    expected_rejection_codes=(),
                ),
            ),
        )
    )

    assert report.passed is False
    assert report.extraction_failure_counts == (("model-error", 1),)


def test_live_concept_matching_accepts_paraphrase_and_confidence_variation() -> None:
    evidence_id = "ev-live-structure"
    extractor = ExtractionHarness(
        model=ScriptedExtractionModel(
            {
                "candidates": [
                    {
                        "type": "memory_mutation",
                        "operation": "create",
                        "kind": "preference",
                        "subject": "Review memory first",
                        "claim": "the user prefers reviewing uncertain memories before storage.",
                        "scope": "global",
                        "project_id": None,
                        "evidence_refs": [evidence_id],
                        "confidence": 0.86,
                        "explicit": True,
                    }
                ]
            }
        )
    )
    case = EvaluationCase(
        name="semantic paraphrase",
        request=ExtractionRequest(
            run_kind="daily",
            evidence=(
                {
                    "id": evidence_id,
                    "source_type": "session-episode",
                    "kind": "session_episode",
                    "project_id": None,
                    "payload": {"analysis_lane": "profile_only", "project_ids": []},
                },
            ),
        ),
        expected_status="accepted",
        expected_candidates=(
            ExpectedCandidate(
                candidate_type="memory_mutation",
                operation="create",
                kind="preference",
                subject="Review uncertain memory changes",
                claim="the user wants uncertain memory changes reviewed before storage.",
                scope="global",
                project_id=None,
                target_memory_id=None,
                evidence_refs=(evidence_id,),
                confidence=0.98,
                explicit=True,
            ),
        ),
        expected_rejection_codes=(),
        candidate_match="concept",
        candidate_meaning_terms=(
            (("review",), ("memory", "memories"), ("before",), ("stor",)),
        ),
    )

    report = EvaluationHarness(extractor=extractor).evaluate(
        EvaluationSuite(name="live-structure", cases=(case,))
    )

    assert report.passed is True
    assert report.candidate_true_positives == 1


def test_live_concept_matching_rejects_unrelated_claim_with_same_structure() -> None:
    evidence_id = "ev-live-wrong-meaning"
    extractor = ExtractionHarness(
        model=ScriptedExtractionModel(
            {
                "candidates": [
                    {
                        "type": "memory_mutation",
                        "operation": "create",
                        "kind": "preference",
                        "subject": "Favorite color",
                        "claim": "the user likes blue.",
                        "scope": "global",
                        "project_id": None,
                        "evidence_refs": [evidence_id],
                        "confidence": 0.9,
                        "explicit": True,
                    }
                ]
            }
        )
    )
    case = EvaluationCase(
        name="wrong meaning",
        request=ExtractionRequest(
            run_kind="daily",
            evidence=(
                {
                    "id": evidence_id,
                    "source_type": "session-episode",
                    "kind": "session_episode",
                    "project_id": None,
                    "payload": {"analysis_lane": "profile_only", "project_ids": []},
                },
            ),
        ),
        expected_status="accepted",
        expected_candidates=(
            ExpectedCandidate(
                candidate_type="memory_mutation",
                operation="create",
                kind="preference",
                subject="Review uncertain memory changes",
                claim="the user wants uncertain memory changes reviewed before storage.",
                scope="global",
                project_id=None,
                target_memory_id=None,
                evidence_refs=(evidence_id,),
                confidence=0.98,
                explicit=True,
            ),
        ),
        expected_rejection_codes=(),
        candidate_match="concept",
        candidate_meaning_terms=(
            (("review",), ("memory", "memories"), ("before",), ("stor",)),
        ),
    )

    report = EvaluationHarness(extractor=extractor).evaluate(
        EvaluationSuite(name="wrong-live-meaning", cases=(case,))
    )

    assert report.passed is False
    assert report.candidate_true_positives == 0
    assert report.candidate_false_positives == 1
    assert report.candidate_false_negatives == 1


def test_live_concept_matching_finds_maximum_one_to_one_assignment() -> None:
    evidence_id = "ev-live-ambiguous-matching"
    extractor = ExtractionHarness(
        model=ScriptedExtractionModel(
            {
                "candidates": [
                    {
                        "type": "memory_mutation",
                        "operation": "create",
                        "kind": "preference",
                        "subject": "Alpha beta",
                        "claim": "alpha beta",
                        "scope": "global",
                        "project_id": None,
                        "evidence_refs": [evidence_id],
                        "confidence": 0.9,
                        "explicit": True,
                    },
                    {
                        "type": "memory_mutation",
                        "operation": "create",
                        "kind": "preference",
                        "subject": "Alpha only",
                        "claim": "alpha",
                        "scope": "global",
                        "project_id": None,
                        "evidence_refs": [evidence_id],
                        "confidence": 0.9,
                        "explicit": True,
                    },
                ]
            }
        )
    )
    common = {
        "candidate_type": "memory_mutation",
        "operation": "create",
        "kind": "preference",
        "scope": "global",
        "project_id": None,
        "target_memory_id": None,
        "evidence_refs": (evidence_id,),
        "confidence": 0.95,
        "explicit": True,
    }
    case = EvaluationCase(
        name="ambiguous one-to-one assignment",
        request=ExtractionRequest(
            run_kind="daily",
            evidence=(
                {
                    "id": evidence_id,
                    "source_type": "session-episode",
                    "kind": "session_episode",
                    "project_id": None,
                    "payload": {"analysis_lane": "profile_only", "project_ids": []},
                },
            ),
        ),
        expected_status="accepted",
        expected_candidates=(
            ExpectedCandidate(subject="Alpha gold", claim="alpha", **common),
            ExpectedCandidate(subject="Alpha beta gold", claim="alpha beta", **common),
        ),
        expected_rejection_codes=(),
        candidate_match="concept",
        candidate_meaning_terms=(
            (("alpha",),),
            (("alpha",), ("beta",)),
        ),
    )

    report = EvaluationHarness(extractor=extractor).evaluate(
        EvaluationSuite(name="maximum-concept-matching", cases=(case,))
    )

    assert report.passed is True
    assert report.candidate_true_positives == 2
