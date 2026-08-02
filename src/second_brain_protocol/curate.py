from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .knowledge import like_knowledge
from .publisher import publish_curate_candidate
from .security import sanitize_text
from .state import StateStore, canonical_hash, utc_now


KNOWLEDGE_LAYER_ORDER = (
    "about_shai",
    "professional_profile",
    "operating_preferences",
    "project_knowledge",
)
KNOWLEDGE_LAYER_KINDS = {
    "about_shai": {"explicit_fact", "goal", "personality", "voice_style", "work_style"},
    "professional_profile": {"education", "experience", "military", "skill"},
    "operating_preferences": {"preference"},
    "project_knowledge": {"decision", "lesson", "project_fact"},
}
KNOWLEDGE_LAYER_NOTES = {
    "about_shai": "Identity/Persona.md",
    "professional_profile": "Skills/Index.md",
    "operating_preferences": "Identity/Preferences.md",
    "project_knowledge": "Projects/Index.md",
}
CURATE_KINDS = tuple(
    sorted({kind for kinds in KNOWLEDGE_LAYER_KINDS.values() for kind in kinds})
)
CANONICAL_ROOTS = ("Identity", "Experience", "Projects", "Skills", "Memory", "Goals")


def require_main_vault_context(
    vault: Path, *, current_directory: Path | None = None
) -> None:
    """Prevent the write route from being invoked by an outside project agent."""

    current = (current_directory or Path.cwd()).resolve()
    try:
        current.relative_to(vault.resolve())
    except ValueError as error:
        raise RuntimeError(
            "Curate capture is available only from inside the main second-brain vault. "
            "Other projects must rely on Daily session ingestion."
        ) from error


def _clean_atomic_text(value: str, *, field: str, max_chars: int) -> str:
    clean = re.sub(
        r"\s+",
        " ",
        sanitize_text(value, redact_email=True, max_chars=max_chars),
    ).strip()
    if not clean:
        raise ValueError(f"{field} is required")
    if "[REDACTED_" in clean or "[LOCAL_PATH]" in clean:
        raise ValueError(f"{field} contains sensitive content and was not captured")
    if "<!-- sb:generated" in clean.casefold() or re.search(
        r"\^obs-[a-f0-9]{8,}", clean, re.IGNORECASE
    ):
        raise ValueError(f"{field} contains reserved publication markup")
    return clean


def _resolve_project(store: StateStore, project: str) -> dict[str, Any]:
    needle = project.strip().casefold()
    candidates = [
        item
        for item in store.present_projects()
        if item.get("classification") not in {"collection", "duplicate"}
        and needle
        in {
            str(item["id"]).casefold(),
            str(item.get("name") or "").casefold(),
            str(item.get("logical_name") or "").casefold(),
        }
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            f"Project lookup returned {len(candidates)} exact matches for {project!r}; "
            "project knowledge must be attributed without guessing."
        )
    return candidates[0]


