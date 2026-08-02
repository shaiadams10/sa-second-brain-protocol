from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .markdown import (
    GeneratedSectionError,
    replace_generated_section,
    slugify,
    update_generated_file,
)
from .feedback_learning import (
    knowledge_feedback_profile,
    should_suppress_candidate,
    should_suppress_review_question,
)
from .question_followup import (
    is_auto_resolvable_question,
    should_create_review_question,
)
from .project_catalog import catalog_groups, project_note_paths
from .review import GROUP_SPECS, build_review_groups, review_summary
from .security import repair_mojibake, sanitize_text, scan_text
from .state import StateStore, canonical_hash, utc_now
import yaml

from .activity import activity_markdown

FRONTMATTER = """---
id: {id}
type: {type}
aliases: []
confidence: {confidence}
provenance: []
first_seen: {today}
last_verified: {today}
tags: [{tags}]
---
"""


def _ensure_generated_note(
    path: Path,
    *,
    note_id: str,
    note_type: str,
    title: str,
    section: str = "canonical",
    confidence: float = 0.0,
    tags: str = "second-brain",
) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    path.write_text(
        FRONTMATTER.format(
            id=note_id,
            type=note_type,
            confidence=f"{confidence:.2f}",
            today=today,
            tags=tags,
        )
        + f"\n# {title}\n\n<!-- sb:generated {section}:start -->\n_No generated content yet._\n<!-- sb:generated {section}:end -->\n\n## Manual notes\n\n",
        encoding="utf-8",
    )


def _write_synthesis_summary(
    vault: Path,
    run_kind: str,
    summary: str,
    *,
    activity: str | None = None,
    period: str | None = None,
) -> Path:
    today = date.today().isoformat()
    if run_kind == "bootstrap":
        path = vault / "System" / "Audits" / "Bootstrap" / "Synthesis.md"
        section = "bootstrap-synthesis"
        note_id = "bootstrap-synthesis"
        title = "Bootstrap synthesis"
        note_type = "bootstrap-synthesis"
    elif run_kind == "daily":
        daily_id = period or today
        path = vault / "Journal" / "Daily" / f"{daily_id}.md"
        section = "daily"
        note_id = f"daily-{daily_id}"
        title = daily_id
        note_type = "daily"
    elif run_kind.startswith("project-history:"):
        project_id = slugify(run_kind.split(":", 1)[1])
        path = vault / "System" / "Audits" / "ProjectSessions" / f"{project_id}.md"
        section = "project-history"
        note_id = f"project-history-{project_id}"
        title = f"Project session analysis — {project_id}"
        note_type = "project-session-analysis"
    elif run_kind.startswith("project-refresh:"):
        project_id = slugify(run_kind.split(":", 1)[1])
        path = vault / "System" / "Audits" / "ProjectRefresh" / f"{project_id}.md"
        section = "project-refresh"
        note_id = f"project-refresh-{project_id}"
        title = f"Project refresh — {project_id}"
        note_type = "project-refresh"
    else:
        week = datetime.now().isocalendar()
        week_id = period or f"{week.year}-W{week.week:02d}"
        path = vault / "Journal" / "Weekly" / f"{week_id}.md"
        section = "weekly"
        note_id = f"weekly-{week_id}"
        title = f"Weekly reflection — {week_id}"
        note_type = "weekly"

    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\n"
            f"id: {note_id}\n"
            f"type: {note_type}\n"
            f"created: {utc_now()}\n"
            "---\n\n"
            f"# {title}\n\n"
            "## Personal notes\n\n"
            "## Automated summary\n\n"
            f"<!-- sb:generated {section}:start -->\n"
            "No automated run yet.\n"
            f"<!-- sb:generated {section}:end -->\n",
            encoding="utf-8",
        )
    generated = sanitize_text(summary)
    if activity and run_kind in {"daily", "weekly"}:
        generated = "### Quick activity recap\n\n" + generated
        generated += "\n\n" + activity.rstrip()
    elif activity:
        generated = (
            activity.rstrip() + "\n\n### Evidence-backed synthesis\n\n" + generated
        )
    update_generated_file(path, section, generated)
    return path


def _ensure_index_markers(path: Path, section: str, heading: str) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {heading}\n", encoding="utf-8")
    text = path.read_text(encoding="utf-8")
    start = f"<!-- sb:generated {section}:start -->"
    end = f"<!-- sb:generated {section}:end -->"
    if start in text or end in text:
        return
    suffix = "" if text.endswith("\n") else "\n"
    path.write_text(
        text + suffix + f"\n{start}\n_No generated entries yet._\n{end}\n",
        encoding="utf-8",
    )


def _update_journal_index(vault: Path, kind: str) -> Path:
    folder_name = "Daily" if kind == "daily" else "Weekly"
    folder = vault / "Journal" / folder_name
    path = folder / "Index.md"
    section = f"{kind}-index"
    _ensure_index_markers(path, section, f"{folder_name} Notes")
    pattern = "20??-??-??.md" if kind == "daily" else "20??-W??.md"
    notes = sorted(folder.glob(pattern), key=lambda item: item.stem, reverse=True)
    label = "Daily summary" if kind == "daily" else "Weekly reflection"
    body = (
        "\n".join(
            f"- [[Journal/{folder_name}/{note.stem}|{note.stem}]] — {label}"
            for note in notes
        )
        or "_No generated entries yet._"
    )
    update_generated_file(path, section, body)
    return path


def refresh_journal_index(vault: Path, kind: str) -> Path:
    """Refresh the bounded Daily or Weekly index after governed publication."""

    if kind not in {"daily", "weekly"}:
        raise ValueError("Journal index kind must be daily or weekly")
    return _update_journal_index(vault, kind)


def _weekly_stewardship_markdown(vault: Path, store: StateStore) -> str:
    coverage = store.session_coverage()
    pending_review = len(store.observations("pending"))
    pending_evidence = store.evidence_count(status="new")
    failures = [
        item for item in store.pipeline_runs(limit=14) if item.get("status") == "failed"
    ]
    missing = store.missing_projects()
    daily_notes = list((vault / "Journal" / "Daily").glob("20??-??-??.md"))
    weekly_notes = list((vault / "Journal" / "Weekly").glob("20??-W??.md"))
    attributed = int(coverage.get("attributed") or 0)
    total = int(coverage.get("total") or 0)
    unattributed = int(coverage.get("unattributed") or 0)
    lines = [
        "### Second-brain stewardship",
        "",
        f"- Session attribution: {attributed} of {total} indexed sessions linked; {unattributed} need attribution.",
        f"- Review and ingestion: {pending_review} review items; {pending_evidence} evidence records waiting.",
        f"- Project catalog: {len(store.present_projects())} present; {len(missing)} currently missing.",
        f"- Journal continuity: {len(daily_notes)} daily notes and {len(weekly_notes)} weekly notes; generated indexes refreshed.",
        f"- Pipeline reliability: {len(failures)} recent failed run{'s' if len(failures) != 1 else ''} retained for audit.",
        "",
        "#### Stewardship actions",
        "",
    ]
    actions = []
    if unattributed:
        actions.append(
            f"Reconcile {unattributed} unattributed sessions so future daily and weekly insight coverage is complete."
        )
    if failures:
        actions.append(
            "Review recent failed pipeline stages and confirm their evidence was recovered by a later run."
        )
    if pending_review:
        actions.append(
            f"Curate {pending_review} pending observations so ambiguous knowledge does not go stale."
        )
    if missing:
        actions.append(
            f"Confirm whether {len(missing)} missing project{'s' if len(missing) != 1 else ''} moved, disconnected, or should remain inactive."
        )
    if not actions:
        actions.append("No stewardship exception needs attention this week.")
    lines.extend(f"- {sanitize_text(action, max_chars=500)}" for action in actions)
    return "\n".join(lines)


def _write_stewardship_note(vault: Path, body: str) -> Path:
    path = vault / "System" / "Stewardship.md"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\ntype: system-stewardship\n---\n\n# Second-brain stewardship\n\n"
            "<!-- sb:generated stewardship:start -->\n"
            "_No weekly stewardship run yet._\n"
            "<!-- sb:generated stewardship:end -->\n\n## Manual notes\n\n",
            encoding="utf-8",
        )
    update_generated_file(path, "stewardship", body)
    return path


