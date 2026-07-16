# Incremental activity and recurring-pattern tracking

The protocol treats project and coding-agent history as append-oriented evidence. It does not send every repository or full conversation to the reasoning model on every run.

## Source progress

Each Codex JSONL file, Antigravity transcript, and DB-only Antigravity conversation receives a stable source key. Machine-local SQLite keeps three related records:

- A collection receipt records that a specific source fingerprint was read successfully.
- Evidence IDs deterministically deduplicate individual messages, tool metadata, artifacts, project snapshots, and deltas.
- A published checkpoint stores the last validated byte offset or database row plus a hash of the consumed prefix.

Unchanged sources are skipped. Growing JSONL files resume at the published byte offset. Antigravity databases resume at the next step row. If a source is truncated, rewritten, or its consumed prefix changes, the collector safely falls back to a complete read; deterministic evidence IDs prevent duplicate promotion.

Checkpoints advance only after schema validation and deterministic publication. A crash or model failure leaves the new evidence available for retry.

## Antigravity database fallback

Exported transcripts are preferred. A conversation database is decoded only when no transcript exists for that conversation. The protobuf decoder accepts the visible user-input, visible assistant-response, and known tool-type fields; system steps, reasoning, permissions, and raw tool output remain excluded.

When a live SQLite WAL exists, the DB, WAL, and shared-memory files are copied into a temporary machine-runtime directory. SQLite reads that snapshot, never the source-side database. The temporary snapshot is deleted after collection.

## Project deltas

Every project keeps a latest deterministic scanner snapshot. A later scan emits a bounded `project_delta` only when something materially changed, including:

- project added, moved, renamed, removed, or reactivated;
- Git head and new commit metadata;
- tracked-file, working-tree, stack, lifecycle, classification, or project-brain changes.

Paths in delta records are repository-relative. Absolute project locations remain machine-local and are removed from cloud packets.

## Recurring patterns

Daily and weekly reasoning may propose a normalized `pattern_signal` for preferences, work style, voice style, personality, or preferred operating protocols. The deterministic registry merges signals by a stable semantic key and retains evidence references, first and last observation, confidence, and counts across sessions, dates, and projects.

A non-explicit pattern remains in the machine registry without creating review work until it has support from at least three sessions, two dates, and two projects. Eligible patterns pass through the normal conflict and promotion policy before entering canonical identity notes. Rejected observations tombstone the linked registry entry so the same pattern is not repeatedly proposed.

Daily and weekly journals include a deterministic activity ledger before the evidence-backed narrative synthesis.

If a collector or parser is upgraded while bootstrap is still awaiting review, run `sb bootstrap --refresh-evidence`. It performs a source-integrity-checked collection and bounded synthesis, then regenerates the final review packet. The command refuses to run after bootstrap completion.

## Deferred objective questions

Weekly synthesis receives a bounded list of unresolved objective project questions alongside new evidence. It may close a question only when the question is machine-resolvable, the answer cites current packet evidence, confidence is at least 0.90, and the host verifies either an authoritative deterministic artifact or two independent consistent signals. Identity, privacy, disclosure, employment, education, military, authorship, ownership, career, and preference decisions are never eligible for automatic resolution.
