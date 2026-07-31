import json
from pathlib import Path


PROTOCOL = Path(__file__).resolve().parents[1]


def _prompt(name: str) -> str:
    return (PROTOCOL / "prompts" / name).read_text(encoding="utf-8")


def test_system_prompt_requires_embedded_insight_detection_and_role_separation() -> None:
    prompt = _prompt("system.md")

    assert "Do not wait for a memory-specific request" in prompt
    assert "Use `observations` for explicit durable facts" in prompt
    assert "Use `pattern_signals` for implied or behavioral candidates" in prompt
    assert "Do not repeat the same claim across these outputs" in prompt


def test_daily_prompt_ends_with_a_two_lens_insight_check() -> None:
    prompt = _prompt("daily.md")

    assert "An insight remains eligible when the user states it while asking for unrelated work" in prompt
    assert "Before returning JSON, perform an insight extraction check" in prompt
    assert "Consider every supplied session through both the work and the user lenses" in prompt
    assert "Remove duplicate claims across output collections" in prompt


def test_weekly_prompt_consolidates_insight_without_forcing_claims() -> None:
    prompt = _prompt("weekly.md")

    assert "Do not wait for memory-specific phrasing" in prompt
    assert "Before returning JSON, perform a weekly insight consolidation check" in prompt
    assert "Consolidate recurring behavior under stable pattern keys" in prompt
    assert "leave arrays empty rather than inventing insight" in prompt


def test_model_schema_requires_every_insight_channel() -> None:
    schema = json.loads(
        (PROTOCOL / "schemas" / "model-output.schema.json").read_text(
            encoding="utf-8"
        )
    )

    required = set(schema["required"])
    assert {
        "observations",
        "pattern_signals",
        "learning_signals",
        "project_updates",
        "skill_updates",
        "review_items",
    } <= required