def _learning_markdown(output: dict[str, Any]) -> str:
    lines = ["### What the brain learned", ""]
    learning_items: list[str] = []
    person_items: list[str] = []
    project_items: list[str] = []
    capability_items: list[str] = []
    pattern_items: list[str] = []
    project_kinds = {"project_fact", "decision", "lesson"}
    for signal in output.get("learning_signals", []):
        claim = sanitize_text(str(signal.get("claim") or ""), max_chars=700).strip()
        if claim:
            signal_type = str(signal.get("signal_type") or "learning").replace("_", " ")
            learning_items.append(
                f"- Learning - {signal.get('label', 'Topic')} ({signal_type}): {claim}"
            )
    for observation in output.get("observations", []):
        label = str(observation.get("kind") or "insight").replace("_", " ").title()
        claim = sanitize_text(
            str(observation.get("claim") or ""), max_chars=700
        ).strip()
        if claim:
            item = f"- {label}: {claim}"
            if observation.get("kind") in project_kinds:
                project_items.append(item)
            else:
                person_items.append(item)
    for skill in output.get("skill_updates", []):
        claim = sanitize_text(str(skill.get("claim") or ""), max_chars=700).strip()
        if claim:
            capability_items.append(
                f"- Skill - {skill.get('name', 'Capability')}: {claim}"
            )
    for pattern in output.get("pattern_signals", []):
        claim = sanitize_text(str(pattern.get("claim") or ""), max_chars=700).strip()
        if claim:
            pattern_items.append(
                f"- Pattern - {pattern.get('label', 'Working pattern')}: {claim}"
            )
    # Keep person-level learning visible even on project-heavy days.
    items = (
        learning_items + person_items + pattern_items + capability_items + project_items
    )
    if not items:
        items.append(
            "- No new durable personal, skill, voice, or work-pattern insight passed the evidence gates."
        )
    lines.extend(items[:12])
    return "\n".join(lines)


def _evidence_dimensions(
    store: StateStore, refs: list[str]
) -> tuple[int, int, int, int]:
    rows = store.evidence_by_ids(refs)
    sources = {
        row["source_ref"].split(":", 1)[0] + ":" + row["source_ref"].split(":")[1]
        for row in rows
        if ":" in row["source_ref"]
    }
    projects = {row["project_id"] for row in rows if row.get("project_id")}
    for row in rows:
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        projects.update(str(item) for item in payload.get("project_ids", []) if item)
    dates = {str(row.get("occurred_at") or row["created_at"])[:10] for row in rows}
    sessions = {
        str(row["payload"].get("session_id"))
        for row in rows
        if isinstance(row.get("payload"), dict) and row["payload"].get("session_id")
    }
    return len(sources), len(projects), len(dates), len(sessions)


def _evidence_project_ids(store: StateStore, refs: list[str]) -> set[str]:
    projects: set[str] = set()
    for row in store.evidence_by_ids(refs):
        if row.get("project_id"):
            projects.add(str(row["project_id"]))
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        projects.update(str(item) for item in payload.get("project_ids", []) if item)
    return projects


def _evidence_context_keys(store: StateStore, refs: list[str]) -> set[str]:
    contexts = {
        f"project:{project_id}" for project_id in _evidence_project_ids(store, refs)
    }
    for row in store.evidence_by_ids(refs):
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if (
            row.get("source_type") == "session-digest"
            and row.get("kind") == "session_digest"
            and not row.get("project_id")
            and not payload.get("project_ids")
            and payload.get("session_id")
        ):
            context_hash = canonical_hash(
                {
                    "source": payload.get("source"),
                    "session_id": payload.get("session_id"),
                }
            )[:16]
            contexts.add(f"profile:{context_hash}")
    return contexts


def _profile_only_session_refs(store: StateStore, refs: list[str]) -> set[str]:
    profile_only: set[str] = set()
    for row in store.evidence_by_ids(refs):
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if (
            row.get("source_type") == "session-digest"
            and row.get("kind") == "session_digest"
            and (
                payload.get("analysis_lane") == "profile_only"
                or (not row.get("project_id") and not payload.get("project_ids"))
            )
        ):
            profile_only.add(str(row["id"]))
    return profile_only


def _validate_evidence_lane_isolation(
    store: StateStore,
    output: dict[str, Any],
    *,
    evidence_ids: list[str],
) -> None:
    """Prevent unattributed session evidence from leaking into project knowledge."""

    allowed_ids = set(evidence_ids)

    def refs(item: dict[str, Any]) -> list[str]:
        return [str(value) for value in item.get("evidence_refs", [])]

    for item in output.get("project_updates", []):
        item_refs = refs(item)
        if _profile_only_session_refs(store, item_refs):
            raise ValueError(
                "Profile-only session evidence cannot support a project update"
            )

    for item in output.get("session_summaries", []):
        evidence_ref = str(item.get("evidence_ref") or "")
        if evidence_ref in allowed_ids and _profile_only_session_refs(
            store, [evidence_ref]
        ):
            raise ValueError(
                "Profile-only sessions cannot be emitted as project session summaries"
            )

    for item in output.get("skill_updates", []):
        if _profile_only_session_refs(store, refs(item)):
            raise ValueError(
                "Profile-only session evidence must use learning signals, not skill updates"
            )

    for item in output.get("observations", []):
        item_refs = refs(item)
        if not _profile_only_session_refs(store, item_refs):
            continue
        if item.get("kind") in {"project_fact", "decision", "lesson"}:
            raise ValueError(
                "Profile-only session evidence cannot support project facts, decisions, or lessons"
            )
        if item.get("scope") == "project":
            raise ValueError(
                "Profile-only session evidence cannot support project-scoped observations"
            )

    for item in output.get("pattern_signals", []):
        if (
            _profile_only_session_refs(store, refs(item))
            and item.get("scope") == "project"
        ):
            raise ValueError(
                "Profile-only session patterns must use context or global scope"
            )

    for item in output.get("learning_signals", []):
        if item.get(
            "signal_type"
        ) == "validated_outcome" and _profile_only_session_refs(store, refs(item)):
            raise ValueError(
                "A validated learning outcome requires attributed project evidence"
            )

    for item in output.get("question_resolutions", []):
        if _profile_only_session_refs(store, refs(item)):
            raise ValueError(
                "Profile-only session evidence cannot resolve project questions"
            )


def _promotion_status(
    store: StateStore, observation: dict[str, Any]
) -> tuple[str, str]:
    source_count, project_count, date_count, session_count = _evidence_dimensions(
        store, observation["evidence_refs"]
    )
    observation["source_count"] = source_count
    observation["project_count"] = project_count
    kind = observation["kind"]
    scope = str(observation.get("scope") or "").casefold()
    pattern_kind = kind in {"work_style", "voice_style", "personality", "preference"}
    if observation.get("public_claim"):
        return "pending", "public-facing claim requires review"
    existing = [
        row
        for row in store.observations()
        if row["kind"] == kind
        and row["subject"] == observation["subject"]
        and row["status"] in {"approved", "promoted"}
    ]
    if any(
        row["claim"].strip().casefold() != observation["claim"].strip().casefold()
        for row in existing
    ):
        return "pending", "conflicts with an existing canonical claim"
    evidence_rows = store.evidence_by_ids(observation["evidence_refs"])
    explicit_user_evidence = any(
        row["source_type"] in {"interview", "linkedin"}
        or (
            isinstance(row.get("payload"), dict)
            and row["payload"].get("role") == "user"
        )
        for row in evidence_rows
    )
    if (
        observation.get("explicit")
        and explicit_user_evidence
        and kind not in {"experience", "education", "military", "project_fact"}
        and (
            not pattern_kind
            or scope == "global"
            or any(
                row["source_type"] in {"interview", "linkedin"} for row in evidence_rows
            )
        )
    ):
        return "promoted", "explicit non-conflicting user fact"
    if kind == "project_fact" and (
        observation.get("authoritative") or source_count >= 2
    ):
        return "promoted", "authoritative or corroborated project fact"
    if pattern_kind:
        context_count = len(_evidence_context_keys(store, observation["evidence_refs"]))
        if (
            observation.get("explicit") and scope == "global" and explicit_user_evidence
        ) or (session_count >= 3 and date_count >= 2 and context_count >= 2):
            return "promoted", "stable multi-session, multi-context pattern"
        return (
            "pending",
            "contextual or insufficient cross-session pattern evidence",
        )
    if kind in {"experience", "education", "military"}:
        trusted = any(
            row["source_type"] in {"linkedin", "interview"} for row in evidence_rows
        )
        if trusted and observation.get("explicit"):
            return "promoted", "explicit profile/interview evidence"
        return "pending", "career and service history requires explicit evidence"
    return "pending", "manual review tier"


def _observation_note(vault: Path, kind: str) -> Path:
    mapping = {
        "explicit_fact": vault / "Memory" / "LongTermMemory.md",
        "project_fact": vault / "Memory" / "LongTermMemory.md",
        "decision": vault / "Memory" / "Decisions.md",
        "lesson": vault / "Memory" / "Lessons.md",
        "preference": vault / "Identity" / "Preferences.md",
        "work_style": vault / "Identity" / "WorkStyle.md",
        "voice_style": vault / "Identity" / "Voice.md",
        "personality": vault / "Identity" / "Persona.md",
        "experience": vault / "Experience" / "Employment.md",
        "education": vault / "Experience" / "Education.md",
        "military": vault / "Experience" / "MilitaryService.md",
        "goal": vault / "Goals" / "ActiveGoals.md",
        "skill": vault / "Identity" / "Capabilities.md",
    }
    return mapping[kind]


