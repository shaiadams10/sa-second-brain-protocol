from __future__ import annotations

import json
import re
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Protocol

import yaml

from .canonical_paths import is_indexable_markdown
from .recall_backends import stable_source_id
from .recall_harness import RecallHarness, RecallHit, RecallRequest
from .security import assert_model_packet_safe, scan_untrusted_memory_text
from .segment_index import segment_markdown
from .state import StateStore, utc_now


GRANT_SCHEMA = """
CREATE TABLE IF NOT EXISTS recall_gateway_grants (
  caller_id TEXT PRIMARY KEY,
  purposes_json TEXT NOT NULL,
  project_ids_json TEXT NOT NULL,
  allow_private_global INTEGER NOT NULL,
  enabled INTEGER NOT NULL,
  updated_at TEXT NOT NULL
);
"""
SEGMENT_METADATA_SCHEMA = """
CREATE TABLE IF NOT EXISTS recall_segment_bindings (
  segment_id TEXT PRIMARY KEY,
  observation_id TEXT NOT NULL REFERENCES observations(id),
  source_id TEXT NOT NULL,
  note_path TEXT NOT NULL,
  text_hash TEXT NOT NULL,
  canonical_id TEXT NOT NULL,
  layer TEXT NOT NULL,
  project_id TEXT,
  last_verified TEXT,
  public_ready INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
"""
TELEMETRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS recall_use_receipts (
  receipt_id TEXT PRIMARY KEY,
  caller_id TEXT NOT NULL,
  purpose TEXT NOT NULL,
  query_hash TEXT NOT NULL,
  rankings_json TEXT NOT NULL,
  result_count INTEGER NOT NULL,
  excluded_by_policy INTEGER NOT NULL,
  packet_chars INTEGER NOT NULL,
  packet_tokens INTEGER NOT NULL,
  latency_ms INTEGER NOT NULL,
  channels_json TEXT NOT NULL,
  outcome TEXT,
  created_at TEXT NOT NULL,
  outcome_at TEXT
);
"""
CALLER_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{2,127}$")
CANONICAL_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,127}$")
SEGMENT_ID = re.compile(r"^segment-[a-f0-9]{24}$")
SOURCE_ID = re.compile(r"^note-[a-f0-9]{24}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
LAYER_BY_ROOT = {
    "Career": "career",
    "Experience": "experience",
    "Goals": "goals",
    "Identity": "identity",
    "Journal": "activity",
    "Memory": "memory",
    "Projects": "project",
    "Skills": "capability",
}
GENERAL_PRIVATE_LAYERS = frozenset(
    {"career", "experience", "goals", "identity", "memory", "project", "capability"}
)
PURPOSE_LAYERS = {
    "career-public": frozenset(
        {"career", "experience", "identity", "project", "capability"}
    ),
    "general-private": GENERAL_PRIVATE_LAYERS,
    "project-handoff": frozenset({"project"}),
    "recent-projects": frozenset({"project"}),
    "resume": frozenset(
        {"career", "experience", "identity", "project", "capability"}
    ),
    "write-as-me": frozenset({"identity", "memory"}),
}
PUBLIC_ONLY_PURPOSES = frozenset({"career-public"})
ALLOWED_CONFIRMATION_BY_PURPOSE = {
    purpose: frozenset({"confirmed", "developing"})
    for purpose in PURPOSE_LAYERS
}
ALLOWED_CONFIRMATION_BY_PURPOSE["career-public"] = frozenset({"confirmed"})
SEGMENT_REQUIRED_PATHS = frozenset(
    {
        "Memory/Decisions.md",
        "Memory/Lessons.md",
        "Memory/LongTermMemory.md",
        "Projects/Index.md",
    }
)


@dataclass(frozen=True)
class RecallGrant:
    caller_id: str
    purposes: tuple[str, ...]
    project_ids: tuple[str, ...]
    allow_private_global: bool

    def __post_init__(self) -> None:
        if not CALLER_ID.fullmatch(self.caller_id):
            raise ValueError("Recall grants require a stable caller ID")
        if (
            not self.purposes
            or self.purposes != tuple(sorted(set(self.purposes)))
            or self.project_ids != tuple(sorted(set(self.project_ids)))
        ):
            raise ValueError("Recall grant scopes must be unique and sorted")
        if set(self.purposes) - set(PURPOSE_LAYERS):
            raise ValueError("Recall grant contains an unknown purpose profile")
        if any(not CANONICAL_ID.fullmatch(value) for value in self.project_ids):
            raise ValueError("Recall grant project IDs must be canonical")
        if not isinstance(self.allow_private_global, bool):
            raise ValueError("Recall global-private permission must be boolean")


@dataclass(frozen=True)
class GatewayRecallRequest:
    caller_id: str
    purpose: str
    query: str
    project_ids: tuple[str, ...] = ()
    max_results: int | None = None
    max_packet_chars: int | None = None
    max_packet_tokens: int | None = None

    def __post_init__(self) -> None:
        if not CALLER_ID.fullmatch(self.caller_id) or not self.purpose.strip():
            raise ValueError("Recall requests require caller and purpose identity")
        if not self.query.strip() or len(self.query) > 500:
            raise ValueError("Recall query must contain at most 500 characters")
        if self.project_ids != tuple(sorted(set(self.project_ids))):
            raise ValueError("Recall project filters must be unique and sorted")
        if any(not CANONICAL_ID.fullmatch(value) for value in self.project_ids):
            raise ValueError("Recall project filters must use canonical IDs")
        if any(
            value is not None
            and (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value <= 0
            )
            for value in (
                self.max_results,
                self.max_packet_chars,
                self.max_packet_tokens,
            )
        ):
            raise ValueError("Recall request bounds must be positive")


@dataclass(frozen=True)
class CanonicalNoteMetadata:
    source_id: str
    canonical_id: str
    note_path: str
    layer: str
    project_id: str | None
    confirmation_state: str
    confidence: float
    last_verified: str | None
    sensitivity: str
    public_ready: bool
    observation_id: str | None = None
    segment_id: str | None = None
    content_hash: str | None = None


@dataclass(frozen=True)
class RecallSegmentBinding:
    segment_id: str
    observation_id: str
    source_id: str
    note_path: str
    text_hash: str
    project_id: str | None

    def __post_init__(self) -> None:
        if (
            not SEGMENT_ID.fullmatch(self.segment_id)
            or not CANONICAL_ID.fullmatch(self.observation_id)
            or not SOURCE_ID.fullmatch(self.source_id)
            or not SHA256.fullmatch(self.text_hash)
            or not is_indexable_markdown(self.note_path)
            or stable_source_id(self.note_path) != self.source_id
        ):
            raise ValueError("Recall segment binding identity is invalid")
        if self.project_id is not None and not CANONICAL_ID.fullmatch(
            self.project_id
        ):
            raise ValueError("Recall segment binding project is invalid")
@dataclass(frozen=True)
class GatewayRecallItem:
    source_id: str
    canonical_id: str
    layer: str
    project_id: str | None
    confirmation_state: str
    confidence: float
    last_verified: str | None
    sensitivity: str
    excerpt: str
    score: float
    canonical_link: str
    retrieval_channels: tuple[str, ...]
    observation_id: str | None = None
    segment_id: str | None = None
    content_hash: str | None = None


@dataclass(frozen=True)
class GatewayRecallPacket:
    purpose: str
    query_hash: str
    items: tuple[GatewayRecallItem, ...]
    excluded_by_policy: int
    used_chars: int
    estimated_tokens: int
    truncated: bool
    executed_channels: tuple[str, ...]


@dataclass(frozen=True)
class RecallUseReceipt:
    receipt_id: str
    caller_id: str
    purpose: str
    query_hash: str
    rankings: tuple[tuple[str, str | None, float], ...]
    result_count: int
    excluded_by_policy: int
    packet_chars: int
    packet_tokens: int
    latency_ms: int
    executed_channels: tuple[str, ...]
    outcome: str | None
    created_at: str

    @property
    def returned_ids(self) -> tuple[str, ...]:
        return tuple(item[0] for item in self.rankings)


class RecallGrantReader(Protocol):
    def resolve(self, caller_id: str) -> RecallGrant | None: ...


class NoteMetadataReader(Protocol):
    def resolve(self, hit: RecallHit) -> CanonicalNoteMetadata | None: ...


class SegmentMetadataReader(Protocol):
    def resolve(self, hit: RecallHit) -> CanonicalNoteMetadata | None: ...


class RecallTelemetryWriter(Protocol):
    def record(
        self,
        request: GatewayRecallRequest,
        packet: GatewayRecallPacket,
        *,
        latency_ms: int,
    ) -> RecallUseReceipt: ...


class SQLiteRecallGrantStore:
    """Durable opt-in registry; administration is not exposed by the gateway."""

    def __init__(self, store: StateStore) -> None:
        self._store = store
        with store.connect() as connection:
            connection.executescript(GRANT_SCHEMA)

    def issue(self, grant: RecallGrant) -> None:
        purposes = _encode(grant.purposes)
        projects = _encode(grant.project_ids)
        with self._store.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM recall_gateway_grants WHERE caller_id=?",
                (grant.caller_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["purposes_json"],
                    existing["project_ids_json"],
                    int(existing["allow_private_global"]),
                    int(existing["enabled"]),
                ) != (purposes, projects, int(grant.allow_private_global), 1):
                    raise RuntimeError("Recall caller grant is already issued")
                return
            connection.execute(
                "INSERT INTO recall_gateway_grants VALUES(?,?,?,?,1,?)",
                (
                    grant.caller_id,
                    purposes,
                    projects,
                    int(grant.allow_private_global),
                    utc_now(),
                ),
            )

    def resolve(self, caller_id: str) -> RecallGrant | None:
        with self._store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM recall_gateway_grants WHERE caller_id=? AND enabled=1",
                (caller_id,),
            ).fetchone()
        if row is None:
            return None
        return RecallGrant(
            caller_id=str(row["caller_id"]),
            purposes=tuple(json.loads(row["purposes_json"])),
            project_ids=tuple(json.loads(row["project_ids_json"])),
            allow_private_global=bool(row["allow_private_global"]),
        )

    def revoke(self, caller_id: str) -> None:
        with self._store.transaction() as connection:
            row = connection.execute(
                "SELECT enabled FROM recall_gateway_grants WHERE caller_id=?",
                (caller_id,),
            ).fetchone()
            if row is None:
                raise KeyError(caller_id)
            if not row["enabled"]:
                return
            connection.execute(
                """UPDATE recall_gateway_grants SET enabled=0,updated_at=?
                WHERE caller_id=?""",
                (utc_now(), caller_id),
            )


class SQLiteRecallTelemetryStore:
    """Store rank-evaluation receipts without query text or retrieved excerpts."""

    OUTCOMES = frozenset(
        {"cited", "ignored", "owner-confirmed", "owner-retracted", "used"}
    )

    def __init__(self, store: StateStore) -> None:
        self._store = store
        with store.connect() as connection:
            connection.executescript(TELEMETRY_SCHEMA)

    def record(
        self,
        request: GatewayRecallRequest,
        packet: GatewayRecallPacket,
        *,
        latency_ms: int,
    ) -> RecallUseReceipt:
        if latency_ms < 0 or packet.purpose != request.purpose:
            raise ValueError("Recall telemetry receipt binding is invalid")
        receipt_id = "recall-use-" + uuid.uuid4().hex
        created_at = utc_now()
        rankings = tuple(
            (
                item.canonical_id,
                item.segment_id,
                float(item.score),
            )
            for item in packet.items
        )
        with self._store.transaction() as connection:
            connection.execute(
                """INSERT INTO recall_use_receipts(
                receipt_id,caller_id,purpose,query_hash,rankings_json,result_count,
                excluded_by_policy,packet_chars,packet_tokens,latency_ms,
                channels_json,outcome,created_at,outcome_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL,?,NULL)""",
                (
                    receipt_id,
                    request.caller_id,
                    request.purpose,
                    packet.query_hash,
                    _encode(rankings),
                    len(packet.items),
                    packet.excluded_by_policy,
                    packet.used_chars,
                    packet.estimated_tokens,
                    latency_ms,
                    _encode(packet.executed_channels),
                    created_at,
                ),
            )
        return RecallUseReceipt(
            receipt_id=receipt_id,
            caller_id=request.caller_id,
            purpose=request.purpose,
            query_hash=packet.query_hash,
            rankings=rankings,
            result_count=len(packet.items),
            excluded_by_policy=packet.excluded_by_policy,
            packet_chars=packet.used_chars,
            packet_tokens=packet.estimated_tokens,
            latency_ms=latency_ms,
            executed_channels=packet.executed_channels,
            outcome=None,
            created_at=created_at,
        )

    def record_outcome(self, receipt_id: str, outcome: str) -> None:
        if outcome not in self.OUTCOMES:
            raise ValueError("Recall telemetry outcome is invalid")
        with self._store.transaction() as connection:
            row = connection.execute(
                "SELECT outcome FROM recall_use_receipts WHERE receipt_id=?",
                (receipt_id,),
            ).fetchone()
            if row is None:
                raise KeyError(receipt_id)
            if row["outcome"] is not None and row["outcome"] != outcome:
                raise RuntimeError("Recall telemetry outcome is already recorded")
            if row["outcome"] is None:
                connection.execute(
                    """UPDATE recall_use_receipts SET outcome=?,outcome_at=?
                    WHERE receipt_id=?""",
                    (outcome, utc_now(), receipt_id),
                )

    def receipts(self) -> tuple[RecallUseReceipt, ...]:
        with self._store.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM recall_use_receipts ORDER BY created_at,receipt_id"
            ).fetchall()
        return tuple(_telemetry_receipt_from_row(row) for row in rows)

    def delete_before(self, timestamp: str) -> int:
        if not timestamp.strip():
            raise ValueError("Recall telemetry retention cutoff is required")
        with self._store.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM recall_use_receipts WHERE created_at < ?",
                (timestamp,),
            )
        return int(cursor.rowcount)


class SQLiteRecallSegmentMetadataCatalog:
    """Bind retrievable segments to current, review-governed observations."""

    def __init__(self, store: StateStore) -> None:
        self._store = store
        with store.connect() as connection:
            connection.executescript(SEGMENT_METADATA_SCHEMA)

    def register(self, binding: RecallSegmentBinding) -> None:
        with self._store.transaction() as connection:
            segment = connection.execute(
                """SELECT segment_id,source_id,path,text_hash,text
                FROM canonical_segments WHERE segment_id=?""",
                (binding.segment_id,),
            ).fetchone()
            if segment is None or (
                segment["source_id"],
                segment["path"],
                segment["text_hash"],
            ) != (
                binding.source_id,
                binding.note_path,
                binding.text_hash,
            ):
                raise RuntimeError("Recall segment binding is stale")
            observation = connection.execute(
                "SELECT * FROM observations WHERE id=?",
                (binding.observation_id,),
            ).fetchone()
            if observation is None or observation["status"] not in {
                "approved",
                "promoted",
            }:
                raise RuntimeError("Recall segment observation is not current")
            valid_project, project_id = _current_observation_project(observation)
            if not valid_project or project_id != binding.project_id:
                raise RuntimeError("Recall segment project binding is stale")
            if _normalized_claim(observation["claim"]) != _normalized_claim(
                segment["text"]
            ):
                raise RuntimeError("Recall segment observation claim is stale")
            layer = LAYER_BY_ROOT.get(Path(binding.note_path).parts[0])
            if layer is None:
                raise RuntimeError("Recall segment layer is not canonical")
            sensitivity = str(observation["sensitivity"]).casefold()
            public_ready = bool(
                observation["status"] == "promoted" and sensitivity == "public"
            )
            values = (
                binding.segment_id,
                binding.observation_id,
                binding.source_id,
                binding.note_path,
                binding.text_hash,
                binding.observation_id,
                layer,
                binding.project_id,
                str(observation["updated_at"]),
                int(public_ready),
            )
            existing = connection.execute(
                "SELECT * FROM recall_segment_bindings WHERE segment_id=?",
                (binding.segment_id,),
            ).fetchone()
            if existing is not None:
                if tuple(existing[key] for key in (
                    "segment_id",
                    "observation_id",
                    "source_id",
                    "note_path",
                    "text_hash",
                    "project_id",
                )) != (
                    binding.segment_id,
                    binding.observation_id,
                    binding.source_id,
                    binding.note_path,
                    binding.text_hash,
                    binding.project_id,
                ):
                    raise RuntimeError("Recall segment binding identity changed")
                connection.execute(
                    """UPDATE recall_segment_bindings SET
                    canonical_id=?,layer=?,last_verified=?,public_ready=?
                    WHERE segment_id=?""",
                    (
                        binding.observation_id,
                        layer,
                        str(observation["updated_at"]),
                        int(public_ready),
                        binding.segment_id,
                    ),
                )
                return
            connection.execute(
                """INSERT INTO recall_segment_bindings(
                segment_id,observation_id,source_id,note_path,text_hash,
                canonical_id,layer,project_id,last_verified,public_ready,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (*values, utc_now()),
            )

    def resolve(self, hit: RecallHit) -> CanonicalNoteMetadata | None:
        if hit.segment_id is None or hit.content_hash is None:
            return None
        with self._store.connect() as connection:
            row = connection.execute(
                """SELECT b.*,s.text,o.status AS observation_status,
                o.claim AS observation_claim,o.updated_at AS observation_updated_at,
                o.confidence AS observation_confidence,
                o.sensitivity AS observation_sensitivity,o.project_count,
                o.payload_json
                FROM recall_segment_bindings b
                JOIN canonical_segments s ON
                  s.segment_id=b.segment_id AND s.source_id=b.source_id
                  AND s.path=b.note_path AND s.text_hash=b.text_hash
                JOIN observations o ON o.id=b.observation_id
                WHERE b.segment_id=?""",
                (hit.segment_id,),
            ).fetchone()
        if row is None or (
            row["source_id"],
            row["note_path"],
            row["text_hash"],
        ) != (hit.source_id, hit.note_path, hit.content_hash):
            return None
        if row["observation_status"] not in {"approved", "promoted"}:
            return None
        if _normalized_claim(row["observation_claim"]) != _normalized_claim(
            row["text"]
        ):
            return None
        valid_project, project_id = _current_observation_project(row)
        if not valid_project or project_id != row["project_id"]:
            return None
        if hit.snippet.strip() not in str(row["text"]):
            return None
        sensitivity = (
            "public"
            if str(row["observation_sensitivity"]).casefold() == "public"
            else "private"
        )
        layer = LAYER_BY_ROOT.get(Path(str(row["note_path"])).parts[0])
        if layer is None:
            return None
        public_ready = bool(
            row["observation_status"] == "promoted" and sensitivity == "public"
        )
        return CanonicalNoteMetadata(
            source_id=str(row["source_id"]),
            canonical_id=str(row["observation_id"]),
            note_path=str(row["note_path"]),
            layer=layer,
            project_id=project_id,
            confirmation_state="confirmed",
            confidence=float(row["observation_confidence"]),
            last_verified=(
                str(row["observation_updated_at"])
                if row["observation_updated_at"]
                else None
            ),
            sensitivity=sensitivity,
            public_ready=public_ready,
            observation_id=str(row["observation_id"]),
            segment_id=str(row["segment_id"]),
            content_hash=str(row["text_hash"]),
        )


