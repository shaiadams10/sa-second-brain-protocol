from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from .memory_mutations import (
    MemoryHead,
    MemoryKey,
    MemoryMutationPlan,
    PlannedMemoryMutation,
)
from .state import StateStore, utc_now


MEMORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_entities (
  memory_id TEXT PRIMARY KEY,
  layer TEXT NOT NULL,
  scope TEXT NOT NULL,
  project_id TEXT,
  project_key TEXT NOT NULL,
  kind TEXT NOT NULL,
  subject_key TEXT NOT NULL,
  destination TEXT NOT NULL,
  current_version_id TEXT NOT NULL,
  status TEXT NOT NULL,
  reinforcement_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(layer,scope,project_key,kind,subject_key)
);

CREATE TABLE IF NOT EXISTS memory_versions (
  version_id TEXT PRIMARY KEY,
  memory_id TEXT NOT NULL REFERENCES memory_entities(memory_id) ON DELETE CASCADE,
  version INTEGER NOT NULL,
  subject TEXT NOT NULL,
  claim TEXT NOT NULL,
  status TEXT NOT NULL,
  confirmation_state TEXT NOT NULL,
  confidence REAL NOT NULL,
  explicit INTEGER NOT NULL,
  supersedes_version_id TEXT REFERENCES memory_versions(version_id),
  contradicts_version_id TEXT REFERENCES memory_versions(version_id),
  created_at TEXT NOT NULL,
  UNIQUE(memory_id,version)
);

CREATE TABLE IF NOT EXISTS memory_version_evidence (
  version_id TEXT NOT NULL REFERENCES memory_versions(version_id) ON DELETE CASCADE,
  evidence_id TEXT NOT NULL REFERENCES evidence(id),
  PRIMARY KEY(version_id,evidence_id)
);

