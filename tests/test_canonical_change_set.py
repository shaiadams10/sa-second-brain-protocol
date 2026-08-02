from pathlib import Path

from second_brain_protocol.canonical_paths import (
    canonical_markdown_fingerprints,
    changed_canonical_markdown_paths,
)


def test_canonical_change_set_reports_only_added_changed_and_deleted_notes(
    tmp_path: Path,
) -> None:
    (tmp_path / "Identity").mkdir()
    (tmp_path / "Memory").mkdir()
    (tmp_path / "Identity" / "Keep.md").write_bytes(b"unchanged\n")
    changed = tmp_path / "Identity" / "Changed.md"
    changed.write_bytes(b"before\n")
    deleted = tmp_path / "Memory" / "Deleted.md"
    deleted.write_bytes(b"remove me\n")
    (tmp_path / "Inbox" / "Raw").mkdir(parents=True)
    raw = tmp_path / "Inbox" / "Raw" / "Secret.md"
    raw.write_bytes(b"excluded\n")
    before = canonical_markdown_fingerprints(tmp_path)

    changed.write_bytes(b"after\n")
    deleted.unlink()
    (tmp_path / "Memory" / "Added.md").write_bytes(b"new\n")
    raw.write_bytes(b"still excluded and changed\n")
    after = canonical_markdown_fingerprints(tmp_path)

    assert changed_canonical_markdown_paths(before, after) == [
        "Identity/Changed.md",
        "Memory/Added.md",
        "Memory/Deleted.md",
    ]
