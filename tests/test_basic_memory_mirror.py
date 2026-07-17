from pathlib import Path

from second_brain_protocol.basic_memory_integration import (
    build_index_mirror,
    update_index_mirror,
)
from second_brain_protocol.config import RuntimePaths


def test_basic_memory_indexes_runtime_mirror_without_touching_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    note = vault / "Identity" / "Persona.md"
    note.parent.mkdir(parents=True)
    note.write_text("# Persona\n", encoding="utf-8")
    raw = vault / "Evidence" / "Raw" / "chat.md"
    raw.parent.mkdir(parents=True)
    raw.write_text("secret raw chat\n", encoding="utf-8")
    paths = RuntimePaths.from_root(tmp_path / "runtime")
    paths.basic_memory.mkdir(parents=True)
    before = note.read_bytes()
    mirror = build_index_mirror(paths, vault)
    assert (mirror / "Identity" / "Persona.md").read_text(encoding="utf-8") == "# Persona\n"
    assert not (mirror / "Evidence" / "Raw" / "chat.md").exists()
    assert note.read_bytes() == before


def test_incremental_mirror_updates_only_named_notes(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    preferences = vault / "Identity" / "Preferences.md"
    lessons = vault / "Memory" / "Lessons.md"
    preferences.parent.mkdir(parents=True)
    lessons.parent.mkdir(parents=True)
    preferences.write_text("version one\n", encoding="utf-8")
    lessons.write_text("lesson one\n", encoding="utf-8")
    paths = RuntimePaths.from_root(tmp_path / "runtime")
    paths.basic_memory.mkdir(parents=True)
    mirror = build_index_mirror(paths, vault)

    preferences.write_text("version two\n", encoding="utf-8")
    lessons.write_text("lesson two\n", encoding="utf-8")
    update_index_mirror(paths, vault, ["Identity/Preferences.md"])

    assert (mirror / "Identity" / "Preferences.md").read_text(encoding="utf-8") == "version two\n"
    assert (mirror / "Memory" / "Lessons.md").read_text(encoding="utf-8") == "lesson one\n"
