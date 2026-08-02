import json
from dataclasses import asdict
from typing import Any

import pytest

from second_brain_protocol.config import protocol_root
from second_brain_protocol.extraction_harness import (
    ExtractionHarness,
    ExtractionLimits,
    ExtractionRequest,
)


class ScriptedExtractionModel:
    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response

    def extract(self, _request: ExtractionRequest) -> dict[str, Any]:
        return self._response


class PrivacyAssertingModel:
    def extract(self, request: ExtractionRequest) -> dict[str, Any]:
        encoded = json.dumps(request.evidence)
        assert "C:\\Users\\person\\secret.txt" not in encoded
        assert "person@example.com" not in encoded
        assert "https://private.example/path" not in encoded
        assert "```python" not in encoded
        assert "raw-session-id" not in encoded
        assert "[LOCAL_PATH]" in encoded
        assert "[REDACTED_EMAIL]" in encoded
        assert "[REDACTED_URL]" in encoded
        assert "[REDACTED_CODE_BLOCK]" in encoded
        return {"candidates": []}


class EpisodeRecordingModel:
    def __init__(self) -> None:
        self.requests: list[ExtractionRequest] = []

    def extract(self, request: ExtractionRequest) -> dict[str, Any]:
        self.requests.append(request)
        return {"candidates": []}


class CrossPacketCitationModel:
    def __init__(self) -> None:
        self.first_evidence_id: str | None = None

    def extract(self, request: ExtractionRequest) -> dict[str, Any]:
        evidence_id = str(request.evidence[0]["id"])
        if self.first_evidence_id is None:
            self.first_evidence_id = evidence_id
            return {"candidates": []}
        return {
            "candidates": [
                {
                    "type": "memory_mutation",
                    "operation": "create",
                    "kind": "preference",
                    "subject": "Cross-packet citation",
                    "claim": "A model response may cite only evidence in its own packet.",
                    "scope": "global",
                    "project_id": None,
                    "evidence_refs": [self.first_evidence_id],
                    "confidence": 0.94,
                    "explicit": True,
                }
            ]
        }


class SequencedUsageModel:
    def __init__(self) -> None:
        self.call_count = 0

    def extract(self, _request: ExtractionRequest) -> dict[str, Any]:
        self.call_count += 1
        if self.call_count == 1:
            return {
                "candidates": [],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 10,
                    "total_tokens": 110,
                },
            }
        return {
            "candidates": [],
            "usage": {
                "input_tokens": 200,
                "output_tokens": 20,
            },
        }


class RawResponseModel:
    def __init__(self, response: Any) -> None:
        self._response = response

    def extract(self, _request: ExtractionRequest) -> Any:
        return self._response


class PartiallyFailingModel:
    def extract(self, request: ExtractionRequest) -> dict[str, Any]:
        evidence = request.evidence[0]
        text = " ".join(
            str(message["text"])
            for message in evidence["payload"]["user_messages"]
        )
        if "MODEL_FAIL" in text:
            raise RuntimeError("sensitive provider failure details")
        return {
            "candidates": [
                {
                    "type": "memory_mutation",
                    "operation": "create",
                    "kind": "preference",
                    "subject": "Successful episode",
                    "claim": "The successful episode remains usable.",
                    "scope": "global",
                    "project_id": None,
                    "evidence_refs": [evidence["id"]],
                    "confidence": 0.92,
                    "explicit": True,
                }
            ],
            "usage": {
                "input_tokens": 400,
                "output_tokens": 80,
                "total_tokens": 480,
            },
        }


@pytest.mark.parametrize(
    "limits",
    (
        {"max_episode_messages": 0, "max_episode_chars": 100},
        {"max_episode_messages": 1, "max_episode_chars": 0},
    ),
)
def test_extraction_limits_require_positive_bounds(limits: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="positive"):
        ExtractionLimits(**limits)