def add_curate_candidate(
    vault: Path,
    store: StateStore,
    *,
    layer: str,
    kind: str,
    subject: str,
    claim: str,
    project: str | None = None,
    confidence: float = 0.85,
    explicit: bool = False,
    confirmed: bool = False,
    allow_cross_project: bool = False,
) -> dict[str, Any]:
    """Publish one distilled, implied owner insight as an unconfirmed Curate card."""

    normalized_layer = layer.strip().casefold()
    normalized_kind = kind.strip().casefold()
    if normalized_layer not in KNOWLEDGE_LAYER_KINDS:
        raise ValueError(f"Unknown knowledge layer: {layer}")
    if normalized_kind not in KNOWLEDGE_LAYER_KINDS[normalized_layer]:
        allowed = ", ".join(sorted(KNOWLEDGE_LAYER_KINDS[normalized_layer]))
        raise ValueError(
            f"{normalized_kind!r} does not belong in {normalized_layer!r}; "
            f"choose one of: {allowed}"
        )
    if not 0.5 <= float(confidence) <= 1.0:
        raise ValueError("Curate confidence must be between 0.5 and 1.0")

    clean_subject = _clean_atomic_text(subject, field="Subject", max_chars=160)
    clean_claim = _clean_atomic_text(claim, field="Claim", max_chars=900)
    project_row = None
    if normalized_layer == "project_knowledge":
        if not project:
            raise ValueError("Project knowledge requires one exact project attribution")
        project_row = _resolve_project(store, project)
        if not project_row.get("managed_vault") and not allow_cross_project:
            raise RuntimeError(
                "Project capture from the main vault requires explicit cross-project "
                "authorization when the target is not the managed vault project."
            )
    elif project:
        raise ValueError("Only project knowledge accepts a project attribution")

    project_id = str(project_row["id"]) if project_row else None
    conflicts = [
        item
        for item in store.observations()
        if item["kind"] == normalized_kind
        and item["subject"].strip().casefold() == clean_subject.casefold()
        and item["status"] in {"approved", "promoted"}
        and item["claim"].strip().casefold() != clean_claim.casefold()
    ]
    if conflicts:
        raise ValueError(
            "Curate candidate conflicts with existing canonical knowledge for this subject"
        )

    evidence_payload = {
        "role": "user",
        "capture": "curate_candidate",
        "explicit": bool(explicit),
        "knowledge_layer": normalized_layer,
        "kind": normalized_kind,
        "subject": clean_subject,
        "claim": clean_claim,
        "project_ids": [project_id] if project_id else [],
    }
    source_ref = (
        "owner-curate:"
        + canonical_hash(
            {
                "layer": normalized_layer,
                "kind": normalized_kind,
                "subject": clean_subject,
                "claim": clean_claim,
                "project_id": project_id,
            }
        )[:24]
    )
    evidence_id, _added = store.add_evidence(
        source_type="owner-interaction",
        source_ref=source_ref,
        kind="curate_candidate",
        project_id=project_id,
        occurred_at=utc_now(),
        payload=evidence_payload,
    )
    result = publish_curate_candidate(
        vault,
        store,
        layer=normalized_layer,
        kind=normalized_kind,
        subject=clean_subject,
        claim=clean_claim,
        evidence_id=evidence_id,
        project_id=project_id,
        confidence=float(confidence),
        explicit=bool(explicit),
    )
    if confirmed and result["status"] != "suppressed":
        like_knowledge(store, result["id"])
        result["status"] = "confirmed"
    elif (
        result["status"] != "suppressed"
        and store.knowledge_feedback().get(result["id"], {}).get("decision") == "liked"
    ):
        result["status"] = "confirmed"
    store.mark_evidence([evidence_id], "processed")
    if result["status"] == "suppressed":
        message = "The matching knowledge remains removed and was not re-added."
    elif result["status"] == "confirmed":
        message = "Brain updated."
    else:
        message = "Added to Curate for the vault owner to accept or decline."
    return {
        **result,
        "message": message,
        "project": (
            str(project_row.get("name") or project_row["id"]) if project_row else None
        ),
    }


