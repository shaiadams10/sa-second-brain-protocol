from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Protocol

from .canonical_paths import is_indexable_markdown
from .recall_backends import stable_source_id
from .state import StateStore, utc_now


SEGMENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS canonical_segments (
  segment_id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  path TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  segment_type TEXT NOT NULL,
  text_hash TEXT NOT NULL,
  text TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(path,ordinal)
);
CREATE INDEX IF NOT EXISTS canonical_segments_path_idx
ON canonical_segments(path,ordinal);
CREATE TABLE IF NOT EXISTS canonical_segment_paths (
  path_key TEXT PRIMARY KEY,
  path TEXT NOT NULL UNIQUE
);
"""
GENERATED_MARKER = re.compile(r"<!-- sb:generated [a-z0-9-]+:(?:start|end) -->")
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
LIST_ITEM = re.compile(r"^\s*(?:[-*+] |\d+[.)] )(.+?)\s*$")
MAX_SEGMENT_CHARS = 1200


@dataclass(frozen=True)
class CanonicalSegment:
    segment_id: str
    source_id: str
    path: str
    ordinal: int
    segment_type: str
    text_hash: str
    text: str


@dataclass(frozen=True)
class SegmentChangeSet:
    path: str
    baseline_fingerprint: str
    retained: tuple[CanonicalSegment, ...]
    upserts: tuple[CanonicalSegment, ...]
    unchanged_segment_ids: tuple[str, ...]
    deleted_segment_ids: tuple[str, ...]


class SegmentBackend(Protocol):
    def apply(self, change_set: SegmentChangeSet) -> None:
        """Delete disappeared IDs and embed only `upserts`, idempotently."""


class SQLiteSegmentManifest:
    def __init__(self, store: StateStore) -> None:
        self._store = store
        with store.connect() as connection:
            connection.executescript(SEGMENT_SCHEMA)
            _backfill_path_identities(connection)

    def records(self, path: str) -> tuple[CanonicalSegment, ...]:
        path_key = _canonical_path_key(path)
        with self._store.connect() as connection:
            _reject_registered_alias(connection, path_key=path_key, path=path)
            rows = connection.execute(
                "SELECT * FROM canonical_segments WHERE path=? ORDER BY ordinal",
                (path,),
            ).fetchall()
        return tuple(_segment_from_row(row) for row in rows)

    def apply(
        self,
        path: str,
        markdown: str | None,
        *,
        backend: SegmentBackend,
    ) -> SegmentChangeSet:
        """Serialize backend mutation and manifest replacement across processes."""

        path_key = _canonical_path_key(path)
        retained = segment_markdown(path, markdown) if markdown is not None else ()
        with self._store.transaction() as connection:
            _reject_registered_alias(connection, path_key=path_key, path=path)
            connection.execute(
                "INSERT OR IGNORE INTO canonical_segment_paths(path_key,path) VALUES(?,?)",
                (path_key, path),
            )
            rows = connection.execute(
                "SELECT * FROM canonical_segments WHERE path=? ORDER BY ordinal",
                (path,),
            ).fetchall()
            existing = tuple(_segment_from_row(row) for row in rows)
            change_set = _change_set(
                path=path,
                existing=existing,
                retained=retained,
            )
            backend.apply(change_set)
            connection.execute(
                "DELETE FROM canonical_segments WHERE path=?",
                (path,),
            )
            connection.executemany(
                """INSERT INTO canonical_segments(
                segment_id,source_id,path,ordinal,segment_type,text_hash,text,updated_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                [
                    (
                        item.segment_id,
                        item.source_id,
                        item.path,
                        item.ordinal,
                        item.segment_type,
                        item.text_hash,
                        item.text,
                        utc_now(),
                    )
                    for item in change_set.retained
                ],
            )
        return change_set


class ChangedSegmentIndexer:
    def __init__(
        self,
        *,
        manifest: SQLiteSegmentManifest,
        backend: SegmentBackend,
    ) -> None:
        self._manifest = manifest
        self._backend = backend

    def index(self, path: str, markdown: str | None) -> SegmentChangeSet:
        return self._manifest.apply(path, markdown, backend=self._backend)