def test_model_output_and_host_candidate_schemas_cannot_drift() -> None:
    schema_root = protocol_root() / "schemas"
    candidate_schema = json.loads(
        (schema_root / "extraction-candidate.schema.json").read_text(encoding="utf-8")
    )
    output_schema = json.loads(
        (schema_root / "extraction-output.schema.json").read_text(encoding="utf-8")
    )

    embedded = output_schema["$defs"]["candidate"]
    assert embedded["$schema"] == candidate_schema["$schema"]
    assert embedded["$defs"] == candidate_schema["$defs"]
    assert len(embedded["oneOf"]) == len(candidate_schema["oneOf"])


def test_full_lane_validated_outcome_can_propose_review_only_procedure() -> None:
    evidence_id = "ev-procedure-session"
    request = ExtractionRequest(
        run_kind="weekly",
        evidence=(
            {
                "id": evidence_id,
                "source_type": "session-episode",
                "kind": "session_episode",
                "project_id": "project-protocol",
                "payload": {
                    "analysis_lane": "full",
                    "project_ids": ["project-protocol"],
                },
            },
        ),
    )
    model = ScriptedExtractionModel(
        {
            "candidates": [
                {
                    "type": "procedure_candidate",
                    "name": "Verify atomic publication",
                    "purpose": "Prove canonical files and state reach one terminal outcome.",
                    "scope": "project",
                    "project_id": "project-protocol",
                    "prerequisites": ["A prepared publication manifest"],
                    "steps": [
                        "Validate authoritative evidence hashes.",
                        "Commit canonical files and transactional state.",
                    ],
                    "failure_branches": [
                        "If state fails after files commit, resume from the durable journal."
                    ],
                    "tests": [
                        "Crash after file commit and verify an idempotent retry."
                    ],
                    "evidence_refs": [evidence_id],
                    "owner_directed": True,
                    "validated_outcome": True,
                }
            ]
        }
    )

    result = ExtractionHarness(model=model).extract(request)

    assert result.candidates == ()
    assert len(result.procedures) == 1
    assert result.procedures[0].name == "Verify atomic publication"
    assert result.procedures[0].disposition == "review"


def test_extract_keeps_valid_profile_candidate_when_project_sibling_is_rejected() -> None:
    evidence_id = "ev-profile-episode"
    request = ExtractionRequest(
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
    )
    model = ScriptedExtractionModel(
        {
            "candidates": [
                {
                    "type": "memory_mutation",
                    "operation": "create",
                    "kind": "work_style",
                    "subject": "Review before durable changes",
                    "claim": "the user asks to review uncertain durable changes.",
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
                    "subject": "Unattributed deployment",
                    "claim": "SECRET INVALID PROJECT CLAIM",
                    "scope": "context",
                    "project_id": None,
                    "evidence_refs": [evidence_id],
                    "confidence": 0.91,
                    "explicit": False,
                },
            ]
        }
    )

    result = ExtractionHarness(model=model).extract(request)

    assert result.status == "partial"
    assert [
        (candidate.candidate_type, candidate.kind, candidate.scope)
        for candidate in result.candidates
    ] == [("memory_mutation", "work_style", "global")]
    assert result.candidates[0].evidence_refs == (evidence_id,)
    assert [
        (rejection.index, rejection.code, rejection.evidence_refs)
        for rejection in result.rejections
    ] == [
        (1, "profile-only-project-knowledge", (evidence_id,)),
    ]
    assert "SECRET INVALID PROJECT CLAIM" not in json.dumps(
        [asdict(item) for item in result.rejections]
    )


