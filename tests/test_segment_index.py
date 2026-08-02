import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from second_brain_protocol.segment_index import (
    ChangedSegmentIndexer,
    SQLiteSegmentManifest,
)
from second_brain_protocol.state import StateStore


FIRST = """---
title: Preferences
---

# Preferences

## Working style

- Prefer concise receipts.
- Preserve exact evidence links.

Longer explanations are useful when a decision has meaningful tradeoffs.
"""


class Backend:
    def __init__(self) -> None:
        self.calls = []

    def apply(self, change_set) -> None:
        self.calls.append(change_set)


def test_changed_segment_index_embeds_only_added_or_changed_text(
    tmp_path: Path,
) -> None:
    manifest = SQLiteSegmentManifest(StateStore(tmp_path / "state.sqlite"))
    backend = Backend()
    indexer = ChangedSegmentIndexer(manifest=manifest, backend=backend)

    first = indexer.index("Identity/Preferences.md", FIRST)
    changed = indexer.index(
        "Identity/Preferences.md",
        FIRST.replace(
            "Preserve exact evidence links.",
            "Preserve exact evidence and checkpoint links.",
        ),
    )

    assert len(first.upserts) == 3
    assert first.deleted_segment_ids == ()
    assert len(changed.upserts) == 1
    assert len(changed.deleted_segment_ids) == 1
    assert len(changed.unchanged_segment_ids) == 2
    assert len(backend.calls) == 2


def test_reordering_unchanged_segments_reuses_all_embeddings(tmp_path: Path) -> None:
    manifest = SQLiteSegmentManifest(StateStore(tmp_path / "state.sqlite"))
    backend = Backend()
    indexer = ChangedSegmentIndexer(manifest=manifest, backend=backend)
    indexer.index("Identity/Preferences.md", FIRST)
    reordered = FIRST.replace(
        "- Prefer concise receipts.\n- Preserve exact evidence links.",
        "- Preserve exact evidence links.\n- Prefer concise receipts.",
    )

    change = indexer.index("Identity/Preferences.md", reordered)

    assert change.upserts == ()
    assert change.deleted_segment_ids == ()
    assert len(change.unchanged_segment_ids) == 3
    assert [item.ordinal for item in manifest.records("Identity/Preferences.md")] == [
        0,
        1,
        2,
    ]


def test_deleted_note_removes_only_its_segment_records(tmp_path: Path) -> None:
    manifest = SQLiteSegmentManifest(StateStore(tmp_path / "state.sqlite"))
    backend = Backend()
    indexer = ChangedSegmentIndexer(manifest=manifest, backend=backend)
    first = indexer.index("Identity/Preferences.md", FIRST)
    indexer.index("Memory/Lessons.md", "# Lessons\n\nKeep retries idempotent.\n")

    removed = indexer.index("Identity/Preferences.md", None)

    assert set(removed.deleted_segment_ids) == {
        item.segment_id for item in first.upserts
    }
    assert manifest.records("Identity/Preferences.md") == ()
    assert len(manifest.records("Memory/Lessons.md")) == 1


