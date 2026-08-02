import hashlib
from pathlib import Path

import pytest

from second_brain_protocol.extraction_harness import ExtractionCandidate
from second_brain_protocol.memory_mutations import (
    InMemoryMemoryCatalog,
    MemoryMutationPlanner,
)
from second_brain_protocol.memory_registry import (
    CanonicalMemoryPublicationVerifier,
    MemoryPublicationReceipt,
    SQLiteMemoryRegistry,
)
from second_brain_protocol.state import StateStore, utc_now


def candidate(
    *,
    operation: str = "create",
    claim: str = "The owner prefers concise, auditable run receipts.",
    target_memory_id: str | None = None,
) -> ExtractionCandidate:
    return ExtractionCandidate(
        candidate_type="memory_mutation",
        operation=operation,
        kind="preference",
        subject="Run receipts",
        claim=claim,
        scope="global",
        project_id=None,
        target_memory_id=target_memory_id,
        evidence_refs=("ev-memory-registry",),
        confidence=0.95,
        explicit=True,
    )


def registry(tmp_path: Path) -> SQLiteMemoryRegistry:
    store = StateStore(tmp_path / "state.sqlite")
    with store.connect() as connection:
        connection.execute(
            """INSERT INTO evidence(
            id,source_type,source_ref,project_id,kind,occurred_at,content_hash,
            payload_json,status,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                "ev-memory-registry",
                "owner-answer",
                "owner:test",
                None,
                "owner_answer_draft",
                "2026-08-01T10:00:00Z",
                "content-v1",
                "{}",
                "new",
                utc_now(),
            ),
        )
    return SQLiteMemoryRegistry(
        store,
        publication_verifier=CanonicalMemoryPublicationVerifier(tmp_path),
    )


def publication(tmp_path: Path, item) -> MemoryPublicationReceipt:
    content = f"<!-- sb:memory-mutation {item.mutation_id} -->\n{item.claim}\n"
    destination = tmp_path / Path(item.destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content.encode("utf-8"))
    return MemoryPublicationReceipt(
        mutation_id=item.mutation_id,
        destination=item.destination,
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )


def test_staged_memory_mutation_is_review_only_until_approved(tmp_path: Path) -> None:
    memory_registry = registry(tmp_path)
    plan = MemoryMutationPlanner(catalog=memory_registry).plan((candidate(),))
    item = plan.items[0]

    memory_registry.stage(plan, run_fingerprint="run-one")

    assert memory_registry.get(item.memory_id) is None
    assert [proposal.status for proposal in memory_registry.proposals()] == [
        "pending"
    ]

    memory_registry.approve(item.mutation_id, publication(tmp_path, item))

    head = memory_registry.get(item.memory_id)
    assert head is not None
    assert head.version_id == item.version_id
    assert head.version == 1
    assert head.claim == item.claim
    assert memory_registry.version_evidence(item.version_id) == (
        "ev-memory-registry",
    )


def test_registry_reopens_with_stable_entity_and_version_identity(tmp_path: Path) -> None:
    memory_registry = registry(tmp_path)
    item = MemoryMutationPlanner(catalog=memory_registry).plan((candidate(),)).items[0]
    memory_registry.stage(
        MemoryMutationPlanner(catalog=memory_registry).plan((candidate(),)),
        run_fingerprint="run-one",
    )
    memory_registry.approve(item.mutation_id, publication(tmp_path, item))

    reopened = SQLiteMemoryRegistry(
        StateStore(tmp_path / "state.sqlite"),
        publication_verifier=CanonicalMemoryPublicationVerifier(tmp_path),
    )

    assert reopened.get(item.memory_id) == memory_registry.get(item.memory_id)


def test_approved_update_supersedes_prior_version(tmp_path: Path) -> None:
    memory_registry = registry(tmp_path)
    create_plan = MemoryMutationPlanner(catalog=memory_registry).plan((candidate(),))
    created = create_plan.items[0]
    memory_registry.stage(create_plan, run_fingerprint="run-create")
    memory_registry.approve(created.mutation_id, publication(tmp_path, created))
    update_plan = MemoryMutationPlanner(catalog=memory_registry).plan(
        (
            candidate(
                operation="update",
                claim="The owner prefers concise receipts with explicit token use.",
                target_memory_id=created.memory_id,
            ),
        )
    )
    updated = update_plan.items[0]

    memory_registry.stage(update_plan, run_fingerprint="run-update")
    memory_registry.approve(updated.mutation_id, publication(tmp_path, updated))

    head = memory_registry.get(created.memory_id)
    assert head is not None and head.version == 2
    assert head.version_id == updated.version_id
    assert memory_registry.version(created.version_id)["status"] == "superseded"
    assert memory_registry.version(updated.version_id)["supersedes_version_id"] == (
        created.version_id
    )


def test_approved_contradiction_preserves_contested_edge(tmp_path: Path) -> None:
    memory_registry = registry(tmp_path)
    create_plan = MemoryMutationPlanner(catalog=memory_registry).plan((candidate(),))
    created = create_plan.items[0]
    memory_registry.stage(create_plan, run_fingerprint="run-create")
    memory_registry.approve(created.mutation_id, publication(tmp_path, created))
    contradiction = MemoryMutationPlanner(catalog=memory_registry).plan(
        (
            candidate(
                operation="contradict",
                claim="The owner prefers long narrative run receipts.",
                target_memory_id=created.memory_id,
            ),
        )
    ).items[0]

    memory_registry.stage(
        type(create_plan)(items=(contradiction,), rejections=()),
        run_fingerprint="run-contradict",
    )
    memory_registry.approve(
        contradiction.mutation_id,
        publication(tmp_path, contradiction),
    )

    head = memory_registry.get(created.memory_id)
    assert head is not None and head.status == "contested"
    assert memory_registry.version(contradiction.version_id)[
        "contradicts_version_id"
    ] == created.version_id


def test_reinforcement_is_idempotent_and_does_not_create_version(tmp_path: Path) -> None:
    memory_registry = registry(tmp_path)
    create_plan = MemoryMutationPlanner(catalog=memory_registry).plan((candidate(),))
    created = create_plan.items[0]
    memory_registry.stage(create_plan, run_fingerprint="run-create")
    memory_registry.approve(created.mutation_id, publication(tmp_path, created))
    reinforcement_plan = MemoryMutationPlanner(catalog=memory_registry).plan(
        (
            candidate(
                operation="reinforce",
                target_memory_id=created.memory_id,
            ),
        )
    )
    reinforcement = reinforcement_plan.items[0]
    memory_registry.stage(reinforcement_plan, run_fingerprint="run-reinforce")

    memory_registry.approve(
        reinforcement.mutation_id,
        publication(tmp_path, reinforcement),
    )
    memory_registry.approve(
        reinforcement.mutation_id,
        publication(tmp_path, reinforcement),
    )

    head = memory_registry.get(created.memory_id)
    assert head is not None
    assert head.version == 1
    assert head.reinforcement_count == 1


def test_approval_rejects_wrong_publication_receipt(tmp_path: Path) -> None:
    memory_registry = registry(tmp_path)
    plan = MemoryMutationPlanner(catalog=memory_registry).plan((candidate(),))
    item = plan.items[0]
    memory_registry.stage(plan, run_fingerprint="run-one")

    with pytest.raises(RuntimeError, match="publication receipt"):
        memory_registry.approve(
            item.mutation_id,
            MemoryPublicationReceipt(
                mutation_id=item.mutation_id,
                destination="Protocol/OperatingContract.md",
                content_hash="b" * 64,
            ),
        )

    assert memory_registry.get(item.memory_id) is None


def test_approval_rejects_unrelated_canonical_bytes_with_forged_receipt(
    tmp_path: Path,
) -> None:
    memory_registry = registry(tmp_path)
    plan = MemoryMutationPlanner(catalog=memory_registry).plan((candidate(),))
    item = plan.items[0]
    memory_registry.stage(plan, run_fingerprint="run-one")
    content = "unrelated canonical content\n"
    destination = tmp_path / Path(item.destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content.encode("utf-8"))
    forged = MemoryPublicationReceipt(
        mutation_id=item.mutation_id,
        destination=item.destination,
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )

    with pytest.raises(RuntimeError, match="mutation marker"):
        memory_registry.approve(item.mutation_id, forged)

    assert memory_registry.get(item.memory_id) is None


def test_stale_pending_update_can_be_replanned_against_new_head(
    tmp_path: Path,
) -> None:
    memory_registry = registry(tmp_path)
    create_plan = MemoryMutationPlanner(catalog=memory_registry).plan((candidate(),))
    created = create_plan.items[0]
    memory_registry.stage(create_plan, run_fingerprint="run-create")
    memory_registry.approve(created.mutation_id, publication(tmp_path, created))
    first_plan = MemoryMutationPlanner(catalog=memory_registry).plan(
        (
            candidate(
                operation="update",
                claim="The owner prefers concise receipts with token accounting.",
                target_memory_id=created.memory_id,
            ),
        )
    )
    stale_plan = MemoryMutationPlanner(catalog=memory_registry).plan(
        (
            candidate(
                operation="update",
                claim="The owner prefers concise receipts with evidence accounting.",
                target_memory_id=created.memory_id,
            ),
        )
    )
    first = first_plan.items[0]
    stale = stale_plan.items[0]
    memory_registry.stage(first_plan, run_fingerprint="run-first")
    memory_registry.stage(stale_plan, run_fingerprint="run-stale")
    memory_registry.approve(first.mutation_id, publication(tmp_path, first))

    with pytest.raises(RuntimeError, match="stale"):
        memory_registry.approve(stale.mutation_id, publication(tmp_path, stale))

    replanned_plan = MemoryMutationPlanner(catalog=memory_registry).plan(
        (
            candidate(
                operation="update",
                claim=stale.claim,
                target_memory_id=created.memory_id,
            ),
        )
    )
    replanned = replanned_plan.items[0]
    assert replanned.mutation_id != stale.mutation_id
    memory_registry.stage(replanned_plan, run_fingerprint="run-replanned")
    memory_registry.approve(
        replanned.mutation_id,
        publication(tmp_path, replanned),
    )

    head = memory_registry.get(created.memory_id)
    assert head is not None and head.version == 3
    assert head.claim == stale.claim