def test_extract_rejects_unknown_evidence_without_losing_valid_sibling() -> None:
    evidence_id = "ev-known-episode"
    request = ExtractionRequest(
        run_kind="daily",
        evidence=(
            {
                "id": evidence_id,
                "source_type": "session-episode",
                "kind": "session_episode",
                "project_id": "project-brain",
                "payload": {
                    "analysis_lane": "full",
                    "project_ids": ["project-brain"],
                },
            },
        ),
    )
    model = ScriptedExtractionModel(
        {
            "candidates": [
                {
                    "type": "memory_mutation",
                    "operation": "create",
                    "kind": "lesson",
                    "subject": "Batch validation",
                    "claim": "Validate candidates independently.",
                    "scope": "project",
                    "project_id": "project-brain",
                    "evidence_refs": [evidence_id],
                    "confidence": 0.95,
                    "explicit": True,
                },
                {
                    "type": "memory_mutation",
                    "operation": "create",
                    "kind": "lesson",
                    "subject": "Invented citation",
                    "claim": "This candidate cites evidence that was never supplied.",
                    "scope": "project",
                    "project_id": "project-brain",
                    "evidence_refs": ["ev-never-supplied"],
                    "confidence": 0.95,
                    "explicit": False,
                },
            ]
        }
    )

    result = ExtractionHarness(model=model).extract(request)

    assert result.status == "partial"
    assert [candidate.subject for candidate in result.candidates] == [
        "Batch validation"
    ]
    assert [rejection.code for rejection in result.rejections] == [
        "unknown-evidence-reference"
    ]
    assert result.rejections[0].evidence_refs == ("ev-never-supplied",)


def test_daily_rejects_weekly_only_candidate_without_losing_valid_sibling() -> None:
    evidence_id = "ev-daily-episode"
    request = ExtractionRequest(
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
    )
    model = ScriptedExtractionModel(
        {
            "candidates": [
                {
                    "type": "memory_mutation",
                    "operation": "create",
                    "kind": "preference",
                    "subject": "Concise status updates",
                    "claim": "the user prefers concise progress updates.",
                    "scope": "global",
                    "project_id": None,
                    "evidence_refs": [evidence_id],
                    "confidence": 0.96,
                    "explicit": True,
                },
                {
                    "type": "question_resolution",
                    "question_id": "obs-weekly-only",
                    "answer": "The deferred question is resolved.",
                    "evidence_refs": [evidence_id],
                    "confidence": 0.95,
                    "authoritative": True,
                },
            ]
        }
    )

    result = ExtractionHarness(model=model).extract(request)

    assert result.status == "partial"
    assert [candidate.kind for candidate in result.candidates] == ["preference"]
    assert [rejection.code for rejection in result.rejections] == [
        "run-kind-capability-violation"
    ]
    assert result.rejections[0].candidate_type == "question_resolution"


def test_extract_sanitizes_episode_before_crossing_model_seam() -> None:
    result = ExtractionHarness(model=PrivacyAssertingModel()).extract(
        ExtractionRequest(
            run_kind="daily",
            evidence=(
                {
                    "id": "ev-private-episode",
                    "source_type": "session-episode",
                    "kind": "session_episode",
                    "project_id": None,
                    "payload": {
                        "session_id": "raw-session-id",
                        "analysis_lane": "profile_only",
                        "project_ids": [],
                        "user_messages": [
                            {
                                "occurred_at": "2026-08-01T10:00:00Z",
                                "text": (
                                    "Open C:\\Users\\person\\secret.txt, email "
                                    "person@example.com, visit "
                                    "https://private.example/path, then run "
                                    "```python\nprint('secret')\n```"
                                ),
                            }
                        ],
                        "assistant_results": [],
                        "artifacts": [{"path": "C:\\Users\\person\\secret.txt"}],
                    },
                },
            ),
        )
    )

    assert result.status == "empty"


