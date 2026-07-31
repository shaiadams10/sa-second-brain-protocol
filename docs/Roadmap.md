# Protocol roadmap

This roadmap covers the reusable protocol. A real vault may maintain a separate deployment roadmap for machine-specific readiness and verification.

## v1 — Evidence-backed foundation

- One-time resumable bootstrap and deep project audit.
- Incremental Codex, Antigravity, Git, filesystem, and project-history ingestion.
- Deterministic evidence IDs, checkpoints, deduplication, and crash recovery.
- Canonical Obsidian Markdown with guarded generated sections.
- Daily and weekly cloud synthesis through an isolated account.
- Grouped human review, durable rejection tombstones, local semantic search, code graphs, private Git publication, and sanitized public protocol export.

## v1.1 — Human interface

- Natural-language routing so users do not need commands or skill names.
- A local HTML dashboard generated from canonical notes and safe operational metadata, with a read-only static fallback.
- Daily briefing, project momentum, recently learned knowledge, recurring-pattern maturity, review summary, run history, schedule, and system health.
- Desktop launcher and automatic refresh after scheduled processing.
- Strict exclusion of raw evidence, credentials, local paths, and unresolved claim text from dashboard output.
- A loopback-only Knowledge Deck for confirm, skip, retract, and undo. Retraction is limited to generated sections, records a tombstone, preserves provenance/manual prose, and refreshes local search.
- Immediate owner decisions with durable, incremental, debounced background search refresh and stale-tombstone filtering.
- On-demand fast launcher reuse without a Windows-logon startup task.
- Scheduled fingerprint-based synchronization of changed sanitized protocol trees to a draft public pull request.

## v1.2 — Operational proof and refinement

- Prove real daily and Saturday weekly runs end to end, including missed-run catch-up and failure recovery.
- Add long-term trend comparisons only after enough real weekly data exists.
- Refine dashboard sections using observed usefulness rather than adding speculative widgets.
- Add optional filtered export views for career, portfolio, and project-handoff use.

## v2 — Read-only MCP gateway

The MCP transport remains disabled until all of the following are implemented and tested:

- Local STDIO transport with explicit per-project opt-in.
- Purpose profiles such as `recent-projects`, `resume`, `write-as-me`, and `project-handoff`.
- Stable canonical note IDs instead of arbitrary filesystem paths.
- Category allowlists and hard denylists for raw evidence, pending review, runtime state, credentials, local paths, staging, logs, and write/delete operations.
- Structured, size-bounded responses with confidence, verification date, sensitivity, and provenance.
- Public-career profiles that return only reviewed public-ready claims.
- Tests for path traversal, symlinks, hostile input, sensitive-note denial, response limits, and cross-project isolation.
- Health checks, audit receipts, and documented revocation.

Later projects will query the brain through bounded tools such as search, recent activity, context building, and sanitized project-graph queries. They will never mount the vault or receive unrestricted file access.

## Later possibilities

- User-approved document vault and encrypted backup policy.
- Multiple-machine ingestion and conflict-safe synchronization.
- Optional knowledge-decision history and bulk restoration UI if real usage proves it useful.
- A general Skills dashboard tab that safely inventories every installed Protocol skill, distinguishes manual and model invocation, explains triggers and dependencies, and derives its guide from validated skill metadata rather than a vendor-specific allowlist.
- A reusable skill-guide generator that can inspect a validated skill package from GitHub or another approved source, model its invocation rules, workflows, relationships, examples, and edge cases at generation time, then emit a self-contained interactive HTML guide with starting sparks, route alternatives, a stable skill landscape, and progressive detail. Generated guides must remain deterministic and make no runtime model calls.
- A narrowly writable MCP for future cross-project candidate submission only if an explicit design preserves the current review and publication boundaries. Other-project agents would submit a size-bounded, classified candidate with provenance to Curate; the MCP would never permit direct canonical-note writes and would require authentication, per-project opt-in, rate limits, auditing, conflict checks, and owner Confirm/Remove controls.