def test_segment_manifest_reopens_without_reembedding_unchanged_text(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "state.sqlite"
    backend = Backend()
    ChangedSegmentIndexer(
        manifest=SQLiteSegmentManifest(StateStore(state_path)),
        backend=backend,
    ).index("Identity/Preferences.md", FIRST)

    reopened = ChangedSegmentIndexer(
        manifest=SQLiteSegmentManifest(StateStore(state_path)),
        backend=backend,
    ).index("Identity/Preferences.md", FIRST)

    assert reopened.upserts == ()
    assert reopened.deleted_segment_ids == ()
    assert len(reopened.unchanged_segment_ids) == 3


def test_bom_and_crlf_frontmatter_is_excluded_from_segments(tmp_path: Path) -> None:
    manifest = SQLiteSegmentManifest(StateStore(tmp_path / "state.sqlite"))
    indexer = ChangedSegmentIndexer(manifest=manifest, backend=Backend())

    change = indexer.index(
        "Identity/Preferences.md",
        "\ufeff---\r\ntitle: Private metadata\r\n---\r\n\r\n# Preferences\r\n\r\nKeep the body.\r\n",
    )

    assert len(change.retained) == 1
    assert "Private metadata" not in change.retained[0].text
    assert change.retained[0].text == "Preferences\nKeep the body."


def test_unclosed_frontmatter_preserves_previous_manifest_and_backend(
    tmp_path: Path,
) -> None:
    manifest = SQLiteSegmentManifest(StateStore(tmp_path / "state.sqlite"))
    backend = Backend()
    indexer = ChangedSegmentIndexer(manifest=manifest, backend=backend)
    indexer.index("Identity/Preferences.md", FIRST)
    before = manifest.records("Identity/Preferences.md")

    with pytest.raises(RuntimeError, match="frontmatter"):
        indexer.index(
            "Identity/Preferences.md",
            "---\ntitle: Broken\n# This is still frontmatter\n",
        )

    assert manifest.records("Identity/Preferences.md") == before
    assert len(backend.calls) == 1


@pytest.mark.parametrize(
    "alias",
    (
        "Identity/./Preferences.md",
        "Identity//Preferences.md",
    ),
)
def test_noncanonical_path_spellings_are_rejected(
    tmp_path: Path,
    alias: str,
) -> None:
    manifest = SQLiteSegmentManifest(StateStore(tmp_path / "state.sqlite"))
    backend = Backend()

    with pytest.raises(ValueError, match="canonical Markdown paths"):
        ChangedSegmentIndexer(manifest=manifest, backend=backend).index(alias, FIRST)

    assert backend.calls == []


def test_case_variant_cannot_claim_an_existing_source_identity(
    tmp_path: Path,
) -> None:
    manifest = SQLiteSegmentManifest(StateStore(tmp_path / "state.sqlite"))
    backend = Backend()
    indexer = ChangedSegmentIndexer(manifest=manifest, backend=backend)
    indexer.index("Identity/Preferences.md", FIRST)

    with pytest.raises(ValueError, match="path alias"):
        indexer.index("identity/Preferences.md", FIRST)

    assert len(backend.calls) == 1
    assert len(manifest.records("Identity/Preferences.md")) == 3


def test_concurrent_indexers_serialize_backend_mutation_with_manifest_commit(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "state.sqlite"
    initial_manifest = SQLiteSegmentManifest(StateStore(state_path))
    ChangedSegmentIndexer(
        manifest=initial_manifest,
        backend=Backend(),
    ).index("Identity/Preferences.md", FIRST)

    class BlockingBackend:
        def __init__(self) -> None:
            self.started = threading.Event()
            self.second_started = threading.Event()
            self.release = threading.Event()
            self._lock = threading.Lock()
            self.segment_ids = {
                item.segment_id
                for item in initial_manifest.records("Identity/Preferences.md")
            }

        def apply(self, change_set) -> None:
            if any("first-worker" in item.text for item in change_set.upserts):
                self.started.set()
                assert self.release.wait(timeout=5)
            if any("second-worker" in item.text for item in change_set.upserts):
                self.second_started.set()
            with self._lock:
                self.segment_ids.difference_update(change_set.deleted_segment_ids)
                self.segment_ids.update(item.segment_id for item in change_set.upserts)

    backend = BlockingBackend()
    first_text = FIRST.replace(
        "Prefer concise receipts.",
        "Prefer concise first-worker receipts.",
    )
    second_text = FIRST.replace(
        "Prefer concise receipts.",
        "Prefer concise second-worker receipts.",
    )

    def index(markdown: str):
        return ChangedSegmentIndexer(
            manifest=SQLiteSegmentManifest(StateStore(state_path)),
            backend=backend,
        ).index("Identity/Preferences.md", markdown)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(index, first_text)
        assert backend.started.wait(timeout=5)
        second = executor.submit(index, second_text)
        backend.second_started.wait(timeout=0.25)
        backend.release.set()
        first.result(timeout=5)
        second.result(timeout=5)

    final_records = SQLiteSegmentManifest(StateStore(state_path)).records(
        "Identity/Preferences.md"
    )
    assert any("second-worker" in item.text for item in final_records)
    assert backend.segment_ids == {item.segment_id for item in final_records}
