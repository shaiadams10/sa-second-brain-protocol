# 🏗️ Architecture

The protocol is built around one rule: evidence may suggest knowledge, but only deterministic policy may publish it.

## Trust boundaries

### 1. Read-only sources

Configured project roots, Git repositories, Codex session history, Antigravity history, and imported documents are evidence sources. Collectors may read them but never execute project code, install dependencies, invoke hooks, or write files into those sources.

### 2. Machine-local state

SQLite checkpoints, source identities, evidence records, model receipts, staging packets, search indexes, code graphs, locks, and logs live outside Git. This state supports deduplication, crash recovery, and provenance without making the private vault unreadable.

### 3. Sanitized reasoning workspace

Each cloud run receives a temporary Git workspace containing only a bounded sanitized packet, a task prompt, and an output schema. The automation account denies access to source projects, normal agent history, credentials, the user profile, network tools, connectors, and unrelated skills.

### 4. Deterministic publication

Structured model output is schema-validated. Promotion policy checks evidence type, authorship, corroboration, sensitivity, conflicts, and public-facing intent. The publisher—not the model—updates canonical Markdown inside named `sb:generated` sections.

### 5. Human review

Claims that are public-facing, contradictory, under-evidenced, or ambiguous remain pending. The human dashboard groups them by decision type while the full evidence ledger remains machine-oriented.

## Canonical and derived data

| Layer | Canonical? | Examples |
| --- | --- | --- |
| Obsidian Markdown | Yes | Identity, experience, projects, skills, memory, goals, journals |
| SQLite state | Operational | Evidence IDs, observations, tombstones, checkpoints, run ledger |
| Basic Memory index | Derived | Search vectors and note relationships |
| Graphify graphs | Derived | AST-based project and cross-project code graphs |
| Model output | Candidate only | Proposed summaries, claims, updates, and review items |

Derived indexes may be rebuilt. Canonical Markdown and explicit review decisions must remain durable.

## One-time bootstrap

```text
not_started → collecting → synthesis_ready → awaiting_review → completed
```

Pre-completion runs resume from checkpoints. After approval, bootstrap is permanently closed and incremental refresh commands must be used.

## Incremental operation

Daily runs collect new evidence, skip the model on empty days, synthesize one bounded update, publish deterministically, reindex, and notify. Weekly runs add cross-project trajectories, durable lessons, stable patterns, and review-backlog summaries. Byte/row cursors avoid reparsing published history, project snapshots produce explicit before/after deltas, and a durable pattern registry accumulates semantically repeated preferences across sessions before promotion.

Failures preserve unprocessed evidence and do not advance checkpoints.

See [Incremental activity and recurring-pattern tracking](IncrementalActivity.md).

## Future MCP boundary

The reserved service layer is read-only. A later transport may expose canonical search, note reading, recent activity, bounded context, and sanitized graph queries. Raw evidence, local paths, ingestion state, and write/delete operations remain unavailable.
