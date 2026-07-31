from pathlib import Path

from second_brain_protocol import model_runner
from second_brain_protocol.config import RuntimePaths


def test_canary_uses_user_evidence_and_each_models_real_prompt(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[dict] = []

    def fake_run_model(**kwargs):
        calls.append(kwargs)
        return (
            {
                "summary": "The canary value is green.",
                "observations": [
                    {
                        "kind": "preference",
                        "subject": "Status indicators",
                        "claim": "the user prefers green status indicators.",
                        "evidence_refs": ["ev-111111111111111111111111"],
                        "confidence": 1.0,
                        "explicit": True,
                        "scope": "global",
                        "public_claim": False,
                        "authoritative": False,
                    }
                ],
                "pattern_signals": [],
                "learning_signals": [],
                "project_updates": [],
                "session_summaries": [],
                "skill_updates": [],
                "voice_samples": [],
                "review_items": [],
                "question_resolutions": [],
            },
            tmp_path / "receipt.json",
        )

    monkeypatch.setattr(model_runner, "run_model", fake_run_model)
    roles = {
        name: {"name": f"model-{name}", "reasoning": "medium"}
        for name in ("daily", "weekly", "bootstrap")
    }

    assert model_runner.canary(RuntimePaths.from_root(tmp_path), roles) == {
        "daily": "ok",
        "weekly": "ok",
        "bootstrap": "ok",
    }
    assert [call["prompt_name"] for call in calls] == [
        "daily.md",
        "weekly.md",
        "bootstrap.md",
    ]
    for call in calls:
        evidence = call["evidence"][0]
        assert evidence["source_type"] == "interview"
        assert evidence["payload"]["role"] == "user"
        assert evidence["payload"]["explicit"] is True
        assert "green status indicators" in evidence["payload"]["text"]
