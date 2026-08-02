from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol

from .daily_weekly_runner import (
    EvidenceManifest,
    RunArtifact,
    RunArtifactStore,
    RunReceipt,
    artifact_checkpoint_eligible,
    recompute_artifact_fingerprint,
)
from .extraction_harness import extraction_contract_fingerprint
from .publication_policy import (
    PublicationManifest,
    PublicationPolicyContext,
    StagedCanonicalFile,
    validate_staged_publication,
)


CUTOVER_CONTRACT_VERSION = "daily-weekly-cutover-v2"


@dataclass(frozen=True)
class CutoverAuthorization:
    authorization_id: str
    contract_version: str
    approved: bool
    run_fingerprint: str
    run_kind: str
    period: str
    artifact_fingerprint: str
    behavior_contract: str


@dataclass(frozen=True)
class ApplyRequest:
    receipt: RunReceipt
    authorization_id: str


@dataclass(frozen=True)
class ApplyReceipt:
    idempotency_key: str
    run_fingerprint: str
    artifact_fingerprint: str
    authorization_id: str
    changed_paths: tuple[str, ...]
    published: bool
    checkpoint_advanced: bool


@dataclass(frozen=True)
class ApplyJournalEntry:
    idempotency_key: str
    run_fingerprint: str
    artifact_fingerprint: str
    authorization_id: str
    staging_id: str
    publication: PublicationManifest
    state: str


@dataclass(frozen=True)
class ApplyStateManifest:
    run_kind: str
    period: str
    evidence: EvidenceManifest
    publication: PublicationManifest


class PreparedPublication(Protocol):
    staging_id: str
    staged_files: tuple[StagedCanonicalFile, ...]

    def commit(self, manifest: PublicationManifest) -> None:
        """Compare-and-swap all files atomically, or confirm exact idempotent state."""

    def rollback(self) -> None:
        """Discard staging that has definitely not been committed."""

    def verify_committed(self, manifest: PublicationManifest) -> bool:
        """Verify current canonical bytes exactly match the durable manifest."""


class PublicationPlanner(Protocol):
    def prepare(
        self,
        artifact: RunArtifact,
        *,
        idempotency_key: str,
    ) -> PreparedPublication:
        """Idempotently create or return a stable staging publication."""

    def open(self, staging_id: str) -> PreparedPublication:
        """Reopen durable staging while reconciling an interrupted apply."""


class PublicationPolicyResolver(Protocol):
    def context_for(self, artifact: RunArtifact) -> PublicationPolicyContext: ...


class BoundedPublicationPolicy:
    def __init__(self, *, owned_sections: tuple[str, ...]) -> None:
        self._owned_sections = tuple(sorted(set(owned_sections)))
        if not self._owned_sections:
            raise ValueError("At least one generated section owner is required")

    def context_for(self, artifact: RunArtifact) -> PublicationPolicyContext:
        return PublicationPolicyContext(
            run_kind=artifact.run_kind,
            period=artifact.period,
            owned_sections=self._owned_sections,
        )


class ApplyJournal(Protocol):
    def get(self, idempotency_key: str) -> ApplyJournalEntry | None: ...

    def put_prepared(self, entry: ApplyJournalEntry) -> None: ...

    def mark_files_committed(self, idempotency_key: str) -> None: ...

    def mark_completed(self, idempotency_key: str) -> None: ...


class ApplyStateCommitter(Protocol):
    """Own the DB transaction for mutations, checkpoint, queue, and receipt."""

    def get_receipt(self, idempotency_key: str) -> ApplyReceipt | None: ...

    def preflight(
        self,
        artifact: RunArtifact,
        *,
        manifest: ApplyStateManifest,
    ) -> None:
        """Validate authoritative state before any canonical file commit."""

    def commit(
        self,
        artifact: RunArtifact,
        *,
        manifest: ApplyStateManifest,
        idempotency_key: str,
        receipt: ApplyReceipt,
    ) -> None:
        """Idempotently store all state changes and receipt in one transaction."""


