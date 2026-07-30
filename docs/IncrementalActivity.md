# Incremental activity and recurring-pattern tracking

The protocol treats project and coding-agent history as append-oriented evidence. It does not send every repository or full conversation to the reasoning model on every run.

## Source progress

Each Codex JSONL file, Antigravity transcript, and DB-only Antigravity conversation receives a stable source key. Machine-local SQLite keeps three related records:

- A collection receipt records that a specific source fingerprint was read successfully.
- Evidence IDs deterministically deduplicate individual messages, tool metadata, artifacts, project snapshots, and deltas.
- A published checkpoint stores the last validated byte offset or database row plus a hash of the consumed prefix.

Unchanged sources are skipped. Growing JSONL files resume at the published byte offset. Antigravity databases resume at the next step row. If a source is truncated, rewritten, or its consumed prefix changes, the collector safely falls back to a complete read; deterministic evidence IDs prevent duplicate promotion.

Checkpoints advance only after schema validation and deterministic publication. A crash, model failure, or publication-policy rejection leaves the new evidence available for the next scheduled attempt. A model batch remains `awaiting_publication` until the publisher succeeds, and cached output from an older evidence-policy version is rejected.

## Metadata-first project-session attribution

Conversation text and project-name mentions are never used to decide which project owns a session. Before a conversation body is parsed, the collector reads only bounded authoritative metadata: the Codex session header, the Antigravity conversation database's workspace URI, or Antigravity's aggregate conversation metadata record. It resolves that workspace and any unique Git remote against current leaf-project paths, validated historical path aliases retained across scans, and unique Git identity. When a recorded workspace no longer exists after a folder reorganization, an exact leaf-folder match may recover it only when that leaf is unique across current projects; an existing duplicate folder or non-unique leaf remains unmatched.

Every session receives exactly one of three machine-local attribution states: matched to one leaf project, unmatched, or ambiguous. A uniquely matched session is ingested in the full lane and may support both project and person learning. Unmatched and ambiguous sources are ingested in the profile-only lane using visible messages and bounded interaction metadata; they may support person, voice, preference, work-style, goal, pattern, and learning signals but never project knowledge, ownership, authorship, skill verification, validated project outcomes, or project-question resolution. Their fingerprinted attribution record remains available so later project moves, newly recovered aliases, or an explicit owner correction can safely move them into the full lane. Conflicting metadata never creates a multi-project session, and conversation text is never used to guess the project.

When Antigravity explicitly records a conversation as outside a project, an owner-confirmed one-session mapping may be stored only in machine-local configuration. This exception is never inferred from a project-name mention, never maps several projects at once, and remains subordinate to an explicit owner correction.

Antigravity transcript/database pairs share one session identity. The database or aggregate hub metadata may supply workspace and Git context, but the transcript remains the canonical visible-message source. Conflicting aggregate records are withheld. Artifact files may inherit a project only when another source for the same session already has one unique resolved project. Collection folders are excluded from session ownership, so a workspace such as `Utilities & Automation` can organize many independently indexed child projects without becoming their shared owner.

Historical aliases are captured during each project scan and may be recovered from machine-local state backups using stable project IDs or unique Git identities. The incremental reconciliation operation changes only local state and evidence: it does not reopen bootstrap, edit personal history or persona notes, call a model, publish Markdown, or push Git.

An explicit historical-analysis pass may process the matched backlog one project at a time. Each bounded model packet contains one canonical project inventory and only that project's indexed session digests. The host accepts exactly one update for the same project ID, requires a cited session digest, discards all personal-profile, career, persona, voice, skill, pattern, and review-question output, and records analyzed digest IDs so retries are resumable. Publication is limited to the project's generated dossier section and its project-session audit note; it does not commit or push Git.

## Antigravity database fallback

Exported transcripts are preferred. When both compact and full exports exist for one Antigravity session, the compact transcript is selected and the duplicate is ignored. The conversation database enriches an exported transcript with its recorded workspace when the transcript does not contain one, allowing project attribution without exposing the database row. A conversation database is decoded only when no transcript exists for that conversation. The protobuf decoder accepts the visible user-input, visible assistant-response, and known tool-type fields; system steps, reasoning, permissions, and raw tool output remain excluded.