CREATE TABLE IF NOT EXISTS memory_reinforcement_events (
  mutation_id TEXT PRIMARY KEY,
  memory_id TEXT NOT NULL REFERENCES memory_entities(memory_id) ON DELETE CASCADE,
  version_id TEXT NOT NULL REFERENCES memory_versions(version_id) ON DELETE CASCADE,
  evidence_refs_json TEXT NOT NULL,
  confidence REAL NOT NULL,
  explicit INTEGER NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_mutation_proposals (
  mutation_id TEXT PRIMARY KEY,
  run_fingerprint TEXT NOT NULL,
  item_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected')),
  publication_json TEXT,
  rejection_reason TEXT,
  created_at TEXT NOT NULL,
  decided_at TEXT
);
"""


@dataclass(frozen=True)
class MemoryPublicationReceipt:
    mutation_id: str
    destination: str
    content_hash: str

    def __post_init__(self) -> None:
        if not self.mutation_id.strip() or not self.destination.strip():
            raise ValueError("Memory publication receipts require stable identity")
        if not re.fullmatch(r"[a-f0-9]{64}", self.content_hash):
            raise ValueError("Memory publication content hash must be SHA-256")


@dataclass(frozen=True)
class MemoryProposal:
    mutation_id: str
    run_fingerprint: str
    item: PlannedMemoryMutation
    status: str
    publication: MemoryPublicationReceipt | None
    rejection_reason: str | None


class MemoryPublicationVerifier(Protocol):
    def verify(
        self,
        item: PlannedMemoryMutation,
        publication: MemoryPublicationReceipt,
    ) -> None: ...


class CanonicalMemoryPublicationVerifier:
    """Prove an approved proposal is present in the exact canonical bytes."""

    def __init__(self, vault: Path) -> None:
        self._vault = vault.resolve(strict=True)
        if not self._vault.is_dir():
            raise ValueError("Memory publication vault must be a directory")

    def verify(
        self,
        item: PlannedMemoryMutation,
        publication: MemoryPublicationReceipt,
    ) -> None:
        destination = PurePosixPath(publication.destination)
        if (
            destination.is_absolute()
            or destination.as_posix() != publication.destination
            or not destination.parts
            or any(part in {"", ".", ".."} for part in destination.parts)
            or "\\" in publication.destination
        ):
            raise RuntimeError("Memory publication destination is not canonical")
        candidate = self._vault.joinpath(*destination.parts)
        cursor = self._vault
        for part in destination.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise RuntimeError("Memory publication destination may not be a symlink")
        try:
            resolved = candidate.resolve(strict=True)
        except (FileNotFoundError, OSError) as error:
            raise RuntimeError("Memory publication destination is unavailable") from error
        if not resolved.is_relative_to(self._vault) or not resolved.is_file():
            raise RuntimeError("Memory publication destination is not a canonical file")
        content = resolved.read_bytes()
        if hashlib.sha256(content).hexdigest() != publication.content_hash:
            raise RuntimeError("Memory publication content hash does not match canon")
        marker = (
            f"<!-- sb:memory-mutation {item.mutation_id} -->".encode("utf-8")
        )
        if marker not in content:
            raise RuntimeError("Memory publication mutation marker is missing")


class UnavailableMemoryPublicationVerifier:
    """Allow proposal staging while keeping approval fail-closed."""

    def verify(
        self,
        item: PlannedMemoryMutation,
        publication: MemoryPublicationReceipt,
    ) -> None:
        raise RuntimeError("Canonical memory publication verifier is unavailable")


class SQLiteMemoryRegistry:
    """Review-gated operational identity/version registry; Markdown remains canon."""

    def __init__(
        self,
        store: StateStore,
        *,
        publication_verifier: MemoryPublicationVerifier,
    ) -> None:
        self._store = store
        self._publication_verifier = publication_verifier
        with store.connect() as connection:
            connection.executescript(MEMORY_SCHEMA)

    def get(self, memory_id: str) -> MemoryHead | None:
        with self._store.connect() as connection:
            row = connection.execute(
                """SELECT e.*,v.subject,v.claim,v.version
                FROM memory_entities e JOIN memory_versions v
                ON v.version_id=e.current_version_id WHERE e.memory_id=?""",
                (memory_id,),
            ).fetchone()
        return _head_from_row(row) if row else None

    def find_by_key(self, key: MemoryKey) -> MemoryHead | None:
        with self._store.connect() as connection:
            row = connection.execute(
                """SELECT e.*,v.subject,v.claim,v.version
                FROM memory_entities e JOIN memory_versions v
                ON v.version_id=e.current_version_id
                WHERE e.layer=? AND e.scope=? AND e.project_key=?
                AND e.kind=? AND e.subject_key=?""",
                (
                    key.layer,
                    key.scope,
                    key.project_id or "",
                    key.kind,
                    key.subject_key,
                ),
            ).fetchone()
        return _head_from_row(row) if row else None

    def stage(self, plan: MemoryMutationPlan, *, run_fingerprint: str) -> None:
        if not run_fingerprint.strip():
            raise ValueError("Memory proposal requires a run fingerprint")
        with self._store.transaction() as connection:
            self.stage_in_transaction(
                connection,
                plan,
                run_fingerprint=run_fingerprint,
            )

    def stage_in_transaction(
        self,
        connection: sqlite3.Connection,
        plan: MemoryMutationPlan,
        *,
        run_fingerprint: str,
    ) -> None:
        if not run_fingerprint.strip():
            raise ValueError("Memory proposal requires a run fingerprint")
        for item in plan.items:
            if item.disposition != "review":
                raise RuntimeError("Only review-gated mutations may be staged")
            _require_evidence(connection, item.evidence_refs)
            encoded = _encode(_item_to_dict(item))
            existing = connection.execute(
                """SELECT run_fingerprint,item_json
                FROM memory_mutation_proposals WHERE mutation_id=?""",
                (item.mutation_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["run_fingerprint"] != run_fingerprint
                    or existing["item_json"] != encoded
                ):
                    raise RuntimeError("Conflicting stable memory proposal")
                continue
            connection.execute(
                """INSERT INTO memory_mutation_proposals(
                mutation_id,run_fingerprint,item_json,status,created_at
                ) VALUES(?,?,?,'pending',?)""",
                (item.mutation_id, run_fingerprint, encoded, utc_now()),
            )

    def proposals(self, status: str | None = None) -> tuple[MemoryProposal, ...]:
        query = "SELECT * FROM memory_mutation_proposals"
        parameters: tuple[str, ...] = ()
        if status is not None:
            query += " WHERE status=?"
            parameters = (status,)
        query += " ORDER BY created_at,mutation_id"
        with self._store.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(_proposal_from_row(row) for row in rows)

    def approve(
        self,
        mutation_id: str,
        publication: MemoryPublicationReceipt,
    ) -> None:
        with self._store.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM memory_mutation_proposals WHERE mutation_id=?",
                (mutation_id,),
            ).fetchone()
            if row is None:
                raise KeyError(mutation_id)
            item = _item_from_dict(json.loads(row["item_json"]))
            _validate_publication_receipt(item, publication)
            encoded_publication = _encode(asdict(publication))
            if row["status"] == "approved":
                if row["publication_json"] != encoded_publication:
                    raise RuntimeError("Approved memory publication receipt changed")
                return
            if row["status"] != "pending":
                raise RuntimeError("Only pending memory proposals may be approved")
            self._publication_verifier.verify(item, publication)
            _require_evidence(connection, item.evidence_refs)
            if item.creates_version:
                self._apply_version(connection, item)
            else:
                self._apply_reinforcement(connection, item)
            connection.execute(
                """UPDATE memory_mutation_proposals SET
                status='approved',publication_json=?,decided_at=?
                WHERE mutation_id=?""",
                (encoded_publication, utc_now(), mutation_id),
            )

    def reject(self, mutation_id: str, *, reason: str) -> None:
        if not reason.strip():
            raise ValueError("Memory proposal rejection requires a reason")
        with self._store.transaction() as connection:
            row = connection.execute(
                "SELECT status FROM memory_mutation_proposals WHERE mutation_id=?",
                (mutation_id,),
            ).fetchone()
            if row is None:
                raise KeyError(mutation_id)
            if row["status"] == "rejected":
                return
            if row["status"] != "pending":
                raise RuntimeError("Only pending memory proposals may be rejected")
            connection.execute(
                """UPDATE memory_mutation_proposals SET
                status='rejected',rejection_reason=?,decided_at=? WHERE mutation_id=?""",
                (reason[:1000], utc_now(), mutation_id),
            )

    def version(self, version_id: str) -> dict[str, Any] | None:
        with self._store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM memory_versions WHERE version_id=?",
                (version_id,),
            ).fetchone()
        return dict(row) if row else None

    def version_evidence(self, version_id: str) -> tuple[str, ...]:
        with self._store.connect() as connection:
            rows = connection.execute(
                """SELECT evidence_id FROM memory_version_evidence
                WHERE version_id=? ORDER BY evidence_id""",
                (version_id,),
            ).fetchall()
        return tuple(str(row["evidence_id"]) for row in rows)

    def _apply_version(
        self,
        connection: sqlite3.Connection,
        item: PlannedMemoryMutation,
    ) -> None:
        existing = connection.execute(
            "SELECT * FROM memory_entities WHERE memory_id=?",
            (item.memory_id,),
        ).fetchone()
        now = utc_now()
        if item.version == 1:
            if existing is not None:
                raise RuntimeError("Memory entity already exists")
            key_conflict = connection.execute(
                """SELECT memory_id FROM memory_entities WHERE
                layer=? AND scope=? AND project_key=? AND kind=? AND subject_key=?""",
                (
                    item.memory_key.layer,
                    item.memory_key.scope,
                    item.memory_key.project_id or "",
                    item.memory_key.kind,
                    item.memory_key.subject_key,
                ),
            ).fetchone()
            if key_conflict is not None:
                raise RuntimeError("Memory key already exists")
            connection.execute(
                """INSERT INTO memory_entities VALUES(
                ?,?,?,?,?,?,?,?,?,?,?,?,?
                )""",
                (
                    item.memory_id,
                    item.memory_key.layer,
                    item.memory_key.scope,
                    item.memory_key.project_id,
                    item.memory_key.project_id or "",
                    item.memory_key.kind,
                    item.memory_key.subject_key,
                    item.destination,
                    item.version_id,
                    item.status,
                    item.reinforcement_count,
                    now,
                    now,
                ),
            )
        else:
            if existing is None:
                raise RuntimeError("Target memory no longer exists")
            expected = item.supersedes_version_id or item.contradicts_version_id
            if (
                item.expected_version_id != expected
                or existing["current_version_id"] != item.expected_version_id
                or int(existing["reinforcement_count"])
                != item.expected_reinforcement_count
                or item.version
                != connection.execute(
                    "SELECT version FROM memory_versions WHERE version_id=?",
                    (existing["current_version_id"],),
                ).fetchone()["version"]
                + 1
            ):
                raise RuntimeError("Memory proposal is stale")
        connection.execute(
            """INSERT INTO memory_versions(
            version_id,memory_id,version,subject,claim,status,confirmation_state,
            confidence,explicit,supersedes_version_id,contradicts_version_id,created_at
            ) VALUES(?,?,?,?,?,?,'confirmed',?,?,?,?,?)""",
            (
                item.version_id,
                item.memory_id,
                item.version,
                item.subject,
                item.claim,
                item.status,
                item.confidence,
                int(item.explicit),
                item.supersedes_version_id,
                item.contradicts_version_id,
                now,
            ),
        )
        connection.executemany(
            "INSERT INTO memory_version_evidence(version_id,evidence_id) VALUES(?,?)",
            [(item.version_id, value) for value in item.evidence_refs],
        )
        if item.version > 1:
            if item.supersedes_version_id:
                connection.execute(
                    "UPDATE memory_versions SET status='superseded' WHERE version_id=?",
                    (item.supersedes_version_id,),
                )
            connection.execute(
                """UPDATE memory_entities SET current_version_id=?,status=?,
                layer=?,scope=?,project_id=?,project_key=?,kind=?,subject_key=?,
                destination=?,reinforcement_count=?,updated_at=? WHERE memory_id=?""",
                (
                    item.version_id,
                    item.status,
                    item.memory_key.layer,
                    item.memory_key.scope,
                    item.memory_key.project_id,
                    item.memory_key.project_id or "",
                    item.memory_key.kind,
                    item.memory_key.subject_key,
                    item.destination,
                    item.reinforcement_count,
                    now,
                    item.memory_id,
                ),
            )

    def _apply_reinforcement(
        self,
        connection: sqlite3.Connection,
        item: PlannedMemoryMutation,
    ) -> None:
        existing = connection.execute(
            "SELECT current_version_id,reinforcement_count FROM memory_entities WHERE memory_id=?",
            (item.memory_id,),
        ).fetchone()
        if (
            existing is None
            or item.expected_version_id != item.version_id
            or existing["current_version_id"] != item.expected_version_id
            or int(existing["reinforcement_count"])
            != item.expected_reinforcement_count
            or item.reinforcement_count != item.expected_reinforcement_count + 1
        ):
            raise RuntimeError("Memory reinforcement proposal is stale")
        connection.execute(
            """INSERT INTO memory_reinforcement_events VALUES(?,?,?,?,?,?,?)""",
            (
                item.mutation_id,
                item.memory_id,
                item.version_id,
                _encode(item.evidence_refs),
                item.confidence,
                int(item.explicit),
                utc_now(),
            ),
        )
        connection.execute(
            """UPDATE memory_entities SET reinforcement_count=?,updated_at=?
            WHERE memory_id=?""",
            (item.reinforcement_count, utc_now(), item.memory_id),
        )


def _validate_publication_receipt(
    item: PlannedMemoryMutation,
    publication: MemoryPublicationReceipt,
) -> None:
    if (
        publication.mutation_id != item.mutation_id
        or publication.destination != item.destination
    ):
        raise RuntimeError("Memory publication receipt does not match proposal")


def _require_evidence(
    connection: sqlite3.Connection,
    evidence_refs: tuple[str, ...],
) -> None:
    placeholders = ",".join("?" for _ in evidence_refs)
    found = {
        str(row["id"])
        for row in connection.execute(
            f"SELECT id FROM evidence WHERE id IN ({placeholders})",
            evidence_refs,
        ).fetchall()
    }
    if found != set(evidence_refs):
        raise RuntimeError("Memory mutation evidence is unavailable")


def _head_from_row(row: sqlite3.Row) -> MemoryHead:
    return MemoryHead(
        memory_id=row["memory_id"],
        key=MemoryKey(
            layer=row["layer"],
            scope=row["scope"],
            project_id=row["project_id"],
            kind=row["kind"],
            subject_key=row["subject_key"],
        ),
        version_id=row["current_version_id"],
        version=int(row["version"]),
        subject=row["subject"],
        claim=row["claim"],
        status=row["status"],
        reinforcement_count=int(row["reinforcement_count"]),
    )


def _proposal_from_row(row: sqlite3.Row) -> MemoryProposal:
    return MemoryProposal(
        mutation_id=row["mutation_id"],
        run_fingerprint=row["run_fingerprint"],
        item=_item_from_dict(json.loads(row["item_json"])),
        status=row["status"],
        publication=(
            MemoryPublicationReceipt(**json.loads(row["publication_json"]))
            if row["publication_json"]
            else None
        ),
        rejection_reason=row["rejection_reason"],
    )


def _item_to_dict(item: PlannedMemoryMutation) -> dict[str, Any]:
    return asdict(item)


def _item_from_dict(value: dict[str, Any]) -> PlannedMemoryMutation:
    return PlannedMemoryMutation(
        **{
            **value,
            "memory_key": MemoryKey(**value["memory_key"]),
            "evidence_refs": tuple(value["evidence_refs"]),
        }
    )


def _encode(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