def _append_generated_bullet(path: Path, text: str) -> None:
    if path.name == "Index.md" and path.parent.name == "Projects":
        # The project catalog is rebuilt from scanner truth. Model-authored
        # names must never append aliases or stale identities to it.
        return
    current = path.read_text(encoding="utf-8")
    sections = re.findall(r"<!-- sb:generated ([a-z0-9-]+):start -->", current)
    if len(sections) != 1:
        raise GeneratedSectionError(
            f"Expected one generated section in {path}; found {sections}"
        )
    section = sections[0]
    start = f"<!-- sb:generated {section}:start -->"
    end = f"<!-- sb:generated {section}:end -->"
    if start not in current or end not in current:
        raise GeneratedSectionError(f"Malformed generated markers in {path}")
    body = current.split(start, 1)[1].split(end, 1)[0].strip()
    placeholders = {
        "",
        "_No generated content yet._",
        "No approved employment records yet.",
        "No approved education records yet.",
        "Approved voice evidence will appear here.",
        "Verified capabilities will appear here.",
        "Bootstrap has not been approved yet.",
        "No suggestions yet.",
    }
    lines = [] if body in placeholders else body.splitlines()
    if text not in lines:
        lines.append(text)
    update_generated_file(path, section, "\n".join(lines))


def _upsert_generated_bullet(path: Path, text: str, *, match_token: str) -> None:
    current = path.read_text(encoding="utf-8")
    sections = re.findall(r"<!-- sb:generated ([a-z0-9-]+):start -->", current)
    if len(sections) != 1:
        raise GeneratedSectionError(
            f"Expected one generated section in {path}; found {sections}"
        )
    section = sections[0]
    start = f"<!-- sb:generated {section}:start -->"
    end = f"<!-- sb:generated {section}:end -->"
    if start not in current or end not in current:
        raise GeneratedSectionError(f"Malformed generated markers in {path}")
    body = current.split(start, 1)[1].split(end, 1)[0].strip()
    placeholders = {
        "",
        "_No generated content yet._",
        "Verified capabilities will appear here.",
    }
    lines = [] if body in placeholders else body.splitlines()
    lines = [line for line in lines if match_token not in line]
    lines.append(text)
    update_generated_file(path, section, "\n".join(lines))


PATTERN_KIND_TO_OBSERVATION = {
    "preference": "preference",
    "work_style": "work_style",
    "voice_style": "voice_style",
    "personality": "personality",
    "protocol_preference": "preference",
}


def _publish_pattern_signals(
    *,
    vault: Path,
    store: StateStore,
    signals: list[dict[str, Any]],
    evidence_ids: list[str],
) -> dict[str, int]:
    stats = {"tracking": 0, "promoted": 0, "pending": 0, "rejected": 0}
    allowed_ids = set(evidence_ids)
    for signal in signals:
        unknown_refs = set(signal["evidence_refs"]) - allowed_ids
        if unknown_refs:
            raise ValueError(
                f"Model invented pattern evidence references: {sorted(unknown_refs)}"
            )
        existing = store.pattern_signal(signal["pattern_key"])
        if existing and existing["status"] == "rejected":
            stats["rejected"] += 1
            continue
        merged_refs = sorted(
            set((existing or {}).get("evidence_refs", []))
            | set(signal["evidence_refs"])
        )[:100]
        kind_conflict = bool(existing and existing["kind"] != signal["kind"])
        kind = existing["kind"] if existing else signal["kind"]
        claim = existing["claim"] if existing else signal["claim"]
        label = existing["label"] if existing else signal["label"]
        confidence = max(
            float((existing or {}).get("confidence", 0.0)),
            float(signal["confidence"]),
        )
        explicit = bool((existing or {}).get("explicit") or signal.get("explicit"))
        existing_scope = str(((existing or {}).get("payload") or {}).get("scope") or "")
        signal_scope = str(signal.get("scope") or "")
        scope = (
            "global"
            if "global" in {existing_scope, signal_scope}
            else (
                "context"
                if "context" in {existing_scope, signal_scope}
                else (signal_scope or existing_scope or "project")
            )
        )
        source_count, project_count, date_count, session_count = _evidence_dimensions(
            store, merged_refs
        )
        context_count = len(_evidence_context_keys(store, merged_refs))
        status = "tracking"
        observation_id = (existing or {}).get("observation_id")
        rejection_reason = None
        if kind_conflict:
            status = "conflict"
            clarification_id = store.add_observation(
                {
                    "kind": "clarification",
                    "subject": f"Recurring pattern {signal['pattern_key']}",
                    "claim": (
                        f"The recurring-pattern key {signal['pattern_key']} was emitted as both "
                        f"{existing['kind']} and {signal['kind']}. Which category is correct?"
                    ),
                    "evidence_refs": merged_refs,
                    "confidence": confidence,
                    "source_count": source_count,
                    "project_count": project_count,
                    "sensitivity": "normal",
                    "promotion_tier": "clarification",
                    "status": "pending",
                    "review_reason": "pattern-kind-conflict",
                }
            )
            observation_id = clarification_id
            stats["pending"] += 1
        elif (explicit and scope == "global") or (
            session_count >= 3 and date_count >= 2 and context_count >= 2
        ):
            observation = {
                "kind": PATTERN_KIND_TO_OBSERVATION[kind],
                "subject": f"pattern:{signal['pattern_key']}",
                "claim": claim,
                "evidence_refs": merged_refs[:30],
                "confidence": confidence,
                "explicit": explicit,
                "scope": scope,
                "public_claim": False,
                "authoritative": False,
            }
            observation_status, reason = _promotion_status(store, observation)
            observation.update(
                {
                    "status": observation_status,
                    "sensitivity": "normal",
                    "promotion_tier": (
                        "automatic" if observation_status == "promoted" else "review"
                    ),
                    "review_reason": reason,
                }
            )
            observation_id = store.add_observation(observation)
            stored = store.observation(observation_id)
            status = stored["status"] if stored else observation_status
            if status in {"promoted", "approved"}:
                note = _observation_note(vault, observation["kind"])
                _append_generated_bullet(
                    note,
                    f"- {sanitize_text(claim)} ^{observation_id}",
                )
                _update_frontmatter(
                    note,
                    evidence_refs=observation["evidence_refs"],
                    confidence=confidence,
                )
                stats["promoted"] += 1
            elif status in {"rejected", "resolved"}:
                stats["rejected"] += 1
            else:
                stats["pending"] += 1
        else:
            stats["tracking"] += 1

        record = {
            "pattern_key": signal["pattern_key"],
            "kind": kind,
            "label": label,
            "claim": claim,
            "evidence_refs": merged_refs,
            "confidence": confidence,
            "explicit": explicit,
            "scope": scope,
            "source_count": source_count,
            "project_count": project_count,
            "context_count": context_count,
            "date_count": date_count,
            "session_count": session_count,
            "status": status,
            "observation_id": observation_id,
            "rejection_reason": rejection_reason,
            "first_seen": (existing or {}).get("first_seen") or utc_now(),
            "last_seen": utc_now(),
        }
        store.upsert_pattern_signal(record)
        pattern_payload = {
            "pattern_key": record["pattern_key"],
            "kind": record["kind"],
            "label": record["label"],
            "claim": record["claim"],
            "confidence": record["confidence"],
            "session_count": record["session_count"],
            "date_count": record["date_count"],
            "project_count": record["project_count"],
            "context_count": record["context_count"],
            "status": record["status"],
            "evidence_refs": merged_refs,
        }
        pattern_evidence_id, _added = store.add_evidence(
            source_type="pattern-registry",
            source_ref=(
                f"recurring-pattern:{record['pattern_key']}:"
                f"{canonical_hash(pattern_payload)[:20]}"
            ),
            kind="recurring_pattern",
            payload=pattern_payload,
        )
        store.mark_evidence([pattern_evidence_id], "processed")
    return stats


LEARNING_PROGRESS_TYPES = {
    "demonstrated_understanding",
    "applied_learning",
    "architectural_judgment",
    "operational_capability",
    "validated_outcome",
}

LEARNING_STATE_LABELS = {
    "exploring": "Exploring",
    "developing": "Developing understanding",
    "demonstrated": "Understanding demonstrated",
    "applied": "Applied in practice",
    "verified": "Verified through a validated outcome",
    "mixed": "Mixed evidence / active learning edge",
}


