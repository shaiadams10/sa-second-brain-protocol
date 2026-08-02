# Project catalog, lifecycle, questions, and session attribution

This document is the operational contract for turning project folders and coding-agent sessions into durable personal knowledge. It keeps project identity, ownership, learning, and session attribution separate so that one noisy source cannot merge unrelated projects or create unsupported claims.

## Project identity and discovery

- Every direct child of a configured project root is evaluated independently.
- An organizational folder becomes a collection only when its path is explicitly configured as one. Its direct children are evaluated as separate project candidates; the collection itself cannot own sessions or receive a project dossier.
- An ordinary project does not become a collection merely because it contains `src`, `packages`, nested repositories, environments, build output, or vendored code.
- A leaf folder with deterministic project content becomes a project. A content-free folder remains visible as an other folder rather than masquerading as a project.
- Current paths, validated historical aliases, and unique Git identities preserve stable project identity across moves and renames.
- The generated project index is rebuilt from scanner truth. Model output cannot add project aliases, combine folders, or invent catalog entries.
- The main vault is registered as one content-free managed project identity even when it sits outside the configured project scan root. It is not recursively scanned as project evidence. This keeps work on the Brain itself distinct from a similarly named protocol/package repository.
- Owner-interaction capture defaults to the managed vault. Writing project knowledge to any other project requires explicit owner naming plus the cross-project authorization guard; similar names and shared terminology are never sufficient.

Both daily and weekly runs perform the read-only project scan. A newly created project is therefore detected on the next incremental run. There is no always-on watcher, and source projects are never modified.

## Ownership and learning are different decisions

Ownership classification controls authorship and public attribution. It does not decide whether a session is useful for learning.

- First-party projects may support project ownership claims when the evidence is sufficient.
- Forks, third-party repositories, experiments, and client code must not be represented as upstream authorship.
- Work in any of those repositories may still support narrow evidence about tools used, infrastructure operated, changes directed, problems solved, or implementations validated.
- A dependency list or repository inventory alone does not prove a skill. Skill evidence requires owner direction plus a successful implementation or validation signal.

This separation allows the personal capability pool to learn from real technical work without misrepresenting project ownership.

## Question admission

A project question is admitted only when it is precise, important to durable learning, and requires owner judgment.

- One question may concern at most one named leaf project and one decision.
- Unrelated projects are never combined because they share a technology, folder, session source, or review category.
- A question is rejected when its project is missing from the visible attribution, when evidence resolves to several projects, or when the wording combines ownership, identity, timeline, status, and disclosure decisions.
- Broad inventory questions such as asking for dates and status for every project are not owner-review questions.
- Technical facts, current status, and other machine-resolvable gaps should be deferred for deterministic evidence or later synthesis instead of interrupting the owner.
- Dismissing a question as not relevant records category-level feedback only. Repeated negative feedback may suppress that category, but it never creates a personal fact or guessed rationale.

## One-project session attribution

Conversation text and project-name mentions are never used to decide session ownership. Each Codex or Antigravity session is assigned one machine-local state: matched to exactly one leaf project, unmatched, or ambiguous.

The resolver uses bounded metadata in this order:

1. the coding surface's explicit workspace or project metadata;
2. the current project path or a validated historical path alias;
3. a unique Git remote or repository identity;
4. for Antigravity only, unanimous exact paths found in project-facing tool metadata;
5. an owner-confirmed mapping for one exact session.

The tool-path fallback is accepted only when every resolvable path identifies the same project. Raw commands, tool output, reasoning, and absolute path strings are not published. Conflicting signals leave the session unattributed rather than attaching it to several projects.

Only uniquely matched sessions are eligible for project ingestion and project-history analysis. Historical model packets are partitioned one project at a time, and the deterministic publisher rejects output for any other project or personal profile area.

## Antigravity IDE limitation

Antigravity IDE on Windows has upstream reports of conversations losing their workspace association, including drive-letter URI normalization failures. Affected conversations may be stored locally while appearing unassigned, missing a workspace badge, or prompting for a workspace again. See the [public Google AI Developers Forum report](https://discuss.ai.google.dev/t/bug-v2-0-1-windows-conversations-still-not-associated-with-workspace-no-workspace-tag-always-prompts-open-in-this-workspace/166926).

The protocol safely handles the metadata that Antigravity does preserve:

- the conversation database workspace URI;
- the aggregate Antigravity hub's workspace and Git metadata;
- one unanimous project recovered from exact project-facing tool paths;
- a previewed, owner-confirmed link for one exact session.

It does not infer a project from chat wording and does not rewrite Antigravity's proprietary history databases.

For future conversations, use the standalone Antigravity application's explicit Project surface:

1. register the actual leaf project folder, not a parent collection;
2. start the conversation with the plus action beside that Project;
3. choose Local Mode when working directly in the existing folder;
4. open Antigravity IDE from that Project when an editor is needed.

Registering an existing folder may surface old conversations that still retain the same workspace metadata. It cannot automatically repair a legacy conversation whose project metadata is absent or conflicting. Such sessions remain safely unmatched unless the owner explicitly links one of them.

## Missing, retired, and forgotten projects

A folder disappearing from disk is not proof that its knowledge should be erased. The scanner marks the project missing, removes it from active attribution, reports it in health, and reactivates the stable identity if the folder returns.

Forgetting is a separate explicit operation. It requires an exact preview and confirmation, creates a machine-local recovery backup, removes project-specific canonical and runtime state, removes orphaned project questions, and ignores the old source path so it is not re-ingested. Source repositories are never deleted or modified.

Reusable skills and capabilities survive project forgetting when independent verified evidence supports them. The retained capability is made project-neutral; obsolete project goals and project-specific claims are retired.

## Incremental operating behavior

- Daily runs scan projects, reconcile new sessions, publish only validated deltas, refresh derived indexes, and skip model work when there is no new evidence.
- Weekly runs use the same scanner and attribution rules before cross-project synthesis.
- The dashboard may emphasize first-party project momentum, while capability learning may still use narrowly verified work from other ownership classes.
- Health reports catalog drift, missing projects, session coverage, pending evidence, review load, scheduled-task status, and recent pipeline failures.
- A collector, model, publication, indexing, or notification failure leaves checkpoints retryable and does not convert ambiguous evidence into canonical knowledge.

See [Architecture](Architecture.md), [Incremental activity](IncrementalActivity.md), [Review flow](ReviewFlow.md), and the [Recovery runbook](../runbooks/Recovery.md) for the supporting boundaries.
