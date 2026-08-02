from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Mapping, Protocol

from jsonschema import Draft202012Validator

from .config import protocol_root
from .security import (
    EXTERNAL_SESSION_MESSAGE_LIMIT,
    EXTERNAL_SESSION_TEXT_CHARS,
    EXTERNAL_SESSION_TOTAL_CHARS,
    assert_model_packet_safe,
    sanitize_model_evidence_item,
    scan_untrusted_memory_text,
)
from .state import canonical_hash


PROJECT_KNOWLEDGE_KINDS = {"project_fact", "decision", "lesson"}
EXTRACTION_POLICY_VERSION = "extraction-policy-v2"
MEMORY_MUTATION_REQUIRED_FIELDS = {
    "type",
    "operation",
    "kind",
    "subject",
    "claim",
    "scope",
    "evidence_refs",
    "confidence",
    "explicit",
}


@dataclass(frozen=True)
class ExtractionLimits:
    max_episode_messages: int = 12
    max_episode_chars: int = 6000

    def __post_init__(self) -> None:
        if self.max_episode_messages <= 0 or self.max_episode_chars <= 0:
            raise ValueError("Extraction episode bounds must be positive")


@dataclass(frozen=True)
class ExtractionRequest:
    run_kind: str
    evidence: tuple[Mapping[str, Any], ...]
    limits: ExtractionLimits = field(default_factory=ExtractionLimits)


@dataclass(frozen=True)
class ExtractionCandidate:
    candidate_type: str
    operation: str
    kind: str
    subject: str
    claim: str
    scope: str
    project_id: str | None
    target_memory_id: str | None
    evidence_refs: tuple[str, ...]
    confidence: float
    explicit: bool


@dataclass(frozen=True)
class ProcedureCandidate:
    procedure_id: str
    name: str
    purpose: str
    scope: str
    project_id: str | None
    prerequisites: tuple[str, ...]
    steps: tuple[str, ...]
    failure_branches: tuple[str, ...]
    tests: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    owner_directed: bool
    validated_outcome: bool
    disposition: str = "review"


@dataclass(frozen=True)
class CandidateRejection:
    index: int
    code: str
    candidate_type: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class ExtractionCoverage:
    source_evidence_count: int
    episode_count: int
    omitted_messages: int
    failed_episodes: int = 0


@dataclass(frozen=True)
class ExtractionUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int
    model_calls: int


@dataclass(frozen=True)
class ExtractionFailure:
    code: str
    error_type: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class EpisodeReceipt:
    episode_id: str
    source_evidence_ids: tuple[str, ...]
    analysis_lane: str
    project_id: str | None
    started_at: str
    ended_at: str
    message_count: int
    content_chars: int
    content_hash: str | None = None
    payload_json: str | None = None


@dataclass(frozen=True)
class ExtractionResult:
    status: str
    candidates: tuple[ExtractionCandidate, ...]
    rejections: tuple[CandidateRejection, ...]
    coverage: ExtractionCoverage
    usage: ExtractionUsage
    failures: tuple[ExtractionFailure, ...]
    episodes: tuple[EpisodeReceipt, ...] = ()
    procedures: tuple[ProcedureCandidate, ...] = ()


@dataclass(frozen=True)
class _CandidateContext:
    raw: Any
    allowed_evidence_ids: frozenset[str]
    profile_only_ids: frozenset[str]
    authoritative_projects: tuple[tuple[str, str | None], ...]


class ModelCallBudgetExceeded(RuntimeError):
    """Raised when an explicit live-evaluation budget blocks delegation."""


class ExtractionModel(Protocol):
    def extract(self, request: ExtractionRequest) -> Mapping[str, Any]: ...


