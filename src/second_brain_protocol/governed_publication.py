from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import asdict
from pathlib import Path, PurePosixPath

from .daily_weekly_runner import RunArtifact
from .publication_policy import PublicationManifest, StagedCanonicalFile
from .security import sanitize_external_text


_STAGING_ID = re.compile(r"^[a-f0-9]{64}$")
GOVERNED_JOURNAL_MARKER = "<!-- sb:engine governed-extraction-v3 -->"


class DurableJournalPublicationPlanner:
    """Create one durable, compare-and-swap Daily or Weekly journal update."""

    def __init__(self, *, vault: Path, staging_root: Path) -> None:
        self._vault = vault.resolve(strict=True)
        self._staging_root = staging_root.resolve()
        self._staging_root.mkdir(parents=True, exist_ok=True)

    def prepare(
        self,
        artifact: RunArtifact,
        *,
        idempotency_key: str,
    ) -> "PreparedJournalPublication":
        if not _STAGING_ID.fullmatch(idempotency_key):
            raise RuntimeError("Publication staging identity is invalid")
        staging = self._staging_root / idempotency_key
        if staging.exists():
            prepared = self.open(idempotency_key)
            metadata = _read_metadata(staging)
            if metadata.get("artifact_fingerprint") != artifact.artifact_fingerprint:
                raise RuntimeError("Publication staging artifact changed")
            return prepared

        relative = _journal_path(artifact)
        target = _safe_target(self._vault, relative)
        before = _read_text_exact(target) if target.is_file() else None
        body = _journal_body(artifact)
        after = (
            _replace_generated_section(before, artifact.run_kind, body)
            if before is not None
            else _new_journal(artifact, body)
        )
        staging.mkdir(parents=False)
        metadata = {
            "artifact_fingerprint": artifact.artifact_fingerprint,
            "files": [
                asdict(
                    StagedCanonicalFile(
                        path=relative,
                        before_text=before,
                        after_text=after,
                    )
                )
            ],
        }
        _write_json(staging / "publication.json", metadata)
        return PreparedJournalPublication(
            vault=self._vault,
            staging_root=self._staging_root,
            staging_id=idempotency_key,
        )

    def open(self, staging_id: str) -> "PreparedJournalPublication":
        if not _STAGING_ID.fullmatch(staging_id):
            raise RuntimeError("Publication staging identity is invalid")
        prepared = PreparedJournalPublication(
            vault=self._vault,
            staging_root=self._staging_root,
            staging_id=staging_id,
        )
        _ = prepared.staged_files
        return prepared


class PreparedJournalPublication:
    def __init__(
        self,
        *,
        vault: Path,
        staging_root: Path,
        staging_id: str,
    ) -> None:
        self._vault = vault
        self._staging_root = staging_root
        self.staging_id = staging_id

    @property
    def _directory(self) -> Path:
        return self._staging_root / self.staging_id

    @property
    def staged_files(self) -> tuple[StagedCanonicalFile, ...]:
        metadata = _read_metadata(self._directory)
        files = metadata.get("files")
        if not isinstance(files, list):
            raise RuntimeError("Publication staging manifest is malformed")
        return tuple(StagedCanonicalFile(**item) for item in files)

    def commit(self, manifest: PublicationManifest) -> None:
        staged = {item.path: item for item in self.staged_files}
        pending: list[tuple[Path, str]] = []
        for file in manifest.files:
            item = staged.get(file.path)
            if item is None:
                raise RuntimeError("Publication manifest is not present in staging")
            target = _safe_target(self._vault, file.path)
            current_hash = _file_hash(target)
            if current_hash == file.content_hash:
                continue
            if current_hash != file.before_hash:
                raise RuntimeError("canonical preimage changed")
            pending.append((target, item.after_text))
        if len(pending) > 1:
            raise RuntimeError("Governed journal publication must change one file")
        for target, text in pending:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(
                f".{target.name}.sb-next-{self.staging_id[:12]}"
            )
            with temporary.open("w", encoding="utf-8", newline="") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)

    def rollback(self) -> None:
        directory = self._directory.resolve()
        if directory.parent != self._staging_root.resolve():
            raise RuntimeError("Publication rollback escaped its staging root")
        if directory.is_dir():
            shutil.rmtree(directory)

    def verify_committed(self, manifest: PublicationManifest) -> bool:
        return all(
            _file_hash(_safe_target(self._vault, item.path)) == item.content_hash
            for item in manifest.files
        )


