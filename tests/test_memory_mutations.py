from second_brain_protocol.extraction_harness import ExtractionCandidate
from second_brain_protocol.memory_mutations import (
    InMemoryMemoryCatalog,
    MemoryHead,
    MemoryMutationPlanner,
    memory_key_for,
)


def candidate(
    *,
    operation: str = "create",
    claim: str = "The owner prefers concise, auditable run receipts.",
    subject: str = "Run receipts",
    kind: str = "preference",
    scope: str = "global",
    project_id: str | None = None,
    target_memory_id: str | None = None,
    confidence: float = 0.95,
    explicit: bool = True,
) -> ExtractionCandidate:
    return ExtractionCandidate(
        candidate_type="memory_mutation",
        operation=operation,
        kind=kind,
        subject=subject,
        claim=claim,
        scope=scope,
        project_id=project_id,
        target_memory_id=target_memory_id,
        evidence_refs=("ev-memory-plan",),
        confidence=confidence,
        explicit=explicit,
    )


def head() -> MemoryHead:
    item = candidate()
    key = memory_key_for(item)
    return MemoryHead(
        memory_id="memory-existing",
        key=key,
        version_id="memory-version-existing-v1",
        version=1,
        subject=item.subject,
        claim=item.claim,
        status="active",
        reinforcement_count=0,
    )


def test_create_has_stable_entity_and_version_identity() -> None:
    planner = MemoryMutationPlanner(catalog=InMemoryMemoryCatalog())

    first = planner.plan((candidate(),))
    second = planner.plan((candidate(),))

    assert first == second
    item = first.items[0]
    assert item.memory_id.startswith("memory-")
    assert item.mutation_id.startswith("memory-mutation-")
    assert item.version_id.startswith("memory-version-")
    assert item.version == 1
    assert item.layer == "operating-preferences"
    assert item.destination == "Identity/Preferences.md"
    assert item.supersedes_version_id is None
    assert item.contradicts_version_id is None


def test_reinforcement_targets_current_version_without_creating_statement_version() -> None:
    existing = head()
    planner = MemoryMutationPlanner(
        catalog=InMemoryMemoryCatalog((existing,))
    )

    plan = planner.plan(
        (
            candidate(
                operation="reinforce",
                target_memory_id=existing.memory_id,
            ),
        )
    )

    item = plan.items[0]
    assert item.memory_id == existing.memory_id
    assert item.version_id == existing.version_id
    assert item.version == 1
    assert item.creates_version is False
    assert item.reinforcement_count == 1


def test_update_creates_next_version_and_supersedes_current_statement() -> None:
    existing = head()
    plan = MemoryMutationPlanner(
        catalog=InMemoryMemoryCatalog((existing,))
    ).plan(
        (
            candidate(
                operation="update",
                claim="The owner now prefers concise receipts with explicit token use.",
                target_memory_id=existing.memory_id,
            ),
        )
    )

    item = plan.items[0]
    assert item.version == 2
    assert item.version_id != existing.version_id
    assert item.supersedes_version_id == existing.version_id
    assert item.contradicts_version_id is None
    assert item.creates_version is True


def test_contradiction_preserves_edge_and_always_routes_to_review() -> None:
    existing = head()
    plan = MemoryMutationPlanner(
        catalog=InMemoryMemoryCatalog((existing,))
    ).plan(
        (
            candidate(
                operation="contradict",
                claim="Long narrative receipts are preferred.",
                target_memory_id=existing.memory_id,
            ),
        )
    )

    item = plan.items[0]
    assert item.contradicts_version_id == existing.version_id
    assert item.supersedes_version_id is None
    assert item.status == "contested"
    assert item.disposition == "review"


def test_unknown_target_and_duplicate_create_fail_closed() -> None:
    existing = head()
    planner = MemoryMutationPlanner(
        catalog=InMemoryMemoryCatalog((existing,))
    )

    plan = planner.plan(
        (
            candidate(operation="update", target_memory_id="memory-missing"),
            candidate(),
        )
    )

    assert plan.items == ()
    assert [item.code for item in plan.rejections] == [
        "unknown-target-memory",
        "duplicate-create-requires-target",
    ]


def test_project_memory_key_isolated_by_exact_project() -> None:
    one = memory_key_for(
        candidate(
            kind="decision",
            scope="project",
            project_id="project-one",
        )
    )
    two = memory_key_for(
        candidate(
            kind="decision",
            scope="project",
            project_id="project-two",
        )
    )

    assert one != two
    assert one.layer == "project-knowledge"


def test_same_batch_cannot_plan_duplicate_creates_for_one_memory_key() -> None:
    plan = MemoryMutationPlanner(catalog=InMemoryMemoryCatalog()).plan(
        (candidate(), candidate(claim="A competing value for the same subject."))
    )

    assert len(plan.items) == 1
    assert [item.code for item in plan.rejections] == [
        "batch-memory-conflict"
    ]


def test_same_batch_cannot_plan_two_mutations_for_one_memory_entity() -> None:
    existing = head()
    planner = MemoryMutationPlanner(catalog=InMemoryMemoryCatalog((existing,)))

    plan = planner.plan(
        (
            candidate(
                operation="update",
                claim="First proposed update.",
                target_memory_id=existing.memory_id,
            ),
            candidate(
                operation="contradict",
                claim="Second competing update.",
                target_memory_id=existing.memory_id,
            ),
        )
    )

    assert len(plan.items) == 1
    assert [item.code for item in plan.rejections] == [
        "batch-memory-conflict"
    ]


def test_targeted_mutation_cannot_rename_stable_memory_key() -> None:
    existing = head()
    plan = MemoryMutationPlanner(
        catalog=InMemoryMemoryCatalog((existing,))
    ).plan(
        (
            candidate(
                operation="update",
                subject="A different stable subject",
                claim="A proposed claim under another identity.",
                target_memory_id=existing.memory_id,
            ),
        )
    )

    assert plan.items == ()
    assert [item.code for item in plan.rejections] == [
        "target-memory-key-mismatch"
    ]


def test_update_proposal_identity_binds_expected_head_version() -> None:
    original = head()
    advanced = MemoryHead(
        memory_id=original.memory_id,
        key=original.key,
        version_id="memory-version-existing-v2",
        version=2,
        subject=original.subject,
        claim="An intervening update changed the current claim.",
        status="active",
        reinforcement_count=0,
    )
    proposed = candidate(
        operation="update",
        claim="The owner prefers receipts with explicit token use.",
        target_memory_id=original.memory_id,
    )

    first = MemoryMutationPlanner(
        catalog=InMemoryMemoryCatalog((original,))
    ).plan((proposed,)).items[0]
    replanned = MemoryMutationPlanner(
        catalog=InMemoryMemoryCatalog((advanced,))
    ).plan((proposed,)).items[0]

    assert first.mutation_id != replanned.mutation_id


def test_reinforcement_identity_binds_expected_reinforcement_sequence() -> None:
    original = head()
    reinforced = MemoryHead(
        memory_id=original.memory_id,
        key=original.key,
        version_id=original.version_id,
        version=original.version,
        subject=original.subject,
        claim=original.claim,
        status=original.status,
        reinforcement_count=1,
    )
    proposed = candidate(
        operation="reinforce",
        target_memory_id=original.memory_id,
    )

    first = MemoryMutationPlanner(
        catalog=InMemoryMemoryCatalog((original,))
    ).plan((proposed,)).items[0]
    replanned = MemoryMutationPlanner(
        catalog=InMemoryMemoryCatalog((reinforced,))
    ).plan((proposed,)).items[0]

    assert first.mutation_id != replanned.mutation_id