class CutoverAuthorizationStore(Protocol):
    def resolve(self, authorization_id: str) -> CutoverAuthorization | None: ...

    def bind_once(self, authorization_id: str, *, operation_key: str) -> None:
        """Durably bind an issued authorization to exactly one apply identity."""


class InMemoryCutoverAuthorizationStore:
    """Reference approval registry; production cutover requires durable storage."""

    def __init__(
        self,
        authorizations: tuple[CutoverAuthorization, ...] = (),
    ) -> None:
        self._authorizations = {
            item.authorization_id: item for item in authorizations
        }
        self._bindings: dict[str, str] = {}

    def add(self, authorization: CutoverAuthorization) -> None:
        existing = self._authorizations.get(authorization.authorization_id)
        if existing is not None and existing != authorization:
            raise RuntimeError("Cutover authorization ID is already issued")
        self._authorizations[authorization.authorization_id] = authorization

    def resolve(self, authorization_id: str) -> CutoverAuthorization | None:
        return self._authorizations.get(authorization_id)

    def bind_once(self, authorization_id: str, *, operation_key: str) -> None:
        if authorization_id not in self._authorizations:
            raise RuntimeError("Cutover authorization was not issued")
        existing = self._bindings.get(authorization_id)
        if existing is not None and existing != operation_key:
            raise RuntimeError("Cutover authorization was already consumed")
        self._bindings[authorization_id] = operation_key


class ApplyCoordinator(Protocol):
    def apply(
        self,
        artifact: RunArtifact,
        authorization: CutoverAuthorization,
    ) -> ApplyReceipt: ...


class InMemoryApplyJournal:
    """Reference state machine; durable implementations use the same transitions."""

    def __init__(self) -> None:
        self._entries: dict[str, ApplyJournalEntry] = {}

    def get(self, idempotency_key: str) -> ApplyJournalEntry | None:
        return self._entries.get(idempotency_key)

    def put_prepared(self, entry: ApplyJournalEntry) -> None:
        if entry.state != "prepared":
            raise RuntimeError("New apply journal entries must be prepared")
        existing = self._entries.get(entry.idempotency_key)
        if existing is not None and existing != entry:
            raise RuntimeError("Conflicting atomic-apply journal entry")
        self._entries[entry.idempotency_key] = entry

    def mark_files_committed(self, idempotency_key: str) -> None:
        entry = self._required(idempotency_key)
        if entry.state in {"files_committed", "completed"}:
            return
        if entry.state != "prepared":
            raise RuntimeError("Invalid atomic-apply journal transition")
        self._entries[idempotency_key] = _journal_with_state(
            entry, "files_committed"
        )

    def mark_completed(self, idempotency_key: str) -> None:
        entry = self._required(idempotency_key)
        if entry.state == "completed":
            return
        if entry.state != "files_committed":
            raise RuntimeError("Invalid atomic-apply journal transition")
        self._entries[idempotency_key] = _journal_with_state(entry, "completed")

    def _required(self, idempotency_key: str) -> ApplyJournalEntry:
        entry = self._entries.get(idempotency_key)
        if entry is None:
            raise RuntimeError("Atomic-apply journal entry is unavailable")
        return entry


