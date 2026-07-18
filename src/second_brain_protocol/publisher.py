from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .markdown import GeneratedSectionError, replace_generated_section, slugify, update_generated_file
from .feedback_learning import knowledge_feedback_profile, should_suppress_candidate
from .question_followup import is_auto_resolvable_question, should_create_review_question
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
    vault: Path, run_kind: str, summary: str, *, activity: str | None = None
) -> Path:
    today = date.today().isoformat()
    if run_kind == "bootstrap":
        path = vault / "System" / "Audits" / "Bootstrap" / "Synthesis.md"
        section = "bootstrap-synthesis"
        note_id = "bootstrap-synthesis"
        title = "Bootstrap synthesis"
        note_type = "bootstrap-synthesis"
    elif run_kind == "daily":
        path = vault / "Journal" / "Daily" / f"{today}.md"
        section = "daily"
        note_id = f"daily-{today}"
        title = today
        note_type = "daily"
    else:
        week = datetime.now().isocalendar()
        week_id = f"{week.year}-W{week.week:02d}"
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
    if activity:
        generated = activity.rstrip() + "\n\n### Evidence-backed synthesis\n\n" + generated
    update_generated_file(path, section, generated)
    return path


def _evidence_dimensions(store: StateStore, refs: list[str]) -> tuple[int, int, int, int]:
    rows = store.evidence_by_ids(refs)
    sources = {row["source_ref"].split(":", 1)[0] + ":" + row["source_ref"].split(":")[1] for row in rows if ":" in row["source_ref"]}
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


def _promotion_status(store: StateStore, observation: dict[str, Any]) -> tuple[str, str]:
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
        row for row in store.observations() if row["kind"] == kind and row["subject"] == observation["subject"]
        and row["status"] in {"approved", "promoted"}
    ]
    if any(row["claim"].strip().casefold() != observation["claim"].strip().casefold() for row in existing):
        return "pending", "conflicts with an existing canonical claim"
    evidence_rows = store.evidence_by_ids(observation["evidence_refs"])
    explicit_user_evidence = any(
        row["source_type"] in {"interview", "linkedin"}
        or (isinstance(row.get("payload"), dict) and row["payload"].get("role") == "user")
        for row in evidence_rows
    )
    if (
        observation.get("explicit")
        and explicit_user_evidence
        and kind not in {"experience", "education", "military", "project_fact"}
        and (not pattern_kind or scope == "global" or any(
            row["source_type"] in {"interview", "linkedin"} for row in evidence_rows
        ))
    ):
        return "promoted", "explicit non-conflicting user fact"
    if kind == "project_fact" and (observation.get("authoritative") or source_count >= 2):
        return "promoted", "authoritative or corroborated project fact"
    if pattern_kind:
        if (
            observation.get("explicit")
            and scope == "global"
            and explicit_user_evidence
        ) or (session_count >= 3 and date_count >= 2 and project_count >= 2):
            return "promoted", "stable multi-session pattern"
        return "pending", "project-scoped or insufficient cross-session pattern evidence"
    if kind in {"experience", "education", "military"}:
        trusted = any(row["source_type"] in {"linkedin", "interview"} for row in evidence_rows)
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
    }
    return mapping[kind]


def _append_generated_bullet(path: Path, text: str) -> None:
    current = path.read_text(encoding="utf-8")
    sections = re.findall(r"<!-- sb:generated ([a-z0-9-]+):start -->", current)
    if len(sections) != 1:
        raise GeneratedSectionError(f"Expected one generated section in {path}; found {sections}")
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
        scope = "global" if "global" in {existing_scope, signal_scope} else (signal_scope or existing_scope or "project")
        source_count, project_count, date_count, session_count = _evidence_dimensions(
            store, merged_refs
        )
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
            session_count >= 3 and date_count >= 2 and project_count >= 2
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


def _upsert_generated_profile_bullet(path: Path, label: str, answer: str) -> None:
    current = path.read_text(encoding="utf-8")
    sections = re.findall(r"<!-- sb:generated ([a-z0-9-]+):start -->", current)
    if len(sections) != 1:
        raise GeneratedSectionError(f"Expected one generated section in {path}; found {sections}")
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
    lines = [] if not body or body.startswith(placeholder_prefixes) else body.splitlines()
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
                    raise GeneratedSectionError(f"Malformed generated markers in {path}")
                body = updated.split(start, 1)[1].split(end, 1)[0].strip()
                updated = replace_generated_section(updated, section, repair_mojibake(body))
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