def test_extract_covers_middle_of_long_session_with_bounded_episodes() -> None:
    model = EpisodeRecordingModel()
    messages = [
        {
            "occurred_at": f"2026-08-01T10:0{index}:00Z",
            "text": text,
        }
        for index, text in enumerate(
            (
                "FIRST request",
                "early constraint",
                "MIDDLE-DECISION must survive",
                "late validation",
                "LAST outcome",
            )
        )
    ]
    result = ExtractionHarness(model=model).extract(
        ExtractionRequest(
            run_kind="daily",
            evidence=(
                {
                    "id": "ev-long-session-digest",
                    "source_type": "session-digest",
                    "kind": "session_digest",
                    "project_id": "project-brain",
                    "payload": {
                        "source": "codex",
                        "analysis_lane": "full",
                        "project_ids": ["project-brain"],
                        "user_messages": messages,
                        "assistant_results": [],
                    },
                },
            ),
            limits=ExtractionLimits(
                max_episode_messages=2,
                max_episode_chars=10_000,
            ),
        )
    )

    assert result.status == "empty"
    assert result.coverage.source_evidence_count == 1
    assert result.coverage.episode_count == 3
    assert result.coverage.omitted_messages == 0
    assert len(result.episodes) == 3
    assert {receipt.source_evidence_ids for receipt in result.episodes} == {
        ("ev-long-session-digest",)
    }
    assert sum(receipt.message_count for receipt in result.episodes) == 5
    assert sum(receipt.content_chars for receipt in result.episodes) == sum(
        len(item["text"]) for item in messages
    )
    assert len(model.requests) == 3
    observed = [
        message["text"]
        for request in model.requests
        for evidence in request.evidence
        for message in evidence["payload"]["user_messages"]
    ]
    assert observed == [item["text"] for item in messages]
    assert observed.count("MIDDLE-DECISION must survive") == 1


def test_extract_counts_malformed_session_messages_as_omitted() -> None:
    result = ExtractionHarness(model=EpisodeRecordingModel()).extract(
        ExtractionRequest(
            run_kind="daily",
            evidence=(
                {
                    "id": "ev-session-with-malformed-message",
                    "source_type": "session-digest",
                    "kind": "session_digest",
                    "project_id": None,
                    "payload": {
                        "source": "codex",
                        "analysis_lane": "profile_only",
                        "project_ids": [],
                        "user_messages": [
                            {
                                "occurred_at": "2026-08-01T10:00:00Z",
                                "text": "valid message",
                            },
                            "not-a-message-object",
                        ],
                        "assistant_results": [],
                    },
                },
            ),
        )
    )

    assert result.coverage.episode_count == 1
    assert result.coverage.omitted_messages == 1


def test_extract_preserves_successful_episode_when_sibling_model_call_fails() -> None:
    result = ExtractionHarness(model=PartiallyFailingModel()).extract(
        ExtractionRequest(
            run_kind="daily",
            evidence=(
                {
                    "id": "ev-two-episode-session",
                    "source_type": "session-digest",
                    "kind": "session_digest",
                    "project_id": None,
                    "payload": {
                        "source": "codex",
                        "analysis_lane": "profile_only",
                        "project_ids": [],
                        "user_messages": [
                            {
                                "occurred_at": "2026-08-01T10:00:00Z",
                                "text": "safe episode",
                            },
                            {
                                "occurred_at": "2026-08-01T10:01:00Z",
                                "text": "MODEL_FAIL",
                            },
                        ],
                        "assistant_results": [],
                    },
                },
            ),
            limits=ExtractionLimits(
                max_episode_messages=1,
                max_episode_chars=10_000,
            ),
        )
    )

    assert result.status == "partial"
    assert [candidate.subject for candidate in result.candidates] == [
        "Successful episode"
    ]
    assert result.coverage.episode_count == 2
    assert result.coverage.failed_episodes == 1
    assert len(result.failures) == 1
    assert result.failures[0].code == "model-error"
    assert result.failures[0].error_type == "RuntimeError"
    assert "sensitive provider failure details" not in json.dumps(
        [asdict(item) for item in result.failures]
    )


def test_extract_rejects_malformed_candidate_without_losing_valid_sibling() -> None:
    evidence_id = "ev-valid-shape"
    result = ExtractionHarness(
        model=ScriptedExtractionModel(
            {
                "candidates": [
                    {
                        "type": "memory_mutation",
                        "operation": "create",
                        "kind": "preference",
                        "subject": "Keep this candidate",
                        "claim": "Valid siblings survive malformed output.",
                        "scope": "global",
                        "project_id": None,
                        "evidence_refs": [evidence_id],
                        "confidence": 0.93,
                        "explicit": True,
                    },
                    {
                        "type": "memory_mutation",
                        "operation": "create",
                        "subject": "Missing kind",
                        "claim": "INVALID SHAPE CONTENT",
                        "scope": "global",
                        "project_id": None,
                        "evidence_refs": [evidence_id],
                        "confidence": 0.90,
                        "explicit": False,
                    },
                ]
            }
        )
    ).extract(
        ExtractionRequest(
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
        )
    )

    assert result.status == "partial"
    assert [candidate.subject for candidate in result.candidates] == [
        "Keep this candidate"
    ]
    assert [rejection.code for rejection in result.rejections] == [
        "invalid-candidate-shape"
    ]
    assert "INVALID SHAPE CONTENT" not in json.dumps(
        [asdict(item) for item in result.rejections]
    )