def _learning_event_record(store: StateStore, signal: dict[str, Any]) -> dict[str, Any]:
    refs = [str(value) for value in signal["evidence_refs"]]
    rows = store.evidence_by_ids(refs)
    project_ids = sorted(_evidence_project_ids(store, refs))
    context_keys = sorted(_evidence_context_keys(store, refs))
    occurred_at = max(
        (
            str(row.get("occurred_at") or row.get("created_at") or utc_now())
            for row in rows
        ),
        default=utc_now(),
    )
    return {
        **signal,
        "evidence_refs": refs,
        "project_ids": project_ids,
        "context_keys": context_keys,
        "occurred_at": occurred_at,
    }


def _rebuild_learning_topic(store: StateStore, topic_key: str) -> dict[str, Any]:
    events = store.learning_signal_events(topic_key)
    if not events:
        raise RuntimeError(f"Learning topic has no events: {topic_key}")
    refs = sorted(
        {evidence_ref for event in events for evidence_ref in event["evidence_refs"]}
    )[:200]
    _source_count, project_count, date_count, session_count = _evidence_dimensions(
        store, refs
    )
    context_count = len(_evidence_context_keys(store, refs))
    counts = Counter(str(event["signal_type"]) for event in events)
    ordered = sorted(events, key=lambda item: (item["occurred_at"], item["id"]))
    positive = [
        event for event in ordered if event["signal_type"] in LEARNING_PROGRESS_TYPES
    ]
    edges = [event for event in ordered if event["signal_type"] == "learning_edge"]
    counterevidence = [
        event for event in ordered if event["signal_type"] == "counterevidence"
    ]
    latest_edge = edges[-1] if edges else None
    progress_after_edge = [
        event
        for event in positive
        if latest_edge is None or event["occurred_at"] > latest_edge["occurred_at"]
    ]
    latest_progress = positive[-1] if positive else None
    open_edge = bool(latest_edge and not progress_after_edge)

    applicable = progress_after_edge if latest_edge else positive
    applicable_types = {str(event["signal_type"]) for event in applicable}
    if "validated_outcome" in applicable_types:
        current_state = "verified"
    elif applicable_types & {"applied_learning", "operational_capability"}:
        current_state = "applied"
    elif applicable_types & {
        "demonstrated_understanding",
        "architectural_judgment",
    }:
        current_state = "demonstrated"
    elif open_edge and positive:
        current_state = "mixed"
    elif open_edge:
        current_state = "exploring"
    else:
        current_state = "developing"

    latest_counter = counterevidence[-1] if counterevidence else None
    if (
        latest_counter
        and latest_progress
        and latest_counter["occurred_at"] > latest_progress["occurred_at"]
    ):
        current_state = "mixed"

    assessment_event = (
        latest_edge if open_edge and latest_edge else (latest_progress or ordered[-1])
    )
    record = {
        "topic_key": topic_key,
        "label": str(ordered[-1]["label"]),
        "current_state": current_state,
        "assessment": str(assessment_event["claim"]),
        "evidence_refs": refs,
        "confidence": max(float(event["confidence"]) for event in events),
        "session_count": session_count,
        "date_count": date_count,
        "project_count": project_count,
        "context_count": context_count,
        "signal_counts": dict(sorted(counts.items())),
        "open_learning_edge": open_edge,
        "first_seen": ordered[0]["occurred_at"],
        "last_seen": ordered[-1]["occurred_at"],
        "last_progress_at": (
            latest_progress["occurred_at"] if latest_progress else None
        ),
    }
    store.upsert_learning_topic(record)
    return record


def _write_learning_tracker(vault: Path, store: StateStore) -> Path:
    path = vault / "Memory" / "Learning.md"
    _ensure_generated_note(
        path,
        note_id="learning",
        note_type="learning-tracker",
        title="Learning and demonstrated understanding",
        section="learning",
        tags="memory, learning, second-brain",
    )
    topics = store.visible_learning_topics()
    if not topics:
        body = "Learning signals will appear here as sessions are evaluated over time."
    else:
        sections: list[str] = []
        for topic in topics:
            profile_contexts = max(
                0, int(topic["context_count"]) - int(topic["project_count"])
            )
            counts = ", ".join(
                f"{name.replace('_', ' ')}: {count}"
                for name, count in topic["signal_counts"].items()
            )
            breadth = (
                f"{topic['session_count']} sessions, {topic['date_count']} dates, "
                f"{topic['project_count']} known projects"
            )
            if profile_contexts:
                breadth += f", {profile_contexts} profile-only contexts"
            sections.extend(
                [
                    f"## {sanitize_text(str(topic['label']), max_chars=160)}",
                    "",
                    f"- **Current state:** {LEARNING_STATE_LABELS.get(str(topic['current_state']), str(topic['current_state']).replace('_', ' ').title())}",
                    f"- **Current assessment:** {sanitize_text(str(topic['assessment']), max_chars=1200)}",
                    f"- **Evidence breadth:** {breadth}",
                    f"- **Signal history:** {counts or 'No classified signals'}",
                    f"- **Observed:** {str(topic['first_seen'])[:10]} to {str(topic['last_seen'])[:10]}",
                ]
            )
            if topic["open_learning_edge"]:
                sections.append(
                    "- **Open edge:** Current evidence still shows an unresolved learning question; this is not treated as a permanent limitation."
                )
            sections.append("")
        body = "\n".join(sections).rstrip()
    update_generated_file(path, "learning", body)
    all_refs = sorted(
        {evidence_ref for topic in topics for evidence_ref in topic["evidence_refs"]}
    )
    if all_refs:
        _update_frontmatter(path, evidence_refs=all_refs, confidence=0.8)
    return path


def refresh_learning_tracker(vault: Path, store: StateStore) -> Path:
    """Rebuild the canonical topic view after an explicit owner correction."""

    return _write_learning_tracker(vault, store)


def _publish_learning_signals(
    *,
    vault: Path,
    store: StateStore,
    signals: list[dict[str, Any]],
    evidence_ids: list[str],
) -> dict[str, Any]:
    if not signals:
        path = vault / "Memory" / "Learning.md"
        return {
            "events_added": 0,
            "topics_updated": 0,
            "profile_only_events": 0,
            "path": str(path) if path.exists() else None,
        }
    allowed_ids = set(evidence_ids)
    updated_topics: set[str] = set()
    events_added = 0
    profile_only_events = 0
    for signal in signals:
        unknown_refs = set(signal["evidence_refs"]) - allowed_ids
        if unknown_refs:
            raise ValueError(
                f"Model invented learning evidence references: {sorted(unknown_refs)}"
            )
        supporting_rows = store.evidence_by_ids(signal["evidence_refs"])
        if not any(
            row.get("kind") == "session_digest" or row.get("source_type") == "interview"
            for row in supporting_rows
        ):
            raise ValueError(
                "Learning signals require new session or explicit interview evidence"
            )
        event = _learning_event_record(store, signal)
        _event_id, added = store.add_learning_signal_event(event)
        events_added += int(added)
        profile_only_events += int(
            bool(_profile_only_session_refs(store, event["evidence_refs"]))
        )
        updated_topics.add(str(signal["topic_key"]))
    for topic_key in sorted(updated_topics):
        topic = _rebuild_learning_topic(store, topic_key)
        registry_payload = {
            "topic_key": topic["topic_key"],
            "label": topic["label"],
            "current_state": topic["current_state"],
            "assessment": topic["assessment"],
            "session_count": topic["session_count"],
            "date_count": topic["date_count"],
            "project_count": topic["project_count"],
            "context_count": topic["context_count"],
            "signal_counts": topic["signal_counts"],
            "open_learning_edge": topic["open_learning_edge"],
            "first_seen": topic["first_seen"],
            "last_seen": topic["last_seen"],
        }
        learning_evidence_id, _added = store.add_evidence(
            source_type="learning-registry",
            source_ref=(
                f"learning-topic:{topic_key}:{canonical_hash(registry_payload)[:20]}"
            ),
            kind="learning_topic",
            payload=registry_payload,
        )
        store.mark_evidence([learning_evidence_id], "processed")
    path = _write_learning_tracker(vault, store)
    return {
        "events_added": events_added,
        "topics_updated": len(updated_topics),
        "profile_only_events": profile_only_events,
        "path": str(path),
    }


def _upsert_generated_profile_bullet(path: Path, label: str, answer: str) -> None:
    current = path.read_text(encoding="utf-8")
    sections = re.findall(r"<!-- sb:generated ([a-z0-9-]+):start -->", current)
    if len(sections) != 1:
        raise GeneratedSectionError(
            f"Expected one generated section in {path}; found {sections}"
        )
    section = sections[0]
    start = f"<!-- sb:generated {section}:start -->"
    end = f"<!-- sb:generated {section}:end -->"
    body = current.split(start, 1)[1].split(end, 1)[0].strip()
    placeholder_prefixes = (
        "No approved ",
        "Approved voice ",
        "Verified capabilities ",
        "Bootstrap has not ",
        "No suggestions yet.",
    )
    lines = (
        [] if not body or body.startswith(placeholder_prefixes) else body.splitlines()
    )
    prefix = f"- **{label}:**"
    lines = [line for line in lines if not line.startswith(prefix)]
    lines.append(f"{prefix} {sanitize_text(answer, max_chars=12000)}")
    update_generated_file(path, section, "\n".join(lines))


