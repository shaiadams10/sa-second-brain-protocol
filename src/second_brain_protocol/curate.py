from __future__ import annotations

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