def test_extract_rejects_invalid_candidate_types_without_losing_valid_sibling() -> None:
    evidence_id = "ev-invalid-types"
    result = ExtractionHarness(
        model=ScriptedExtractionModel(
            {
                "candidates": [
                    {
                        "type": "memory_mutation",
                        "operation": "create",
                        "kind": "preference",
                        "subject": "Valid candidate",
                        "claim": "This valid candidate must survive.",
                        "scope": "global",
                        "project_id": None,
                        "evidence_refs": [evidence_id],
                        "confidence": 0.93,
                        "explicit": True,
                    },
                    {
                        "type": "memory_mutation",
                        "operation": "create",
                        "kind": [],
                        "subject": "Unhashable kind",
                        "claim": "INVALID TYPE CONTENT",
                        "scope": "global",
                        "project_id": None,
                        "evidence_refs": [evidence_id],
                        "confidence": "not-a-number",
                        "explicit": False,
                    },
                ]
            }
        )
    ).extract(
        ExtractionRequest(
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
        )
    )

    assert result.status == "partial"
    assert [candidate.subject for candidate in result.candidates] == [
        "Valid candidate"
    ]
    assert [rejection.code for rejection in result.rejections] == [
        "invalid-candidate-schema"
    ]
    assert "INVALID TYPE CONTENT" not in json.dumps(
        [asdict(item) for item in result.rejections]
    )


def test_extract_schema_rejects_invalid_enums_without_losing_valid_sibling() -> None:
    evidence_id = "ev-invalid-enum"
    result = ExtractionHarness(
        model=ScriptedExtractionModel(
            {
                "candidates": [
                    {
                        "type": "memory_mutation",
                        "operation": "create",
                        "kind": "preference",
                        "subject": "Valid schema candidate",
                        "claim": "This candidate matches the extraction schema.",
                        "scope": "global",
                        "project_id": None,
                        "evidence_refs": [evidence_id],
                        "confidence": 0.93,
                        "explicit": True,
                    },
                    {
                        "type": "memory_mutation",
                        "operation": "invent",
                        "kind": "imaginary_kind",
                        "subject": "Invalid enums",
                        "claim": "This candidate must fail schema validation.",
                        "scope": "planet",
                        "project_id": None,
                        "evidence_refs": [evidence_id],
                        "confidence": 0.90,
                        "explicit": False,
                    },
                ]
            }
        )
    ).extract(
        ExtractionRequest(
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
        )
    )

    assert result.status == "partial"
    assert [candidate.subject for candidate in result.candidates] == [
        "Valid schema candidate"
    ]
    assert [rejection.code for rejection in result.rejections] == [
        "invalid-candidate-schema"
    ]


def test_extract_treats_missing_session_attribution_as_profile_only() -> None:
    evidence_id = "ev-ambiguous-session"
    result = ExtractionHarness(
        model=ScriptedExtractionModel(
            {
                "candidates": [
                    {
                        "type": "memory_mutation",
                        "operation": "create",
                        "kind": "decision",
                        "subject": "Ambiguous project decision",
                        "claim": "Missing attribution cannot support this decision.",
                        "scope": "context",
                        "project_id": None,
                        "evidence_refs": [evidence_id],
                        "confidence": 0.90,
                        "explicit": False,
                    }
                ]
            }
        )
    ).extract(
        ExtractionRequest(
            run_kind="daily",
            evidence=(
                {
                    "id": evidence_id,
                    "source_type": "session-episode",
                    "kind": "session_episode",
                    "project_id": None,
                    "payload": {
                        "attribution_status": "ambiguous",
                        "project_ids": [],
                    },
                },
            ),
        )
    )

    assert result.status == "rejected"
    assert [rejection.code for rejection in result.rejections] == [
        "profile-only-project-knowledge"
    ]