def _update_frontmatter(path: Path, *, evidence_refs: list[str], confidence: float) -> None:
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
        rendered = json.dumps(value, ensure_ascii=False) if isinstance(value, list) else str(value)
        match = next((index for index, line in enumerate(lines) if line.startswith(f"{key}:")), None)
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
                f"- Reject all: `sb review reject-group {group['id']} --reason \"...\"`",
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
                    f"Answer with: `sb review answer {group['id']} {index} --answer \"...\"`",
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
        if stale_path.exists() and "<!-- sb:generated review-group:start -->" in stale_path.read_text(encoding="utf-8"):
            update_generated_file(stale_path, "review-group", "_No pending items in this group._")

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
        body.extend([f"## {section_titles[section]}", "", section_guidance[section], ""])
        for group in section_groups:
            link = f"Inbox/Review/Groups/{Path(group['filename']).stem}"
            mode = "answer-only" if group["mode"] == "answer" else "snapshot-safe batch decision available"
            body.append(f"- [[{link}|{group['title']}]] — {group['count']} items; {mode}.")
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
    authored = any(
        row.get("project_id") in projects
        and projects[row["project_id"]].get("classification") == "first-party"
        for row in rows
    )
    success_words = re.compile(r"(?i)\b(?:implemented|completed|working|verified|tests? passed|successful|shipped|fixed)\b")
    successful = any(
        row["kind"] == "artifact"
        or (
            isinstance(row.get("payload"), dict)
            and row["payload"].get("role") == "assistant"
            and success_words.search(str(row["payload"].get("text") or ""))
        )
        for row in rows
    )
    return authored and successful


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
) -> dict[str, Any]:
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
    pattern_stats = _publish_pattern_signals(
        vault=vault,
        store=store,
        signals=output.get("pattern_signals", []),
        evidence_ids=evidence_ids,
    )
    promoted += pattern_stats["promoted"]
    pending += pattern_stats["pending"]
    feedback_profile = knowledge_feedback_profile(store)
    for item in output.get("review_items", []):
        if not should_create_review_question(item):
            continue
        unknown_refs = set(item["evidence_refs"]) - set(evidence_ids)
        if unknown_refs:
            raise ValueError(f"Model invented review evidence references: {sorted(unknown_refs)}")
        source_count, project_count, _date_count, _session_count = _evidence_dimensions(store, item["evidence_refs"])
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
            }
        )
        clarification = store.observation(clarification_id)
        if clarification and clarification["status"] == "pending":
            pending += 1
    for observation in output["observations"]:
        unknown_refs = set(observation["evidence_refs"]) - set(evidence_ids)
        if unknown_refs:
            raise ValueError(f"Model invented evidence references: {sorted(unknown_refs)}")
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
                "sensitivity": "public" if observation.get("public_claim") else "normal",
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
                _append_generated_bullet(timeline, f"- {sanitize_text(observation['claim'])} ^{observation_id}")
                _update_frontmatter(
                    timeline,
                    evidence_refs=observation["evidence_refs"],
                    confidence=float(observation["confidence"]),
                )
            promoted += 1
        else:
            pending += 1

    known_project_ids = {project["id"] for project in store.projects()}
    for update in output["project_updates"]:
        if update["project_id"] not in known_project_ids:
            source_count, project_count, _date_count, _session_count = _evidence_dimensions(
                store, update["evidence_refs"]
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
        path = vault / "Projects" / f"{slugify(update['name'])}.md"
        _ensure_generated_note(
            path,
            note_id=f"project-{update['project_id']}",
            note_type="project",
            title=update["name"],
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
        _append_generated_bullet(
            vault / "Skills" / "Index.md",
            f"- [[Skills/{path.stem}|{update['name']}]] — {status}",
        )
        if status == "verified":
            _append_generated_bullet(
                vault / "Identity" / "Capabilities.md",
                f"- {sanitize_text(update['claim'])} ^skill-{update['skill_id']}",
            )
            _update_frontmatter(
                vault / "Identity" / "Capabilities.md",
                evidence_refs=update["evidence_refs"],
                confidence=float(update["confidence"]),
            )
        skills_written += 1

    for sample in output["voice_samples"]:
        if not sample["safe_for_private_git"] or sample["evidence_ref"] not in evidence_ids:
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

    activity = activity_markdown(
        store.evidence_by_ids(evidence_ids), pattern_stats=pattern_stats
    )
    synthesis_path = _write_synthesis_summary(
        vault, run_kind, output["summary"], activity=activity
    )
    review_path = _write_review_note(vault, store) if pending or questions_resolved else None
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
        "questions_resolved": questions_resolved,
        "synthesis_path": str(synthesis_path),
        "review_path": str(review_path) if review_path else None,
    }


def promote_approved_observation(vault: Path, store: StateStore, observation_id: str) -> None:
    matches = [item for item in store.observations("approved") if item["id"] == observation_id]
    if not matches:
        raise KeyError(f"Approved observation not found: {observation_id}")
    item = matches[0]
    _append_generated_bullet(_observation_note(vault, item["kind"]), f"- {item['claim']} ^{item['id']}")
    store.decide_observation(observation_id, "promoted")


def promote_observation_group(vault: Path, store: StateStore, observation_ids: list[str]) -> None:
    ids = sorted(set(observation_ids))
    pending = {item["id"]: item for item in store.observations("pending") if item["id"] in ids}
    missing = [observation_id for observation_id in ids if observation_id not in pending]
    if missing:
        raise RuntimeError("Batch promotion requires pending observations: " + ", ".join(missing))
    if any(item["kind"] == "clarification" for item in pending.values()):
        raise RuntimeError("Clarification questions cannot be approved or promoted as a group.")

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