class JournaledApplyCoordinator:
    """Reconcile staged files and transactional state through a durable journal."""

    def __init__(
        self,
        *,
        publisher: PublicationPlanner,
        state_committer: ApplyStateCommitter,
        journal: ApplyJournal,
        publication_policy: PublicationPolicyResolver,
    ) -> None:
        self._publisher = publisher
        self._state_committer = state_committer
        self._journal = journal
        self._publication_policy = publication_policy

    def apply(
        self,
        artifact: RunArtifact,
        authorization: CutoverAuthorization,
    ) -> ApplyReceipt:
        idempotency_key = _idempotency_key(artifact, authorization)
        recorded = self._state_committer.get_receipt(idempotency_key)
        if recorded is not None:
            _validate_recorded_receipt(
                recorded,
                artifact=artifact,
                authorization=authorization,
                idempotency_key=idempotency_key,
            )
            entry = self._journal.get(idempotency_key)
            if entry is not None and entry.state != "completed":
                self._journal.mark_completed(idempotency_key)
            return recorded

        entry = self._journal.get(idempotency_key)
        if entry is None:
            prepared = self._publisher.prepare(
                artifact,
                idempotency_key=idempotency_key,
            )
            try:
                publication = validate_staged_publication(
                    tuple(prepared.staged_files),
                    context=self._publication_policy.context_for(artifact),
                )
                _validate_publication_has_candidate_changes(artifact, publication)
                if not prepared.staging_id.strip():
                    raise RuntimeError("Prepared publication has no durable staging ID")
            except Exception:
                prepared.rollback()
                raise
            entry = ApplyJournalEntry(
                idempotency_key=idempotency_key,
                run_fingerprint=artifact.run_fingerprint,
                artifact_fingerprint=artifact.artifact_fingerprint,
                authorization_id=authorization.authorization_id,
                staging_id=prepared.staging_id,
                publication=publication,
                state="prepared",
            )
            self._journal.put_prepared(entry)
        else:
            _validate_journal_entry(
                entry,
                artifact=artifact,
                authorization=authorization,
                idempotency_key=idempotency_key,
            )

        state_manifest = ApplyStateManifest(
            run_kind=artifact.run_kind,
            period=artifact.period,
            evidence=artifact.evidence_manifest,
            publication=entry.publication,
        )

        if entry.state == "prepared":
            self._state_committer.preflight(
                artifact,
                manifest=state_manifest,
            )
            prepared = _open_validated_publication(
                self._publisher,
                artifact=artifact,
                entry=entry,
                policy=self._publication_policy,
            )
            prepared.commit(entry.publication)
            self._journal.mark_files_committed(idempotency_key)
            entry = self._journal.get(idempotency_key)
            if entry is None:
                raise RuntimeError("Atomic-apply journal entry disappeared")

        if entry.state == "completed":
            raise RuntimeError("Completed apply journal is missing its durable receipt")
        if entry.state != "files_committed":
            raise RuntimeError("Atomic-apply journal has an unknown state")

        prepared = _open_validated_publication(
            self._publisher,
            artifact=artifact,
            entry=entry,
            policy=self._publication_policy,
        )
        if not prepared.verify_committed(entry.publication):
            raise RuntimeError(
                "Committed publication no longer matches its durable manifest"
            )

        pending_receipt = ApplyReceipt(
            idempotency_key=idempotency_key,
            run_fingerprint=artifact.run_fingerprint,
            artifact_fingerprint=artifact.artifact_fingerprint,
            authorization_id=entry.authorization_id,
            changed_paths=entry.publication.changed_paths,
            published=True,
            checkpoint_advanced=True,
        )
        self._state_committer.commit(
            artifact,
            manifest=state_manifest,
            idempotency_key=idempotency_key,
            receipt=pending_receipt,
        )
        recorded = self._state_committer.get_receipt(idempotency_key)
        if recorded != pending_receipt:
            raise RuntimeError("Transactional state did not persist its apply receipt")
        self._journal.mark_completed(idempotency_key)
        return recorded


