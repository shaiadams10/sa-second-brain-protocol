import pytest

from second_brain_protocol.markdown import GeneratedSectionError, replace_generated_section
from second_brain_protocol.security import (
    assert_model_packet_safe,
    repair_mojibake,
    sanitize_text,
    scan_text,
)


def test_secret_and_path_sanitization() -> None:
    text = "key=" + "sk-" + "abcdefghijklmnopqrstuvwxyz123456 path=C:\\Users\\person\\secret.txt"
    sanitized = sanitize_text(text)
    assert "sk-" not in sanitized
    assert "C:\\Users" not in sanitized
    assert "[REDACTED_SECRET]" in sanitized
    assert scan_text(text, "fixture")


def test_drive_roots_are_redacted_before_json_transport() -> None:
    for drive_root in ("C:\\", "D:/"):
        sanitized = sanitize_text(f"Use {drive_root} for local work")
        assert drive_root not in sanitized
        assert "[LOCAL_PATH]" in sanitized
        assert_model_packet_safe({"text": sanitized})


def test_model_packet_preflight_reports_safe_field_location() -> None:
    with pytest.raises(
        ValueError,
        match=r"absolute-path at external-model-packet\.evidence\[0\]\.payload\.text",
    ):
        assert_model_packet_safe(
            {"evidence": [{"payload": {"text": "Read C:\\private\\note.txt"}}]}
        )


def test_model_packet_preflight_scans_tuple_members() -> None:
    with pytest.raises(ValueError, match="privacy preflight"):
        assert_model_packet_safe(
            {"hits": ({"note_path": "C:\\Users\\person\\private.md"},)}
        )


def test_generated_section_preserves_manual_prose() -> None:
    original = "# Note\nmanual before\n<!-- sb:generated canonical:start -->\nold\n<!-- sb:generated canonical:end -->\nmanual after\n"
    updated = replace_generated_section(original, "canonical", "new")
    assert "manual before" in updated and "manual after" in updated and "new" in updated
    assert "old" not in updated
    with pytest.raises(GeneratedSectionError):
        replace_generated_section("# malformed", "canonical", "new")


def test_mojibake_repair_handles_punctuation_and_hebrew() -> None:
    assert repair_mojibake("AIâ€”Verified Intelligence") == "AI—Verified Intelligence"
    assert repair_mojibake("×—×¤×©×©") == "חפשש"
    assert repair_mojibake("normal café × 3") == "normal café × 3"