def _journal_path(artifact: RunArtifact) -> str:
    if artifact.run_kind == "daily":
        return f"Journal/Daily/{artifact.period}.md"
    return f"Journal/Weekly/{artifact.period}.md"


def _journal_body(artifact: RunArtifact) -> str:
    heading = (
        "### New durable memory proposals"
        if artifact.run_kind == "daily"
        else "### Weekly trajectory proposals"
    )
    lines = [GOVERNED_JOURNAL_MARKER, "", heading, ""]
    if artifact.extraction.candidates:
        for item in artifact.extraction.candidates:
            kind = sanitize_external_text(
                item.kind.replace("_", " ").title(), max_chars=80
            )
            subject = sanitize_external_text(item.subject, max_chars=160)
            claim = sanitize_external_text(item.claim, max_chars=1000)
            lines.append(f"- **Pending review · {kind} · {subject}:** {claim}")
    else:
        lines.append(
            "- No new durable memory proposal was identified from newly processed evidence."
        )
    meaningful_sessions = [
        item
        for item in artifact.session_summaries
        if item.candidate_count or item.procedure_candidate_count
    ]
    if meaningful_sessions:
        lines.extend(["", "### Session extraction", ""])
        for item in meaningful_sessions:
            lines.append("- " + sanitize_external_text(item.summary, max_chars=1200))
    stale = sum(
        item.code == "stale-established-memory"
        for item in artifact.extraction.rejections
    )
    tombstones = sum(
        item.code == "rejected-memory-tombstone"
        for item in artifact.extraction.rejections
    )
    lines.extend(
        [
            "",
            "### Governed extraction",
            "",
            f"- {artifact.period_summary.summary}",
            f"- Repeated established insights suppressed: {stale}.",
            f"- Previously rejected insights suppressed: {tombstones}.",
            "- Canonical knowledge was preserved; proposals remain review-required.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _replace_generated_section(text: str, section: str, body: str) -> str:
    start = f"<!-- sb:generated {section}:start -->"
    end = f"<!-- sb:generated {section}:end -->"
    if text.count(start) != 1 or text.count(end) != 1:
        raise RuntimeError("Journal generated section is unavailable or ambiguous")
    before, remainder = text.split(start, 1)
    _old, after = remainder.split(end, 1)
    return f"{before}{start}\n{body}{end}{after}"


def _new_journal(artifact: RunArtifact, body: str) -> str:
    if artifact.run_kind == "daily":
        note_id = f"daily-{artifact.period}"
        title = artifact.period
        date_field = f"date: {artifact.period}\n"
    else:
        note_id = f"weekly-{artifact.period}"
        title = f"Weekly reflection - {artifact.period}"
        date_field = f"week: {artifact.period}\n"
    section = artifact.run_kind
    return (
        "---\n"
        f"id: {note_id}\n"
        f"type: {artifact.run_kind}\n"
        f"created: {artifact.period}\n"
        f"{date_field}"
        "---\n\n"
        f"# {title}\n\n"
        "## Personal notes\n\n"
        "## Automated summary\n\n"
        f"<!-- sb:generated {section}:start -->\n"
        f"{body}"
        f"<!-- sb:generated {section}:end -->\n"
    )


def _safe_target(vault: Path, relative: str) -> Path:
    path = PurePosixPath(relative)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RuntimeError("Publication target is invalid")
    target = vault.joinpath(*path.parts)
    cursor = vault
    for part in path.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise RuntimeError("Publication target may not be a symlink")
    if not target.resolve().is_relative_to(vault):
        raise RuntimeError("Publication target escaped the vault")
    return target


def _file_hash(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _read_text_exact(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _read_metadata(directory: Path) -> dict:
    try:
        value = json.loads((directory / "publication.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("Publication staging is unavailable") from error
    if not isinstance(value, dict):
        raise RuntimeError("Publication staging manifest is malformed")
    return value


def _write_json(path: Path, value: dict) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        json.dump(
            value, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