INTERVIEW_PROFILE_TARGETS = {
    "preferred_name": ("Identity/Persona.md", "Preferred name"),
    "current_work": ("Identity/Persona.md", "Current work"),
    "employment": ("Experience/Employment.md", "Verified employment history"),
    "higher_education": ("Experience/Education.md", "Higher education and training"),
    "high_school": ("Experience/Education.md", "High school"),
    "military": ("Experience/MilitaryService.md", "Military service"),
    "accomplishments": ("Identity/Capabilities.md", "Representative accomplishments"),
    "services": ("Goals/ActiveGoals.md", "Target work and role scope"),
    "preferred_work": ("Identity/WorkStyle.md", "Preferred working conditions"),
    "voice": ("Identity/Voice.md", "Writing voice"),
    "values": ("Identity/Preferences.md", "Values"),
    "timeline_gaps": ("Experience/Timeline.md", "Verified timeline"),
}


def publish_interview_profile(
    vault: Path, store: StateStore, runtime_root: Path
) -> dict[str, Any]:
    interview_path = runtime_root / "interview.json"
    if not interview_path.exists():
        return {"answers_published": 0, "complete": False}
    data = json.loads(interview_path.read_text(encoding="utf-8"))
    answers = data.get("answers", {})
    published = 0
    for question_id, (relative, label) in INTERVIEW_PROFILE_TARGETS.items():
        answer = answers.get(question_id)
        if not answer:
            continue
        evidence = store.latest_evidence_by_source(
            source_type="interview", source_ref=f"interview:{question_id}:v1"
        )
        if evidence is None:
            raise RuntimeError(f"Missing explicit interview evidence for {question_id}")
        path = vault / relative
        _upsert_generated_profile_bullet(path, label, answer)
        _update_frontmatter(path, evidence_refs=[evidence["id"]], confidence=1.0)
        published += 1
    return {"answers_published": published, "complete": bool(data.get("complete"))}


def repair_generated_markdown(vault: Path) -> int:
    repaired = 0
    roots = (
        "Identity",
        "Experience",
        "Projects",
        "Skills",
        "Memory",
        "Goals",
        "Journal",
        "System/Audits/Bootstrap",
    )
    for relative in roots:
        root = vault / relative
        if not root.exists():
            continue
        for path in root.rglob("*.md"):
            current = path.read_text(encoding="utf-8")
            updated = current
            sections = re.findall(r"<!-- sb:generated ([a-z0-9-]+):start -->", current)
            for section in sections:
                start = f"<!-- sb:generated {section}:start -->"
                end = f"<!-- sb:generated {section}:end -->"
                if updated.count(start) != 1 or updated.count(end) != 1:
                    raise GeneratedSectionError(
                        f"Malformed generated markers in {path}"
                    )
                body = updated.split(start, 1)[1].split(end, 1)[0].strip()
                updated = replace_generated_section(
                    updated, section, repair_mojibake(body)
                )
            if updated != current:
                path.write_text(updated, encoding="utf-8")
                repaired += 1
    voice_root = vault / "Evidence" / "VoiceSamples"
    if voice_root.exists():
        for path in voice_root.glob("*.md"):
            current = path.read_text(encoding="utf-8")
            updated = repair_mojibake(current)
            if updated != current:
                path.write_text(updated, encoding="utf-8")
                repaired += 1
    return repaired


def write_bootstrap_review_artifacts(vault: Path, store: StateStore) -> dict[str, Any]:
    pending = store.observations("pending")
    review_artifacts = write_review_artifacts(vault, store) if pending else {}
    review_path = review_artifacts.get("digest")
    breakdown = Counter(item["kind"] for item in pending)
    summary = review_summary(pending)
    project_count = len(
        [path for path in (vault / "Projects").glob("*.md") if path.name != "Index.md"]
    )
    skill_count = len(
        [path for path in (vault / "Skills").glob("*.md") if path.name != "Index.md"]
    )
    voice_count = len(
        [
            path
            for path in (vault / "Evidence" / "VoiceSamples").glob("*.md")
            if path.name != "Index.md"
        ]
    )
    final = vault / "System" / "Audits" / "Bootstrap" / "FinalReviewPacket.md"
    final.parent.mkdir(parents=True, exist_ok=True)
    review_link = Path(review_path).stem if review_path else "Index"
    final.write_text(
        "---\n"
        "id: bootstrap-final-review\n"
        "type: review-packet\n"
        "status: awaiting-review\n"
        "---\n\n"
        "# Bootstrap final review\n\n"
        "The one-time collection and synthesis completed. The private guided-interview answers "
        "are canonicalized separately from public-facing career wording.\n\n"
        "## Generated material\n\n"
        f"- Project dossiers: {project_count}\n"
        f"- Skill notes: {skill_count}\n"
        f"- Sanitized voice samples: {voice_count}\n"
        f"- Pending questions: {summary['needs_answers']}\n"
        f"- Pending public-facing claims: {summary['public_claims']}\n"
        f"- Pending private observations: {summary['private_review']}\n\n"
        "The pending backlog does **not** need to be cleared before bootstrap approval. "
        "Unreviewed records remain pending and cannot become canonical by themselves.\n\n"
        "## Review order\n\n"
        "1. Review [[Identity/Persona]], [[Identity/Voice]], [[Identity/Capabilities]], and [[Identity/WorkStyle]].\n"
        "2. Review [[Experience/Employment]], [[Experience/Education]], [[Experience/MilitaryService]], and [[Experience/Timeline]].\n"
        "3. Spot-check [[Projects/Index]] and [[Skills/Index]].\n"
        f"4. Open [[Inbox/Review/{review_link}|the short review dashboard]] and check only the groups that matter now.\n\n"
        "## Approval gate\n\n"
        "Do not run `sb bootstrap --approve` until the user explicitly approves this packet. "
        "No private push, public draft PR, or scheduled task occurs before approval.\n",
        encoding="utf-8",
    )
    return {
        "review_path": str(review_path) if review_path else None,
        "final_review": str(final),
        "pending": len(pending),
        "breakdown": dict(breakdown),
        "review_ledger": review_artifacts.get("ledger"),
        "review_groups": review_artifacts.get("groups", []),
    }


def _update_frontmatter(
    path: Path, *, evidence_refs: list[str], confidence: float
) -> None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise GeneratedSectionError(f"Missing frontmatter in {path}")
    end = text.index("\n---\n", 4)
    raw = text[4:end]
    data = yaml.safe_load(raw) or {}
    refs = sorted(set((data.get("provenance") or []) + evidence_refs))
    today = date.today().isoformat()
    fields = {
        "confidence": max(float(data.get("confidence") or 0), float(confidence)),
        "provenance": refs,
        "first_seen": data.get("first_seen") or today,
        "last_verified": today,
    }
    lines = raw.splitlines()
    for key, value in fields.items():
        rendered = (
            json.dumps(value, ensure_ascii=False)
            if isinstance(value, list)
            else str(value)
        )
        match = next(
            (index for index, line in enumerate(lines) if line.startswith(f"{key}:")),
            None,
        )
        if match is None:
            lines.append(f"{key}: {rendered}")
        else:
            lines[match] = f"{key}: {rendered}"
    updated = "---\n" + "\n".join(lines) + text[end:]
    path.write_text(updated, encoding="utf-8")


def _review_group_body(group: dict[str, Any]) -> str:
    lines = [
        group["description"],
        "",
        f"**Pending items:** {group['count']}",
        "",
        f"**Easy response guidance:** {group['answer_template']}",
        "",
    ]
    if group["mode"] == "answer":
        lines.extend(
            [
                "These are questions, not claims. They cannot be approved as a group. "
                "Answer only the ones you know; saying `defer` is valid.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "After reading every item on this page, you may make one explicit batch decision:",
                "",
                f"- Approve all: `sb review approve-group {group['id']}`",
                f'- Reject all: `sb review reject-group {group["id"]} --reason "..."`',
                "- Do nothing: the group stays pending.",
                "",
                "The token includes a snapshot of this group's membership. It becomes invalid if new items enter the group.",
                "",
            ]
        )

    for index, item in enumerate(group["items"], 1):
        reason = item["payload"].get("review_reason", "review required")
        lines.extend(
            [
                f"## {index}. {sanitize_text(item['subject'], max_chars=200)}",
                "",
            ]
        )
        if group["mode"] == "answer":
            question = item["payload"].get("question") or item["claim"]
            lines.extend(
                [
                    f"**Question:** {sanitize_text(question, max_chars=3000)}",
                    "",
                    f'Answer with: `sb review answer {group["id"]} {index} --answer "..."`',
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    sanitize_text(item["claim"], max_chars=5000),
                    "",
                    "**Decision:** approve, reject with a reason, or leave pending.",
                    "",
                ]
            )
        lines.extend(
            [
                "<details>",
                "<summary>Technical provenance</summary>",
                "",
                f"- Observation: `{item['id']}`",
                f"- Evidence: {', '.join(f'`{ref}`' for ref in item['evidence_refs'])}",
                f"- Confidence: {item['confidence']:.2f}",
                f"- Sources: {item['source_count']}",
                f"- Projects: {item['project_count']}",
                f"- Sensitivity: {item['sensitivity']}",
                f"- Review reason: {reason}",
                "",
                "</details>",
                "",
            ]
        )
    return "\n".join(lines).rstrip()


