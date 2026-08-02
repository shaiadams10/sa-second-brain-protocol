import json

import pytest
from jsonschema import ValidationError

from second_brain_protocol.config import protocol_root
from second_brain_protocol.evaluation_corpus import load_evaluation_corpus
from second_brain_protocol.evaluation_harness import EvaluationHarness
from second_brain_protocol.extraction_harness import ExtractionHarness


def test_versioned_evaluation_corpora_replay_through_public_harnesses() -> None:
    corpus_root = protocol_root() / "evaluation" / "corpora"

    for name in ("extraction-policy-adversarial.json", "extraction-quality.json"):
        corpus = load_evaluation_corpus(corpus_root / name)
        report = EvaluationHarness(
            extractor=ExtractionHarness(model=corpus.replay_model())
        ).evaluate(corpus.suite)

        assert report.suite == corpus.name
        assert report.total_cases > 0
        assert report.passed is True


def test_corpus_declares_whether_cases_are_safe_for_live_model_evaluation() -> None:
    corpus = load_evaluation_corpus(
        protocol_root() / "evaluation" / "corpora" / "extraction-quality.json"
    )

    assert corpus.live_case_count == 3
    assert all(case.candidate_match == "concept" for case in corpus.live_suite.cases)


def test_replay_routes_derived_digest_episodes_without_transporting_source_ids(
    tmp_path,
) -> None:
    corpus_path = tmp_path / "digest-corpus.json"
    corpus_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "digest-routing",
                "cases": [
                    {
                        "name": "two derived calls",
                        "run_kind": "daily",
                        "live_enabled": False,
                        "limits": {
                            "max_episode_messages": 1,
                            "max_episode_chars": 100,
                        },
                        "evidence": [
                            {
                                "id": "ev-digest-routing",
                                "source_type": "session-digest",
                                "kind": "session_digest",
                                "project_id": None,
                                "payload": {
                                    "source": "synthetic",
                                    "analysis_lane": "profile_only",
                                    "project_ids": [],
                                    "user_messages": [
                                        {"occurred_at": "2026-08-01T10:00:00Z", "text": "one"},
                                        {"occurred_at": "2026-08-01T10:01:00Z", "text": "two"},
                                    ],
                                    "assistant_results": [],
                                },
                            }
                        ],
                        "scripted_responses": [
                            {"candidates": []},
                            {"candidates": []},
                        ],
                        "expected": {
                            "status": "empty",
                            "candidates": [],
                            "rejection_codes": [],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    corpus = load_evaluation_corpus(corpus_path)
    report = EvaluationHarness(
        extractor=ExtractionHarness(model=corpus.replay_model())
    ).evaluate(corpus.suite)

    assert report.passed is True
    assert report.model_calls == 2


def test_corpus_gold_candidates_must_pass_canonical_candidate_schema(tmp_path) -> None:
    source = protocol_root() / "evaluation" / "corpora" / "extraction-quality.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["cases"][0]["expected"]["candidates"][0]["kind"] = "not-a-memory-kind"
    invalid = tmp_path / "invalid-gold.json"
    invalid.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_evaluation_corpus(invalid)