class ExtractionHarness:
    """Extract and independently validate durable-memory candidates."""

    def __init__(self, *, model: ExtractionModel) -> None:
        self._model = model

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        model_requests, coverage = _model_requests(request)
        episode_receipts = _episode_receipts(model_requests)
        raw_candidates: list[_CandidateContext] = []
        input_tokens = 0
        output_tokens = 0
        total_tokens = 0
        model_calls = 0
        failures: list[ExtractionFailure] = []
        for model_request in model_requests:
            sanitized = sanitized_extraction_request(model_request)
            packet_evidence_refs = tuple(
                str(item["id"]) for item in model_request.evidence
            )
            try:
                response = self._model.extract(sanitized)
            except ModelCallBudgetExceeded:
                raise
            except Exception as error:
                model_calls += 1
                failures.append(
                    ExtractionFailure(
                        code="model-error",
                        error_type=type(error).__name__,
                        evidence_refs=packet_evidence_refs,
                    )
                )
                continue
            try:
                if not isinstance(response, Mapping):
                    raise TypeError("Model response must be an object")
                response_candidates = response.get("candidates", [])
                if not isinstance(response_candidates, list):
                    raise TypeError("Model candidates must be an array")
                (
                    call_input_tokens,
                    call_output_tokens,
                    call_total_tokens,
                    call_count,
                ) = _response_usage(response)
                allowed_evidence_ids = frozenset(packet_evidence_refs)
                profile_only_ids = frozenset(
                    str(item["id"])
                    for item in model_request.evidence
                    if _is_profile_only(item)
                )
                authoritative_projects = tuple(
                    (
                        str(item["id"]),
                        _authoritative_project_id(item),
                    )
                    for item in model_request.evidence
                )
                response_contexts = [
                    _CandidateContext(
                        raw=raw,
                        allowed_evidence_ids=allowed_evidence_ids,
                        profile_only_ids=profile_only_ids,
                        authoritative_projects=authoritative_projects,
                    )
                    for raw in response_candidates
                ]
            except (TypeError, ValueError, OverflowError) as error:
                model_calls += 1
                failures.append(
                    ExtractionFailure(
                        code="invalid-model-response",
                        error_type=type(error).__name__,
                        evidence_refs=packet_evidence_refs,
                    )
                )
                continue
            raw_candidates.extend(response_contexts)
            input_tokens += call_input_tokens
            output_tokens += call_output_tokens
            total_tokens += call_total_tokens
            model_calls += call_count
        accepted: list[ExtractionCandidate] = []
        procedures: list[ProcedureCandidate] = []
        rejected: list[CandidateRejection] = []

        for index, context in enumerate(raw_candidates):
            raw = context.raw
            if not isinstance(raw, Mapping):
                rejected.append(
                    CandidateRejection(
                        index=index,
                        code="invalid-candidate-shape",
                        candidate_type="unknown",
                        evidence_refs=(),
                    )
                )
                continue
            raw_candidate_type = raw.get("type")
            candidate_type = (
                raw_candidate_type
                if isinstance(raw_candidate_type, str) and raw_candidate_type
                else "unknown"
            )
            raw_refs = raw.get("evidence_refs")
            evidence_refs = (
                tuple(str(value) for value in raw_refs)
                if isinstance(raw_refs, list)
                else ()
            )
            if candidate_type == "memory_mutation" and (
                MEMORY_MUTATION_REQUIRED_FIELDS - set(raw)
            ):
                rejected.append(
                    CandidateRejection(
                        index=index,
                        code="invalid-candidate-shape",
                        candidate_type=candidate_type,
                        evidence_refs=evidence_refs,
                    )
                )
                continue
            if set(evidence_refs) - context.allowed_evidence_ids:
                rejected.append(
                    CandidateRejection(
                        index=index,
                        code="unknown-evidence-reference",
                        candidate_type=candidate_type,
                        evidence_refs=evidence_refs,
                    )
                )
                continue
            if candidate_type == "question_resolution":
                rejected.append(
                    CandidateRejection(
                        index=index,
                        code=(
                            "run-kind-capability-violation"
                            if request.run_kind != "weekly"
                            else "unsupported-candidate-type"
                        ),
                        candidate_type=candidate_type,
                        evidence_refs=evidence_refs,
                    )
                )
                continue
            if candidate_type not in {"memory_mutation", "procedure_candidate"}:
                rejected.append(
                    CandidateRejection(
                        index=index,
                        code="invalid-candidate-shape",
                        candidate_type=candidate_type,
                        evidence_refs=evidence_refs,
                    )
                )
                continue
            if not _candidate_schema_validator().is_valid(raw):
                rejected.append(
                    CandidateRejection(
                        index=index,
                        code="invalid-candidate-schema",
                        candidate_type=candidate_type,
                        evidence_refs=evidence_refs,
                    )
                )
                continue
            if candidate_type == "procedure_candidate":
                try:
                    procedure = _procedure_candidate(
                        raw,
                        evidence_refs=evidence_refs,
                    )
                except (TypeError, ValueError):
                    rejected.append(
                        CandidateRejection(
                            index=index,
                            code="invalid-candidate-shape",
                            candidate_type=candidate_type,
                            evidence_refs=evidence_refs,
                        )
                    )
                    continue
                unsafe_findings = tuple(
                    finding
                    for field_name, values in (
                        ("name", (procedure.name,)),
                        ("purpose", (procedure.purpose,)),
                        ("prerequisites", procedure.prerequisites),
                        ("steps", procedure.steps),
                        ("failure_branches", procedure.failure_branches),
                        ("tests", procedure.tests),
                    )
                    for text in values
                    for finding in scan_untrusted_memory_text(
                        text,
                        f"candidate.{field_name}",
                    )
                )
                if unsafe_findings:
                    rejected.append(
                        CandidateRejection(
                            index=index,
                            code="unsafe-candidate-content",
                            candidate_type=candidate_type,
                            evidence_refs=evidence_refs,
                        )
                    )
                    continue
                if context.profile_only_ids.intersection(evidence_refs):
                    rejected.append(
                        CandidateRejection(
                            index=index,
                            code="procedure-requires-full-lane",
                            candidate_type=candidate_type,
                            evidence_refs=evidence_refs,
                        )
                    )
                    continue
                if procedure.project_id is not None:
                    project_by_evidence = dict(context.authoritative_projects)
                    if {
                        project_by_evidence.get(evidence_ref)
                        for evidence_ref in evidence_refs
                    } != {procedure.project_id}:
                        rejected.append(
                            CandidateRejection(
                                index=index,
                                code="project-attribution-mismatch",
                                candidate_type=candidate_type,
                                evidence_refs=evidence_refs,
                            )
                        )
                        continue
                procedures.append(procedure)
                continue
            try:
                candidate = _memory_candidate(raw, evidence_refs=evidence_refs)
            except (TypeError, ValueError):
                rejected.append(
                    CandidateRejection(
                        index=index,
                        code="invalid-candidate-shape",
                        candidate_type=candidate_type,
                        evidence_refs=evidence_refs,
                    )
                )
                continue
            unsafe_findings = tuple(
                finding
                for field_name, text in (
                    ("subject", candidate.subject),
                    ("claim", candidate.claim),
                    ("project_id", candidate.project_id),
                    ("target_memory_id", candidate.target_memory_id),
                )
                if text is not None
                for finding in scan_untrusted_memory_text(
                    text,
                    f"candidate.{field_name}",
                )
            )
            if unsafe_findings:
                rejected.append(
                    CandidateRejection(
                        index=index,
                        code="unsafe-candidate-content",
                        candidate_type=candidate_type,
                        evidence_refs=evidence_refs,
                    )
                )
                continue
            if context.profile_only_ids.intersection(evidence_refs) and (
                candidate.kind in PROJECT_KNOWLEDGE_KINDS
                or candidate.scope == "project"
                or candidate.project_id is not None
            ):
                rejected.append(
                    CandidateRejection(
                        index=index,
                        code="profile-only-project-knowledge",
                        candidate_type=candidate_type,
                        evidence_refs=evidence_refs,
                    )
                )
                continue
            is_project_knowledge = (
                candidate.kind in PROJECT_KNOWLEDGE_KINDS
                or candidate.scope == "project"
                or candidate.project_id is not None
            )
            if is_project_knowledge:
                project_by_evidence = dict(context.authoritative_projects)
                cited_projects = {
                    project_by_evidence.get(evidence_ref)
                    for evidence_ref in evidence_refs
                }
                if (
                    candidate.project_id is None
                    or cited_projects != {candidate.project_id}
                ):
                    rejected.append(
                        CandidateRejection(
                            index=index,
                            code="project-attribution-mismatch",
                            candidate_type=candidate_type,
                            evidence_refs=evidence_refs,
                        )
                    )
                    continue
            accepted.append(candidate)

        status = _result_status(
            accepted=accepted,
            procedures=procedures,
            rejected=rejected,
            failures=failures,
        )
        return ExtractionResult(
            status=status,
            candidates=tuple(accepted),
            rejections=tuple(rejected),
            coverage=ExtractionCoverage(
                source_evidence_count=coverage.source_evidence_count,
                episode_count=coverage.episode_count,
                omitted_messages=coverage.omitted_messages,
                failed_episodes=len(failures),
            ),
            usage=ExtractionUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                model_calls=model_calls,
            ),
            failures=tuple(failures),
            episodes=episode_receipts,
            procedures=tuple(procedures),
        )