def test_extract_splits_single_oversized_message_without_omission() -> None:
    model = EpisodeRecordingModel()
    original = "ABCDEFGHIJKLMNO"
    result = ExtractionHarness(model=model).extract(
        ExtractionRequest(
            run_kind="daily",
            evidence=(
                {
                    "id": "ev-oversized-message",
                    "source_type": "session-digest",
                    "kind": "session_digest",
                    "project_id": None,
                    "payload": {
                        "source": "codex",
                        "analysis_lane": "profile_only",
                        "project_ids": [],
                        "user_messages": [
                            {
                                "occurred_at": "2026-08-01T10:00:00Z",
                                "text": original,
                            }
                        ],
                        "assistant_results": [],
                    },
                },
            ),
            limits=ExtractionLimits(
                max_episode_messages=10,
                max_episode_chars=5,
            ),
        )
    )

    fragments = [
        message["text"]
        for request in model.requests
        for evidence in request.evidence
        for message in evidence["payload"]["user_messages"]
    ]
    assert result.coverage.episode_count == 3
    assert result.coverage.omitted_messages == 0
    assert all(len(fragment) <= 5 for fragment in fragments)
    assert "".join(fragments) == original


def test_extract_rejects_citation_to_evidence_outside_the_model_packet() -> None:
    result = ExtractionHarness(model=CrossPacketCitationModel()).extract(
        ExtractionRequest(
            run_kind="daily",
            evidence=(
                {
                    "id": "ev-cross-packet-source",
                    "source_type": "session-digest",
                    "kind": "session_digest",
                    "project_id": None,
                    "payload": {
                        "source": "codex",
                        "analysis_lane": "profile_only",
                        "project_ids": [],
                        "user_messages": [
                            {
                                "occurred_at": "2026-08-01T10:00:00Z",
                                "text": "First bounded episode.",
                            },
                            {
                                "occurred_at": "2026-08-01T10:01:00Z",
                                "text": "Second bounded episode.",
                            },
                        ],
                        "assistant_results": [],
                    },
                },
            ),
            limits=ExtractionLimits(
                max_episode_messages=1,
                max_episode_chars=100,
            ),
        )
    )

    assert result.candidates == ()
    assert [rejection.code for rejection in result.rejections] == [
        "unknown-evidence-reference"
    ]


def test_extract_rebounds_oversized_prebuilt_session_episode() -> None:
    model = EpisodeRecordingModel()
    result = ExtractionHarness(model=model).extract(
        ExtractionRequest(
            run_kind="daily",
            evidence=(
                {
                    "id": "ev-prebuilt-episode",
                    "source_type": "session-episode",
                    "kind": "session_episode",
                    "project_id": None,
                    "payload": {
                        "source": "codex",
                        "analysis_lane": "profile_only",
                        "project_ids": [],
                        "user_messages": [
                            {
                                "occurred_at": f"2026-08-01T10:0{index}:00Z",
                                "text": f"message-{index}",
                            }
                            for index in range(3)
                        ],
                        "assistant_results": [],
                    },
                },
            ),
            limits=ExtractionLimits(
                max_episode_messages=2,
                max_episode_chars=100,
            ),
        )
    )

    assert result.coverage.episode_count == 2
    assert len(model.requests) == 2
    assert all(
        sum(
            len(evidence["payload"][key])
            for key in ("user_messages", "assistant_results")
        )
        <= 2
        for request in model.requests
        for evidence in request.evidence
    )


