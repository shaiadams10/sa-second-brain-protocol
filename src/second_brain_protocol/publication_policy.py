from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from .canonical_paths import is_indexable_markdown
from .security import scan_untrusted_memory_text


CANONICAL_WRITE_ROOTS = {
    "Goals",
    "Identity",
    "Experience",
    "Journal",
    "Memory",
    "Projects",
    "Skills",
}
GENERATED_MARKER = re.compile(
    r"<!-- sb:generated ([a-z0-9-]+):(start|end) -->"
)
NEW_JOURNAL_NOTE = re.compile(
    r"^Journal/(?:Daily/\d{4}-\d{2}-\d{2}|Weekly/\d{4}-W\d{2})\.md$"
)


@dataclass(frozen=True)
class StagedCanonicalFile:
    path: str
    before_text: str | None
    after_text: str


@dataclass(frozen=True)
class PublicationFile:
    path: str
    before_hash: str | None
    content_hash: str
    changed_sections: tuple[str, ...]


@dataclass(frozen=True)
class PublicationManifest:
    run_kind: str
    period: str
    files: tuple[PublicationFile, ...]

    @property
    def changed_paths(self) -> tuple[str, ...]:
        return tuple(item.path for item in self.files)


@dataclass(frozen=True)
class PublicationPolicyContext:
    run_kind: str
    period: str
    owned_sections: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.run_kind not in {"daily", "weekly"} or not self.period.strip():
            raise ValueError("Publication policy requires a Daily/Weekly period")
        if (
            not self.owned_sections
            or self.owned_sections != tuple(sorted(set(self.owned_sections)))
        ):
            raise ValueError("Publication section ownership must be unique and sorted")


def validate_staged_publication(
    files: tuple[StagedCanonicalFile, ...],
    *,
    context: PublicationPolicyContext,
) -> PublicationManifest:
    """Enforce the Daily/Weekly canonical write and generated-section boundary."""

    if not files:
        return PublicationManifest(
            run_kind=context.run_kind,
            period=context.period,
            files=(),
        )
    paths = tuple(item.path for item in files)
    identities = tuple(path.casefold() for path in paths)
    if paths != tuple(sorted(paths, key=str.casefold)):
        raise RuntimeError("Staged publication paths must be sorted")
    if len(identities) != len(set(identities)):
        raise RuntimeError("Staged publication paths must be unique")

    manifest: list[PublicationFile] = []
    for item in files:
        path = PurePosixPath(item.path)
        if (
            not is_indexable_markdown(item.path)
            or path.as_posix() != item.path
            or not path.parts
            or path.parts[0] not in CANONICAL_WRITE_ROOTS
        ):
            raise RuntimeError("Staged publication path is not writable canon")
        expected_journal_path = (
            f"Journal/Daily/{context.period}.md"
            if context.run_kind == "daily"
            else f"Journal/Weekly/{context.period}.md"
        )
        if (
            len(path.parts) >= 2
            and path.parts[:2] in {("Journal", "Daily"), ("Journal", "Weekly")}
            and item.path != expected_journal_path
        ):
            raise RuntimeError(
                "Journal path does not match the authorized run period"
            )
        after_projection, after_sections, after_bodies = _generated_projection(
            item.after_text
        )
        if item.before_text is None:
            if (
                item.path != expected_journal_path
                or not NEW_JOURNAL_NOTE.fullmatch(item.path)
            ):
                raise RuntimeError(
                    "New journal path does not match the authorized run period"
                )
            _validate_new_journal_scaffold(after_projection)
            scaffold_findings = scan_untrusted_memory_text(
                item.after_text,
                f"publication.{item.path}.new-file",
            )
            if scaffold_findings:
                kinds = ", ".join(
                    sorted({finding.kind for finding in scaffold_findings})
                )
                raise RuntimeError(
                    f"Canonical publication privacy preflight failed: {kinds}"
                )
            changed_sections = after_sections
            before_hash = None
        else:
            before_projection, before_sections, before_bodies = _generated_projection(
                item.before_text
            )
            if not before_sections or before_sections != after_sections:
                raise RuntimeError("Generated section markers may not be changed")
            if before_projection != after_projection:
                raise RuntimeError("Publication may edit only sb:generated sections")
            changed_sections = tuple(
                section
                for section in after_sections
                if before_bodies[section] != after_bodies[section]
            )
            before_hash = hashlib.sha256(
                item.before_text.encode("utf-8")
            ).hexdigest()
        if not changed_sections:
            raise RuntimeError("Staged publication contains no generated change")
        if set(changed_sections) - set(context.owned_sections):
            raise RuntimeError("Publication changed an unowned generated section")
        for section in changed_sections:
            findings = scan_untrusted_memory_text(
                after_bodies[section],
                f"publication.{item.path}.{section}",
            )
            if findings:
                kinds = ", ".join(sorted({finding.kind for finding in findings}))
                raise RuntimeError(
                    f"Canonical publication privacy preflight failed: {kinds}"
                )
        manifest.append(
            PublicationFile(
                path=item.path,
                before_hash=before_hash,
                content_hash=hashlib.sha256(
                    item.after_text.encode("utf-8")
                ).hexdigest(),
                changed_sections=changed_sections,
            )
        )
    return PublicationManifest(
        run_kind=context.run_kind,
        period=context.period,
        files=tuple(manifest),
    )


def _generated_projection(
    text: str,
) -> tuple[str, tuple[str, ...], dict[str, str]]:
    output: list[str] = []
    sections: list[str] = []
    active: str | None = None
    body_start = 0
    bodies: dict[str, str] = {}
    position = 0
    for match in GENERATED_MARKER.finditer(text):
        section, boundary = match.groups()
        if boundary == "start":
            if active is not None:
                raise RuntimeError("Generated sections may not nest")
            output.append(text[position : match.end()])
            active = section
            sections.append(section)
            body_start = match.end()
        else:
            if active != section:
                raise RuntimeError("Generated section markers are malformed")
            output.append(match.group(0))
            bodies[section] = text[body_start : match.start()]
            active = None
        position = match.end()
    if active is not None:
        raise RuntimeError("Generated section markers are malformed")
    output.append(text[position:])
    if len(sections) != len(set(sections)):
        raise RuntimeError("Generated section names must be unique")
    return "".join(output), tuple(sections), bodies


def _validate_new_journal_scaffold(projection: str) -> None:
    in_frontmatter = False
    frontmatter_closed = False
    for index, line in enumerate(projection.splitlines()):
        stripped = line.strip()
        if index == 0 and stripped == "---":
            in_frontmatter = True
            continue
        if in_frontmatter and stripped == "---":
            in_frontmatter = False
            frontmatter_closed = True
            continue
        if in_frontmatter:
            if not re.fullmatch(
                r"(?:id|type|created|date|week|tags|permalink|status): [A-Za-z0-9_./:+\-\[\], ]{1,200}",
                stripped,
            ):
                raise RuntimeError("New journal frontmatter is not allowlisted")
            continue
        stripped = GENERATED_MARKER.sub("", stripped).strip()
        if (
            not stripped
            or stripped.startswith("#")
        ):
            continue
        raise RuntimeError("New journal prose must stay inside generated sections")
    if projection.startswith("---") and not frontmatter_closed:
        raise RuntimeError("New journal frontmatter is malformed")