def _write_review_group(path: Path, group: dict[str, Any]) -> None:
    section = "review-group"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\n"
            f"id: review-group-{group['key']}\n"
            "type: review-group\n"
            "generated_by: second-brain-protocol\n"
            "---\n\n"
            f"# {group['title']}\n\n"
            f"<!-- sb:generated {section}:start -->\n"
            "_No pending items._\n"
            f"<!-- sb:generated {section}:end -->\n\n"
            "## Manual notes\n\n",
            encoding="utf-8",
        )
    update_generated_file(path, section, _review_group_body(group))


def _write_review_ledger(vault: Path, pending: list[dict[str, Any]]) -> Path:
    path = vault / "Inbox" / "Review" / f"Ledger-{date.today().isoformat()}.md"
    lines = [
        "---",
        f"id: review-ledger-{date.today().isoformat()}",
        "type: review-ledger",
        "generated_by: second-brain-protocol",
        f"generated_at: {utc_now()}",
        "---",
        "",
        f"# Machine review ledger — {date.today().isoformat()}",
        "",
        "This is the complete provenance ledger. Normal human review starts from the review dashboard, not this file.",
        "",
    ]
    for item in pending:
        reason = item["payload"].get("review_reason", "review required")
        lines.extend(
            [
                f"## {item['id']}",
                "",
                f"- Claim: {sanitize_text(item['claim'], max_chars=5000)}",
                f"- Kind: {item['kind']}",
                f"- Evidence: {', '.join(item['evidence_refs'])}",
                f"- Confidence: {item['confidence']:.2f}",
                f"- Source count: {item['source_count']}",
                f"- Project count: {item['project_count']}",
                f"- Sensitivity: {item['sensitivity']}",
                f"- Promotion tier: {item['promotion_tier']}",
                f"- Status: {item['status']}",
                f"- Reason: {reason}",
                "",
            ]
        )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def write_review_artifacts(vault: Path, store: StateStore) -> dict[str, Any]:
    pending = store.observations("pending")
    groups = build_review_groups(pending)
    summary = review_summary(pending)
    root = vault / "Inbox" / "Review"
    root.mkdir(parents=True, exist_ok=True)
    group_root = root / "Groups"
    group_paths: list[str] = []
    for group in groups:
        group_path = group_root / group["filename"]
        _write_review_group(group_path, group)
        group_paths.append(str(group_path))

    for key, spec in GROUP_SPECS.items():
        if any(group["key"] == key for group in groups):
            continue
        stale_path = group_root / spec["filename"]
        if (
            stale_path.exists()
            and "<!-- sb:generated review-group:start -->"
            in stale_path.read_text(encoding="utf-8")
        ):
            update_generated_file(
                stale_path, "review-group", "_No pending items in this group._"
            )

    ledger = _write_review_ledger(vault, pending)
    digest = root / f"Review-{date.today().isoformat()}.md"
    section_titles = {
        "questions": "Questions you can answer gradually",
        "public": "Public-facing claims",
        "private": "Private observations",
    }
    section_guidance = {
        "questions": "These do not block bootstrap approval. Open a topic only when you want to clarify it.",
        "public": "Review these before using them in a resume, LinkedIn profile, portfolio, or biography.",
        "private": "These may be approved in groups after a spot-check or simply left pending.",
    }
    body = [
        "You do **not** need to read or clear the complete machine ledger. Start here and open only the topics that matter now.",
        "",
        "| Review area | Pending | What to do |",
        "| --- | ---: | --- |",
        f"| Questions | {summary['needs_answers']} | Answer gradually; defer anything unclear |",
        f"| Public-facing claims | {summary['public_claims']} | Check wording before career/public use |",
        f"| Private observations | {summary['private_review']} | Spot-check, batch-decide, or leave pending |",
        "",
        "Pending items never become canonical merely because bootstrap is approved.",
        "",
    ]
    for section in ("questions", "public", "private"):
        section_groups = [group for group in groups if group["section"] == section]
        if not section_groups:
            continue
        body.extend(
            [f"## {section_titles[section]}", "", section_guidance[section], ""]
        )
        for group in section_groups:
            link = f"Inbox/Review/Groups/{Path(group['filename']).stem}"
            mode = (
                "answer-only"
                if group["mode"] == "answer"
                else "snapshot-safe batch decision available"
            )
            body.append(
                f"- [[{link}|{group['title']}]] — {group['count']} items; {mode}."
            )
        body.append("")
    body.extend(
        [
            "## Machine provenance",
            "",
            f"The complete `obs-*` and `ev-*` audit trail is in [[Inbox/Review/{ledger.stem}|the machine review ledger]]. "
            "Those stable IDs support traceability, deduplication, crash recovery, and durable rejection tombstones.",
            "",
            "You normally do not need to open it.",
        ]
    )
    digest.write_text(
        "---\n"
        f"id: review-{date.today().isoformat()}\n"
        "type: review-dashboard\n"
        "generated_by: second-brain-protocol\n"
        f"generated_at: {utc_now()}\n"
        "---\n\n"
        f"# Review dashboard — {date.today().isoformat()}\n\n"
        + "\n".join(body).rstrip()
        + "\n",
        encoding="utf-8",
    )
    return {
        "digest": str(digest),
        "ledger": str(ledger),
        "group_paths": group_paths,
        **summary,
    }


def _write_review_note(vault: Path, store: StateStore) -> Path:
    return Path(write_review_artifacts(vault, store)["digest"])


def _verified_skill(store: StateStore, update: dict[str, Any]) -> bool:
    if not update["authorship_confirmed"] or not update["successful_implementation"]:
        return False
    projects = {item["id"]: item for item in store.projects()}
    rows = store.evidence_by_ids(update["evidence_refs"])
    first_party_context = any(
        row.get("project_id") in projects
        and projects[row["project_id"]].get("classification") == "first-party"
        for row in rows
    )
    success_words = re.compile(
        r"(?i)\b(?:implemented|completed|working|verified|tests? passed|successful|shipped|fixed)\b"
    )
    legacy_success = any(
        row["kind"] == "artifact"
        or (
            isinstance(row.get("payload"), dict)
            and row["payload"].get("role") == "assistant"
            and success_words.search(str(row["payload"].get("text") or ""))
        )
        for row in rows
    )
    session_supported_success = False
    for row in rows:
        payload = row.get("payload") or {}
        if row["kind"] != "session_digest" or not isinstance(payload, dict):
            continue
        user_messages = payload.get("user_messages")
        outcomes = [
            *list(payload.get("assistant_results") or []),
            *list(payload.get("artifacts") or []),
        ]
        if user_messages and any(
            success_words.search(str(item.get("text") or ""))
            for item in outcomes
            if isinstance(item, dict)
        ):
            session_supported_success = True
            break
    return session_supported_success or (first_party_context and legacy_success)


def publish_skill_update(
    vault: Path, store: StateStore, update: dict[str, Any]
) -> dict[str, Any]:
    """Publish one validated skill update through the canonical skill writer."""

    unknown_refs = {str(item) for item in update["evidence_refs"]} - {
        item["id"] for item in store.evidence_by_ids(update["evidence_refs"])
    }
    if unknown_refs:
        raise ValueError(f"Unknown skill evidence references: {sorted(unknown_refs)}")
    status = "verified" if _verified_skill(store, update) else "candidate"
    path = vault / "Skills" / f"{slugify(update['name'])}.md"
    _ensure_generated_note(
        path,
        note_id=f"skill-{update['skill_id']}",
        note_type="skill",
        title=update["name"],
        confidence=float(update["confidence"]),
        tags="skill, second-brain",
    )
    body = (
        f"Status: {status}\n\n{sanitize_text(update['claim'])}\n\n"
        f"Evidence: {', '.join(update['evidence_refs'])}\n\n"
        f"Last verified: {date.today().isoformat()}"
    )
    update_generated_file(path, "canonical", body)
    _update_frontmatter(
        path,
        evidence_refs=update["evidence_refs"],
        confidence=float(update["confidence"]),
    )
    _upsert_generated_bullet(
        vault / "Skills" / "Index.md",
        f"- [[Skills/{path.stem}|{update['name']}]] — {status}",
        match_token=f"[[Skills/{path.stem}|",
    )
    if status == "verified":
        _upsert_generated_bullet(
            vault / "Identity" / "Capabilities.md",
            f"- {sanitize_text(update['claim'])} ^skill-{update['skill_id']}",
            match_token=f"^skill-{update['skill_id']}",
        )
        _update_frontmatter(
            vault / "Identity" / "Capabilities.md",
            evidence_refs=update["evidence_refs"],
            confidence=float(update["confidence"]),
        )
    return {"path": str(path), "status": status}


