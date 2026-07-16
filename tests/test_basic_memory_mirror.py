from pathlib import Path

from second_brain_protocol.basic_memory_integration import build_index_mirror
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
