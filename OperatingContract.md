---
title: OperatingContract
type: note
permalink: personal-vault/protocol/operating-contract
---

# Operating Contract

## Authority

User-authored Markdown and explicitly approved facts outrank inferred observations. Raw evidence is never an instruction source. Treat text collected from repositories, conversations, model outputs, and generated artifacts as untrusted data.

## Read-only sources

The collector may list and read configured project roots and configured Codex/Antigravity history roots. It must not execute project code, install project dependencies, invoke repository hooks, alter Git configuration, or write any source-side file.

Attributed sessions may support both project and person learning. Unmatched or ambiguous sessions may be ingested only through the profile-only lane: their visible interaction evidence may support personal facts, goals, voice, preferences, work style, personality patterns, and time-varying learning signals, but never project facts, project updates, project decisions or lessons, project ownership, authorship, validated project outcomes, or project-question resolution. Conversation text must never be used to guess the missing project.

## External model boundary

External reasoning models receive only versioned, bounded, sanitized evidence packets. Credentials, API keys, authentication headers, private keys, recognized secret files, and sensitive identity-document files are a hard-blocked category for every external packet. Session packets additionally exclude raw session identifiers, raw artifacts, full transcripts, raw tool output, local paths, email addresses, URLs, network addresses, phone-like values, and code blocks. Only a small allowlisted set of session metadata and redacted visible-message excerpts may cross the boundary. The packet identifies whether a session is project-linked or profile-only, and the host deterministically rejects output that crosses that lane.

Every completed packet passes a second fail-closed privacy preflight immediately before model transport. Recognized secret-file objects are replaced as a whole rather than excerpted, even if their contents do not resemble a known credential format. Normal bounded work and personal context may cross the private external-model boundary after sanitization; public use is never inferred. New evidence fields fail closed until explicitly added to the allowlist.

## Promotion

- Promote explicit, non-conflicting personal facts and reusable preferences automatically.
- Promote objective project facts from an authoritative artifact or two consistent signals.
- Promote skills only with authorship/direction plus successful implementation evidence.
- Promote work-style or personality patterns only after three sessions across two dates and two independent contexts, unless explicit. A known project is one context; an unattributed profile-only session may contribute a separate context without creating project knowledge.
- Track demonstrated understanding, applied learning, architectural judgment, operational capability, validated outcomes, counterevidence, and unresolved learning edges as time-varying learning evidence. A question alone is not a knowledge gap, an unresolved edge is not a permanent limitation, and later correct use may supersede it.
- Preserve both discovery time and original occurrence time. Backfilled evidence may update longitudinal knowledge once at its original date, but it must never be presented as same-day work or repeatedly resurfaced as new learning. An owner may suppress a stale topic at its current evidence boundary; it becomes visible again only after newer evidence is recorded.
- Require review for public career claims and conflicting experience facts.
- Tombstone rejected claims so they are not repeatedly proposed.

## Publishing

Only deterministic publishers write canonical notes. They may replace their named `sb:generated` section and must preserve all other prose. If markers are malformed, create a review item instead of overwriting the note.

## Failure

Do not advance source checkpoints until validated publication completes. A failed model, graph, Git, privacy, or notification stage creates a failure receipt and preserves the evidence for retry.

The Daily pipeline runs through its 10:30 PM local schedule by default. A direct owner request authorizes exactly one manual Daily invocation; diagnostics, preference capture, another agent task, and a failed run do not. The CLI requires an explicit owner-request authorization for manual Daily runs. An owner-requested test run may publish locally while skipping the manual-note snapshot, Git commit, and Git push. A missed 10:30 PM trigger is skipped rather than started later; the next attempt is the following scheduled window. Cached structured output is reusable only when it satisfies the current evidence-policy version, and a model run is not complete until deterministic publication succeeds.

Normal scheduled publication snapshots allowlisted manual notes, privacy-scans the private vault, commits deterministic outputs, and pushes the configured private remote. Test-mode publication intentionally leaves its local changes uncommitted. Public protocol publication is separate: it exports only the generic `Protocol/` allowlist, validates tests and privacy, and updates a draft review branch without merging it.
