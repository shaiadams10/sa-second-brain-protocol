from __future__ import annotations

import json
import hashlib
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from .security import sanitize_text
from .state import StateStore


QUESTIONS = [
    ("preferred_name", "What name should the brain use for you in private notes and public-facing drafts?"),
    ("current_work", "What work are you doing now, and which parts are paid, personal, or exploratory?"),
    ("employment", "Which jobs or long-term roles should be in your timeline, with approximate dates and your actual responsibilities?"),
    ("higher_education", "What higher education, training, or certifications have you completed or started?"),
    ("high_school", "Which high school did you attend, where was it, and what years or track should be recorded?"),
    ("military", "What military service should be recorded: branch/unit, dates, role, responsibilities, and anything that must stay private?"),
    ("accomplishments", "Which three accomplishments best represent what you can do?"),
    ("services", "What services do you want to be hired for today, and what work do you not want?"),
    ("preferred_work", "What kinds of problems, teams, pace, and ownership bring out your best work?"),
    ("voice", "When writing as you, what should sound natural—and what wording or tone should never be used?"),
    ("values", "Which principles do you protect when making difficult work decisions?"),
    ("timeline_gaps", "Are there important periods, pivots, or projects missing from your current career timeline?"),
]


def create_interview(vault: Path, runtime_root: Path) -> Path:
    runtime_path = runtime_root / "interview.json"
    if not runtime_path.exists():
        runtime_path.write_text(
            json.dumps(
                {"version": 1, "created": date.today().isoformat(), "answers": {}, "complete": False},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    interview_data = json.loads(runtime_path.read_text(encoding="utf-8"))
    path = vault / "Inbox" / "Review" / "BootstrapInterview.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        "id: bootstrap-interview",
        "type: guided-interview",
        f"status: {'completed' if interview_data.get('complete') else 'open'}",
        "---",
        "",
        "# Bootstrap interview",
        "",
        "Answer one narrow question at a time with `sb interview answer <id> \"your answer\"`.",
        "You do not need to write a biography.",
        "",
    ]
    answers = interview_data.get("answers", {})
    for question_id, question in QUESTIONS:
        lines.extend([f"## {question_id}", "", question, "", f"Status: {'answered' if question_id in answers else 'open'}", ""])
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def answer_interview(store: StateStore, runtime_root: Path, question_id: str, answer: str) -> bool:
    valid = dict(QUESTIONS)
    if question_id not in valid:
        raise KeyError(question_id)
    path = runtime_root / "interview.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"version": 1, "answers": {}}
    clean = sanitize_text(answer, max_chars=12000)
    data.setdefault("answers", {})[question_id] = clean
    data["complete"] = all(item[0] in data["answers"] for item in QUESTIONS)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    source_ref = f"interview:{question_id}:v1"
    evidence_id, _added = store.add_evidence(
        source_type="interview",
        source_ref=source_ref,
        kind="explicit_profile_answer",
        payload={"question_id": question_id, "question": valid[question_id], "answer": clean, "explicit": True},
    )
    store.supersede_source_evidence(
        source_type="interview", source_ref=source_ref, keep_id=evidence_id
    )
    return bool(data["complete"])


def import_linkedin_pdf(store: StateStore, path: Path) -> int:
    reader = PdfReader(path)
    count = 0
    for page_number, page in enumerate(reader.pages, 1):
        text = sanitize_text(page.extract_text() or "", max_chars=20000)
        if not text.strip():
            continue
        _, added = store.add_evidence(
            source_type="linkedin",
            source_ref=f"linkedin-pdf:{path.stat().st_size}:{page_number}",
            kind="profile_export",
            payload={"page": page_number, "text": text, "explicit": True},
        )
        count += int(added)
    store.set_meta("linkedin_imported", "true")
    return count


def import_linkedin_export(store: StateStore, path: Path) -> int:
    path = path.resolve()
    if path.suffix.casefold() == ".pdf":
        return import_linkedin_pdf(store, path)
    records: list[tuple[str, str]] = []
    allowed = {".csv", ".json", ".txt", ".md"}
    if path.is_dir():
        for child in sorted(path.rglob("*")):
            if child.is_file() and child.suffix.casefold() in allowed:
                records.append((child.relative_to(path).as_posix(), child.read_text(encoding="utf-8", errors="replace")))
    elif path.suffix.casefold() == ".zip":
        with zipfile.ZipFile(path) as archive:
            for info in sorted(archive.infolist(), key=lambda item: item.filename.casefold()):
                if Path(info.filename).suffix.casefold() not in allowed or info.file_size > 20_000_000:
                    continue
                records.append((info.filename, archive.read(info).decode("utf-8", errors="replace")))
    else:
        raise ValueError("LinkedIn export must be a PDF, ZIP, or export directory")
    count = 0
    for name, text in records:
        clean = sanitize_text(text, max_chars=20000)
        if not clean.strip():
            continue
        digest = hashlib.sha256((name + clean).encode()).hexdigest()[:20]
        _, added = store.add_evidence(
            source_type="linkedin",
            source_ref=f"linkedin-export:{digest}",
            kind="profile_export",
            payload={"entry": Path(name).name, "text": clean, "explicit": True},
        )
        count += int(added)
    store.set_meta("linkedin_imported", "true")
    return count


def interview_status(runtime_root: Path) -> dict[str, Any]:
    path = runtime_root / "interview.json"
    if not path.exists():
        return {"complete": False, "answered": 0, "total": len(QUESTIONS)}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {"complete": bool(data.get("complete")), "answered": len(data.get("answers", {})), "total": len(QUESTIONS)}