def test_extract_sums_mixed_reported_and_derived_token_totals() -> None:
    result = ExtractionHarness(model=SequencedUsageModel()).extract(
        ExtractionRequest(
            run_kind="daily",
            evidence=(
                {
                    "id": "ev-token-total-source",
                    "source_type": "session-digest",
                    "kind": "session_digest",
                    "project_id": None,
                    "payload": {
                        "source": "codex",
                        "analysis_lane": "profile_only",
                        "project_ids": [],
                        "user_messages": [
                            {
                                "occurred_at": "2026-08-01T10:00:00Z",
                                "text": "first",
                            },
                            {
                                "occurred_at": "2026-08-01T10:01:00Z",
                                "text": "second",
                            },
                        ],
                        "assistant_results": [],
                    },
                },
            ),
            limits=ExtractionLimits(
                max_episode_messages=1,
                max_episode_chars=100,
            ),
        )
    )

    assert result.usage.input_tokens == 300
    assert result.usage.output_tokens == 30
    assert result.usage.total_tokens == 330


def test_extract_rejects_project_candidate_with_mismatched_attribution() -> None:
    evidence_id = "ev-project-one"
    result = ExtractionHarness(
        model=ScriptedExtractionModel(
            {
                "candidates": [
                    {
                        "type": "memory_mutation",
                        "operation": "create",
                        "kind": "decision",
                        "subject": "Wrong project",
                        "claim": "This decision is attributed to a different project.",
                        "scope": "project",
                        "project_id": "project-two",
                        "evidence_refs": [evidence_id],
                        "confidence": 0.9,
                        "explicit": True,
                    }
                ]
            }
        )
    ).extract(
        ExtractionRequest(
            run_kind="daily",
            evidence=(
                {
                    "id": evidence_id,
                    "source_type": "session-episode",
                    "kind": "session_episode",
                    "project_id": "project-one",
                    "payload": {
                        "analysis_lane": "full",
                        "project_ids": ["project-one"],
                    },
                },
            ),
        )
    )

    assert result.candidates == ()
    assert [rejection.code for rejection in result.rejections] == [
        "project-attribution-mismatch"
    ]


@pytest.mark.parametrize(
    "response",
    (
        None,
        [],
        {"candidates": [], "usage": {"input_tokens": "many"}},
    ),
)
def test_extract_isolates_malformed_model_envelopes(response: Any) -> None:
    result = ExtractionHarness(model=RawResponseModel(response)).extract(
        ExtractionRequest(
            run_kind="daily",
            evidence=(
                {
                    "id": "ev-malformed-envelope",
                    "source_type": "session-episode",
                    "kind": "session_episode",
                    "project_id": None,
                    "payload": {
                        "analysis_lane": "profile_only",
                        "project_ids": [],
                    },
                },
            ),
        )
    )

    assert result.status == "failed"
    assert result.candidates == ()
    assert [failure.code for failure in result.failures] == [
        "invalid-model-response"
    ]


def test_extract_preserves_long_message_across_sanitized_fragments() -> None:
    model = EpisodeRecordingModel()
    original = "x" * 5000
    result = ExtractionHarness(model=model).extract(
        ExtractionRequest(
            run_kind="daily",
            evidence=(
                {
                    "id": "ev-long-sanitized-message",
                    "source_type": "session-digest",
                    "kind": "session_digest",
                    "project_id": None,
                    "payload": {
                        "source": "codex",
                        "analysis_lane": "profile_only",
                        "project_ids": [],
                        "user_messages": [
                            {
                                "occurred_at": "2026-08-01T10:00:00Z",
                                "text": original,
                            }
                        ],
                        "assistant_results": [],
                    },
                },
            ),
        )
    )

    fragments = [
        message["text"]
        for request in model.requests
        for evidence in request.evidence
        for message in evidence["payload"]["user_messages"]
    ]
    assert result.coverage.omitted_messages == 0
    assert "".join(fragments) == original


def test_extract_rejects_weekly_question_resolution_as_unsupported() -> None:
    evidence_id = "ev-weekly-question"
    result = ExtractionHarness(
        model=ScriptedExtractionModel(
            {
                "candidates": [
                    {
                        "type": "question_resolution",
                        "question_id": "question-1",
                        "evidence_refs": [evidence_id],
                    }
                ]
            }
        )
    ).extract(
        ExtractionRequest(
            run_kind="weekly",
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
        )
    )

    assert [rejection.code for rejection in result.rejections] == [
        "unsupported-candidate-type"
    ]