class CanonicalNoteMetadataCatalog:
    """Read allowlisted canonical frontmatter without exposing note contents."""

    def __init__(
        self,
        vault: Path,
        *,
        segments: SegmentMetadataReader | None = None,
    ) -> None:
        self._vault = vault.resolve()
        self._segments = segments

    def resolve(self, hit: RecallHit) -> CanonicalNoteMetadata | None:
        if (
            not is_indexable_markdown(hit.note_path)
            or stable_source_id(hit.note_path) != hit.source_id
        ):
            return None
        candidate = self._vault / hit.note_path
        try:
            resolved = candidate.resolve(strict=True)
            normalized = resolved.relative_to(self._vault).as_posix()
        except (OSError, ValueError):
            return None
        if normalized != hit.note_path or candidate.is_symlink() or not resolved.is_file():
            return None
        layer = LAYER_BY_ROOT.get(Path(normalized).parts[0])
        if layer is None:
            return None
        try:
            markdown = resolved.read_text(encoding="utf-8")
            frontmatter = _frontmatter(markdown)
        except (OSError, UnicodeError, yaml.YAMLError):
            return None
        if hit.segment_id is not None or hit.content_hash is not None:
            if (
                self._segments is None
                or hit.segment_id is None
                or hit.content_hash is None
            ):
                return None
            try:
                current = {
                    (segment.segment_id, segment.text_hash)
                    for segment in segment_markdown(normalized, markdown)
                }
            except (RuntimeError, ValueError):
                return None
            if (hit.segment_id, hit.content_hash) not in current:
                return None
            return self._segments.resolve(hit)
        if not _excerpt_is_current(markdown, hit.snippet):
            return None
        if (
            normalized in SEGMENT_REQUIRED_PATHS
            or str(frontmatter.get("segment_policy") or "").casefold()
            == "required"
            or str(frontmatter.get("status") or "").casefold() == "generated"
        ):
            return None
        canonical_id = str(frontmatter.get("id") or hit.source_id).strip().casefold()
        if not CANONICAL_ID.fullmatch(canonical_id):
            return None
        status = str(frontmatter.get("status") or "").strip().casefold()
        explicit_confirmation = str(
            frontmatter.get("confirmation_state") or ""
        ).strip().casefold()
        confirmation_state = explicit_confirmation or (
            "confirmed"
            if status == "active" and frontmatter.get("last_verified")
            else "developing"
            if status == "developing"
            else "generated"
            if status == "generated"
            else "unverified"
        )
        try:
            confidence = float(frontmatter.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        if not 0.0 <= confidence <= 1.0:
            confidence = 0.0
        sensitivity = str(frontmatter.get("sensitivity") or "private").casefold()
        if sensitivity not in {"private", "public"}:
            sensitivity = "private"
        project_id = canonical_id if layer == "project" else None
        last_verified = frontmatter.get("last_verified")
        return CanonicalNoteMetadata(
            source_id=hit.source_id,
            canonical_id=canonical_id,
            note_path=normalized,
            layer=layer,
            project_id=project_id,
            confirmation_state=confirmation_state,
            confidence=confidence,
            last_verified=(str(last_verified) if last_verified else None),
            sensitivity=sensitivity,
            public_ready=frontmatter.get("public_ready") is True,
        )


class RecallGateway:
    """Return purpose-scoped typed context through one governed read-only seam."""

    def __init__(
        self,
        *,
        harness: RecallHarness,
        metadata: NoteMetadataReader,
        grants: RecallGrantReader,
        telemetry: RecallTelemetryWriter | None = None,
    ) -> None:
        self._harness = harness
        self._metadata = metadata
        self._grants = grants
        self._telemetry = telemetry

    def retrieve(self, request: GatewayRecallRequest) -> GatewayRecallPacket:
        started = time.perf_counter()
        grant = self._grants.resolve(request.caller_id)
        if grant is None or request.purpose not in grant.purposes:
            raise PermissionError("Recall caller is not opted in for this purpose")
        allowed_layers = PURPOSE_LAYERS.get(request.purpose)
        if allowed_layers is None:
            raise ValueError("Unknown recall purpose profile")
        if request.purpose == "write-as-me" and request.project_ids:
            raise ValueError("Recall project scope exceeds its purpose profile")
        if request.purpose == "project-handoff" and len(request.project_ids) != 1:
            raise ValueError("Project handoff requires one explicit project")
        if request.purpose == "recent-projects" and not request.project_ids:
            raise ValueError("Recent projects requires explicit project scope")
        if set(request.project_ids) - set(grant.project_ids):
            raise PermissionError("Recall project scope exceeds the caller grant")
        findings = scan_untrusted_memory_text(request.query, "recall.query")
        if findings:
            raise ValueError("Recall query failed privacy preflight")
        max_results = request.max_results or 8
        max_chars = request.max_packet_chars or 6000
        max_tokens = request.max_packet_tokens or 1500
        if max_results > 8 or max_chars > 6000 or max_tokens > 1500:
            raise ValueError("Recall request attempts to widen its purpose profile")
        raw = self._harness.retrieve(
            RecallRequest(
                query=request.query,
                max_results=max_results * 3,
                max_packet_chars=max_chars * 3,
                max_packet_tokens=max_tokens * 3,
            )
        )
        items: list[GatewayRecallItem] = []
        layer_counts: Counter[str] = Counter()
        excluded = raw.excluded_candidates
        for hit in raw.hits:
            note = self._metadata.resolve(hit)
            if note is None or note.layer not in allowed_layers:
                excluded += 1
                continue
            if (
                note.confirmation_state
                not in ALLOWED_CONFIRMATION_BY_PURPOSE[request.purpose]
            ):
                excluded += 1
                continue
            if request.purpose in PUBLIC_ONLY_PURPOSES and not (
                note.sensitivity == "public"
                and note.public_ready
                and note.confirmation_state == "confirmed"
            ):
                excluded += 1
                continue
            if note.project_id is None:
                if note.sensitivity == "private" and not grant.allow_private_global:
                    excluded += 1
                    continue
            elif note.project_id not in request.project_ids:
                excluded += 1
                continue
            if layer_counts[note.layer] >= 2:
                excluded += 1
                continue
            items.append(
                GatewayRecallItem(
                    source_id=note.source_id,
                    canonical_id=note.canonical_id,
                    layer=note.layer,
                    project_id=note.project_id,
                    confirmation_state=note.confirmation_state,
                    confidence=note.confidence,
                    last_verified=note.last_verified,
                    sensitivity=note.sensitivity,
                    excerpt=hit.snippet,
                    score=hit.score,
                    canonical_link=f"[[{note.note_path[:-3]}]]",
                    retrieval_channels=hit.provenance,
                    observation_id=note.observation_id,
                    segment_id=note.segment_id,
                    content_hash=note.content_hash,
                )
            )
            layer_counts[note.layer] += 1
            if len(items) >= max_results:
                break
        packet_budget = min(max_chars, max_tokens * 4)
        truncated = raw.truncated or len(items) < len(raw.hits)
        empty = _gateway_packet(
            purpose=request.purpose,
            query_hash=raw.query_hash,
            items=(),
            excluded_by_policy=excluded,
            truncated=truncated,
            executed_channels=raw.executed_channels,
        )
        if empty.used_chars > packet_budget:
            raise ValueError("Recall gateway budget is too small for its envelope")
        fitted_items: list[GatewayRecallItem] = []
        for item in items:
            fitted = _fit_item_to_budget(
                item=item,
                existing_items=tuple(fitted_items),
                packet_budget=packet_budget,
                purpose=request.purpose,
                query_hash=raw.query_hash,
                excluded_by_policy=excluded,
                executed_channels=raw.executed_channels,
            )
            if fitted is None:
                truncated = True
                continue
            if fitted != item:
                truncated = True
            fitted_items.append(fitted)
        packet = _gateway_packet(
            purpose=request.purpose,
            query_hash=raw.query_hash,
            items=tuple(fitted_items),
            excluded_by_policy=excluded,
            truncated=truncated,
            executed_channels=raw.executed_channels,
        )
        if packet.used_chars > packet_budget:
            raise AssertionError("Recall gateway packet exceeded its fitted budget")
        assert_model_packet_safe(asdict(packet))
        if self._telemetry is not None:
            self._telemetry.record(
                request,
                packet,
                latency_ms=max(0, round((time.perf_counter() - started) * 1000)),
            )
        return packet


def _frontmatter(text: str) -> dict[str, object]:
    if not text.startswith("---\n"):
        return {}
    closing = text.find("\n---", 4)
    if closing < 0:
        return {}
    value = yaml.safe_load(text[4:closing]) or {}
    return value if isinstance(value, dict) else {}


def _current_observation_project(row) -> tuple[bool, str | None]:
    try:
        payload = json.loads(row["payload_json"])
    except (json.JSONDecodeError, KeyError, TypeError):
        return False, None
    if not isinstance(payload, dict):
        return False, None
    raw = payload.get("project_ids_override")
    if raw is None:
        raw = payload.get("project_ids")
    if raw is None and payload.get("project_id") is not None:
        raw = [payload["project_id"]]
    if raw is None:
        return (int(row["project_count"]) == 0), None
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return False, None
    project_ids = tuple(
        sorted({str(value).strip() for value in raw if str(value).strip()})
    )
    if not project_ids and int(row["project_count"]) == 0:
        return True, None
    if (
        len(project_ids) != 1
        or not CANONICAL_ID.fullmatch(project_ids[0])
    ):
        return False, None
    return True, project_ids[0]


def _normalized_claim(value: object) -> str:
    return " ".join(str(value or "").split())


def _excerpt_is_current(markdown: str, excerpt: str) -> bool:
    current = excerpt.strip()
    current = current.removesuffix("[TRUNCATED]").rstrip()
    for marker in ("…", "â€¦"):
        current = current.removeprefix(marker).removesuffix(marker).strip()
    return bool(current and current in markdown)


def _telemetry_receipt_from_row(row) -> RecallUseReceipt:
    return RecallUseReceipt(
        receipt_id=str(row["receipt_id"]),
        caller_id=str(row["caller_id"]),
        purpose=str(row["purpose"]),
        query_hash=str(row["query_hash"]),
        rankings=tuple(
            (str(item[0]), str(item[1]) if item[1] is not None else None, float(item[2]))
            for item in json.loads(row["rankings_json"])
        ),
        result_count=int(row["result_count"]),
        excluded_by_policy=int(row["excluded_by_policy"]),
        packet_chars=int(row["packet_chars"]),
        packet_tokens=int(row["packet_tokens"]),
        latency_ms=int(row["latency_ms"]),
        executed_channels=tuple(json.loads(row["channels_json"])),
        outcome=(str(row["outcome"]) if row["outcome"] else None),
        created_at=str(row["created_at"]),
    )


def _sized_packet(packet: GatewayRecallPacket) -> GatewayRecallPacket:
    for _ in range(12):
        encoded = json.dumps(asdict(packet), ensure_ascii=False, separators=(",", ":"))
        chars = len(encoded)
        tokens = (chars + 3) // 4
        if packet.used_chars == chars and packet.estimated_tokens == tokens:
            return packet
        packet = replace(packet, used_chars=chars, estimated_tokens=tokens)
    raise AssertionError("Recall gateway packet size metadata did not converge")


def _gateway_packet(
    *,
    purpose: str,
    query_hash: str,
    items: tuple[GatewayRecallItem, ...],
    excluded_by_policy: int,
    truncated: bool,
    executed_channels: tuple[str, ...],
) -> GatewayRecallPacket:
    return _sized_packet(
        GatewayRecallPacket(
            purpose=purpose,
            query_hash=query_hash,
            items=items,
            excluded_by_policy=excluded_by_policy,
            used_chars=0,
            estimated_tokens=0,
            truncated=truncated,
            executed_channels=executed_channels,
        )
    )


def _fit_item_to_budget(
    *,
    item: GatewayRecallItem,
    existing_items: tuple[GatewayRecallItem, ...],
    packet_budget: int,
    purpose: str,
    query_hash: str,
    excluded_by_policy: int,
    executed_channels: tuple[str, ...],
) -> GatewayRecallItem | None:
    def fits(candidate: GatewayRecallItem) -> bool:
        return (
            _gateway_packet(
                purpose=purpose,
                query_hash=query_hash,
                items=(*existing_items, candidate),
                excluded_by_policy=excluded_by_policy,
                truncated=False,
                executed_channels=executed_channels,
            ).used_chars
            <= packet_budget
        )

    if fits(item):
        return item
    low = 0
    high = len(item.excerpt)
    best: GatewayRecallItem | None = None
    while low <= high:
        midpoint = (low + high) // 2
        excerpt = item.excerpt[:midpoint].rstrip()
        if midpoint < len(item.excerpt):
            excerpt += "…"
        candidate = replace(item, excerpt=excerpt)
        if fits(candidate):
            best = candidate
            low = midpoint + 1
        else:
            high = midpoint - 1
    return best


def _encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