AUTHORITATIVE_QUESTION_EVIDENCE = {
    "project_inventory",
    "project_delta",
    "code_graph_summary",
    "cross_project_graph_summary",
    "database_inventory",
}


def _publish_question_resolutions(
    *,
    store: StateStore,
    resolutions: list[dict[str, Any]],
    run_kind: str,
    evidence_ids: list[str],
    question_ids: list[str],
) -> int:
    if not resolutions:
        return 0
    if run_kind != "weekly":
        raise ValueError("Only weekly synthesis may resolve deferred questions")
    allowed_ids = set(question_ids)
    pending = {
        item["id"]: item
        for item in store.observations("pending")
        if item["id"] in allowed_ids and is_auto_resolvable_question(item)
    }
    resolved = 0
    for item in resolutions:
        question_id = item["question_id"]
        if question_id not in pending:
            raise ValueError(
                f"Model attempted to resolve an ineligible or unsupplied question: {question_id}"
            )
        refs = sorted(set(item["evidence_refs"]))
        unknown_refs = set(refs) - set(evidence_ids)
        if unknown_refs:
            raise ValueError(
                f"Model invented question-resolution evidence: {sorted(unknown_refs)}"
            )
        if not item["authoritative"] or float(item["confidence"]) < 0.90:
            continue
        evidence = store.evidence_by_ids(refs)
        if len(evidence) != len(refs):
            raise ValueError("Question resolution references missing evidence")
        authoritative_artifact = any(
            row["kind"] in AUTHORITATIVE_QUESTION_EVIDENCE for row in evidence
        )
        independent_signals = {
            (row["source_type"], row.get("source_ref"), row.get("project_id"))
            for row in evidence
        }
        if not authoritative_artifact and len(independent_signals) < 2:
            continue
        store.resolve_clarification_from_evidence(
            question_id,
            answer=sanitize_text(item["answer"], max_chars=2000),
            evidence_refs=refs,
            confidence=float(item["confidence"]),
        )
        resolved += 1
    return resolved


def publish_model_output(
    *,
    vault: Path,
    store: StateStore,
    output: dict[str, Any],
    run_kind: str,
    evidence_ids: list[str],
    question_ids: list[str] | None = None,
    summary_period: str | None = None,
) -> dict[str, Any]:
    _validate_evidence_lane_isolation(
        store,
        output,
        evidence_ids=evidence_ids,
    )
    promoted = 0
    pending = 0
    projects_written = 0
    skills_written = 0
    voice_samples_written = 0
    questions_resolved = _publish_question_resolutions(
        store=store,
        resolutions=output.get("question_resolutions", []),
        run_kind=run_kind,
        evidence_ids=evidence_ids,
        question_ids=question_ids or [],
    )
    learning_stats = _publish_learning_signals(
        vault=vault,
        store=store,
        signals=output.get("learning_signals", []),
        evidence_ids=evidence_ids,
    )
    pattern_stats = _publish_pattern_signals(
        vault=vault,
        store=store,
        signals=output.get("pattern_signals", []),
        evidence_ids=evidence_ids,
    )
    promoted += pattern_stats["promoted"]
    pending += pattern_stats["pending"]
    feedback_profile = knowledge_feedback_profile(store)
    known_projects = {project["id"]: project for project in store.present_projects()}
    catalog_projects, _collections, _folders = catalog_groups(
        list(known_projects.values())
    )
    question_project_names = {
        str(project["id"]): str(project.get("name") or project["id"])
        for project in catalog_projects
    }
    session_summaries = output.get("session_summaries", [])
    for item in session_summaries:
        evidence_ref = str(item.get("evidence_ref") or "")
        if evidence_ref not in evidence_ids:
            raise ValueError(
                f"Model invented session-summary evidence reference: {evidence_ref}"
            )
        if str(item.get("project_id") or "") not in known_projects:
            raise ValueError(
                f"Model referenced an unknown session-summary project: {item.get('project_id')}"
            )
    accepted_question_destinations: set[str] = set()
    accepted_question_count = 0
    ranked_review_items = sorted(
        enumerate(output.get("review_items", [])),
        key=lambda pair: (-float(pair[1].get("confidence", 0.0)), pair[0]),
    )
    for _index, item in ranked_review_items:
        unknown_refs = set(item["evidence_refs"]) - set(evidence_ids)
        if unknown_refs:
            raise ValueError(
                f"Model invented review evidence references: {sorted(unknown_refs)}"
            )
        project_ids = _evidence_project_ids(store, item["evidence_refs"])
        if not should_create_review_question(
            item,
            project_ids=project_ids,
            project_names=question_project_names,
        ) or should_suppress_review_question(item, feedback_profile):
            continue
        destination = next(iter(project_ids)) if project_ids else "profile"
        if (
            destination in accepted_question_destinations
            or accepted_question_count >= 3
        ):
            continue
        source_count, project_count, _date_count, _session_count = _evidence_dimensions(
            store, item["evidence_refs"]
        )
        clarification_id = store.add_observation(
            {
                "kind": "clarification",
                "subject": item["subject"],
                "claim": f"{item['description']} Question: {item['question']}",
                "evidence_refs": item["evidence_refs"],
                "confidence": item["confidence"],
                "source_count": source_count,
                "project_count": project_count,
                "sensitivity": "normal",
                "promotion_tier": "clarification",
                "status": "pending",
                "review_reason": item["kind"],
                "question": item["question"],
                "scope": "project" if project_ids else "profile",
                "project_id": next(iter(project_ids)) if project_ids else None,
                "project_ids": sorted(project_ids),
            }
        )
        clarification = store.observation(clarification_id)
        if clarification and clarification["status"] == "pending":
            pending += 1
            accepted_question_count += 1
            accepted_question_destinations.add(destination)
    for observation in output["observations"]:
        unknown_refs = set(observation["evidence_refs"]) - set(evidence_ids)
        if unknown_refs:
            raise ValueError(
                f"Model invented evidence references: {sorted(unknown_refs)}"
            )
        if should_suppress_candidate(observation, feedback_profile):
            status, reason = (
                "rejected",
                "suppressed by repeated owner removals of similar low-value knowledge",
            )
        else:
            status, reason = _promotion_status(store, observation)
        record = dict(observation)
        record.update(
            {
                "status": status,
                "sensitivity": "public"
                if observation.get("public_claim")
                else "normal",
                "promotion_tier": "automatic" if status == "promoted" else "review",
                "review_reason": reason,
            }
        )
        observation_id = store.add_observation(record)
        stored = store.observation(observation_id)
        if stored is None:
            raise RuntimeError(f"Observation was not persisted: {observation_id}")
        effective_status = stored["status"]
        if effective_status in {"rejected", "resolved"}:
            continue
        if effective_status in {"promoted", "approved"}:
            note = _observation_note(vault, observation["kind"])
            _append_generated_bullet(
                note,
                f"- {sanitize_text(observation['claim'])} ^{observation_id}",
            )
            _update_frontmatter(
                note,
                evidence_refs=observation["evidence_refs"],
                confidence=float(observation["confidence"]),
            )
            if observation["kind"] in {"experience", "education", "military"}:
                timeline = vault / "Experience" / "Timeline.md"
                _append_generated_bullet(
                    timeline,
                    f"- {sanitize_text(observation['claim'])} ^{observation_id}",
                )
                _update_frontmatter(
                    timeline,
                    evidence_refs=observation["evidence_refs"],
                    confidence=float(observation["confidence"]),
                )
            promoted += 1
        else:
            pending += 1

    catalog_project_ids = {project["id"] for project in catalog_projects}
    catalog_note_paths = project_note_paths(catalog_projects)
    for update in output["project_updates"]:
        if update["project_id"] not in known_projects:
            source_count, project_count, _date_count, _session_count = (
                _evidence_dimensions(store, update["evidence_refs"])
            )
            clarification_id = store.add_observation(
                {
                    "kind": "clarification",
                    "subject": f"Unknown project ID {update['project_id']}",
                    "claim": (
                        f"The model proposed a dossier named {update['name']} for a project ID "
                        "that was not found by the deterministic scanner."
                    ),
                    "evidence_refs": update["evidence_refs"],
                    "confidence": 1.0,
                    "source_count": source_count,
                    "project_count": project_count,
                    "sensitivity": "normal",
                    "promotion_tier": "clarification",
                    "status": "pending",
                    "review_reason": "unknown-project-id",
                }
            )
            clarification = store.observation(clarification_id)
            if clarification and clarification["status"] == "pending":
                pending += 1
            continue
        if update["project_id"] not in catalog_project_ids:
            # Collection containers, duplicate identities, and empty folders
            # are scanner inventory concepts, not project dossiers.
            continue
        update_projects = _evidence_project_ids(store, update["evidence_refs"])
        if update_projects != {str(update["project_id"])}:
            raise ValueError(
                "Project updates must cite evidence attributed only to their project"
            )
        project = known_projects[update["project_id"]]
        canonical_name = str(project.get("name") or update["name"])
        path = vault / "Projects" / f"{catalog_note_paths[update['project_id']]}.md"
        _ensure_generated_note(
            path,
            note_id=f"project-{update['project_id']}",
            note_type="project",
            title=canonical_name,
            tags="project, second-brain",
        )
        body = sanitize_text(update["summary"])
        body += "\n\nEvidence: " + ", ".join(update["evidence_refs"])
        update_generated_file(path, "canonical", body)
        _update_frontmatter(path, evidence_refs=update["evidence_refs"], confidence=0.8)
        _append_generated_bullet(
            vault / "Projects" / "Index.md",
            f"- [[Projects/{path.stem}|{update['name']}]] — `{update['project_id']}`",
        )
        projects_written += 1

    for update in output["skill_updates"]:
        publish_skill_update(vault, store, update)
        skills_written += 1

    for sample in output["voice_samples"]:
        if (
            not sample["safe_for_private_git"]
            or sample["evidence_ref"] not in evidence_ids
        ):
            continue
        excerpt = sanitize_text(sample["excerpt"], redact_email=True, max_chars=1200)
        if scan_text(excerpt, "voice-sample"):
            continue
        sample_id = slugify(sample["evidence_ref"])
        path = vault / "Evidence" / "VoiceSamples" / f"{sample_id}.md"
        path.write_text(
            f"---\nid: voice-{sample_id}\ntype: voice-sample\nprovenance: [{sample['evidence_ref']}]\n---\n\n{excerpt}\n",
            encoding="utf-8",
        )
        voice_samples_written += 1

    stewardship = (
        _weekly_stewardship_markdown(vault, store) if run_kind == "weekly" else None
    )
    activity = activity_markdown(
        store.evidence_by_ids(evidence_ids),
        pattern_stats=pattern_stats,
        project_names_by_id={
            str(project_id): str(project.get("name") or project_id)
            for project_id, project in known_projects.items()
        },
        session_summaries=session_summaries,
        period=summary_period
        or (date.today().isoformat() if run_kind == "daily" else None),
        learning=_learning_markdown(output),
        stewardship=stewardship,
    )
    synthesis_path = _write_synthesis_summary(
        vault,
        run_kind,
        output["summary"],
        activity=activity,
        period=summary_period,
    )
    journal_index_path = None
    stewardship_path = None
    if run_kind in {"daily", "weekly"}:
        period = synthesis_path.stem
        store.replace_summary_evidence(run_kind, period, evidence_ids)
        journal_index_path = _update_journal_index(vault, run_kind)
    if run_kind == "weekly" and stewardship:
        stewardship_path = _write_stewardship_note(vault, stewardship)
    review_path = (
        _write_review_note(vault, store) if pending or questions_resolved else None
    )
    published_evidence_ids = store.expand_derived_evidence(evidence_ids)
    store.mark_evidence(published_evidence_ids, "processed")
    store.publish_checkpoint_candidates(published_evidence_ids)
    return {
        "promoted": promoted,
        "pending": pending,
        "projects_written": projects_written,
        "skills_written": skills_written,
        "voice_samples_written": voice_samples_written,
        "patterns_tracking": pattern_stats["tracking"],
        "patterns_promoted": pattern_stats["promoted"],
        "patterns_pending": pattern_stats["pending"],
        "learning_events_added": learning_stats["events_added"],
        "learning_topics_updated": learning_stats["topics_updated"],
        "learning_profile_only_events": learning_stats["profile_only_events"],
        "learning_path": learning_stats["path"],
        "questions_resolved": questions_resolved,
        "synthesis_path": str(synthesis_path),
        "journal_index_path": str(journal_index_path) if journal_index_path else None,
        "stewardship_path": str(stewardship_path) if stewardship_path else None,
        "review_path": str(review_path) if review_path else None,
    }


