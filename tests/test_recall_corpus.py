import json

import pytest
from jsonschema import ValidationError

from second_brain_protocol.config import protocol_root
from second_brain_protocol.recall_benchmark import RecallBenchmark
from second_brain_protocol.recall_corpus import load_recall_corpus


def _corpus_payload() -> dict[str, object]:
    path = protocol_root() / "evaluation" / "corpora" / "recall-ablation.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_versioned_recall_ablation_corpus_measures_graph_lift() -> None:
    corpus = load_recall_corpus(
        protocol_root() / "evaluation" / "corpora" / "recall-ablation.json"
    )

    report = RecallBenchmark(retriever=corpus.harness()).evaluate(corpus.suite)

    assert report.source_recall == 0.75
    assert report.irrelevant_context_rate == 0.0
    assert report.graph_recall_lift == 0.5
    assert report.lexical_recall_lift == 0.5
    assert report.vector_recall_lift == 0.5
    assert report.average_packet_chars > 0


def test_recall_corpus_rejects_coerced_boolean(tmp_path) -> None:
    payload = _corpus_payload()
    payload["cases"][0]["include_graph"] = "false"
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_recall_corpus(path)


def test_recall_corpus_rejects_expected_forbidden_overlap(tmp_path) -> None:
    payload = _corpus_payload()
    payload["cases"][0]["forbidden_source_ids"] = ["note-shadow-cutover"]
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="expected and forbidden"):
        load_recall_corpus(path)


def test_recall_corpus_rejects_conflicting_fixtures_for_same_query(tmp_path) -> None:
    payload = _corpus_payload()
    payload["cases"][1]["lexical"][0]["snippet"] = "Conflicting fixture."
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="conflicting lexical fixtures"):
        load_recall_corpus(path)


def test_recall_corpus_graph_backend_validates_exact_seed_set(tmp_path) -> None:
    payload = _corpus_payload()
    for case in payload["cases"]:
        case["graph_seed_source_ids"] = ["note-wrong-seed"]
    path = tmp_path / "invalid-seeds.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    corpus = load_recall_corpus(path)

    graph_case = next(
        case for case in corpus.suite.cases if case.request.include_graph
    )
    with pytest.raises(ValueError, match="graph seed mismatch"):
        corpus.harness().retrieve(graph_case.request)


def test_recall_corpus_schema_rejects_raw_evidence_paths(tmp_path) -> None:
    payload = _corpus_payload()
    payload["cases"][0]["lexical"][0]["note_path"] = "Evidence/Raw/session.md"
    payload["cases"][1]["lexical"][0]["note_path"] = "Evidence/Raw/session.md"
    path = tmp_path / "invalid-raw-path.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_recall_corpus(path)


def test_recall_corpus_rejects_casefolded_or_root_hidden_paths(tmp_path) -> None:
    for invalid_path in ("evidence/raw/session.md", ".venv/package/README.md"):
        payload = _corpus_payload()
        for case in payload["cases"][:2]:
            case["lexical"][0]["note_path"] = invalid_path
        path = tmp_path / (invalid_path.replace("/", "-") + ".json")
        path.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(ValidationError):
            load_recall_corpus(path)
