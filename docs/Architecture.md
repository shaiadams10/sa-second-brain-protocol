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
| Local dashboard | Derived plus narrow owner feedback | Safe briefing and Knowledge Deck served only on loopback; static fallback remains read-only |
| Model output | Candidate only | Proposed summaries, claims, updates, and review items |

Derived indexes may be rebuilt. Canonical Markdown and explicit review decisions must remain durable.

The dashboard is also rebuildable. Its generic template and deterministic generator belong to the protocol, while its populated HTML stays outside Git. It may contain canonical summary excerpts, the complete generated sections of daily/weekly notes, safe pending-question text, and aggregate operational metadata, but never raw evidence, unresolved candidate-claim wording, credentials, local paths, session identifiers, or staging packets.

Interactive dashboard actions pass through a separate loopback-only boundary. The server binds to `127.0.0.1`, serves no arbitrary files, validates same-origin requests with an in-memory token, limits request bodies, and exposes only confirm, retract, undo, answer-question, dismiss-question, and undo-question-dismissal routes. Confirm writes operational feedback only. Retract invokes the deterministic publisher to remove the observation exclusively from valid generated sections and records a durable rejection tombstone. Undo restores the last retraction. A question answer records explicit private evidence with a deterministic project or profile destination and resolves only that pending clarification. A question dismissal records that its category was not relevant without inventing a reason; undo restores the latest safely matched dismissal. Both actions refresh the derived review dashboard. Manual prose and raw provenance are never deleted.

Search indexing remains derived and must not delay an explicit owner decision. Retraction and undo queue their changed Markdown paths in the same SQLite transaction as the decision, return immediately, and wake one debounced background worker. The worker updates only those files in the Basic Memory mirror and retries durable queue entries after a failure or restart. While a retraction is waiting to be indexed, service search filters its tombstoned claim and observation ID so stale derived results cannot resurface removed knowledge.

Daily and weekly synthesis periods persist an explicit evidence mapping in local state. The dashboard uses that mapping to build sanitized, hoverable provenance surfaces and canonical Obsidian links for project changes, session coverage, patterns, and stewardship claims. Legacy summaries may recover the same mapping from machine-local validated model-cache receipts, but raw cache content is never rendered. Journal indexes are deterministic bounded publishers, not manually maintained lists.

The human Knowledge Deck separates promoted observations into four deterministic layers. **About the person** contains identity, voice, work style, personality, enduring facts, and goals. **Professional profile** contains capabilities, experience, education, service, and verified skills. **Operating preferences** contains reusable agent behavior, formats, validation rules, design taste, and protocols. **Project knowledge** contains decision-relevant project status, ownership, decisions, and lessons. Routine stack inventories, feature lists, technical trivia, and one-off project instructions remain in project summaries instead of becoming Curate cards. Project-scoped cards carry a visible project-attribution stamp; reusable preferences stay in the operating layer even when their evidence originated in one project.

Time-varying understanding is kept separate from permanent identity. A longitudinal learning registry records dated evidence for learning edges, demonstrated understanding, later application, architectural judgment, operational capability, validated outcomes, and counterevidence under stable topic keys. Its deterministic current state and cross-session/date/project/context breadth are published to `Memory/Learning.md`. An open edge is explicitly temporary and later correct use may supersede it.

The question deck surfaces only owner-judgment questions and defers agent-resolvable technical gaps. Every question declares its project/profile destination, shows available project attribution, provides question-specific editable answer starters, and supports Tab completion without saving until the owner explicitly presses Save. Project resolution considers the complete project catalog instead of favoring a noisy source-session candidate; an explicit owner correction is stored on the pending observation and overrides inference for both the visible stamp and any later saved answer.

Every direct child of a configured projects root is scanned as a candidate identity, regardless of its name or technology markers. Collection behavior is explicit and path-scoped: only configured organizational folders are treated as zero-count containers, and their direct children are scanned independently. Ordinary projects never become collections merely because they contain nested repositories or source trees. The canonical catalog then separates three roles deterministically: leaf folders with project content become projects, configured collection containers remain collections, and content-free candidates remain visible only as other folders rather than masquerading as projects. Generic directories such as `src`, `app`, `packages`, and `lib` are suppressed when encountered recursively inside a real project, preventing accidental project explosion. The deterministic publisher rejects machine-resolvable questions, multi-project questions, mixed-decision questions, and project questions whose evidence does not resolve to exactly one named leaf project.

`Projects/Index.md` is rebuilt from the latest scanner result inside its bounded generated section. Project names and note paths come from scanner metadata; a model may update the knowledge section of a known leaf dossier but cannot append an alias, create a collection dossier, or choose the catalog identity. Duplicate scanner identities are omitted, collection containers are shown separately, and stale generated index entries disappear on the next catalog sync while manual prose remains untouched. Health compares the generated catalog with the present runtime registry so drift is visible.

