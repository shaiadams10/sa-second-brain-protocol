from pathlib import Path

import pytest

from second_brain_protocol.config import RuntimePaths
from second_brain_protocol.recall_backends import (
    BasicMemoryVectorBackend,
    CanonicalLexicalBackend,
    stable_source_id,
)
from second_brain_protocol.recall_harness import RecallHarness, RecallRequest


def _note(vault: Path, relative: str, text: str) -> Path:
    path = vault / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_lexical_and_basic_memory_share_stable_source_identity(tmp_path) -> None:
    vault = tmp_path / "vault"
    _note(
        vault,
        "Memory/Decisions.md",
        "# Decisions\n\nKeep the scheduler authoritative during shadow cutover.\n",
    )
    vector = BasicMemoryVectorBackend(
        paths=RuntimePaths.from_root(tmp_path / "runtime"),
        vault=vault,
        search_fn=lambda _paths, _query, *, limit: [
            {
                "file_path": "Memory/Decisions.md",
                "title": "Decisions",
                "matched_chunk": "Keep the scheduler authoritative during shadow cutover.",
                "score": 0.91,
            }
        ][:limit],
    )

    packet = RecallHarness(
        lexical=CanonicalLexicalBackend(vault),
        vector=vector,
    ).retrieve(RecallRequest(query="scheduler authoritative shadow cutover"))

    assert len(packet.hits) == 1
    assert packet.hits[0].source_id == stable_source_id("Memory/Decisions.md")
    assert packet.hits[0].provenance == ("lexical", "vector")


def test_lexical_backend_ranks_matching_canonical_notes_and_excludes_raw(tmp_path) -> None:
    vault = tmp_path / "vault"
    _note(
        vault,
        "Memory/Decisions.md",
        "# Scheduler Decision\n\nShadow scheduler cutover requires evaluation.\n",
    )
    _note(vault, "Memory/Other.md", "# Other\n\nScheduler only.\n")
    _note(
        vault,
        "Inbox/Raw/session.md",
        "# Raw\n\nShadow scheduler cutover requires evaluation.\n",
    )
    _note(
        vault,
        "Protocol/.venv/package/README.md",
        "# Dependency\n\nShadow scheduler cutover requires evaluation.\n",
    )

    candidates = CanonicalLexicalBackend(vault).search(
        "shadow scheduler cutover", limit=10
    )

    assert candidates[0].note_path == "Memory/Decisions.md"
    assert "Inbox/Raw/session.md" not in {
        item.note_path for item in candidates
    }
    assert "Protocol/.venv/package/README.md" not in {
        item.note_path for item in candidates
    }
    assert all(not Path(item.note_path).is_absolute() for item in candidates)


def test_basic_memory_backend_rejects_untrusted_or_missing_paths(tmp_path) -> None:
    vault = tmp_path / "vault"
    _note(vault, "Memory/Safe.md", "# Safe\n\nBounded canonical note.\n")
    rows = [
        None,
        "malformed-row",
        {
            "file_path": "C:/Users/person/private.md",
            "title": "Absolute",
            "matched_chunk": "private",
            "score": 1.0,
        },
        {
            "file_path": "Identity/passport.md",
            "title": "Sensitive",
            "matched_chunk": "private",
            "score": 0.9,
        },
        {
            "file_path": "Memory/Missing.md",
            "title": "Missing",
            "matched_chunk": "stale index row",
            "score": 0.8,
        },
        {
            "file_path": "Memory/Safe.md",
            "title": "Safe",
            "matched_chunk": "bounded canonical note",
            "score": 0.7,
        },
    ]
    backend = BasicMemoryVectorBackend(
        paths=RuntimePaths.from_root(tmp_path / "runtime"),
        vault=vault,
        search_fn=lambda _paths, _query, *, limit: rows[:limit],
    )

    candidates = backend.search("canonical", limit=10)

    assert [item.note_path for item in candidates] == ["Memory/Safe.md"]


def test_stable_source_id_rejects_unsafe_note_paths() -> None:
    with pytest.raises(ValueError, match="canonical relative Markdown"):
        stable_source_id("../raw/session.md")


def test_lexical_backend_never_follows_out_of_vault_note_symlink(tmp_path) -> None:
    vault = tmp_path / "vault"
    linked = vault / "Memory" / "Linked.md"
    linked.parent.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("# External\n\nunique external transcript phrase\n", encoding="utf-8")
    try:
        linked.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"File symlinks unavailable: {error}")

    candidates = CanonicalLexicalBackend(vault).search(
        "unique external transcript phrase", limit=5
    )

    assert candidates == ()


def test_lexical_backend_never_follows_symlink_into_raw_evidence(tmp_path) -> None:
    vault = tmp_path / "vault"
    raw = _note(
        vault,
        "Evidence/Raw/session.md",
        "# Raw\n\nunique in-vault raw transcript phrase\n",
    )
    linked = vault / "Memory" / "Linked.md"
    linked.parent.mkdir(parents=True)
    try:
        linked.symlink_to(raw)
    except OSError as error:
        pytest.skip(f"File symlinks unavailable: {error}")

    assert CanonicalLexicalBackend(vault).search(
        "unique in-vault raw transcript phrase", limit=5
    ) == ()


def test_vector_backend_never_follows_symlink_into_raw_evidence(tmp_path) -> None:
    vault = tmp_path / "vault"
    raw = _note(vault, "Evidence/Raw/session.md", "# Raw\n\nprivate\n")
    linked = vault / "Memory" / "Linked.md"
    linked.parent.mkdir(parents=True)
    try:
        linked.symlink_to(raw)
    except OSError as error:
        pytest.skip(f"File symlinks unavailable: {error}")
    backend = BasicMemoryVectorBackend(
        paths=RuntimePaths.from_root(tmp_path / "runtime"),
        vault=vault,
        search_fn=lambda _paths, _query, *, limit: [
            {
                "file_path": "Memory/Linked.md",
                "title": "Linked",
                "matched_chunk": "private",
                "score": 0.9,
            }
        ],
    )

    assert backend.search("private", limit=5) == ()


def test_backends_reject_safe_symlink_aliases_of_canonical_notes(tmp_path) -> None:
    vault = tmp_path / "vault"
    target = _note(vault, "Memory/Target.md", "# Target\n\nunique alias phrase\n")
    alias = vault / "Memory" / "Alias.md"
    try:
        alias.symlink_to(target)
    except OSError as error:
        pytest.skip(f"File symlinks unavailable: {error}")

    lexical = CanonicalLexicalBackend(vault).search("unique alias phrase", limit=5)
    vector = BasicMemoryVectorBackend(
        paths=RuntimePaths.from_root(tmp_path / "runtime"),
        vault=vault,
        search_fn=lambda _paths, _query, *, limit: [
            {
                "file_path": "Memory/Alias.md",
                "title": "Alias",
                "matched_chunk": "unique alias phrase",
                "score": 0.9,
            }
        ],
    ).search("unique alias phrase", limit=5)

    assert [item.note_path for item in lexical] == ["Memory/Target.md"]
    assert vector == ()