class AtomicApplyGate:
    """Validate review authorization before entering the durable apply coordinator."""

    def __init__(
        self,
        *,
        artifact_store: RunArtifactStore,
        coordinator: ApplyCoordinator,
        authorization_store: CutoverAuthorizationStore,
    ) -> None:
        self._artifact_store = artifact_store
        self._coordinator = coordinator
        self._authorization_store = authorization_store

    def apply(self, request: ApplyRequest) -> ApplyReceipt:
        receipt = request.receipt
        authorization = self._authorization_store.resolve(request.authorization_id)
        if authorization is None:
            raise RuntimeError("Daily/Weekly cutover is not approved")
        if (
            not authorization.approved
            or authorization.contract_version != CUTOVER_CONTRACT_VERSION
            or not authorization.authorization_id.strip()
        ):
            raise RuntimeError("Daily/Weekly cutover is not approved")
        if authorization.artifact_fingerprint != receipt.artifact_fingerprint:
            raise RuntimeError("Cutover artifact fingerprint mismatch")
        if (
            authorization.run_fingerprint != receipt.fingerprint
            or authorization.run_kind != receipt.run_kind
            or authorization.period != receipt.period
        ):
            raise RuntimeError("Cutover authorization binding mismatch")
        if receipt.published:
            raise RuntimeError("Run receipt has already been marked published")

        artifact = self._artifact_store.get(receipt.fingerprint)
        if artifact is None:
            raise RuntimeError("Governed run artifact is unavailable")
        if (
            artifact.run_fingerprint != receipt.fingerprint
            or artifact.artifact_fingerprint != receipt.artifact_fingerprint
            or artifact.run_kind != receipt.run_kind
            or artifact.period != receipt.period
            or artifact.mode != receipt.mode
            or authorization.behavior_contract != artifact.behavior_contract
        ):
            raise RuntimeError("Stored governed artifact does not match the run receipt")
        if artifact.artifact_fingerprint != recompute_artifact_fingerprint(artifact):
            raise RuntimeError("Stored governed artifact fingerprint is invalid")
        if artifact.extraction_contract != extraction_contract_fingerprint(
            artifact.run_kind
        ):
            raise RuntimeError("Governed artifact uses an obsolete extraction contract")
        if not artifact_checkpoint_eligible(artifact):
            raise RuntimeError("stored artifact is not checkpoint eligible")
        self._authorization_store.bind_once(
            authorization.authorization_id,
            operation_key=_idempotency_key(artifact, authorization),
        )
        return self._coordinator.apply(artifact, authorization)


def _validate_publication_has_candidate_changes(
    artifact: RunArtifact,
    publication: PublicationManifest,
) -> None:
    if artifact.extraction.candidates and not publication.files:
        raise RuntimeError("Accepted memory candidates require a canonical change set")


def _open_validated_publication(
    publisher: PublicationPlanner,
    *,
    artifact: RunArtifact,
    entry: ApplyJournalEntry,
    policy: PublicationPolicyResolver,
) -> PreparedPublication:
    prepared = publisher.open(entry.staging_id)
    publication = validate_staged_publication(
        tuple(prepared.staged_files),
        context=policy.context_for(artifact),
    )
    _validate_publication_has_candidate_changes(artifact, publication)
    if prepared.staging_id != entry.staging_id or publication != entry.publication:
        raise RuntimeError("Durable staging no longer matches apply journal")
    return prepared


def _idempotency_key(
    artifact: RunArtifact,
    authorization: CutoverAuthorization,
) -> str:
    payload = {
        "contract_version": authorization.contract_version,
        "run_fingerprint": artifact.run_fingerprint,
        "artifact_fingerprint": artifact.artifact_fingerprint,
        "run_kind": artifact.run_kind,
        "period": artifact.period,
        "behavior_contract": artifact.behavior_contract,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _validate_journal_entry(
    entry: ApplyJournalEntry,
    *,
    artifact: RunArtifact,
    authorization: CutoverAuthorization,
    idempotency_key: str,
) -> None:
    if (
        entry.idempotency_key != idempotency_key
        or entry.run_fingerprint != artifact.run_fingerprint
        or entry.artifact_fingerprint != artifact.artifact_fingerprint
    ):
        raise RuntimeError("Atomic-apply journal binding mismatch")


def _validate_recorded_receipt(
    receipt: ApplyReceipt,
    *,
    artifact: RunArtifact,
    authorization: CutoverAuthorization,
    idempotency_key: str,
) -> None:
    if (
        receipt.idempotency_key != idempotency_key
        or receipt.run_fingerprint != artifact.run_fingerprint
        or receipt.artifact_fingerprint != artifact.artifact_fingerprint
        or not receipt.published
        or not receipt.checkpoint_advanced
    ):
        raise RuntimeError("Transactional apply receipt binding mismatch")


def _journal_with_state(
    entry: ApplyJournalEntry,
    state: str,
) -> ApplyJournalEntry:
    return ApplyJournalEntry(
        idempotency_key=entry.idempotency_key,
        run_fingerprint=entry.run_fingerprint,
        artifact_fingerprint=entry.artifact_fingerprint,
        authorization_id=entry.authorization_id,
        staging_id=entry.staging_id,
        publication=entry.publication,
        state=state,
    )