def test_extract_rejects_non_create_mutation_without_target_memory() -> None:
    evidence_id = "ev-update-without-target"
    result = ExtractionHarness(
        model=ScriptedExtractionModel(
            {
                "candidates": [
                    {
                        "type": "memory_mutation",
                        "operation": "update",
                        "kind": "preference",
                        "subject": "Missing target",
                        "claim": "An update must identify the memory it changes.",
                        "scope": "global",
                        "project_id": None,
                        "evidence_refs": [evidence_id],
                        "confidence": 0.9,
                        "explicit": True,
                    }
                ]
            }
        )
    ).extract(
        ExtractionRequest(
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
        )
    )

    assert [rejection.code for rejection in result.rejections] == [
        "invalid-candidate-schema"
    ]


def test_extract_propagates_hard_model_call_budget_without_inflating_usage() -> None:
    from second_brain_protocol.extraction_model import (
        BudgetedExtractionModel,
        ModelCallBudgetExceeded,
    )

    delegate = EpisodeRecordingModel()
    model = BudgetedExtractionModel(delegate=delegate, max_model_calls=1)
    request = ExtractionRequest(
        run_kind="daily",
        evidence=(
            {
                "id": "ev-budgeted-session",
                "source_type": "session-digest",
                "kind": "session_digest",
                "project_id": None,
                "payload": {
                    "analysis_lane": "profile_only",
                    "project_ids": [],
                    "user_messages": [
                        {"occurred_at": "2026-08-01T10:00:00Z", "text": "one"},
                        {"occurred_at": "2026-08-01T10:01:00Z", "text": "two"},
                    ],
                    "assistant_results": [],
                },
            },
        ),
        limits=ExtractionLimits(max_episode_messages=1, max_episode_chars=100),
    )

    with pytest.raises(ModelCallBudgetExceeded):
        ExtractionHarness(model=model).extract(request)

    assert len(delegate.requests) == 1


@pytest.mark.parametrize(
    "unsafe_claim",
    (
        "The credential is " + "sk-" + "1234567890abcdefghijklmnop.",
        "Use the private page https://private.example/account.",
        "Contact person@example.com for access.",
        r"The source file is C:\Users\person\private\notes.md.",
    ),
)
def test_extract_rejects_privacy_unsafe_candidate_content(
    unsafe_claim: str,
) -> None:
    evidence_id = "ev-safe-candidate-input"
    request = ExtractionRequest(
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
    )
    model = ScriptedExtractionModel(
        {
            "candidates": [
                {
                    "type": "memory_mutation",
                    "operation": "create",
                    "kind": "preference",
                    "subject": "Unsafe candidate",
                    "claim": unsafe_claim,
                    "scope": "global",
                    "project_id": None,
                    "evidence_refs": [evidence_id],
                    "confidence": 0.95,
                    "explicit": True,
                }
            ]
        }
    )

    result = ExtractionHarness(model=model).extract(request)

    assert result.candidates == ()
    assert [item.code for item in result.rejections] == [
        "unsafe-candidate-content"
    ]
    assert unsafe_claim not in json.dumps([asdict(item) for item in result.rejections])


def test_extract_rejects_privacy_unsafe_target_memory_id() -> None:
    evidence_id = "ev-unsafe-target-input"
    request = ExtractionRequest(
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
    )
    model = ScriptedExtractionModel(
        {
            "candidates": [
                {
                    "type": "memory_mutation",
                    "operation": "update",
                    "kind": "preference",
                    "subject": "Unsafe target",
                    "claim": "The preference changed.",
                    "scope": "global",
                    "project_id": None,
                    "target_memory_id": "C:/Users/person/private.txt",
                    "evidence_refs": [evidence_id],
                    "confidence": 0.95,
                    "explicit": True,
                }
            ]
        }
    )

    result = ExtractionHarness(model=model).extract(request)

    assert result.candidates == ()
    assert [item.code for item in result.rejections] == [
        "unsafe-candidate-content"
    ]