def promote_approved_observation(
    vault: Path, store: StateStore, observation_id: str
) -> None:
    matches = [
        item for item in store.observations("approved") if item["id"] == observation_id
    ]
    if not matches:
        raise KeyError(f"Approved observation not found: {observation_id}")
    item = matches[0]
    mutation_id = str((item.get("payload") or {}).get("memory_mutation_id") or "")
    mutation_marker = (
        f" <!-- sb:memory-mutation {mutation_id} -->" if mutation_id else ""
    )
    _append_generated_bullet(
        _observation_note(vault, item["kind"]),
        f"- {item['claim']} ^{item['id']}{mutation_marker}",
    )
    store.decide_observation(observation_id, "promoted")


def publish_curate_candidate(
    vault: Path,
    store: StateStore,
    *,
    layer: str,
    kind: str,
    subject: str,
    claim: str,
    evidence_id: str,
    project_id: str | None,
    confidence: float,
    explicit: bool,
) -> dict[str, Any]:
    """Write one provisional owner-interaction insight into the Knowledge Deck."""

    existing_claims = [
        item
        for item in store.observations()
        if item["kind"] == kind
        and item["subject"].strip().casefold() == subject.strip().casefold()
        and item["status"] in {"approved", "promoted"}
        and item["claim"].strip().casefold() != claim.strip().casefold()
    ]
    if existing_claims:
        raise ValueError(
            "Curate candidate conflicts with existing canonical knowledge for this subject"
        )

    expected_id = (
        "obs-" + canonical_hash({"kind": kind, "subject": subject, "claim": claim})[:24]
    )
    existed = store.observation(expected_id) is not None
    observation_id = store.add_observation(
        {
            "kind": kind,
            "subject": subject,
            "claim": claim,
            "evidence_refs": [evidence_id],
            "confidence": confidence,
            "source_count": 1,
            "project_count": 1 if project_id else 0,
            "sensitivity": "normal",
            "promotion_tier": "curate",
            "status": "promoted",
            "review_reason": "implied owner insight awaiting Knowledge Deck decision",
            "explicit": explicit,
            "scope": "project" if project_id else "global",
            "public_claim": False,
            "authoritative": False,
            "curate_candidate": True,
            "knowledge_layer": layer,
            "project_id": project_id,
            "project_ids": [project_id] if project_id else [],
        }
    )
    stored = store.observation(observation_id)
    if stored is None:
        raise RuntimeError(f"Curate candidate was not persisted: {observation_id}")
    if stored["status"] in {"rejected", "resolved"}:
        return {
            "id": observation_id,
            "status": "suppressed",
            "created": False,
            "layer": layer,
            "kind": kind,
            "path": None,
        }

    note = _observation_note(vault, kind)
    _append_generated_bullet(
        note,
        f"- {sanitize_text(claim)} ^{observation_id}",
    )
    _update_frontmatter(
        note,
        evidence_refs=[evidence_id],
        confidence=confidence,
    )
    return {
        "id": observation_id,
        "status": "new" if not existed else "already_present",
        "created": not existed,
        "layer": layer,
        "kind": kind,
        "path": str(note),
    }


def promote_observation_group(
    vault: Path, store: StateStore, observation_ids: list[str]
) -> None:
    ids = sorted(set(observation_ids))
    pending = {
        item["id"]: item for item in store.observations("pending") if item["id"] in ids
    }
    missing = [
        observation_id for observation_id in ids if observation_id not in pending
    ]
    if missing:
        raise RuntimeError(
            "Batch promotion requires pending observations: " + ", ".join(missing)
        )
    if any(item["kind"] == "clarification" for item in pending.values()):
        raise RuntimeError(
            "Clarification questions cannot be approved or promoted as a group."
        )

    targets = {_observation_note(vault, item["kind"]) for item in pending.values()}
    backups = {path: path.read_text(encoding="utf-8") for path in targets}
    try:
        store.decide_observations(ids, "approved")
        for observation_id in ids:
            promote_approved_observation(vault, store, observation_id)
    except Exception:
        for path, content in backups.items():
            path.write_text(content, encoding="utf-8")
        store.restore_observations_pending(ids)
        raise