When a live SQLite WAL exists, the DB, WAL, and shared-memory files are copied into a temporary machine-runtime directory. SQLite reads that snapshot, never the source-side database. The temporary snapshot is deleted after collection.

Antigravity sometimes records a conversation as outside a project even when its file or command tools operated inside one. The collector may use absolute paths from project-facing tool records as an attribution fallback only when all resolvable paths point to one current or historical project. It stores only the resulting project ID, resolver label, confidence, and a path hash; raw commands, tool output, reasoning, and machine paths are not copied into canonical Markdown. Conflicting paths remain unmatched. When no deterministic metadata exists, an owner may explicitly link one exact session to one exact project; the machine-local override is durable and reconciliation ingests that session on the next pass.

Antigravity IDE may fail to persist usable project metadata for a conversation. The preferred future workflow is to register each real leaf folder as an explicit Project in the standalone Antigravity application, start conversations from that Project, and open the IDE from there. Reopening an old folder can surface sessions whose original workspace metadata survived, but it does not manufacture a missing association. The protocol leaves those legacy sessions unmatched unless deterministic metadata or one explicit owner correction identifies exactly one project. See [Project and session lifecycle](ProjectSessionLifecycle.md#antigravity-ide-limitation).

## Project deltas

Every project keeps a latest deterministic scanner snapshot. A later scan emits a bounded `project_delta` only when something materially changed, including:

- project added, moved, renamed, removed, or reactivated;
- Git head and new commit metadata;
- tracked-file, working-tree, stack, lifecycle, classification, or project-brain changes.

Paths in delta records are repository-relative. Absolute project locations remain machine-local and are removed from cloud packets.

Both daily and weekly incremental runs begin with the same read-only project scan. A new direct child of a configured project root therefore enters the runtime registry and generated catalog on the next run, not through an always-on filesystem watcher. A direct child of an explicitly configured collection is also scanned independently. Content-free folders remain in Other folders, configured containers remain Collections, and a folder becomes a project dossier only when deterministic project content is present. Ignored paths remain excluded.

Every direct child of a configured projects root is scanned as a candidate, even without Git, a manifest, or recognized source files. A direct child that acts as a human collection folder, such as `Utilities & Automation`, remains represented as a collection and contributes each of its direct children as candidates too. Discovery stops that generic expansion after one collection level so folders inside a real project, such as `src` or `packages`, do not become separate projects merely because they exist. Nested Git repositories and bounded marker-based projects can still be discovered deeper, while dependency, build, cache, virtual-environment, and vendored directories remain pruned.

Scanner representation and canonical project status are intentionally distinct. Leaf identities with detected project content enter the Projects section. Containers with child project identities enter a separate Collections section, and content-free folders enter an Other folders section without project dossiers. The generated project index is replaced from the current scan rather than appended from model output, so stable IDs cannot accumulate aliases or stale entries.

Recent-session lookup is a separate local, read-only operation. Once a session index exists, it returns only sessions with a unique indexed project and bounded sanitized fields such as surface, timestamps, project names, and the last visible user message. It never turns raw conversation history into canonical notes, and it does not expose reasoning, tool output, local paths, or session identifiers by default.

## Ownership and technical learning

Project ownership and learning eligibility are separate. A session attributed to a fork, experiment, or third-party repository may support narrow evidence about tools the user used, infrastructure he operated, changes he directed, or problems he solved. It cannot establish upstream repository authorship. A verified skill requires both the user's direction in the session and a successful implementation or validation outcome; inventories, manifests, and dependencies alone remain insufficient.

## Model usage receipts

Daily and weekly model batches record machine-local usage metadata. New Codex runs preserve input, cached-input, output, reasoning-output, and total token counts from JSONL completion events. Cached synthesis results record zero model calls. Summary-to-run links associate the currently published daily or weekly note with only the batches that produced it; older receipts can recover an exact total even when the input/output split predates structured capture.

## Recurring patterns

Daily and weekly reasoning may propose a normalized `pattern_signal` for preferences, work style, voice style, personality, or preferred operating protocols. The deterministic registry merges signals by a stable semantic key and retains evidence references, first and last observation, confidence, and counts across sessions, dates, known projects, and independent profile-only contexts.

A non-explicit pattern remains in the machine registry without creating review work until it has support from at least three sessions, two dates, and two independent contexts. A known project is one context; an unattributed profile-only session may provide another context without acquiring a project identity. Eligible patterns pass through the normal conflict and promotion policy before entering canonical identity notes. Rejected observations tombstone the linked registry entry so the same pattern is not repeatedly proposed.

## Longitudinal learning

Daily and weekly reasoning may also emit a stable topic-keyed `learning_signal`. Signal types distinguish an active learning edge, demonstrated understanding, later application, architectural judgment, operational capability, an attributed validated outcome, and counterevidence. The deterministic learning registry stores every event with its evidence, date, known-project breadth, and profile-only context breadth, then derives the current topic state:

- `exploring` when only an unresolved edge is supported;
- `demonstrated` when correct understanding or judgment is shown;
- `applied` when later evidence shows use in practice;
- `verified` when attributed evidence contains a validated outcome;
- `mixed` when a newer edge or counterevidence remains after prior progress.

Merely asking a question is never sufficient evidence for a learning edge. The prompt must show explicit uncertainty, repeated unresolved misunderstanding, or clear correction evidence. A later assistant answer does not resolve an edge by itself; the user must later use, explain, diagnose, transfer, or validate the concept correctly. The current state and breadth are published deterministically to `Memory/Learning.md`, while event details remain machine-local and evidence-backed.

Discovery time and work time are separate. A delayed or one-time backlog pass may discover old evidence today, but the learning event keeps its original occurrence date. The dashboard's “today” view admits only signals whose cited evidence occurred on that Daily's date; older discoveries are summarized only as historical recovery and are not repeated on future days. An explicit owner suppression stores the topic's current `last_seen` boundary and hides it from canonical/dashboard trajectories until a strictly newer event appears.

The Personal Learning trajectory is narrower than the machine learning registry. A topic supported in only one project/context remains project knowledge there; it enters the personal trajectory only after transfer across at least two independent contexts or a verified attributed outcome. This prevents one project's prompt limit, deployment choice, or implementation constraint from masquerading as a personal-profile feature.

Daily and weekly journals lead with evidence-backed learning about the user: judgment, demonstrated understanding, capabilities, preferences, voice, work style, goals, and longitudinal movement. Project changes remain a concise work-context section because they explain where the evidence came from, but they do not dominate the synthesis. Coverage separately reports how many projects changed, how many sessions were reviewed, how many were linked to projects, and how many remained profile-only.

Project-scoped facts and decisions never become personal-profile cards merely because the user made the decision. Numeric prompt limits, output formats, deployment targets, named-agent architecture, and project folder conventions stay in project knowledge unless separate evidence supports a genuinely reusable personal preference.

If a collector or parser is upgraded while bootstrap is still awaiting review, run `sb bootstrap --refresh-evidence`. It performs a source-integrity-checked collection and bounded synthesis, then regenerates the final review packet. The command refuses to run after bootstrap completion.

## Deferred objective questions

Weekly synthesis receives a bounded list of unresolved objective project questions alongside new evidence. It may close a question only when the question is machine-resolvable, the answer cites current packet evidence, confidence is at least 0.90, and the host verifies either an authoritative deterministic artifact or two independent consistent signals. Identity, privacy, disclosure, employment, education, military, authorship, ownership, career, and preference decisions are never eligible for automatic resolution.

The owner can dismiss a pending question as not relevant and undo the latest dismissal. The protocol stores only the question category and decision as feedback, not a guessed reason. Repeated dismissals can suppress future questions from the same low-value category; an answered question counts as positive feedback, and feedback alone can never create a personal fact.

An owner answer is never copied verbatim into durable memory. A dedicated structured evaluation normalizes the answer into private destination-specific claims; the host rejects unknown project IDs, project claims in the wrong lane, and inferred public approval before the evidence is queued for deterministic publication.