def _rewrite_canonical_observation_claim(
    vault: Path,
    *,
    observation_id: str,
    claim: str,
    required: bool,
) -> tuple[dict[Path, str], int]:
    token = f"^{observation_id}"
    originals: dict[Path, str] = {}
    updates: dict[Path, str] = {}
    occurrences = 0
    for root_name in CANONICAL_ROOTS:
        root = vault / root_name
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.md")):
            current = path.read_text(encoding="utf-8", errors="strict")
            if token not in current:
                continue
            section: str | None = None
            rendered: list[str] = []
            changed = False
            for line in current.splitlines(keepends=True):
                stripped = line.strip()
                start = re.fullmatch(r"<!-- sb:generated ([a-z0-9-]+):start -->", stripped)
                end = re.fullmatch(r"<!-- sb:generated ([a-z0-9-]+):end -->", stripped)
                if start:
                    if section is not None:
                        raise RuntimeError("Nested generated sections are not allowed")
                    section = start.group(1)
                elif end:
                    if section != end.group(1):
                        raise RuntimeError("Malformed generated section markers")
                    section = None
                if token in line:
                    if section is None:
                        raise RuntimeError(
                            "Observation correction may edit only generated knowledge"
                        )
                    eol = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
                    content = line[: -len(eol)] if eol else line
                    match = re.fullmatch(
                        rf"(\s*-\s+).+?(\s+\^{re.escape(observation_id)}(?:\s+.*)?)",
                        content,
                    )
                    if match is None:
                        raise RuntimeError("Canonical observation line is malformed")
                    line = f"{match.group(1)}{claim}{match.group(2)}{eol}"
                    changed = True
                    occurrences += 1
                rendered.append(line)
            if section is not None:
                raise RuntimeError("Unclosed generated section marker")
            if changed:
                originals[path] = current
                updates[path] = "".join(rendered)
    if required and not occurrences:
        raise RuntimeError("Promoted knowledge has no canonical generated occurrence")
    try:
        for path, text in updates.items():
            temporary = path.with_suffix(path.suffix + ".sbtmp")
            temporary.write_text(text, encoding="utf-8")
            temporary.replace(path)
    except Exception:
        for path, text in originals.items():
            path.write_text(text, encoding="utf-8")
        raise
    return originals, occurrences


def correct_project_knowledge_attribution(
    vault: Path,
    store: StateStore,
    *,
    observation_id: str,
    project: str,
    reason: str,
    replace_project_name: str | None = None,
) -> dict[str, Any]:
    """Apply one explicit owner correction to project-scoped knowledge."""

    if not reason.strip():
        raise ValueError("Project-attribution correction requires an audit reason")
    item = store.observation(observation_id)
    if item is None:
        raise KeyError(observation_id)
    if item["kind"] not in KNOWLEDGE_LAYER_KINDS["project_knowledge"]:
        raise ValueError("Only project knowledge can be reattributed")
    target = _resolve_project(store, project)
    target_id = str(target["id"])
    target_name = str(target.get("name") or target_id)
    previous_claim = str(item["claim"])
    corrected_claim = previous_claim
    if replace_project_name:
        if replace_project_name not in previous_claim:
            raise ValueError("The project name to replace is absent from the claim")
        corrected_claim = previous_claim.replace(replace_project_name, target_name)
    corrected_claim = _clean_atomic_text(
        corrected_claim,
        field="Corrected claim",
        max_chars=900,
    )
    payload = dict(item.get("payload") or {})
    previous_projects = [
        str(value) for value in payload.get("project_ids", []) if value
    ]
    if not previous_projects and payload.get("project_id"):
        previous_projects = [str(payload["project_id"])]
    corrected_at = utc_now()
    corrections = list(payload.get("project_attribution_corrections") or [])
    corrections.append(
        {
            "from_project_ids": previous_projects,
            "to_project_id": target_id,
            "previous_claim": previous_claim,
            "reason": reason.strip()[:1000],
            "corrected_at": corrected_at,
        }
    )
    payload.update(
        {
            "claim": corrected_claim,
            "project_id": target_id,
            "project_ids": [target_id],
            "project_ids_override": [target_id],
            "project_attribution_source": "explicit_owner_correction",
            "project_attribution_corrections": corrections,
        }
    )
    originals: dict[Path, str] = {}
    try:
        originals, occurrences = _rewrite_canonical_observation_claim(
            vault,
            observation_id=observation_id,
            claim=corrected_claim,
            required=item["status"] == "promoted",
        )
        with store.transaction() as connection:
            connection.execute(
                """UPDATE observations SET claim=?,project_count=1,payload_json=?,
                updated_at=? WHERE id=?""",
                (
                    corrected_claim,
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    corrected_at,
                    observation_id,
                ),
            )
            store.enqueue_search_refresh(
                connection,
                {path.relative_to(vault).as_posix() for path in originals},
            )
    except Exception:
        for path, text in originals.items():
            path.write_text(text, encoding="utf-8")
        raise
    return {
        "id": observation_id,
        "status": "corrected",
        "project": target_name,
        "claim": corrected_claim,
        "canonical_occurrences": occurrences,
    }