def _response_usage(response: Mapping[str, Any]) -> tuple[int, int, int, int]:
    usage = response.get("usage")
    if usage is None:
        return 0, 0, 0, 1
    if not isinstance(usage, Mapping):
        raise TypeError("Model usage must be an object")
    input_tokens = _nonnegative_integer(usage.get("input_tokens"), default=0)
    output_tokens = _nonnegative_integer(usage.get("output_tokens"), default=0)
    reported_total = _nonnegative_integer(usage.get("total_tokens"), default=0)
    model_calls = _nonnegative_integer(usage.get("model_calls"), default=1)
    return (
        input_tokens,
        output_tokens,
        reported_total or input_tokens + output_tokens,
        model_calls,
    )


def _nonnegative_integer(value: Any, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("Usage values must be numbers")
    if not math.isfinite(float(value)) or int(value) != value or value < 0:
        raise ValueError("Usage values must be non-negative integers")
    return int(value)


def _is_profile_only(item: Mapping[str, Any]) -> bool:
    if item.get("kind") not in {"session_digest", "session_episode"}:
        return False
    payload = item.get("payload")
    if not isinstance(payload, Mapping):
        return True
    project_ids = [str(value) for value in payload.get("project_ids", []) if value]
    project_id = str(item.get("project_id") or "")
    return not (
        payload.get("analysis_lane") == "full"
        and len(project_ids) == 1
        and project_id == project_ids[0]
    )


def _authoritative_project_id(item: Mapping[str, Any]) -> str | None:
    project_id = item.get("project_id")
    if not isinstance(project_id, str) or not project_id:
        return None
    if item.get("kind") in {"session_digest", "session_episode"}:
        return None if _is_profile_only(item) else project_id
    return project_id


def _memory_candidate(
    raw: Mapping[str, Any], *, evidence_refs: tuple[str, ...]
) -> ExtractionCandidate:
    string_fields = {
        name: raw[name]
        for name in ("type", "operation", "kind", "subject", "claim", "scope")
    }
    if any(not isinstance(value, str) or not value for value in string_fields.values()):
        raise TypeError("Memory candidate string fields must be non-empty strings")
    if not evidence_refs or not all(
        isinstance(value, str) and value for value in raw["evidence_refs"]
    ):
        raise TypeError("Memory candidate evidence references must be non-empty strings")
    confidence = raw["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise TypeError("Memory candidate confidence must be numeric")
    confidence = float(confidence)
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        raise ValueError("Memory candidate confidence must be between zero and one")
    if not isinstance(raw["explicit"], bool):
        raise TypeError("Memory candidate explicit must be boolean")
    project_id = raw.get("project_id")
    if project_id is not None and (not isinstance(project_id, str) or not project_id):
        raise TypeError("Memory candidate project ID must be null or a string")
    target_memory_id = raw.get("target_memory_id")
    if target_memory_id is not None and (
        not isinstance(target_memory_id, str) or not target_memory_id
    ):
        raise TypeError("Target memory ID must be null or a string")
    return ExtractionCandidate(
        candidate_type=string_fields["type"],
        operation=string_fields["operation"],
        kind=string_fields["kind"],
        subject=string_fields["subject"],
        claim=string_fields["claim"],
        scope=string_fields["scope"],
        project_id=project_id,
        target_memory_id=target_memory_id,
        evidence_refs=evidence_refs,
        confidence=confidence,
        explicit=raw["explicit"],
    )


def _procedure_candidate(
    raw: Mapping[str, Any], *, evidence_refs: tuple[str, ...]
) -> ProcedureCandidate:
    string_fields = {
        name: raw[name] for name in ("name", "purpose", "scope")
    }
    if any(not isinstance(value, str) or not value for value in string_fields.values()):
        raise TypeError("Procedure candidate strings must be non-empty")
    project_id = raw.get("project_id")
    if project_id is not None and (
        not isinstance(project_id, str) or not project_id
    ):
        raise TypeError("Procedure candidate project must be null or a string")

    def strings(name: str) -> tuple[str, ...]:
        values = raw[name]
        if not isinstance(values, list) or not values or any(
            not isinstance(value, str) or not value.strip() for value in values
        ):
            raise TypeError(f"Procedure candidate {name} must contain text")
        return tuple(value.strip() for value in values)

    prerequisites = strings("prerequisites")
    steps = strings("steps")
    failure_branches = strings("failure_branches")
    tests = strings("tests")
    if not evidence_refs:
        raise TypeError("Procedure candidate requires evidence")
    if raw.get("owner_directed") is not True or raw.get("validated_outcome") is not True:
        raise ValueError("Procedure candidates require direction and a validated outcome")
    identity = {
        "name": string_fields["name"].strip().casefold(),
        "purpose": string_fields["purpose"].strip().casefold(),
        "scope": string_fields["scope"],
        "project_id": project_id,
        "prerequisites": prerequisites,
        "steps": steps,
        "failure_branches": failure_branches,
        "tests": tests,
        "evidence_refs": sorted(evidence_refs),
    }
    procedure_id = "procedure-" + hashlib.sha256(
        json.dumps(
            identity,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()[:24]
    return ProcedureCandidate(
        procedure_id=procedure_id,
        name=string_fields["name"].strip(),
        purpose=string_fields["purpose"].strip(),
        scope=string_fields["scope"],
        project_id=project_id,
        prerequisites=prerequisites,
        steps=steps,
        failure_branches=failure_branches,
        tests=tests,
        evidence_refs=tuple(sorted(evidence_refs)),
        owner_directed=True,
        validated_outcome=True,
    )


@lru_cache(maxsize=1)
def _candidate_schema_validator() -> Draft202012Validator:
    schema = json.loads(
        (protocol_root() / "schemas" / "extraction-candidate.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def sanitized_extraction_request(request: ExtractionRequest) -> ExtractionRequest:
    """Return the canonical privacy-safe request used at the model seam."""

    safe_evidence: list[Mapping[str, Any]] = []
    for item in request.evidence:
        safe_evidence.append(sanitize_model_evidence_item(dict(item)))
    assert_model_packet_safe({"evidence": safe_evidence})
    return ExtractionRequest(
        run_kind=request.run_kind,
        evidence=tuple(safe_evidence),
        limits=request.limits,
    )


def canonical_extraction_replay_payload(
    request: ExtractionRequest,
) -> tuple[dict[str, Any], ...]:
    """Serialize every bounded model packet so replay identity covers all input."""

    model_requests, _coverage = _model_requests(request)
    payload: list[dict[str, Any]] = []
    for model_request in model_requests:
        safe = sanitized_extraction_request(model_request)
        payload.append(
            {
                "run_kind": safe.run_kind,
                "limits": {
                    "max_episode_messages": safe.limits.max_episode_messages,
                    "max_episode_chars": safe.limits.max_episode_chars,
                },
                "evidence": list(safe.evidence),
            }
        )
    return tuple(payload)


def extraction_contract_fingerprint(run_kind: str) -> str:
    """Bind replay identity to the prompts, schemas, and extraction policy."""

    if run_kind not in {"daily", "weekly"}:
        raise ValueError("Extraction contract supports daily and weekly runs")
    root = protocol_root()
    files = (
        root / "prompts" / "extraction-system.md",
        root / "prompts" / "extraction.md",
        root / "prompts" / f"{run_kind}.md",
        root / "schemas" / "extraction-candidate.schema.json",
        root / "schemas" / "extraction-output.schema.json",
    )
    digest = hashlib.sha256()
    digest.update(EXTRACTION_POLICY_VERSION.encode("utf-8"))
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _model_requests(
    request: ExtractionRequest,
) -> tuple[tuple[ExtractionRequest, ...], ExtractionCoverage]:
    expanded: list[Mapping[str, Any]] = []
    passthrough: list[Mapping[str, Any]] = []
    omitted_messages = 0
    for item in request.evidence:
        if item.get("kind") in {"session_digest", "session_episode"}:
            omitted_messages += _session_message_omissions(item)
        if item.get("kind") == "session_digest" or (
            item.get("kind") == "session_episode"
            and _session_item_exceeds_limits(item, request.limits)
        ):
            expanded.extend(_session_episodes(item, request.limits))
        else:
            passthrough.append(item)

    requests = [
        ExtractionRequest(
            run_kind=request.run_kind,
            evidence=(episode,),
            limits=request.limits,
        )
        for episode in expanded
    ]
    if passthrough:
        requests.append(
            ExtractionRequest(
                run_kind=request.run_kind,
                evidence=tuple(passthrough),
                limits=request.limits,
            )
        )
    return (
        tuple(requests),
        ExtractionCoverage(
            source_evidence_count=len(request.evidence),
            episode_count=len(expanded) + sum(
                item.get("kind") == "session_episode" for item in passthrough
            ),
            omitted_messages=omitted_messages,
        ),
    )


def _session_message_omissions(item: Mapping[str, Any]) -> int:
    payload = item.get("payload")
    if not isinstance(payload, Mapping):
        return 1
    omitted = 0
    for key in ("user_messages", "assistant_results"):
        values = payload.get(key)
        if values is None:
            continue
        if not isinstance(values, list):
            omitted += 1
            continue
        omitted += sum(not isinstance(value, Mapping) for value in values)
    return omitted


def _session_item_exceeds_limits(
    item: Mapping[str, Any], limits: ExtractionLimits
) -> bool:
    payload = item.get("payload")
    if not isinstance(payload, Mapping):
        return False
    message_count = 0
    character_count = 0
    for key in ("user_messages", "assistant_results"):
        values = payload.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, Mapping):
                continue
            message_count += 1
            character_count += len(str(value.get("text") or ""))
    return (
        message_count > _effective_episode_message_limit(limits)
        or character_count > _effective_episode_char_limit(limits)
        or any(
            len(str(value.get("text") or "")) > EXTERNAL_SESSION_TEXT_CHARS
            for key in ("user_messages", "assistant_results")
            for value in (payload.get(key) or [])
            if isinstance(value, Mapping)
        )
    )


def _effective_episode_message_limit(limits: ExtractionLimits) -> int:
    return min(limits.max_episode_messages, EXTERNAL_SESSION_MESSAGE_LIMIT)


def _effective_episode_char_limit(limits: ExtractionLimits) -> int:
    return min(limits.max_episode_chars, EXTERNAL_SESSION_TOTAL_CHARS)


def _session_episodes(
    item: Mapping[str, Any], limits: ExtractionLimits
) -> list[Mapping[str, Any]]:
    payload = item.get("payload")
    if not isinstance(payload, Mapping):
        return []
    messages: list[dict[str, str]] = []
    for key, role in (("user_messages", "user"), ("assistant_results", "assistant")):
        values = payload.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, Mapping):
                continue
            occurred_at = str(value.get("occurred_at") or "")
            text = str(value.get("text") or "")
            fragment_chars = min(
                limits.max_episode_chars,
                EXTERNAL_SESSION_TEXT_CHARS,
            )
            for offset in range(0, max(len(text), 1), fragment_chars):
                messages.append(
                    {
                        "role": role,
                        "occurred_at": occurred_at,
                        "text": text[offset : offset + fragment_chars],
                    }
                )
    messages.sort(key=lambda value: (value["occurred_at"], value["role"]))
    chunks: list[list[dict[str, str]]] = []
    current: list[dict[str, str]] = []
    current_chars = 0
    for message in messages:
        message_chars = len(message["text"])
        if current and (
            len(current) >= _effective_episode_message_limit(limits)
            or current_chars + message_chars > _effective_episode_char_limit(limits)
        ):
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(message)
        current_chars += message_chars
    if current:
        chunks.append(current)

    episodes: list[Mapping[str, Any]] = []
    root_evidence_ids = tuple(
        str(value)
        for value in payload.get("source_evidence_ids", [])
        if value
    ) or (str(item["id"]),)
    for index, chunk in enumerate(chunks):
        digest = hashlib.sha256(
            json.dumps(
                {
                    "source_evidence_ids": root_evidence_ids,
                    "index": index,
                    "messages": chunk,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()[:24]
        episodes.append(
            {
                "id": f"ev-episode-{digest}",
                "source_type": "session-episode",
                "kind": "session_episode",
                "project_id": item.get("project_id"),
                "occurred_at": chunk[0]["occurred_at"],
                "payload": {
                    "source": payload.get("source"),
                    "project_ids": list(payload.get("project_ids") or []),
                    "analysis_lane": payload.get("analysis_lane"),
                    "attribution_status": payload.get("attribution_status"),
                    "started_at": chunk[0]["occurred_at"],
                    "ended_at": chunk[-1]["occurred_at"],
                    "user_messages": [
                        {
                            "occurred_at": message["occurred_at"],
                            "text": message["text"],
                        }
                        for message in chunk
                        if message["role"] == "user"
                    ],
                    "assistant_results": [
                        {
                            "occurred_at": message["occurred_at"],
                            "text": message["text"],
                        }
                        for message in chunk
                        if message["role"] == "assistant"
                    ],
                    "source_evidence_ids": list(root_evidence_ids),
                },
            }
        )
    return episodes


def _episode_receipts(
    model_requests: tuple[ExtractionRequest, ...],
) -> tuple[EpisodeReceipt, ...]:
    receipts: dict[str, EpisodeReceipt] = {}
    for model_request in model_requests:
        sanitized_request = sanitized_extraction_request(model_request)
        sanitized_by_id = {
            str(item.get("id") or ""): item
            for item in sanitized_request.evidence
        }
        for original in model_request.evidence:
            if original.get("kind") != "session_episode":
                continue
            episode_id = str(original.get("id") or "")
            item = sanitized_by_id.get(episode_id)
            if item is None:
                continue
            payload = item.get("payload")
            if not isinstance(payload, Mapping):
                payload = {}
            if not episode_id:
                continue
            original_payload = original.get("payload")
            if not isinstance(original_payload, Mapping):
                original_payload = {}
            source_evidence_ids = tuple(
                sorted(
                    {
                        str(value)
                        for value in original_payload.get(
                            "source_evidence_ids", []
                        )
                        if value
                    }
                )
            ) or (episode_id,)
            messages = [
                value
                for key in ("user_messages", "assistant_results")
                for value in (payload.get(key) or [])
                if isinstance(value, Mapping)
            ]
            payload_dict = {
                **dict(payload),
                "source_evidence_ids": list(source_evidence_ids),
            }
            receipts[episode_id] = EpisodeReceipt(
                episode_id=episode_id,
                source_evidence_ids=source_evidence_ids,
                analysis_lane=str(payload.get("analysis_lane") or "profile_only"),
                project_id=_authoritative_project_id(original),
                started_at=str(payload.get("started_at") or item.get("occurred_at") or ""),
                ended_at=str(payload.get("ended_at") or item.get("occurred_at") or ""),
                message_count=len(messages),
                content_chars=sum(len(str(value.get("text") or "")) for value in messages),
                content_hash=canonical_hash(payload_dict),
                payload_json=json.dumps(
                    payload_dict,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            )
    return tuple(receipts[key] for key in sorted(receipts))


def _result_status(
    *,
    accepted: list[ExtractionCandidate],
    procedures: list[ProcedureCandidate],
    rejected: list[CandidateRejection],
    failures: list[ExtractionFailure],
) -> str:
    has_accepted = bool(accepted or procedures)
    if has_accepted and (rejected or failures):
        return "partial"
    if has_accepted:
        return "accepted"
    if failures:
        return "failed"
    if rejected:
        return "rejected"
    return "empty"
