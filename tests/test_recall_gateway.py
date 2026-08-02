from pathlib import Path

import pytest

from second_brain_protocol.recall_backends import (
    CanonicalLexicalBackend,
    stable_source_id,
)
from second_brain_protocol.recall_gateway import (
    CanonicalNoteMetadataCatalog,
    GatewayRecallRequest,
    RecallGateway,
    RecallGrant,
    RecallSegmentBinding,
    SQLiteRecallGrantStore,
    SQLiteRecallSegmentMetadataCatalog,
    SQLiteRecallTelemetryStore,
)
from second_brain_protocol.recall_harness import RecallCandidate, RecallHarness
from second_brain_protocol.segment_index import (
    ChangedSegmentIndexer,
    SQLiteSegmentManifest,
)
from second_brain_protocol.state import StateStore


class EmptySearchBackend:
    def search(self, _query: str, *, limit: int):
        return ()


class StaticSearchBackend:
    def __init__(self, candidates: tuple[RecallCandidate, ...]) -> None:
        self._candidates = candidates

    def search(self, _query: str, *, limit: int):
        return self._candidates[:limit]


class SegmentBackend:
    def apply(self, _change_set) -> None:
        return None


class EmptySegmentMetadata:
    def resolve(self, _hit):
        return None


def _note(vault: Path, relative: str, text: str) -> None:
    path = vault / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_opted_in_private_caller_receives_typed_canonical_context(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    _note(
        vault,
        "Identity/Preferences.md",
        """---
id: identity-preferences
type: identity
status: active
confidence: 0.9
last_verified: 2026-08-01
sensitivity: private
---

# Preferences

The owner prefers concise, auditable run receipts.
""",
    )
    grants = SQLiteRecallGrantStore(StateStore(tmp_path / "state.sqlite"))
    grants.issue(
        RecallGrant(
            caller_id="project-agent",
            purposes=("general-private",),
            project_ids=(),
            allow_private_global=True,
        )
    )
    gateway = RecallGateway(
        harness=RecallHarness(
            lexical=CanonicalLexicalBackend(vault),
            vector=EmptySearchBackend(),
        ),
        metadata=CanonicalNoteMetadataCatalog(vault),
        grants=grants,
    )

    packet = gateway.retrieve(
        GatewayRecallRequest(
            caller_id="project-agent",
            purpose="general-private",
            query="concise auditable receipts",
        )
    )

    assert packet.purpose == "general-private"
    assert len(packet.items) == 1
    item = packet.items[0]
    assert item.canonical_id == "identity-preferences"
    assert item.layer == "identity"
    assert item.project_id is None
    assert item.confirmation_state == "confirmed"
    assert item.confidence == 0.9
    assert item.last_verified == "2026-08-01"
    assert item.sensitivity == "private"
    assert "concise, auditable run receipts" in item.excerpt
    assert item.canonical_link == "[[Identity/Preferences]]"
    assert packet.used_chars <= 6000


def test_write_as_me_profile_excludes_project_and_career_layers(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    frontmatter = """---
id: {note_id}
type: {note_type}
status: active
confidence: 0.9
last_verified: 2026-08-01
---

# {title}

{body}
"""
    _note(
        vault,
        "Identity/Voice.md",
        frontmatter.format(
            note_id="identity-voice",
            note_type="identity",
            title="Voice",
            body="Use a concise natural voice for architecture explanations.",
        ),
    )
    _note(
        vault,
        "Experience/Employment.md",
        frontmatter.format(
            note_id="experience-employment",
            note_type="experience",
            title="Employment",
            body="Architecture work appears in verified employment history.",
        ),
    )
    grants = SQLiteRecallGrantStore(StateStore(tmp_path / "state.sqlite"))
    grants.issue(
        RecallGrant(
            caller_id="writing-agent",
            purposes=("write-as-me",),
            project_ids=(),
            allow_private_global=True,
        )
    )
    gateway = RecallGateway(
        harness=RecallHarness(
            lexical=CanonicalLexicalBackend(vault),
            vector=EmptySearchBackend(),
        ),
        metadata=CanonicalNoteMetadataCatalog(vault),
        grants=grants,
    )

    packet = gateway.retrieve(
        GatewayRecallRequest(
            caller_id="writing-agent",
            purpose="write-as-me",
            query="concise architecture voice",
        )
    )

    assert [item.canonical_id for item in packet.items] == ["identity-voice"]
    assert packet.excluded_by_policy == 1


def test_career_public_requires_explicit_public_ready_metadata(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    _note(
        vault,
        "Experience/Public.md",
        """---
id: experience-public
type: experience
status: active
confidence: 1.0
last_verified: 2026-08-01
sensitivity: public
public_ready: true
---

# Public experience

Verified Python automation experience.
""",
    )
    _note(
        vault,
        "Experience/Private.md",
        """---
id: experience-private
type: experience
status: active
confidence: 1.0
last_verified: 2026-08-01
---

# Private experience

Private Python automation context.
""",
    )
    grants = SQLiteRecallGrantStore(StateStore(tmp_path / "state.sqlite"))
    grants.issue(
        RecallGrant(
            caller_id="career-exporter",
            purposes=("career-public",),
            project_ids=(),
            allow_private_global=False,
        )
    )
    gateway = RecallGateway(
        harness=RecallHarness(
            lexical=CanonicalLexicalBackend(vault),
            vector=EmptySearchBackend(),
        ),
        metadata=CanonicalNoteMetadataCatalog(vault),
        grants=grants,
    )

    packet = gateway.retrieve(
        GatewayRecallRequest(
            caller_id="career-exporter",
            purpose="career-public",
            query="Python automation",
        )
    )

    assert [item.canonical_id for item in packet.items] == ["experience-public"]
    assert packet.items[0].sensitivity == "public"
    assert packet.excluded_by_policy == 1


def test_project_handoff_is_isolated_to_one_explicitly_granted_project(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    for project_id in ("project-one", "project-two"):
        _note(
            vault,
            f"Projects/{project_id}.md",
            f"""---
id: {project_id}
type: project
status: active
confidence: 0.9
last_verified: 2026-08-01
---

# {project_id}

Durable handoff architecture and recovery notes for {project_id}.
""",
        )
    grants = SQLiteRecallGrantStore(StateStore(tmp_path / "state.sqlite"))
    grants.issue(
        RecallGrant(
            caller_id="handoff-agent",
            purposes=("project-handoff",),
            project_ids=("project-one", "project-two"),
            allow_private_global=False,
        )
    )
    gateway = RecallGateway(
        harness=RecallHarness(
            lexical=CanonicalLexicalBackend(vault),
            vector=EmptySearchBackend(),
        ),
        metadata=CanonicalNoteMetadataCatalog(vault),
        grants=grants,
    )

    packet = gateway.retrieve(
        GatewayRecallRequest(
            caller_id="handoff-agent",
            purpose="project-handoff",
            query="handoff architecture recovery",
            project_ids=("project-one",),
        )
    )

    assert [item.project_id for item in packet.items] == ["project-one"]
    assert packet.excluded_by_policy == 1


def test_aggregate_memory_requires_current_segment_and_observation_binding(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    relative = "Memory/LongTermMemory.md"
    markdown = """---
id: memory-long-term
type: memory
status: active
confidence: 0.9
last_verified: 2026-08-01
---

# Project facts

- Project one uses an atomic publication boundary.
- Project two uses an independent recovery boundary.
"""
    _note(vault, relative, markdown)
    store = StateStore(tmp_path / "state.sqlite")
    manifest = SQLiteSegmentManifest(store)
    ChangedSegmentIndexer(
        manifest=manifest,
        backend=SegmentBackend(),
    ).index(relative, markdown)
    segments = manifest.records(relative)
    assert len(segments) == 2
    metadata = SQLiteRecallSegmentMetadataCatalog(store)
    candidates: list[RecallCandidate] = []
    for index, (segment, project_id) in enumerate(
        zip(segments, ("project-one", "project-two"), strict=True)
    ):
        observation_id = f"obs-project-{index + 1}"
        store.add_observation(
            {
                "id": observation_id,
                "kind": "project_fact",
                "subject": project_id,
                "claim": segment.text,
                "evidence_refs": (),
                "confidence": 0.9,
                "source_count": 1,
                "project_count": 1,
                "project_ids": [project_id],
                "sensitivity": "normal",
                "promotion_tier": "review",
                "status": "approved",
            }
        )
        metadata.register(
            RecallSegmentBinding(
                segment_id=segment.segment_id,
                observation_id=observation_id,
                source_id=segment.source_id,
                note_path=relative,
                text_hash=segment.text_hash,
                project_id=project_id,
            )
        )
        candidates.append(
            RecallCandidate(
                source_id=segment.source_id,
                note_path=relative,
                title="Long-term memory",
                snippet=segment.text,
                score=2.0 - index,
                canonical=True,
                relations=(),
                segment_id=segment.segment_id,
                content_hash=segment.text_hash,
            )
        )
    grants = SQLiteRecallGrantStore(store)
    grants.issue(
        RecallGrant(
            caller_id="aggregate-agent",
            purposes=("general-private",),
            project_ids=("project-one", "project-two"),
            allow_private_global=False,
        )
    )
    gateway = RecallGateway(
        harness=RecallHarness(
            lexical=StaticSearchBackend(tuple(candidates)),
            vector=EmptySearchBackend(),
        ),
        metadata=CanonicalNoteMetadataCatalog(vault, segments=metadata),
        grants=grants,
    )

    packet = gateway.retrieve(
        GatewayRecallRequest(
            caller_id="aggregate-agent",
            purpose="general-private",
            query="project publication recovery boundary",
            project_ids=("project-one",),
        )
    )

    assert [item.project_id for item in packet.items] == ["project-one"]
    assert packet.items[0].observation_id == "obs-project-1"
    assert packet.items[0].canonical_id == "obs-project-1"
    assert packet.items[0].segment_id == segments[0].segment_id
    assert packet.items[0].content_hash == segments[0].text_hash
    assert packet.excluded_by_policy == 1


def test_rejected_observation_and_stale_aggregate_segment_are_not_recalled(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    relative = "Memory/LongTermMemory.md"
    markdown = "# Durable fact\n\nProject one uses the original boundary.\n"
    _note(vault, relative, markdown)
    store = StateStore(tmp_path / "state.sqlite")
    manifest = SQLiteSegmentManifest(store)
    indexer = ChangedSegmentIndexer(manifest=manifest, backend=SegmentBackend())
    indexer.index(relative, markdown)
    segment = manifest.records(relative)[0]
    observation_id = store.add_observation(
        {
            "id": "obs-stale-fact",
            "kind": "project_fact",
            "subject": "project-one",
            "claim": segment.text,
            "evidence_refs": (),
            "confidence": 0.9,
            "source_count": 1,
            "project_count": 1,
            "project_ids": ["project-one"],
            "sensitivity": "normal",
            "promotion_tier": "review",
            "status": "approved",
        }
    )
    metadata = SQLiteRecallSegmentMetadataCatalog(store)
    metadata.register(
        RecallSegmentBinding(
            segment_id=segment.segment_id,
            observation_id=observation_id,
            source_id=segment.source_id,
            note_path=relative,
            text_hash=segment.text_hash,
            project_id="project-one",
        )
    )
    candidate = RecallCandidate(
        source_id=segment.source_id,
        note_path=relative,
        title="Long-term memory",
        snippet=segment.text,
        score=1.0,
        canonical=True,
        relations=(),
        segment_id=segment.segment_id,
        content_hash=segment.text_hash,
    )
    grants = SQLiteRecallGrantStore(store)
    grants.issue(
        RecallGrant(
            caller_id="stale-agent",
            purposes=("general-private",),
            project_ids=("project-one",),
            allow_private_global=False,
        )
    )
    gateway = RecallGateway(
        harness=RecallHarness(
            lexical=StaticSearchBackend((candidate,)),
            vector=EmptySearchBackend(),
        ),
        metadata=CanonicalNoteMetadataCatalog(vault, segments=metadata),
        grants=grants,
    )
    request = GatewayRecallRequest(
        caller_id="stale-agent",
        purpose="general-private",
        query="original project boundary",
        project_ids=("project-one",),
    )
    assert len(gateway.retrieve(request).items) == 1

    store.decide_observation(observation_id, "rejected", "Owner retracted it")
    assert gateway.retrieve(request).items == ()

    store.restore_observations_pending([observation_id])
    store.decide_observation(observation_id, "approved")
    changed = markdown.replace("original boundary", "replacement boundary")
    _note(vault, relative, changed)
    indexer.index(relative, changed)

    assert gateway.retrieve(request).items == ()


def test_unbound_aggregate_note_cannot_fall_back_to_note_level_metadata(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    _note(
        vault,
        "Memory/LongTermMemory.md",
        """---
id: memory-long-term
type: memory
status: active
confidence: 1.0
last_verified: 2026-08-01
---

# Memory

Project one private deployment details.
""",
    )
    grants = SQLiteRecallGrantStore(StateStore(tmp_path / "state.sqlite"))
    grants.issue(
        RecallGrant(
            caller_id="unbound-agent",
            purposes=("general-private",),
            project_ids=("project-one",),
            allow_private_global=True,
        )
    )

    packet = RecallGateway(
        harness=RecallHarness(
            lexical=CanonicalLexicalBackend(vault),
            vector=EmptySearchBackend(),
        ),
        metadata=CanonicalNoteMetadataCatalog(vault),
        grants=grants,
    ).retrieve(
        GatewayRecallRequest(
            caller_id="unbound-agent",
            purpose="general-private",
            query="private deployment details",
            project_ids=("project-one",),
        )
    )

    assert packet.items == ()
    assert packet.excluded_by_policy == 1


def test_stale_unsegmented_excerpt_must_still_exist_in_current_canon(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    relative = "Identity/Preferences.md"
    _note(
        vault,
        relative,
        """---
id: identity-preferences
type: identity
status: active
confidence: 1.0
last_verified: 2026-08-01
---

# Preferences

The current preference replaced the old value.
""",
    )
    store = StateStore(tmp_path / "state.sqlite")
    grants = SQLiteRecallGrantStore(store)
    grants.issue(
        RecallGrant(
            caller_id="stale-note-agent",
            purposes=("general-private",),
            project_ids=(),
            allow_private_global=True,
        )
    )
    stale = RecallCandidate(
        source_id=stable_source_id(relative),
        note_path=relative,
        title="Preferences",
        snippet="The removed preference must not return.",
        score=1.0,
        canonical=True,
        relations=(),
    )

    packet = RecallGateway(
        harness=RecallHarness(
            lexical=StaticSearchBackend((stale,)),
            vector=EmptySearchBackend(),
        ),
        metadata=CanonicalNoteMetadataCatalog(vault),
        grants=grants,
    ).retrieve(
        GatewayRecallRequest(
            caller_id="stale-note-agent",
            purpose="general-private",
            query="removed preference",
        )
    )

    assert packet.items == ()
    assert packet.excluded_by_policy == 1


def test_segment_binding_rejects_unrelated_approved_observation(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    relative = "Memory/LongTermMemory.md"
    markdown = "# Memory\n\nThe segment contains one governed claim.\n"
    _note(vault, relative, markdown)
    store = StateStore(tmp_path / "state.sqlite")
    manifest = SQLiteSegmentManifest(store)
    ChangedSegmentIndexer(
        manifest=manifest,
        backend=SegmentBackend(),
    ).index(relative, markdown)
    segment = manifest.records(relative)[0]
    store.add_observation(
        {
            "id": "obs-unrelated-claim",
            "kind": "preference",
            "subject": "unrelated",
            "claim": "A different approved claim.",
            "evidence_refs": (),
            "confidence": 1.0,
            "source_count": 1,
            "project_count": 0,
            "project_ids": [],
            "sensitivity": "public",
            "promotion_tier": "review",
            "status": "promoted",
        }
    )

    with pytest.raises(RuntimeError, match="claim"):
        SQLiteRecallSegmentMetadataCatalog(store).register(
            RecallSegmentBinding(
                segment_id=segment.segment_id,
                observation_id="obs-unrelated-claim",
                source_id=segment.source_id,
                note_path=relative,
                text_hash=segment.text_hash,
                project_id=None,
            )
        )


def test_malformed_segment_bound_aggregate_note_fails_closed(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    relative = "Memory/LongTermMemory.md"
    _note(
        vault,
        relative,
        """---
id: memory-long-term
type: memory

# Memory

Private project detail.
""",
    )
    store = StateStore(tmp_path / "state.sqlite")
    grants = SQLiteRecallGrantStore(store)
    grants.issue(
        RecallGrant(
            caller_id="malformed-agent",
            purposes=("general-private",),
            project_ids=("project-one",),
            allow_private_global=False,
        )
    )
    candidate = RecallCandidate(
        source_id="note-4561636d081526c53006f5d1",
        note_path=relative,
        title="Long-term memory",
        snippet="Private project detail.",
        score=1.0,
        canonical=True,
        relations=(),
        segment_id="segment-aaaaaaaaaaaaaaaaaaaaaaaa",
        content_hash="b" * 64,
    )

    packet = RecallGateway(
        harness=RecallHarness(
            lexical=StaticSearchBackend((candidate,)),
            vector=EmptySearchBackend(),
        ),
        metadata=CanonicalNoteMetadataCatalog(
            vault,
            segments=EmptySegmentMetadata(),
        ),
        grants=grants,
    ).retrieve(
        GatewayRecallRequest(
            caller_id="malformed-agent",
            purpose="general-private",
            query="private project detail",
            project_ids=("project-one",),
        )
    )

    assert packet.items == ()
    assert packet.excluded_by_policy == 1


def test_recent_projects_requires_explicit_project_scope(tmp_path: Path) -> None:
    grants = SQLiteRecallGrantStore(StateStore(tmp_path / "state.sqlite"))
    grants.issue(
        RecallGrant(
            caller_id="recent-agent",
            purposes=("recent-projects",),
            project_ids=("project-one",),
            allow_private_global=False,
        )
    )
    gateway = RecallGateway(
        harness=RecallHarness(
            lexical=EmptySearchBackend(),
            vector=EmptySearchBackend(),
        ),
        metadata=CanonicalNoteMetadataCatalog(tmp_path / "vault"),
        grants=grants,
    )

    with pytest.raises(ValueError, match="explicit project"):
        gateway.retrieve(
            GatewayRecallRequest(
                caller_id="recent-agent",
                purpose="recent-projects",
                query="recent project progress",
            )
        )


def test_resume_profile_excludes_general_memory_layer(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    for relative, note_id, note_type, title in (
        (
            "Identity/Capabilities.md",
            "identity-capabilities",
            "identity",
            "Capabilities",
        ),
        ("Memory/Decisions.md", "memory-decisions", "memory", "Decisions"),
    ):
        _note(
            vault,
            relative,
            f"""---
id: {note_id}
type: {note_type}
status: active
confidence: 0.9
last_verified: 2026-08-01
---

# {title}

Verified architecture and evidence practice.
""",
        )
    grants = SQLiteRecallGrantStore(StateStore(tmp_path / "state.sqlite"))
    grants.issue(
        RecallGrant(
            caller_id="resume-agent",
            purposes=("resume",),
            project_ids=(),
            allow_private_global=True,
        )
    )
    gateway = RecallGateway(
        harness=RecallHarness(
            lexical=CanonicalLexicalBackend(vault),
            vector=EmptySearchBackend(),
        ),
        metadata=CanonicalNoteMetadataCatalog(vault),
        grants=grants,
    )

    packet = gateway.retrieve(
        GatewayRecallRequest(
            caller_id="resume-agent",
            purpose="resume",
            query="verified architecture evidence",
        )
    )

    assert [item.canonical_id for item in packet.items] == [
        "identity-capabilities"
    ]
    assert packet.excluded_by_policy == 1


def test_gateway_applies_per_layer_diversity_before_result_limit(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    for index in range(4):
        _note(
            vault,
            f"Identity/Identity{index}.md",
            f"""---
id: identity-note-{index}
type: identity
status: active
confidence: 0.9
last_verified: 2026-08-01
---

# Identity {index}

Architecture evidence protocol. Identity detail {index}.
""",
        )
    _note(
        vault,
        "Skills/Architecture.md",
        """---
id: skills-architecture
type: skill
status: active
confidence: 0.9
last_verified: 2026-08-01
---

# Architecture

Architecture decisions preserve protocol links and evidence.
""",
    )
    grants = SQLiteRecallGrantStore(StateStore(tmp_path / "state.sqlite"))
    grants.issue(
        RecallGrant(
            caller_id="diversity-agent",
            purposes=("general-private",),
            project_ids=(),
            allow_private_global=True,
        )
    )
    packet = RecallGateway(
        harness=RecallHarness(
            lexical=CanonicalLexicalBackend(vault),
            vector=EmptySearchBackend(),
        ),
        metadata=CanonicalNoteMetadataCatalog(vault),
        grants=grants,
    ).retrieve(
        GatewayRecallRequest(
            caller_id="diversity-agent",
            purpose="general-private",
            query="architecture evidence protocol",
            max_results=3,
        )
    )

    assert [item.layer for item in packet.items].count("identity") == 2
    assert [item.layer for item in packet.items].count("capability") == 1


def test_gateway_truncates_high_ranked_excerpt_to_complete_packet_budget(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    _note(
        vault,
        "Identity/Long.md",
        """---
id: memory-long
type: memory
status: active
confidence: 0.9
last_verified: 2026-08-01
---

# Long memory

architecture evidence protocol """ + ("important context " * 200),
    )
    grants = SQLiteRecallGrantStore(StateStore(tmp_path / "state.sqlite"))
    grants.issue(
        RecallGrant(
            caller_id="budget-agent",
            purposes=("general-private",),
            project_ids=(),
            allow_private_global=True,
        )
    )

    packet = RecallGateway(
        harness=RecallHarness(
            lexical=CanonicalLexicalBackend(vault),
            vector=EmptySearchBackend(),
        ),
        metadata=CanonicalNoteMetadataCatalog(vault),
        grants=grants,
    ).retrieve(
        GatewayRecallRequest(
            caller_id="budget-agent",
            purpose="general-private",
            query="architecture evidence protocol",
            max_packet_chars=700,
            max_packet_tokens=175,
        )
    )

    assert len(packet.items) == 1
    assert packet.used_chars <= 700
    assert len(packet.items[0].excerpt) < 1200
    assert packet.truncated is True


def test_private_profile_excludes_unverified_canonical_note(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _note(
        vault,
        "Goals/Unverified.md",
        """---
id: memory-unverified
type: memory
confidence: 0
---

# Unverified

Unverified architecture claim.
""",
    )
    grants = SQLiteRecallGrantStore(StateStore(tmp_path / "state.sqlite"))
    grants.issue(
        RecallGrant(
            caller_id="verified-only-agent",
            purposes=("general-private",),
            project_ids=(),
            allow_private_global=True,
        )
    )

    packet = RecallGateway(
        harness=RecallHarness(
            lexical=CanonicalLexicalBackend(vault),
            vector=EmptySearchBackend(),
        ),
        metadata=CanonicalNoteMetadataCatalog(vault),
        grants=grants,
    ).retrieve(
        GatewayRecallRequest(
            caller_id="verified-only-agent",
            purpose="general-private",
            query="unverified architecture claim",
        )
    )

    assert packet.items == ()
    assert packet.excluded_by_policy == 1


def test_revoked_caller_cannot_recall_context(tmp_path: Path) -> None:
    grants = SQLiteRecallGrantStore(StateStore(tmp_path / "state.sqlite"))
    grant = RecallGrant(
        caller_id="revoked-agent",
        purposes=("general-private",),
        project_ids=(),
        allow_private_global=True,
    )
    grants.issue(grant)
    grants.revoke(grant.caller_id)
    gateway = RecallGateway(
        harness=RecallHarness(
            lexical=EmptySearchBackend(),
            vector=EmptySearchBackend(),
        ),
        metadata=CanonicalNoteMetadataCatalog(tmp_path / "vault"),
        grants=SQLiteRecallGrantStore(StateStore(tmp_path / "state.sqlite")),
    )

    with pytest.raises(PermissionError, match="not opted in"):
        gateway.retrieve(
            GatewayRecallRequest(
                caller_id="revoked-agent",
                purpose="general-private",
                query="safe query",
            )
        )


def test_gateway_records_privacy_safe_retrieval_use_receipt(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _note(
        vault,
        "Identity/Preferences.md",
        """---
id: identity-preferences
type: identity
status: active
confidence: 0.9
last_verified: 2026-08-01
---

# Preferences

The owner prefers concise architecture receipts.
""",
    )
    store = StateStore(tmp_path / "state.sqlite")
    grants = SQLiteRecallGrantStore(store)
    grants.issue(
        RecallGrant(
            caller_id="telemetry-agent",
            purposes=("general-private",),
            project_ids=(),
            allow_private_global=True,
        )
    )
    telemetry = SQLiteRecallTelemetryStore(store)
    query = "concise architecture receipts"
    packet = RecallGateway(
        harness=RecallHarness(
            lexical=CanonicalLexicalBackend(vault),
            vector=EmptySearchBackend(),
        ),
        metadata=CanonicalNoteMetadataCatalog(vault),
        grants=grants,
        telemetry=telemetry,
    ).retrieve(
        GatewayRecallRequest(
            caller_id="telemetry-agent",
            purpose="general-private",
            query=query,
        )
    )

    receipts = telemetry.receipts()
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt.caller_id == "telemetry-agent"
    assert receipt.purpose == "general-private"
    assert receipt.query_hash == packet.query_hash
    assert receipt.returned_ids == ("identity-preferences",)
    assert receipt.result_count == 1
    assert receipt.packet_chars == packet.used_chars
    assert receipt.packet_tokens == packet.estimated_tokens
    assert receipt.latency_ms >= 0
    assert receipt.outcome is None
    assert query not in repr(receipt)
    assert packet.items[0].excerpt not in repr(receipt)

    telemetry.record_outcome(receipt.receipt_id, "used")
    assert telemetry.receipts()[0].outcome == "used"


def test_recall_grant_rejects_unsafe_project_identity() -> None:
    with pytest.raises(ValueError, match="project IDs"):
        RecallGrant(
            caller_id="unsafe-grant-agent",
            purposes=("project-handoff",),
            project_ids=("../private",),
            allow_private_global=False,
        )