Knowledge confirmation/removal and question answering/dismissal form an implicit feedback profile. The profile stores only aggregate content or question categories and counts, never a guessed free-text reason. Daily, weekly, and reusable bootstrap synthesis use those aggregates as ranking guidance, while deterministic guards suppress categories that the owner repeatedly removes or dismisses with a strong negative ratio. Feedback metadata is not biographical evidence and can never justify a personal claim.

Dashboard snapshots batch project and evidence reads for each deck instead of querying once per card. Independent scheduler and automation-account status checks run concurrently, keeping a browser refresh bounded as the review inventory grows.

## One-time bootstrap

```text
not_started → collecting → synthesis_ready → awaiting_review → completed
```

Pre-completion runs resume from checkpoints. After approval, bootstrap is permanently closed and incremental refresh commands must be used.

## Incremental operation

Daily runs collect new evidence, skip the model on empty days, evaluate each session through both project and person lenses, synthesize one bounded update, publish a terse change recap plus explicit coverage details, update the learning and pattern registries, reindex, and notify. Weekly runs add a short trajectory recap, cross-project lessons, stable patterns, learning progression, and review-backlog summaries. Byte/row cursors avoid reparsing published history, project snapshots produce explicit before/after deltas, and durable registries accumulate semantically repeated preferences and topic-level learning evidence across sessions before promotion or state change.

Canonical notes intentionally contain synthesized durable knowledge, not a queryable copy of raw conversations. Exact latest-session requests therefore use a bounded machine-local lookup that returns sanitized surface, time, project attribution, and last-visible-user-message fields. This preserves the source trust boundary while supporting questions that canonical search cannot answer exactly.

Session collection is metadata-first. A local session-source index reads the Codex header, Antigravity database metadata, or Antigravity aggregate hub metadata before any visible-message body and resolves it to at most one current leaf project through current paths, historical aliases, or unique Git identity. A uniquely matched session enters the `full` analysis lane. A missing or conflicting attribution enters the isolated `profile_only` lane: visible messages and bounded tool metadata may support only person, voice, preference, pattern, goal, and learning analysis. Deterministic publication rejects profile-only evidence used for project facts, project updates, project decisions or lessons, ownership, authorship, skill verification, validated project outcomes, project session summaries, or project-question resolution. For Antigravity conversations marked outside a project, absolute paths embedded in project-facing file and command tool records may recover attribution only when every resolvable path identifies the same project; commands, tool output, reasoning, and path strings are never published. An explicit owner-confirmed session link is stored in machine-local configuration and outranks missing metadata. The project-session index is authoritative for recent-session views and digest attribution. Collection containers cannot own sessions, conversation text cannot create a link, and an ambiguous session is attached to no project rather than several.

Historical session backfill adds a second isolation boundary. Model calls are partitioned by the authoritative project-session index, and the deterministic host accepts only one project update whose ID and cited digests match that packet. All other model output categories are discarded for this operation, preventing a project-history repair from modifying personal identity, career, persona, voice, skills, reusable patterns, or the review-question queue.

Failures preserve unprocessed evidence and do not advance checkpoints.

## Project forgetting

Project disappearance and project forgetting are separate states. A scan that no longer sees a known folder marks it missing, records one removal delta, excludes it from the active catalog and new session attribution, and reports it in health output. It does not erase knowledge because a move, disconnected drive, or accidental deletion is indistinguishable from intentional retirement. If the folder returns, the same project identity is reactivated.

Project forgetting is an explicit destructive operation with a read-only preview and required confirmation. Matching is exact, similarly named protected projects are excluded, and a machine-local backup is created before mutation. The operation removes the project catalog identity, project-specific observations and questions (including orphaned questions that name the exact project), canonical dossier, generated review artifacts, direct project evidence, graph/cache artifacts, and search entries. Shared evidence is stripped of the forgotten project identity; evidence still required by unrelated canonical facts or capability notes is reduced to a content-free support stub rather than deleting durable cross-project learning. The source directory is added to the ignored-path list so later collection cannot reintroduce it. Source repositories are never modified or deleted.

See [Project and session lifecycle](ProjectSessionLifecycle.md) and [Incremental activity and recurring-pattern tracking](IncrementalActivity.md).

## Future MCP boundary

The reserved service layer is read-only. A later transport may expose canonical search, note reading, recent activity, bounded context, and sanitized graph queries. Raw evidence, local paths, ingestion state, and write/delete operations remain unavailable.

## Public protocol synchronization

The private vault's `Protocol/` tree is canonical. After each scheduled run, a deterministic allowlist exporter produces a sanitized generic tree and hashes that exported result—not the private source tree. If the fingerprint matches the last successful publication, the process stops locally without contacting GitHub. If it changed, the full protocol test suite and privacy scan must pass before the automation branch and draft pull request are updated. The fingerprint advances only after a successful push. Failures retry on a later scheduled run, and public `main` always requires manual review and merge.
