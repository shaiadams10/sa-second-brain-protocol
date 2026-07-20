from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator


SQL_CHUNK_SIZE = 500


def _chunks(
    values: list[str] | set[str], size: int = SQL_CHUNK_SIZE
) -> Iterator[list[str]]:
    ordered = sorted(values)
    for index in range(0, len(ordered), size):
        yield ordered[index : index + size]


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS checkpoints (
  source_key TEXT PRIMARY KEY,
  cursor TEXT,
  fingerprint TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collection_receipts (
  source_key TEXT PRIMARY KEY,
  fingerprint TEXT NOT NULL,
  completed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence (
  id TEXT PRIMARY KEY,
  source_type TEXT NOT NULL,
  source_ref TEXT NOT NULL,
  project_id TEXT,
  kind TEXT NOT NULL,
  occurred_at TEXT,
  content_hash TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'new',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS evidence_status_idx ON evidence(status);
CREATE INDEX IF NOT EXISTS evidence_project_idx ON evidence(project_id);
CREATE INDEX IF NOT EXISTS evidence_source_idx ON evidence(source_type,source_ref);

CREATE TABLE IF NOT EXISTS evidence_checkpoints (
  evidence_id TEXT NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
  source_key TEXT NOT NULL,
  cursor TEXT,
  fingerprint TEXT,
  PRIMARY KEY(evidence_id, source_key)
);

CREATE TABLE IF NOT EXISTS evidence_derivations (
  derived_evidence_id TEXT NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
  source_evidence_id TEXT NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
  PRIMARY KEY(derived_evidence_id, source_evidence_id)
);

CREATE TABLE IF NOT EXISTS observations (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  subject TEXT NOT NULL,
  claim TEXT NOT NULL,
  evidence_refs_json TEXT NOT NULL,
  confidence REAL NOT NULL,
  source_count INTEGER NOT NULL,
  project_count INTEGER NOT NULL,
  sensitivity TEXT NOT NULL,
  promotion_tier TEXT NOT NULL,
  status TEXT NOT NULL,
  rejection_reason TEXT,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS observations_status_idx ON observations(status);

CREATE TABLE IF NOT EXISTS knowledge_feedback (
  observation_id TEXT PRIMARY KEY REFERENCES observations(id) ON DELETE CASCADE,
  decision TEXT NOT NULL CHECK(decision IN ('liked','disliked')),
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_feedback_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  observation_id TEXT NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
  action TEXT NOT NULL CHECK(action IN ('like','dislike','undo')),
  previous_decision TEXT,
  occurrences_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  undone_at TEXT
);

CREATE INDEX IF NOT EXISTS knowledge_feedback_events_action_idx
ON knowledge_feedback_events(action,undone_at,created_at);

CREATE TABLE IF NOT EXISTS review_feedback (
  observation_id TEXT PRIMARY KEY REFERENCES observations(id) ON DELETE CASCADE,
  decision TEXT NOT NULL CHECK(decision IN ('answered','dismissed')),
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_feedback_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  observation_id TEXT NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
  action TEXT NOT NULL CHECK(action IN ('answer','dismiss','undo')),
  previous_decision TEXT,
  previous_status TEXT,
  created_at TEXT NOT NULL,
  undone_at TEXT
);

CREATE INDEX IF NOT EXISTS review_feedback_events_action_idx
ON review_feedback_events(action,undone_at,created_at);

CREATE TABLE IF NOT EXISTS search_refresh_queue (
  path TEXT PRIMARY KEY,
  queued_at TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT
);

CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  logical_name TEXT NOT NULL,
  classification TEXT NOT NULL,
  remote_url TEXT,
  initial_commit TEXT,
  head_commit TEXT,
  fingerprint TEXT,
  metadata_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_presence (
  project_id TEXT PRIMARY KEY,
  present INTEGER NOT NULL,
  last_seen_at TEXT,
  missing_since TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_path_aliases (
  normalized_path TEXT NOT NULL,
  project_id TEXT NOT NULL,
  source TEXT NOT NULL,
  confidence REAL NOT NULL,
  is_current INTEGER NOT NULL DEFAULT 0,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  PRIMARY KEY(normalized_path,project_id)
);

CREATE INDEX IF NOT EXISTS project_path_aliases_project_idx
ON project_path_aliases(project_id,is_current);

CREATE TABLE IF NOT EXISTS session_sources (
  source_key TEXT PRIMARY KEY,
  surface TEXT NOT NULL,
  session_id TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  workspace_hash TEXT,
  project_id TEXT,
  status TEXT NOT NULL CHECK(status IN ('matched','unmatched','ambiguous')),
  resolver TEXT NOT NULL,
  confidence REAL NOT NULL,
  ingested INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS session_sources_session_idx
ON session_sources(surface,session_id,status);

CREATE TABLE IF NOT EXISTS session_project_index (
  surface TEXT NOT NULL,
  session_id TEXT NOT NULL,
  project_id TEXT,
  status TEXT NOT NULL CHECK(status IN ('matched','unmatched','ambiguous')),
  resolver TEXT NOT NULL,
  confidence REAL NOT NULL,
  workspace_count INTEGER NOT NULL,
  source_record_count INTEGER NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(surface,session_id)
);

CREATE INDEX IF NOT EXISTS session_project_index_project_idx
ON session_project_index(project_id,status);

CREATE TABLE IF NOT EXISTS project_session_analysis (
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  evidence_id TEXT NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
  run_id TEXT NOT NULL,
  analyzed_at TEXT NOT NULL,
  PRIMARY KEY(project_id,evidence_id)
);

CREATE INDEX IF NOT EXISTS project_session_analysis_project_idx
ON project_session_analysis(project_id,analyzed_at);

CREATE TABLE IF NOT EXISTS pattern_signals (
  pattern_key TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  label TEXT NOT NULL,
  claim TEXT NOT NULL,
  evidence_refs_json TEXT NOT NULL,
  confidence REAL NOT NULL,
  explicit INTEGER NOT NULL,
  session_count INTEGER NOT NULL,
  date_count INTEGER NOT NULL,
  project_count INTEGER NOT NULL,
  status TEXT NOT NULL,
  observation_id TEXT,
  rejection_reason TEXT,
  payload_json TEXT NOT NULL,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS pattern_signals_status_idx ON pattern_signals(status);

CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  model TEXT,
  reasoning TEXT,
  evidence_count INTEGER NOT NULL DEFAULT 0,
  receipt_path TEXT,
  error TEXT
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  trigger TEXT NOT NULL,
  status TEXT NOT NULL,
  stage TEXT NOT NULL,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  error TEXT
);

CREATE INDEX IF NOT EXISTS pipeline_runs_started_idx
ON pipeline_runs(started_at DESC);

CREATE TABLE IF NOT EXISTS run_usage (
  run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
  usage_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS summary_runs (
  kind TEXT NOT NULL,
  period TEXT NOT NULL,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  PRIMARY KEY(kind,period,run_id)
);

CREATE TABLE IF NOT EXISTS bootstrap (
  singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
  state TEXT NOT NULL,
  generation TEXT NOT NULL,
  review_path TEXT,
  approved_at TEXT,
  updated_at TEXT NOT NULL
);
"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def canonical_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _checkpoint_cursor_position(cursor: str | None) -> tuple[int, int]:
    if not cursor:
        return (0, -1)
    try:
        parsed = json.loads(cursor)
    except (json.JSONDecodeError, TypeError):
        return (0, -1)
    if not isinstance(parsed, dict):
        return (0, -1)
    if parsed.get("kind") == "jsonl":
        return (1, int(parsed.get("offset", -1)))
    if parsed.get("kind") == "sqlite-steps":
        return (2, int(parsed.get("row_idx", -1)))
    return (0, -1)


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            connection.execute(
                "INSERT OR IGNORE INTO bootstrap(singleton,state,generation,updated_at) VALUES(1,'not_started',?,?)",
                (str(uuid.uuid4()), utc_now()),
            )

    def backup(self, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(destination)
        try:
            with self.connect() as source:
                source.backup(target)
        finally:
            target.close()
        backups = sorted(
            destination.parent.glob("state-*.sqlite"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for old in backups[20:]:
            old.unlink()
        return destination

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def bootstrap_state(self) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM bootstrap WHERE singleton=1"
            ).fetchone()
        return dict(row)

    def set_bootstrap_state(
        self, state: str, *, review_path: str | None = None
    ) -> None:
        allowed = {
            "not_started",
            "collecting",
            "synthesis_ready",
            "awaiting_review",
            "completed",
        }
        if state not in allowed:
            raise ValueError(f"Invalid bootstrap state: {state}")
        current = self.bootstrap_state()
        if current["state"] == "completed" and state != "completed":
            raise RuntimeError(
                "Completed bootstrap cannot be reopened; use incremental commands."
            )
        approved_at = utc_now() if state == "completed" else current.get("approved_at")
        with self.connect() as connection:
            connection.execute(
                "UPDATE bootstrap SET state=?,review_path=COALESCE(?,review_path),approved_at=?,updated_at=? WHERE singleton=1",
                (state, review_path, approved_at, utc_now()),
            )

    def checkpoint(self, source_key: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM checkpoints WHERE source_key=?", (source_key,)
            ).fetchone()
        return dict(row) if row else None

    def set_checkpoint(
        self, source_key: str, cursor: str | None, fingerprint: str | None
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO checkpoints(source_key,cursor,fingerprint,updated_at) VALUES(?,?,?,?)
                ON CONFLICT(source_key) DO UPDATE SET cursor=excluded.cursor,fingerprint=excluded.fingerprint,updated_at=excluded.updated_at""",
                (source_key, cursor, fingerprint, utc_now()),
            )

    def collection_receipt(self, source_key: str) -> dict[str, Any] | None:
        """Return a local read-completion receipt, never a publish checkpoint."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM collection_receipts WHERE source_key=?", (source_key,)
            ).fetchone()
        return dict(row) if row else None

    def set_collection_receipt(self, source_key: str, fingerprint: str) -> None:
        """Record that one source file was read fully at this fingerprint.

        This accelerates crash/integrity retries but deliberately does not move
        the ingestion checkpoint. Checkpoints still publish only after validated
        canonical output.
        """
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO collection_receipts(source_key,fingerprint,completed_at)
                VALUES(?,?,?) ON CONFLICT(source_key) DO UPDATE SET
                fingerprint=excluded.fingerprint,completed_at=excluded.completed_at""",
                (source_key, fingerprint, utc_now()),
            )

    def set_meta(self, key: str, value: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get_meta(self, key: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM meta WHERE key=?", (key,)
            ).fetchone()
        return row["value"] if row else None

    @staticmethod
    def enqueue_search_refresh(
        connection: sqlite3.Connection, relative_paths: list[str] | set[str]
    ) -> None:
        now = utc_now()
        for relative_path in sorted(set(relative_paths)):
            normalized = Path(relative_path).as_posix().lstrip("/")
            if (
                not normalized
                or normalized.startswith("../")
                or not normalized.endswith(".md")
            ):
                raise ValueError("Search refresh paths must be relative Markdown files")
            connection.execute(
                """INSERT INTO search_refresh_queue(path,queued_at,attempts,last_error)
                VALUES(?,?,0,NULL) ON CONFLICT(path) DO UPDATE SET
                queued_at=excluded.queued_at,attempts=0,last_error=NULL""",
                (normalized, now),
            )
        connection.execute(
            """INSERT INTO meta(key,value) VALUES('search_refresh_state',?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (json.dumps({"state": "pending", "updated_at": now}),),
        )

    def search_refresh_batch(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT path,queued_at,attempts,last_error FROM search_refresh_queue ORDER BY queued_at,path"
            ).fetchall()
        return [dict(row) for row in rows]

    def set_search_refresh_state(self, state: str, *, error: str | None = None) -> None:
        if state not in {"ready", "pending", "indexing", "failed"}:
            raise ValueError("Invalid search refresh state")
        self.set_meta(
            "search_refresh_state",
            json.dumps({"state": state, "error": error, "updated_at": utc_now()}),
        )

    def complete_search_refresh(self, batch: list[dict[str, Any]]) -> None:
        with self.transaction() as connection:
            for item in batch:
                connection.execute(
                    "DELETE FROM search_refresh_queue WHERE path=? AND queued_at=?",
                    (item["path"], item["queued_at"]),
                )
            pending = connection.execute(
                "SELECT COUNT(*) FROM search_refresh_queue"
            ).fetchone()[0]
            state = "pending" if pending else "ready"
            connection.execute(
                """INSERT INTO meta(key,value) VALUES('search_refresh_state',?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (json.dumps({"state": state, "updated_at": utc_now()}),),
            )

    def fail_search_refresh(self, batch: list[dict[str, Any]], error: str) -> None:
        safe_error = str(error)[:1000]
        with self.transaction() as connection:
            for item in batch:
                connection.execute(
                    """UPDATE search_refresh_queue SET attempts=attempts+1,last_error=?
                    WHERE path=? AND queued_at=?""",
                    (safe_error, item["path"], item["queued_at"]),
                )
            connection.execute(
                """INSERT INTO meta(key,value) VALUES('search_refresh_state',?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (
                    json.dumps(
                        {
                            "state": "failed",
                            "error": safe_error,
                            "updated_at": utc_now(),
                        }
                    ),
                ),
            )

    def search_refresh_status(self) -> dict[str, Any]:
        with self.connect() as connection:
            pending = int(
                connection.execute(
                    "SELECT COUNT(*) FROM search_refresh_queue"
                ).fetchone()[0]
            )
            row = connection.execute(
                "SELECT value FROM meta WHERE key='search_refresh_state'"
            ).fetchone()
        if row:
            try:
                payload = dict(json.loads(row["value"]))
            except (json.JSONDecodeError, TypeError, ValueError):
                payload = {"state": "unknown"}
        else:
            payload = {"state": "ready" if pending == 0 else "pending"}
        payload["pending"] = pending
        return payload

    def add_evidence(
        self,
        *,
        source_type: str,
        source_ref: str,
        kind: str,
        payload: dict[str, Any],
        project_id: str | None = None,
        occurred_at: str | None = None,
    ) -> tuple[str, bool]:
        content_hash = canonical_hash(payload)
        evidence_id = (
            "ev-"
            + canonical_hash(
                {
                    "source_type": source_type,
                    "source_ref": source_ref,
                    "content_hash": content_hash,
                }
            )[:24]
        )
        with self.connect() as connection:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO evidence(
                id,source_type,source_ref,project_id,kind,occurred_at,content_hash,payload_json,status,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    evidence_id,
                    source_type,
                    source_ref,
                    project_id,
                    kind,
                    occurred_at,
                    content_hash,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    "new",
                    utc_now(),
                ),
            )
        return evidence_id, cursor.rowcount == 1

    def add_evidence_batch(
        self,
        records: list[dict[str, Any]],
        *,
        source_key: str | None = None,
        fingerprint: str | None = None,
        supersede_mutable_source: bool = False,
    ) -> list[tuple[str, bool]]:
        """Insert one source file atomically without opening a transaction per record."""
        if not records:
            return []
        results: list[tuple[str, bool]] = []
        with self.transaction() as connection:
            for record in records:
                payload = dict(record["payload"])
                source_type = str(record["source_type"])
                source_ref = str(record["source_ref"])
                content_hash = canonical_hash(payload)
                evidence_id = (
                    "ev-"
                    + canonical_hash(
                        {
                            "source_type": source_type,
                            "source_ref": source_ref,
                            "content_hash": content_hash,
                        }
                    )[:24]
                )
                cursor = connection.execute(
                    """INSERT OR IGNORE INTO evidence(
                    id,source_type,source_ref,project_id,kind,occurred_at,
                    content_hash,payload_json,status,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        evidence_id,
                        source_type,
                        source_ref,
                        record.get("project_id"),
                        str(record["kind"]),
                        record.get("occurred_at"),
                        content_hash,
                        json.dumps(payload, ensure_ascii=False, sort_keys=True),
                        "new",
                        utc_now(),
                    ),
                )
                if supersede_mutable_source:
                    connection.execute(
                        """UPDATE evidence SET status='superseded'
                        WHERE source_type=? AND source_ref=? AND id<>?
                        AND status IN ('new','processed')""",
                        (source_type, source_ref, evidence_id),
                    )
                if source_key is not None:
                    connection.execute(
                        """INSERT INTO evidence_checkpoints(
                        evidence_id,source_key,cursor,fingerprint
                        ) VALUES(?,?,?,?) ON CONFLICT(evidence_id,source_key) DO UPDATE SET
                        cursor=excluded.cursor,fingerprint=excluded.fingerprint""",
                        (
                            evidence_id,
                            source_key,
                            record.get("cursor"),
                            fingerprint,
                        ),
                    )
                results.append((evidence_id, cursor.rowcount == 1))
        return results

    def add_derived_evidence(
        self,
        *,
        source_type: str,
        source_ref: str,
        kind: str,
        payload: dict[str, Any],
        source_evidence_ids: list[str],
        project_id: str | None = None,
        occurred_at: str | None = None,
    ) -> tuple[str, bool]:
        source_ids = sorted(set(source_evidence_ids))
        if not source_ids:
            raise ValueError("Derived evidence requires source evidence IDs")
        content_hash = canonical_hash(payload)
        evidence_id = (
            "ev-"
            + canonical_hash(
                {
                    "source_type": source_type,
                    "source_ref": source_ref,
                    "content_hash": content_hash,
                }
            )[:24]
        )
        with self.transaction() as connection:
            found = 0
            for batch in _chunks(source_ids):
                placeholders = ",".join("?" for _ in batch)
                found += int(
                    connection.execute(
                        f"SELECT COUNT(*) AS count FROM evidence WHERE id IN ({placeholders})",
                        batch,
                    ).fetchone()["count"]
                )
            if int(found) != len(source_ids):
                raise ValueError("Derived evidence references unknown source evidence")
            cursor = connection.execute(
                """INSERT OR IGNORE INTO evidence(
                id,source_type,source_ref,project_id,kind,occurred_at,content_hash,payload_json,status,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    evidence_id,
                    source_type,
                    source_ref,
                    project_id,
                    kind,
                    occurred_at,
                    content_hash,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    "new",
                    utc_now(),
                ),
            )
            connection.executemany(
                """INSERT OR IGNORE INTO evidence_derivations(
                derived_evidence_id,source_evidence_id
                ) VALUES(?,?)""",
                [(evidence_id, source_id) for source_id in source_ids],
            )
            for batch in _chunks(source_ids):
                placeholders = ",".join("?" for _ in batch)
                connection.execute(
                    f"UPDATE evidence SET status='compacted' WHERE id IN ({placeholders}) AND status='new'",
                    batch,
                )
        return evidence_id, cursor.rowcount == 1

    def expand_derived_evidence(self, evidence_ids: list[str]) -> list[str]:
        if not evidence_ids:
            return []
        expanded = set(evidence_ids)
        frontier = set(evidence_ids)
        with self.connect() as connection:
            while frontier:
                discovered: set[str] = set()
                for batch in _chunks(frontier):
                    placeholders = ",".join("?" for _ in batch)
                    rows = connection.execute(
                        f"""SELECT source_evidence_id FROM evidence_derivations
                        WHERE derived_evidence_id IN ({placeholders})""",
                        batch,
                    ).fetchall()
                    discovered.update(row["source_evidence_id"] for row in rows)
                discovered -= expanded
                expanded.update(discovered)
                frontier = discovered
        return sorted(expanded)

    def attach_checkpoint_candidate(
        self,
        evidence_id: str,
        *,
        source_key: str,
        cursor: str | None,
        fingerprint: str | None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO evidence_checkpoints(evidence_id,source_key,cursor,fingerprint)
                VALUES(?,?,?,?) ON CONFLICT(evidence_id,source_key) DO UPDATE SET
                cursor=excluded.cursor,fingerprint=excluded.fingerprint""",
                (evidence_id, source_key, cursor, fingerprint),
            )

    def publish_checkpoint_candidates(self, evidence_ids: list[str]) -> None:
        if not evidence_ids:
            return
        with self.transaction() as connection:
            candidates: dict[str, dict[str, Any]] = {}
            for batch in _chunks(evidence_ids):
                placeholders = ",".join("?" for _ in batch)
                rows = connection.execute(
                    f"""SELECT ec.source_key,ec.cursor,ec.fingerprint,e.created_at,e.id
                    FROM evidence_checkpoints ec JOIN evidence e ON e.id=ec.evidence_id
                    WHERE ec.evidence_id IN ({placeholders})""",
                    batch,
                ).fetchall()
                for row in rows:
                    candidate = dict(row)
                    current = candidates.get(candidate["source_key"])
                    sort_key = (
                        candidate["created_at"],
                        _checkpoint_cursor_position(candidate.get("cursor")),
                        candidate["id"],
                    )
                    if current is None:
                        candidates[candidate["source_key"]] = candidate
                        continue
                    current_key = (
                        current["created_at"],
                        _checkpoint_cursor_position(current.get("cursor")),
                        current["id"],
                    )
                    if sort_key > current_key:
                        candidates[candidate["source_key"]] = candidate
            for candidate in candidates.values():
                connection.execute(
                    """INSERT INTO checkpoints(source_key,cursor,fingerprint,updated_at) VALUES(?,?,?,?)
                    ON CONFLICT(source_key) DO UPDATE SET cursor=excluded.cursor,
                    fingerprint=excluded.fingerprint,updated_at=excluded.updated_at""",
                    (
                        candidate["source_key"],
                        candidate["cursor"],
                        candidate["fingerprint"],
                        utc_now(),
                    ),
                )

    def evidence(
        self, *, status: str | None = None, limit: int | None = None
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM evidence"
        params: list[Any] = []
        if status:
            query += " WHERE status=?"
            params.append(status)
        query += " ORDER BY created_at,id"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    def evidence_since(
        self, since_iso: str, *, limit: int | None = None
    ) -> list[dict[str, Any]]:
        query = """SELECT * FROM evidence
                WHERE COALESCE(occurred_at,created_at) >= ?
                ORDER BY COALESCE(occurred_at,created_at),id"""
        params: list[Any] = [since_iso]
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    def evidence_counts_by_source(self) -> dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT source_type,COUNT(*) AS count FROM evidence GROUP BY source_type"
            ).fetchall()
        return {row["source_type"]: int(row["count"]) for row in rows}

    def evidence_count(self, *, status: str | None = None) -> int:
        query = "SELECT COUNT(*) AS count FROM evidence"
        params: list[Any] = []
        if status:
            query += " WHERE status=?"
            params.append(status)
        with self.connect() as connection:
            row = connection.execute(query, params).fetchone()
        return int(row["count"])

    def session_coverage(self) -> dict[str, Any]:
        """Return deduplicated session attribution coverage without message text."""

        with self.connect() as connection:
            index_rows = connection.execute(
                """SELECT surface,session_id,project_id,status
                FROM session_project_index"""
            ).fetchall()
            rows = connection.execute(
                """SELECT project_id,occurred_at,created_at,payload_json FROM evidence
                WHERE source_type='session-digest' AND kind='session_digest'
                AND status NOT IN ('superseded','compacted')"""
            ).fetchall()
        if index_rows:
            latest_by_session: dict[tuple[str, str], str] = {}
            for row in rows:
                try:
                    payload = json.loads(row["payload_json"])
                except (json.JSONDecodeError, TypeError):
                    continue
                key = (
                    str(payload.get("source") or "unknown").casefold(),
                    str(payload.get("session_id") or ""),
                )
                ended_at = str(
                    payload.get("ended_at")
                    or row["occurred_at"]
                    or row["created_at"]
                    or ""
                )
                if ended_at and ended_at > latest_by_session.get(key, ""):
                    latest_by_session[key] = ended_at
            by_surface: dict[str, dict[str, Any]] = {}
            project_ids: set[str] = set()
            attributed = 0
            for row in index_rows:
                surface = str(row["surface"])
                stats = by_surface.setdefault(
                    surface,
                    {
                        "total": 0,
                        "attributed": 0,
                        "unattributed": 0,
                        "latest_at": None,
                    },
                )
                stats["total"] += 1
                matched = row["status"] == "matched" and bool(row["project_id"])
                if matched:
                    attributed += 1
                    stats["attributed"] += 1
                    project_ids.add(str(row["project_id"]))
                else:
                    stats["unattributed"] += 1
                latest = latest_by_session.get((surface, str(row["session_id"])))
                if latest and (
                    not stats["latest_at"] or latest > str(stats["latest_at"])
                ):
                    stats["latest_at"] = latest
            total = len(index_rows)
            return {
                "total": total,
                "attributed": attributed,
                "unattributed": total - attributed,
                "projects_represented": len(project_ids),
                "by_surface": by_surface,
            }
        sessions: dict[str, dict[str, Any]] = {}
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except (json.JSONDecodeError, TypeError):
                continue
            surface = str(payload.get("source") or "unknown").casefold()
            session_id = str(payload.get("session_id") or "")
            if surface == "antigravity" and session_id.casefold() in {
                "transcript.jsonl",
                "transcript_full.jsonl",
            }:
                continue
            project_ids = {
                str(value) for value in payload.get("project_ids", []) if value
            }
            if row["project_id"]:
                project_ids.add(str(row["project_id"]))
            signature = canonical_hash(
                {
                    "surface": surface,
                    "started_at": payload.get("started_at") or row["occurred_at"],
                    "ended_at": payload.get("ended_at") or row["occurred_at"],
                    "user_messages": [
                        item.get("text")
                        for item in payload.get("user_messages", [])
                        if isinstance(item, dict)
                    ],
                }
            )
            candidate = {
                "surface": surface,
                "project_ids": project_ids,
                "ended_at": payload.get("ended_at")
                or row["occurred_at"]
                or row["created_at"],
            }
            existing = sessions.get(signature)
            if existing is None or len(project_ids) > len(existing["project_ids"]):
                sessions[signature] = candidate

        by_surface: dict[str, dict[str, Any]] = {}
        all_projects: set[str] = set()
        attributed = 0
        for item in sessions.values():
            surface = item["surface"]
            stats = by_surface.setdefault(
                surface,
                {"total": 0, "attributed": 0, "unattributed": 0, "latest_at": None},
            )
            stats["total"] += 1
            if item["project_ids"]:
                attributed += 1
                stats["attributed"] += 1
                all_projects.update(item["project_ids"])
            else:
                stats["unattributed"] += 1
            if item["ended_at"] and (
                not stats["latest_at"]
                or str(item["ended_at"]) > str(stats["latest_at"])
            ):
                stats["latest_at"] = item["ended_at"]
        total = len(sessions)
        return {
            "total": total,
            "attributed": attributed,
            "unattributed": total - attributed,
            "projects_represented": len(all_projects),
            "by_surface": by_surface,
        }

    def mark_evidence(self, evidence_ids: list[str], status: str) -> None:
        if not evidence_ids:
            return
        with self.transaction() as connection:
            for batch in _chunks(evidence_ids):
                placeholders = ",".join("?" for _ in batch)
                connection.execute(
                    f"UPDATE evidence SET status=? WHERE id IN ({placeholders})",
                    [status, *batch],
                )

    def supersede_source_evidence(
        self, *, source_type: str, source_ref: str, keep_id: str
    ) -> int:
        """Retire older mutable-source records while preserving their audit trail."""
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE evidence SET status='superseded'
                WHERE source_type=? AND source_ref=? AND id<>?
                AND status IN ('new','processed')""",
                (source_type, source_ref, keep_id),
            )
        return cursor.rowcount

    def evidence_by_ids(self, evidence_ids: list[str]) -> list[dict[str, Any]]:
        if not evidence_ids:
            return []
        with self.connect() as connection:
            rows = []
            for batch in _chunks(evidence_ids):
                placeholders = ",".join("?" for _ in batch)
                rows.extend(
                    connection.execute(
                        f"SELECT * FROM evidence WHERE id IN ({placeholders})",
                        batch,
                    ).fetchall()
                )
        rows.sort(key=lambda row: (row["created_at"], row["id"]))
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    def latest_evidence_by_source(
        self, *, source_type: str, source_ref: str
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT * FROM evidence
                WHERE source_type=? AND source_ref=?
                ORDER BY created_at DESC,id DESC LIMIT 1""",
                (source_type, source_ref),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        return item

    def reassign_evidence_project(
        self, evidence_id: str, project_id: str | None
    ) -> None:
        self.reassign_evidence_projects({evidence_id: project_id})

    def reassign_evidence_projects(self, assignments: dict[str, str | None]) -> None:
        """Update evidence attribution and its mirrored payload atomically."""

        if not assignments:
            return
        with self.transaction() as connection:
            for evidence_id, project_id in assignments.items():
                row = connection.execute(
                    "SELECT payload_json FROM evidence WHERE id=?", (evidence_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(evidence_id)
                payload = json.loads(row["payload_json"])
                if "project_id" in payload:
                    if project_id:
                        payload["project_id"] = project_id
                    else:
                        payload.pop("project_id", None)
                if "project_ids" in payload:
                    payload["project_ids"] = [project_id] if project_id else []
                connection.execute(
                    """UPDATE evidence SET project_id=?,payload_json=?,content_hash=?
                    WHERE id=?""",
                    (
                        project_id,
                        json.dumps(payload, ensure_ascii=False, sort_keys=True),
                        canonical_hash(payload),
                        evidence_id,
                    ),
                )

    def upsert_project(self, project: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO projects(id,logical_name,classification,remote_url,initial_commit,head_commit,fingerprint,metadata_json,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET logical_name=excluded.logical_name,classification=excluded.classification,
                remote_url=excluded.remote_url,initial_commit=excluded.initial_commit,head_commit=excluded.head_commit,
                fingerprint=excluded.fingerprint,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
                (
                    project["id"],
                    project["name"],
                    project["classification"],
                    project.get("remote_url"),
                    project.get("initial_commit"),
                    project.get("head_commit"),
                    project.get("fingerprint"),
                    json.dumps(project, ensure_ascii=False, sort_keys=True),
                    utc_now(),
                ),
            )

    def projects(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT metadata_json FROM projects ORDER BY logical_name"
            ).fetchall()
        return [json.loads(row["metadata_json"]) for row in rows]

    def present_projects(self) -> list[dict[str, Any]]:
        """Return only projects present in the most recent completed scan."""

        with self.connect() as connection:
            rows = connection.execute(
                """SELECT projects.metadata_json FROM projects
                LEFT JOIN project_presence ON project_presence.project_id=projects.id
                WHERE project_presence.present=1 OR project_presence.project_id IS NULL
                ORDER BY projects.logical_name"""
            ).fetchall()
        return [json.loads(row["metadata_json"]) for row in rows]

    def project_presence(self, project_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM project_presence WHERE project_id=?", (project_id,)
            ).fetchone()
        return dict(row) if row else None

    def missing_projects(self) -> list[dict[str, Any]]:
        """Return projects absent from the latest scan without exposing paths."""

        with self.connect() as connection:
            rows = connection.execute(
                """SELECT projects.id,projects.logical_name,
                project_presence.last_seen_at,project_presence.missing_since
                FROM projects JOIN project_presence
                ON project_presence.project_id=projects.id
                WHERE project_presence.present=0
                ORDER BY projects.logical_name"""
            ).fetchall()
        return [
            {
                "project_id": str(row["id"]),
                "project": str(row["logical_name"]),
                "last_seen_at": row["last_seen_at"],
                "missing_since": row["missing_since"],
            }
            for row in rows
        ]

    def set_project_presence(self, project_id: str, *, present: bool) -> None:
        now = utc_now()
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM project_presence WHERE project_id=?", (project_id,)
            ).fetchone()
            last_seen_at = (
                now if present else (existing["last_seen_at"] if existing else None)
            )
            missing_since = (
                None
                if present
                else (
                    existing["missing_since"]
                    if existing and existing["missing_since"]
                    else now
                )
            )
            connection.execute(
                """INSERT INTO project_presence(
                project_id,present,last_seen_at,missing_since,updated_at
                ) VALUES(?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET
                present=excluded.present,last_seen_at=excluded.last_seen_at,
                missing_since=excluded.missing_since,updated_at=excluded.updated_at""",
                (project_id, int(present), last_seen_at, missing_since, now),
            )

    def pattern_signal(self, pattern_key: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM pattern_signals WHERE pattern_key=?", (pattern_key,)
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["evidence_refs"] = json.loads(item.pop("evidence_refs_json"))
        item["payload"] = json.loads(item.pop("payload_json"))
        item["explicit"] = bool(item["explicit"])
        return item

    def pattern_signals(self, status: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT pattern_key FROM pattern_signals"
        params: list[Any] = []
        if status:
            query += " WHERE status=?"
            params.append(status)
        query += " ORDER BY pattern_key"
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            item
            for row in rows
            if (item := self.pattern_signal(str(row["pattern_key"]))) is not None
        ]

    def upsert_pattern_signal(self, record: dict[str, Any]) -> None:
        now = utc_now()
        first_seen = str(record.get("first_seen") or now)
        payload = dict(record)
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO pattern_signals(
                pattern_key,kind,label,claim,evidence_refs_json,confidence,explicit,
                session_count,date_count,project_count,status,observation_id,rejection_reason,
                payload_json,first_seen,last_seen
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(pattern_key) DO UPDATE SET
                kind=excluded.kind,label=excluded.label,claim=excluded.claim,
                evidence_refs_json=excluded.evidence_refs_json,
                confidence=excluded.confidence,explicit=excluded.explicit,
                session_count=excluded.session_count,date_count=excluded.date_count,
                project_count=excluded.project_count,status=excluded.status,
                observation_id=excluded.observation_id,rejection_reason=excluded.rejection_reason,
                payload_json=excluded.payload_json,last_seen=excluded.last_seen""",
                (
                    record["pattern_key"],
                    record["kind"],
                    record["label"],
                    record["claim"],
                    json.dumps(sorted(set(record["evidence_refs"]))),
                    float(record["confidence"]),
                    int(bool(record.get("explicit"))),
                    int(record.get("session_count", 0)),
                    int(record.get("date_count", 0)),
                    int(record.get("project_count", 0)),
                    record.get("status", "tracking"),
                    record.get("observation_id"),
                    record.get("rejection_reason"),
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    first_seen,
                    str(record.get("last_seen") or now),
                ),
            )

    def clear_current_project_paths(self) -> None:
        with self.connect() as connection:
            connection.execute("UPDATE project_path_aliases SET is_current=0")

    def register_project_path(
        self,
        project_id: str,
        normalized_path: str,
        *,
        source: str,
        confidence: float = 1.0,
        current: bool = False,
    ) -> None:
        path = str(normalized_path or "").strip()
        if not path:
            return
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO project_path_aliases(
                normalized_path,project_id,source,confidence,is_current,first_seen,last_seen
                ) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(normalized_path,project_id) DO UPDATE SET
                source=CASE
                  WHEN excluded.is_current=1 THEN excluded.source
                  ELSE project_path_aliases.source
                END,
                confidence=MAX(project_path_aliases.confidence,excluded.confidence),
                is_current=MAX(project_path_aliases.is_current,excluded.is_current),
                last_seen=excluded.last_seen""",
                (
                    path,
                    project_id,
                    source,
                    float(confidence),
                    int(current),
                    now,
                    now,
                ),
            )

    def project_path_aliases(
        self, *, project_ids: set[str] | None = None
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM project_path_aliases"
        params: list[str] = []
        if project_ids:
            placeholders = ",".join("?" for _ in project_ids)
            query += f" WHERE project_id IN ({placeholders})"
            params.extend(sorted(project_ids))
        query += (
            " ORDER BY is_current DESC,LENGTH(normalized_path) DESC,normalized_path"
        )
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def session_source(self, source_key: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM session_sources WHERE source_key=?", (source_key,)
            ).fetchone()
        return dict(row) if row else None

    def session_sources(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM session_sources ORDER BY surface,session_id,source_key"
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_session_source(
        self,
        *,
        source_key: str,
        surface: str,
        session_id: str,
        fingerprint: str,
        workspace_hash: str | None,
        project_id: str | None,
        status: str,
        resolver: str,
        confidence: float,
        ingested: bool,
    ) -> None:
        if status not in {"matched", "unmatched", "ambiguous"}:
            raise ValueError("Invalid session source status")
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO session_sources(
                source_key,surface,session_id,fingerprint,workspace_hash,project_id,
                status,resolver,confidence,ingested,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source_key) DO UPDATE SET
                surface=excluded.surface,session_id=excluded.session_id,
                fingerprint=excluded.fingerprint,workspace_hash=excluded.workspace_hash,
                project_id=excluded.project_id,status=excluded.status,
                resolver=excluded.resolver,confidence=excluded.confidence,
                ingested=excluded.ingested,updated_at=excluded.updated_at""",
                (
                    source_key,
                    surface,
                    session_id,
                    fingerprint,
                    workspace_hash,
                    project_id,
                    status,
                    resolver,
                    float(confidence),
                    int(ingested),
                    utc_now(),
                ),
            )

    def mark_session_source_ingested(
        self, source_key: str, *, fingerprint: str
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE session_sources SET ingested=1,fingerprint=?,updated_at=?
                WHERE source_key=?""",
                (fingerprint, utc_now(), source_key),
            )

    def resolved_session_source_project(
        self, surface: str, session_id: str
    ) -> str | None:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT DISTINCT project_id FROM session_sources
                WHERE surface=? AND session_id=? AND status='matched'
                AND project_id IS NOT NULL""",
                (surface, session_id),
            ).fetchall()
        values = {str(row["project_id"]) for row in rows}
        return next(iter(values)) if len(values) == 1 else None

    def replace_session_project_index(self, records: list[dict[str, Any]]) -> None:
        with self.transaction() as connection:
            connection.execute("DELETE FROM session_project_index")
            connection.executemany(
                """INSERT INTO session_project_index(
                surface,session_id,project_id,status,resolver,confidence,
                workspace_count,source_record_count,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        item["surface"],
                        item["session_id"],
                        item.get("project_id"),
                        item["status"],
                        item["resolver"],
                        float(item.get("confidence", 0.0)),
                        int(item.get("workspace_count", 0)),
                        int(item.get("source_record_count", 0)),
                        utc_now(),
                    )
                    for item in records
                ],
            )

    def session_project_index(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM session_project_index
                ORDER BY surface,session_id"""
            ).fetchall()
        return [dict(row) for row in rows]

    def analyzed_project_session_evidence(self, project_id: str) -> set[str]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT evidence_id FROM project_session_analysis
                WHERE project_id=?""",
                (project_id,),
            ).fetchall()
        return {str(row["evidence_id"]) for row in rows}

    def mark_project_session_evidence_analyzed(
        self, project_id: str, evidence_ids: list[str], *, run_id: str
    ) -> None:
        if not evidence_ids:
            return
        with self.transaction() as connection:
            connection.executemany(
                """INSERT INTO project_session_analysis(
                project_id,evidence_id,run_id,analyzed_at
                ) VALUES(?,?,?,?)
                ON CONFLICT(project_id,evidence_id) DO UPDATE SET
                run_id=excluded.run_id,analyzed_at=excluded.analyzed_at""",
                [
                    (project_id, evidence_id, run_id, utc_now())
                    for evidence_id in sorted(set(evidence_ids))
                ],
            )

    def clear_project_session_analysis(self, project_ids: set[str]) -> int:
        if not project_ids:
            return 0
        deleted = 0
        with self.transaction() as connection:
            for batch in _chunks(project_ids):
                placeholders = ",".join("?" for _ in batch)
                cursor = connection.execute(
                    f"DELETE FROM project_session_analysis "
                    f"WHERE project_id IN ({placeholders})",
                    batch,
                )
                deleted += int(cursor.rowcount)
        return deleted

    def set_pattern_status(
        self,
        pattern_key: str,
        status: str,
        *,
        observation_id: str | None = None,
        reason: str | None = None,
    ) -> None:
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE pattern_signals SET status=?,
                observation_id=COALESCE(?,observation_id),rejection_reason=?,last_seen=?
                WHERE pattern_key=?""",
                (status, observation_id, reason, utc_now(), pattern_key),
            )
        if cursor.rowcount != 1:
            raise KeyError(pattern_key)

    def start_run(
        self, kind: str, model: str | None = None, reasoning: str | None = None
    ) -> str:
        run_id = (
            f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
        )
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO runs(id,kind,status,started_at,model,reasoning) VALUES(?,?, 'running',?,?,?)",
                (run_id, kind, utc_now(), model, reasoning),
            )
        return run_id

    def start_pipeline_run(
        self,
        kind: str,
        *,
        trigger: str,
        stage: str = "starting",
        started_at: str | None = None,
    ) -> str:
        run_id = f"pipeline-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO pipeline_runs(
                id,kind,trigger,status,stage,started_at
                ) VALUES(?,?,?,'running',?,?)""",
                (run_id, kind, trigger, stage, started_at or utc_now()),
            )
        return run_id

    def set_pipeline_stage(self, run_id: str, stage: str) -> None:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE pipeline_runs SET stage=? WHERE id=? AND status='running'",
                (stage, run_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(run_id)

    def finish_pipeline_run(
        self,
        run_id: str,
        status: str,
        *,
        error: str | None = None,
        completed_at: str | None = None,
    ) -> None:
        if status not in {"completed", "failed"}:
            raise ValueError(f"Invalid pipeline status: {status}")
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE pipeline_runs
                SET status=?,completed_at=?,error=? WHERE id=?""",
                (status, completed_at or utc_now(), error, run_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(run_id)

    def pipeline_runs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def finish_run(
        self,
        run_id: str,
        status: str,
        *,
        evidence_count: int = 0,
        receipt_path: str | None = None,
        error: str | None = None,
        usage: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE runs SET status=?,completed_at=?,evidence_count=?,receipt_path=?,error=? WHERE id=?",
                (status, utc_now(), evidence_count, receipt_path, error, run_id),
            )
            if usage is not None:
                connection.execute(
                    """INSERT INTO run_usage(run_id,usage_json) VALUES(?,?)
                    ON CONFLICT(run_id) DO UPDATE SET usage_json=excluded.usage_json""",
                    (run_id, json.dumps(usage, sort_keys=True)),
                )

    def complete_validated_run(self, run_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE runs SET status='completed',completed_at=? WHERE id=?",
                (utc_now(), run_id),
            )

    def runs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def replace_summary_runs(self, kind: str, period: str, run_ids: list[str]) -> None:
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM summary_runs WHERE kind=? AND period=?", (kind, period)
            )
            connection.executemany(
                "INSERT INTO summary_runs(kind,period,run_id) VALUES(?,?,?)",
                [(kind, period, run_id) for run_id in run_ids],
            )

    def summary_run_ids(self, kind: str, period: str) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT run_id FROM summary_runs WHERE kind=? AND period=? ORDER BY run_id",
                (kind, period),
            ).fetchall()
        return [str(row["run_id"]) for row in rows]

    def usage_for_runs(self, run_ids: list[str]) -> list[dict[str, Any]]:
        if not run_ids:
            return []
        result: list[dict[str, Any]] = []
        for batch in _chunks(set(run_ids)):
            placeholders = ",".join("?" for _ in batch)
            with self.connect() as connection:
                rows = connection.execute(
                    f"SELECT run_id,usage_json FROM run_usage WHERE run_id IN ({placeholders})",
                    batch,
                ).fetchall()
            for row in rows:
                usage = json.loads(row["usage_json"])
                usage["run_id"] = row["run_id"]
                result.append(usage)
        return sorted(result, key=lambda item: str(item["run_id"]))

    def add_observation(self, record: dict[str, Any]) -> str:
        evidence_refs = sorted(set(record["evidence_refs"]))
        observation_id = (
            record.get("id")
            or "obs-"
            + canonical_hash(
                {
                    "kind": record["kind"],
                    "subject": record["subject"],
                    "claim": record["claim"],
                }
            )[:24]
        )
        now = utc_now()
        payload = dict(record)
        payload["id"] = observation_id
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM observations WHERE id=?", (observation_id,)
            ).fetchone()
            if existing is not None:
                # Rejections and resolved clarifications are tombstones. Identical
                # model output may never reopen or promote them automatically.
                if existing["status"] in {"rejected", "resolved"}:
                    return observation_id
                previous_refs = json.loads(existing["evidence_refs_json"])
                merged_refs = sorted(set(previous_refs) | set(evidence_refs))
                requested_status = record.get("status", "pending")
                status = existing["status"]
                if (
                    status not in {"approved", "promoted"}
                    and requested_status == "promoted"
                ):
                    status = "promoted"
                merged_payload = json.loads(existing["payload_json"])
                merged_payload.update(payload)
                merged_payload["evidence_refs"] = merged_refs
                connection.execute(
                    """UPDATE observations SET
                    evidence_refs_json=?,confidence=?,source_count=?,project_count=?,sensitivity=?,
                    promotion_tier=?,status=?,payload_json=?,updated_at=? WHERE id=?""",
                    (
                        json.dumps(merged_refs),
                        max(float(existing["confidence"]), float(record["confidence"])),
                        max(
                            int(existing["source_count"]),
                            int(record.get("source_count", len(merged_refs))),
                        ),
                        max(
                            int(existing["project_count"]),
                            int(record.get("project_count", 0)),
                        ),
                        record.get("sensitivity", existing["sensitivity"]),
                        record.get("promotion_tier", existing["promotion_tier"]),
                        status,
                        json.dumps(merged_payload, ensure_ascii=False, sort_keys=True),
                        now,
                        observation_id,
                    ),
                )
                return observation_id
            connection.execute(
                """INSERT INTO observations(
                id,kind,subject,claim,evidence_refs_json,confidence,source_count,project_count,sensitivity,
                promotion_tier,status,rejection_reason,payload_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    observation_id,
                    record["kind"],
                    record["subject"],
                    record["claim"],
                    json.dumps(evidence_refs),
                    float(record["confidence"]),
                    int(record.get("source_count", len(evidence_refs))),
                    int(record.get("project_count", 0)),
                    record.get("sensitivity", "normal"),
                    record.get("promotion_tier", "review"),
                    record.get("status", "pending"),
                    record.get("rejection_reason"),
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                ),
            )
        return observation_id

    def observation(self, observation_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM observations WHERE id=?", (observation_id,)
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["evidence_refs"] = json.loads(item.pop("evidence_refs_json"))
        item["payload"] = json.loads(item.pop("payload_json"))
        return item

    def set_observation_project_override(
        self, observation_id: str, project_ids: list[str]
    ) -> None:
        """Persist an explicit owner correction over inferred evidence attribution."""

        corrected_ids = sorted(
            {str(project_id).strip() for project_id in project_ids if project_id}
        )
        if not corrected_ids:
            raise ValueError("At least one project is required")
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT payload_json FROM observations WHERE id=?", (observation_id,)
            ).fetchone()
            if row is None:
                raise KeyError(observation_id)
            placeholders = ",".join("?" for _ in corrected_ids)
            known_ids = {
                str(item["id"])
                for item in connection.execute(
                    f"SELECT id FROM projects WHERE id IN ({placeholders})",
                    corrected_ids,
                ).fetchall()
            }
            unknown_ids = sorted(set(corrected_ids) - known_ids)
            if unknown_ids:
                raise KeyError(f"Unknown project: {', '.join(unknown_ids)}")
            payload = json.loads(row["payload_json"])
            payload["project_ids_override"] = corrected_ids
            payload["project_attribution_source"] = "explicit_owner_correction"
            connection.execute(
                "UPDATE observations SET payload_json=?,updated_at=? WHERE id=?",
                (
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    utc_now(),
                    observation_id,
                ),
            )

    def observations(self, status: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM observations"
        params: list[Any] = []
        if status:
            query += " WHERE status=?"
            params.append(status)
        query += " ORDER BY created_at,id"
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["evidence_refs"] = json.loads(item.pop("evidence_refs_json"))
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    def knowledge_feedback(self) -> dict[str, dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT observation_id,decision,updated_at FROM knowledge_feedback
                ORDER BY updated_at,observation_id"""
            ).fetchall()
        return {str(row["observation_id"]): dict(row) for row in rows}

    def last_knowledge_dislike(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT id,observation_id,previous_decision,occurrences_json,created_at
                FROM knowledge_feedback_events
                WHERE action='dislike' AND undone_at IS NULL
                ORDER BY id DESC LIMIT 1"""
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["occurrences"] = json.loads(item.pop("occurrences_json"))
        return item

    def review_feedback(self) -> dict[str, dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT observation_id,decision,updated_at FROM review_feedback
                ORDER BY updated_at,observation_id"""
            ).fetchall()
        return {str(row["observation_id"]): dict(row) for row in rows}

    def last_question_dismissal(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT id,observation_id,previous_decision,previous_status,created_at
                FROM review_feedback_events
                WHERE action='dismiss' AND undone_at IS NULL
                ORDER BY id DESC LIMIT 1"""
            ).fetchone()
        return dict(row) if row is not None else None

    def decide_observation(
        self, observation_id: str, status: str, reason: str | None = None
    ) -> None:
        if status not in {"approved", "rejected", "promoted", "resolved"}:
            raise ValueError("Invalid observation decision")
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE observations SET status=?,rejection_reason=?,updated_at=? WHERE id=?",
                (status, reason, utc_now(), observation_id),
            )
            connection.execute(
                """UPDATE pattern_signals SET status=?,rejection_reason=?,last_seen=?
                WHERE observation_id=?""",
                (status, reason, utc_now(), observation_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(observation_id)

    def resolve_clarification_from_evidence(
        self,
        observation_id: str,
        *,
        answer: str,
        evidence_refs: list[str],
        confidence: float,
    ) -> None:
        """Close an objective question while retaining resolution provenance."""

        now = utc_now()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM observations WHERE id=?", (observation_id,)
            ).fetchone()
            if row is None:
                raise KeyError(observation_id)
            if row["kind"] != "clarification" or row["status"] != "pending":
                raise RuntimeError(
                    "Automatic resolution requires a pending clarification"
                )
            merged_refs = sorted(
                set(json.loads(row["evidence_refs_json"])) | set(evidence_refs)
            )
            payload = json.loads(row["payload_json"])
            payload["automatic_resolution"] = {
                "answer": answer,
                "evidence_refs": sorted(set(evidence_refs)),
                "confidence": float(confidence),
                "resolved_at": now,
            }
            connection.execute(
                """UPDATE observations SET status='resolved',rejection_reason=?,
                evidence_refs_json=?,confidence=?,payload_json=?,updated_at=? WHERE id=?""",
                (
                    answer,
                    json.dumps(merged_refs),
                    max(float(row["confidence"]), float(confidence)),
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    now,
                    observation_id,
                ),
            )

    def decide_observations(
        self, observation_ids: list[str], status: str, reason: str | None = None
    ) -> None:
        if status not in {"approved", "rejected", "promoted", "resolved"}:
            raise ValueError("Invalid observation decision")
        ids = sorted(set(observation_ids))
        if not ids:
            raise ValueError("At least one observation is required")
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT id,status FROM observations WHERE id IN ({placeholders})", ids
            ).fetchall()
            found = {row["id"]: row["status"] for row in rows}
            missing = [
                observation_id for observation_id in ids if observation_id not in found
            ]
            if missing:
                raise KeyError(", ".join(missing))
            not_pending = [
                observation_id
                for observation_id in ids
                if found[observation_id] != "pending"
            ]
            if not_pending:
                raise RuntimeError(
                    "Batch decisions require pending observations: "
                    + ", ".join(not_pending)
                )
            connection.execute(
                f"UPDATE observations SET status=?,rejection_reason=?,updated_at=? WHERE id IN ({placeholders})",
                [status, reason, utc_now(), *ids],
            )
            connection.execute(
                f"UPDATE pattern_signals SET status=?,rejection_reason=?,last_seen=? "
                f"WHERE observation_id IN ({placeholders})",
                [status, reason, utc_now(), *ids],
            )

    def restore_observations_pending(self, observation_ids: list[str]) -> None:
        """Rollback only a failed deterministic batch promotion."""
        ids = sorted(set(observation_ids))
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE observations SET status='pending',rejection_reason=NULL,updated_at=? "
                f"WHERE id IN ({placeholders}) AND status IN ('approved','promoted')",
                [utc_now(), *ids],
            )
            connection.execute(
                f"UPDATE pattern_signals SET status='pending',rejection_reason=NULL,last_seen=? "
                f"WHERE observation_id IN ({placeholders}) AND status IN ('approved','promoted')",
                [utc_now(), *ids],
            )