def segment_markdown(
    path: str,
    markdown: str,
) -> tuple[CanonicalSegment, ...]:
    _canonical_path_key(path)
    source_id = stable_source_id(path)
    segment_type = path.split("/", 1)[0].casefold()
    headings: list[str] = []
    blocks: list[str] = []
    paragraph: list[str] = []
    lines = markdown.splitlines()
    if lines:
        lines[0] = lines[0].removeprefix("\ufeff")
    body_start = 0
    if lines and lines[0].strip() == "---":
        closing = next(
            (
                index
                for index, line in enumerate(lines[1:], start=1)
                if line.strip() == "---"
            ),
            None,
        )
        if closing is None:
            raise RuntimeError("Canonical Markdown frontmatter is unclosed")
        body_start = closing + 1

    def flush() -> None:
        if not paragraph:
            return
        body = " ".join(value.strip() for value in paragraph if value.strip())
        paragraph.clear()
        if body:
            blocks.extend(_bounded_blocks(_with_heading_context(headings, body)))

    for line in lines[body_start:]:
        if GENERATED_MARKER.fullmatch(line.strip()):
            flush()
            continue
        heading = HEADING.match(line)
        if heading:
            flush()
            level = len(heading.group(1))
            headings = headings[: level - 1]
            headings.append(heading.group(2).strip())
            continue
        list_item = LIST_ITEM.match(line)
        if list_item:
            flush()
            blocks.extend(
                _bounded_blocks(
                    _with_heading_context(headings, list_item.group(1).strip())
                )
            )
            continue
        if not line.strip():
            flush()
            continue
        paragraph.append(line)
    flush()

    occurrences: dict[str, int] = {}
    result: list[CanonicalSegment] = []
    for ordinal, text in enumerate(blocks):
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        occurrence = occurrences.get(text_hash, 0)
        occurrences[text_hash] = occurrence + 1
        segment_id = "segment-" + hashlib.sha256(
            f"{source_id}\0{text_hash}\0{occurrence}".encode("utf-8")
        ).hexdigest()[:24]
        result.append(
            CanonicalSegment(
                segment_id=segment_id,
                source_id=source_id,
                path=path,
                ordinal=ordinal,
                segment_type=segment_type,
                text_hash=text_hash,
                text=text,
            )
        )
    return tuple(result)


def _canonical_path_key(path: str) -> str:
    normalized = PurePosixPath(path)
    if (
        not is_indexable_markdown(path)
        or normalized.is_absolute()
        or normalized.as_posix() != path
        or "\\" in path
    ):
        raise ValueError("Segment manifests require canonical Markdown paths")
    return path.casefold()


def _reject_registered_alias(connection, *, path_key: str, path: str) -> None:
    row = connection.execute(
        "SELECT path FROM canonical_segment_paths WHERE path_key=?",
        (path_key,),
    ).fetchone()
    if row is not None and row["path"] != path:
        raise ValueError("Segment path alias conflicts with canonical identity")


def _backfill_path_identities(connection) -> None:
    paths = tuple(
        str(row["path"])
        for row in connection.execute(
            "SELECT DISTINCT path FROM canonical_segments ORDER BY path"
        ).fetchall()
    )
    observed: dict[str, str] = {}
    for path in paths:
        path_key = _canonical_path_key(path)
        if path_key in observed and observed[path_key] != path:
            raise RuntimeError("Existing segment paths contain identity aliases")
        observed[path_key] = path
        _reject_registered_alias(connection, path_key=path_key, path=path)
        connection.execute(
            "INSERT OR IGNORE INTO canonical_segment_paths(path_key,path) VALUES(?,?)",
            (path_key, path),
        )


def _with_heading_context(headings: list[str], body: str) -> str:
    breadcrumb = " > ".join(headings)
    return f"{breadcrumb}\n{body}" if breadcrumb else body


def _bounded_blocks(text: str) -> list[str]:
    return [
        text[offset : offset + MAX_SEGMENT_CHARS]
        for offset in range(0, len(text), MAX_SEGMENT_CHARS)
    ] or [""]


def _records_fingerprint(records: tuple[CanonicalSegment, ...]) -> str:
    payload = [
        {
            "segment_id": item.segment_id,
            "ordinal": item.ordinal,
            "text_hash": item.text_hash,
        }
        for item in records
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _change_set(
    *,
    path: str,
    existing: tuple[CanonicalSegment, ...],
    retained: tuple[CanonicalSegment, ...],
) -> SegmentChangeSet:
    existing_by_id = {item.segment_id: item for item in existing}
    retained_by_id = {item.segment_id: item for item in retained}
    unchanged = tuple(sorted(set(existing_by_id).intersection(retained_by_id)))
    upserts = tuple(
        item for item in retained if item.segment_id not in existing_by_id
    )
    deleted = tuple(sorted(set(existing_by_id) - set(retained_by_id)))
    return SegmentChangeSet(
        path=path,
        baseline_fingerprint=_records_fingerprint(existing),
        retained=retained,
        upserts=upserts,
        unchanged_segment_ids=unchanged,
        deleted_segment_ids=deleted,
    )


def _segment_from_row(row) -> CanonicalSegment:
    return CanonicalSegment(
        segment_id=row["segment_id"],
        source_id=row["source_id"],
        path=row["path"],
        ordinal=int(row["ordinal"]),
        segment_type=row["segment_type"],
        text_hash=row["text_hash"],
        text=row["text"],
    )
