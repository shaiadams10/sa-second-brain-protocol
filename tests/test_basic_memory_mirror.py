from pathlib import Path
from subprocess import CompletedProcess

import second_brain_protocol.basic_memory_integration as memory_module

from second_brain_protocol.basic_memory_integration import (
    build_index_mirror,
    search_vector,
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
    dependency = vault / "Protocol" / ".venv" / "package" / "README.md"
    dependency.parent.mkdir(parents=True)
    dependency.write_text("third-party package documentation\n", encoding="utf-8")
    paths = RuntimePaths.from_root(tmp_path / "runtime")
    paths.basic_memory.mkdir(parents=True)
    before = note.read_bytes()
    mirror = build_index_mirror(paths, vault)
    assert (mirror / "Identity" / "Persona.md").read_text(encoding="utf-8") == "# Persona\n"
    assert not (mirror / "Evidence" / "Raw" / "chat.md").exists()
    assert not (mirror / "Protocol" / ".venv" / "package" / "README.md").exists()
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


def test_vector_search_uses_vector_only_basic_memory_mode(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(_paths, *args: str, timeout: int):
        calls.append(args)
        return CompletedProcess(
            args=args,
            returncode=0,
            stdout='[{"file_path":"Memory/Lessons.md","score":0.8}]',
            stderr="",
        )

    monkeypatch.setattr(memory_module, "_run", fake_run)

    rows = search_vector(
        RuntimePaths.from_root(tmp_path / "runtime"), "semantic lesson", limit=3
    )

    assert rows[0]["score"] == 0.8
    assert "--vector" in calls[0]
    assert "--hybrid" not in calls[0]


def test_vector_search_rejects_dictionary_shaped_result_collection(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        memory_module,
        "_run",
        lambda _paths, *args, timeout: CompletedProcess(
            args=args,
            returncode=0,
            stdout='{"results":{"unexpected":"mapping"}}',
            stderr="",
        ),
    )

    assert search_vector(RuntimePaths.from_root(tmp_path), "query") == []
