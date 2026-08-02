from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import replace
from pathlib import Path
from typing import Iterable

from .extraction_harness import (
    CandidateRejection,
    ExtractionCandidate,
    ExtractionRequest,
    ExtractionResult,
)
from .project_catalog import catalog_groups, project_note_paths
from .state import StateStore


class SQLiteEstablishedMemoryFilter:
    """Suppress semantically unchanged memories already known to the Brain."""

    def __init__(self, store: StateStore, *, vault: Path | None = None) -> None:
        self._store = store
        self._vault = vault

    def filter(
        self,
        result: ExtractionResult,
        _request: ExtractionRequest,
    ) -> ExtractionResult:
        established = tuple(self._established_claims())
        accepted: list[ExtractionCandidate] = []
        rejected = list(result.rejections)
        for index, candidate in enumerate(result.candidates):
            status = _established_status(candidate, established)
            if status is not None:
                rejected.append(
                    CandidateRejection(
                        index=index,
                        code=(
                            "rejected-memory-tombstone"
                            if status == "rejected"
                            else "stale-established-memory"
                        ),
                        candidate_type=candidate.candidate_type,
                        evidence_refs=candidate.evidence_refs,
                    )
                )
                continue
            accepted.append(candidate)
        return replace(
            result,
            status=_status(
                candidates=accepted,
                procedures=result.procedures,
                rejections=rejected,
                failures=result.failures,
            ),
            candidates=tuple(accepted),
            rejections=tuple(rejected),
        )

    def _established_claims(
        self,
    ) -> Iterable[tuple[str, str, str, str | None, str]]:
        for item in self._store.observations():
            payload = item.get("payload") or {}
            yield (
                str(item.get("kind") or ""),
                str(item.get("subject") or ""),
                str(item.get("claim") or ""),
                _project_id(payload),
                str(item.get("status") or ""),
            )
        with self._store.connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='memory_mutation_proposals'"
            ).fetchone()
            if exists:
                rows = connection.execute(
                    "SELECT item_json,status FROM memory_mutation_proposals"
                ).fetchall()
                for row in rows:
                    item = json.loads(str(row["item_json"]))
                    yield (
                        str(item.get("memory_key", {}).get("kind") or ""),
                        str(item.get("subject") or ""),
                        str(item.get("claim") or ""),
                        item.get("memory_key", {}).get("project_id"),
                        str(row["status"] or ""),
                    )
        if self._vault is not None:
            projects, _collections, _folders = catalog_groups(
                self._store.present_projects()
            )
            note_projects = {
                f"Projects/{stem}.md": project_id
                for project_id, stem in project_note_paths(projects).items()
            }
            for path in sorted(self._vault.rglob("*.md")):
                relative = path.relative_to(self._vault).as_posix()
                if relative.startswith((".git/", "Protocol/", "Inbox/", "System/")):
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    continue
                yield (
                    "",
                    "",
                    text,
                    note_projects.get(relative),
                    "canonical",
                )


def _project_id(payload: dict) -> str | None:
    direct = payload.get("project_id")
    if direct:
        return str(direct)
    values = payload.get("project_ids")
    if isinstance(values, list) and len(values) == 1 and values[0]:
        return str(values[0])
    return None


def _established_status(
    candidate: ExtractionCandidate,
    established: tuple[tuple[str, str, str, str | None, str], ...],
) -> str | None:
    claim = _normalized(candidate.claim)
    subject = _normalized(candidate.subject)
    candidate_tokens = set(claim.split())
    for kind, known_subject, known_claim, project_id, status in established:
        known = _normalized(known_claim)
        if not known:
            continue
        if project_id == candidate.project_id and (
            claim == known or (len(claim) >= 24 and claim in known)
        ):
            return status
        if kind and kind != candidate.kind:
            continue
        if project_id != candidate.project_id:
            continue
        if subject != _normalized(known_subject):
            continue
        known_tokens = set(known.split())
        union = candidate_tokens | known_tokens
        similarity = len(candidate_tokens & known_tokens) / len(union) if union else 1.0
        if similarity >= 0.9 and not _materially_changed(claim, known):
            return status
    return None


def _materially_changed(left: str, right: str) -> bool:
    left_numbers = set(re.findall(r"\b\d+(?:\.\d+)?\b", left))
    right_numbers = set(re.findall(r"\b\d+(?:\.\d+)?\b", right))
    if left_numbers != right_numbers:
        return True
    negations = {"no", "not", "never", "without", "cannot", "can't", "won't"}
    return bool((set(left.split()) ^ set(right.split())) & negations)


def _normalized(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"[\w']+", normalized, flags=re.UNICODE))


def _status(*, candidates, procedures, rejections, failures) -> str:
    if candidates or procedures:
        return "partial" if rejections or failures else "accepted"
    if failures:
        return "failed"
    if rejections:
        return "rejected"
    return "empty"
