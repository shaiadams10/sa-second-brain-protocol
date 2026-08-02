from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass

from .extraction_harness import ProcedureCandidate
from .state import StateStore, utc_now


PROCEDURE_SCHEMA = """
CREATE TABLE IF NOT EXISTS procedure_review_proposals (
  procedure_id TEXT PRIMARY KEY,
  run_fingerprint TEXT NOT NULL,
  candidate_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected')),
  rejection_reason TEXT,
  created_at TEXT NOT NULL,
  decided_at TEXT
);
"""
PROCEDURE_ID = re.compile(r"^procedure-[a-f0-9]{24}$")


@dataclass(frozen=True)
class ProcedureProposal:
    procedure_id: str
    run_fingerprint: str
    candidate: ProcedureCandidate
    status: str
    rejection_reason: str | None


@dataclass(frozen=True)
class ProcedureAuthoringHandoff:
    procedure_id: str
    workflow: str
    candidate: ProcedureCandidate


class SQLiteProcedureReviewStore:
    """Review queue that can hand off, but never author executable skills."""

    def __init__(self, store: StateStore) -> None:
        self._store = store
        with store.connect() as connection:
            connection.executescript(PROCEDURE_SCHEMA)

    def stage(
        self,
        candidates: tuple[ProcedureCandidate, ...],
        *,
        run_fingerprint: str,
    ) -> None:
        if not run_fingerprint.strip():
            raise ValueError("Procedure review requires a run fingerprint")
        with self._store.transaction() as connection:
            self.stage_in_transaction(
                connection,
                candidates,
                run_fingerprint=run_fingerprint,
            )

    def stage_in_transaction(
        self,
        connection: sqlite3.Connection,
        candidates: tuple[ProcedureCandidate, ...],
        *,
        run_fingerprint: str,
    ) -> None:
        if not run_fingerprint.strip():
            raise ValueError("Procedure review requires a run fingerprint")
        for candidate in candidates:
            _validate_candidate(candidate)
            placeholders = ",".join("?" for _ in candidate.evidence_refs)
            found = {
                str(row["id"])
                for row in connection.execute(
                    f"SELECT id FROM evidence WHERE id IN ({placeholders})",
                    candidate.evidence_refs,
                ).fetchall()
            }
            if found != set(candidate.evidence_refs):
                raise RuntimeError("Procedure candidate evidence is unavailable")
            encoded = _encode(asdict(candidate))
            existing = connection.execute(
                """SELECT run_fingerprint,candidate_json
                FROM procedure_review_proposals WHERE procedure_id=?""",
                (candidate.procedure_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["run_fingerprint"] != run_fingerprint
                    or existing["candidate_json"] != encoded
                ):
                    raise RuntimeError("Conflicting stable procedure proposal")
                continue
            connection.execute(
                """INSERT INTO procedure_review_proposals(
                procedure_id,run_fingerprint,candidate_json,status,created_at
                ) VALUES(?,?,?,'pending',?)""",
                (
                    candidate.procedure_id,
                    run_fingerprint,
                    encoded,
                    utc_now(),
                ),
            )

    def proposals(self, status: str | None = None) -> tuple[ProcedureProposal, ...]:
        query = "SELECT * FROM procedure_review_proposals"
        parameters: tuple[str, ...] = ()
        if status is not None:
            query += " WHERE status=?"
            parameters = (status,)
        query += " ORDER BY created_at,procedure_id"
        with self._store.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(_proposal_from_row(row) for row in rows)

    def approve(self, procedure_id: str) -> ProcedureAuthoringHandoff:
        with self._store.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM procedure_review_proposals WHERE procedure_id=?",
                (procedure_id,),
            ).fetchone()
            if row is None:
                raise KeyError(procedure_id)
            if row["status"] == "rejected":
                raise RuntimeError("Rejected procedure proposals cannot be approved")
            if row["status"] == "pending":
                connection.execute(
                    """UPDATE procedure_review_proposals
                    SET status='approved',decided_at=? WHERE procedure_id=?""",
                    (utc_now(), procedure_id),
                )
            candidate = _candidate_from_dict(json.loads(row["candidate_json"]))
        return ProcedureAuthoringHandoff(
            procedure_id=procedure_id,
            workflow="skill-creator-review-required",
            candidate=candidate,
        )

    def reject(self, procedure_id: str, *, reason: str) -> None:
        if not reason.strip():
            raise ValueError("Procedure rejection requires a reason")
        with self._store.transaction() as connection:
            row = connection.execute(
                "SELECT status FROM procedure_review_proposals WHERE procedure_id=?",
                (procedure_id,),
            ).fetchone()
            if row is None:
                raise KeyError(procedure_id)
            if row["status"] == "rejected":
                return
            if row["status"] != "pending":
                raise RuntimeError("Only pending procedure proposals may be rejected")
            connection.execute(
                """UPDATE procedure_review_proposals SET status='rejected',
                rejection_reason=?,decided_at=? WHERE procedure_id=?""",
                (reason[:1000], utc_now(), procedure_id),
            )


def _validate_candidate(candidate: ProcedureCandidate) -> None:
    if (
        not PROCEDURE_ID.fullmatch(candidate.procedure_id)
        or candidate.disposition != "review"
        or not candidate.owner_directed
        or not candidate.validated_outcome
        or not candidate.evidence_refs
    ):
        raise ValueError("Procedure proposal is not review eligible")


def _candidate_from_dict(value: dict) -> ProcedureCandidate:
    return ProcedureCandidate(
        **{
            **value,
            "prerequisites": tuple(value["prerequisites"]),
            "steps": tuple(value["steps"]),
            "failure_branches": tuple(value["failure_branches"]),
            "tests": tuple(value["tests"]),
            "evidence_refs": tuple(value["evidence_refs"]),
        }
    )


def _proposal_from_row(row) -> ProcedureProposal:
    return ProcedureProposal(
        procedure_id=str(row["procedure_id"]),
        run_fingerprint=str(row["run_fingerprint"]),
        candidate=_candidate_from_dict(json.loads(row["candidate_json"])),
        status=str(row["status"]),
        rejection_reason=(
            str(row["rejection_reason"]) if row["rejection_reason"] else None
        ),
    )


def _encode(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
