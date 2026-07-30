# 🧭 Implementation guide

This guide turns the reusable protocol repository into a working private personal second brain. Complete the phases in order; each phase establishes a safety boundary used by the next one.

## 1. Create the two-repository layout

Use two independent Git repositories:

1. A **private Obsidian vault** for personal identity, experience, projects, skills, memories, goals, journals, review notes, and the canonical `Protocol/` tree.
2. A **public protocol repository** created only by the deterministic allowlist exporter.

Do not work directly in the generated public checkout. Changes flow from the private canonical `Protocol/` tree to a public draft pull request.

Keep all operational state outside both repositories in a machine-local runtime directory. That directory contains authentication, SQLite checkpoints, raw evidence, staging packets, logs, search indexes, and code graphs.

## 2. Install the protocol

```powershell
git clone https://github.com/YOUR_GITHUB_USER/sa-second-brain-protocol.git
Set-Location sa-second-brain-protocol
uv sync --locked --all-groups
uv run --locked sb setup
```

`sb setup` creates the runtime skeleton and installs a standalone Codex CLI inside the isolated runtime. It does not scan projects yet.

## 3. Configure machine-local sources

Edit the generated `runtime.json`. At minimum, verify:

- private vault root;
- projects root and explicit ignored paths;
- Codex active and archived visible-session roots;
- Antigravity conversation and artifact roots;
- private and public GitHub repositories;
- confirmed Git owners, author names, and author emails;
- known forks, vendors, duplicate repositories, and excluded authors;
- Windows task name.

Treat authorship as a security decision. A name fragment alone is not enough to attribute third-party code. Confirm repository ownership, email, remote ownership, or explicit user direction.

## 4. Isolate the automation account

```powershell
uv run --locked sb auth login
```

Complete the browser login with the account dedicated to unattended second-brain reasoning. Its isolated Codex home must not share credentials, sessions, connectors, MCP servers, web search, or unrelated skills with everyday coding-agent accounts.

Then verify every locked role:

```powershell
uv run --locked sb models check
```

If any role is unavailable, stop and correct the account or model policy. The protocol does not silently substitute another model.

## 5. Run the one-time bootstrap

```powershell
uv run --locked sb bootstrap --linkedin-export <path-to-export>
```

The bootstrap is resumable before approval. It inventories repositories, classifies project ownership, examines technical evidence, collects visible session history, builds derived code graphs for eligible first-party projects, imports supported documents, and creates narrow guided-interview questions.

The collector must never execute project code, install dependencies, invoke hooks, or write into source projects. Capture a source-tree integrity manifest before and after the audit when validating a new installation.

Continue interrupted synthesis with:

```powershell
uv run --locked sb bootstrap
```

Answer profile gaps without writing a biography:

```powershell
uv run --locked sb interview next
uv run --locked sb interview answer <question-id> "<answer>"
```

## 6. Review and close bootstrap

Generate the short dashboard:

```powershell
uv run --locked sb review digest
```

Review canonical identity, experience, project, skill, and timeline notes plus the final bootstrap packet. The full machine ledger does not need to be cleared; pending observations are safe because they cannot promote themselves.

Only after explicit approval:

```powershell
uv run --locked sb bootstrap --approve
```

Approval writes the one-time completion marker, installs the Windows task, runs a scheduled-task canary, publishes Git safely, and closes normal bootstrap commands. Later corrections use `sb daily`, `sb weekly`, `sb refresh-project`, `sb interview`, or direct reviewed note edits.

## 7. Understand daily and weekly operation

The Windows task runs once each day at the configured local time:

- Sunday through Friday: daily incremental pipeline, plus a missing-week catch-up when the latest weekly note was not completed.
- Saturday: daily pipeline followed by weekly synthesis.
- Empty day: record the run without a model call.
- Missed run: skip the missed window and wait for the next 10:30 PM schedule unless the owner directly requests one manual Daily.
- Existing run: ignore the overlapping instance.
- Failed run: preserve evidence and checkpoints for retry.

Daily output leads with evidence-gated learning about the person: demonstrated judgment, capabilities, preferences, voice, work style, goals, and longitudinal movement. A concise named project-change ledger and one sanitized recap for every attributed session remain as supporting work context. Late-arriving sessions are labeled as backfill instead of being presented as same-day work. Weekly output connects trajectories, wins, lessons, repeated work patterns, stale claims, contradictions, and next-focus suggestions, then adds deterministic stewardship checks for attribution coverage, review load, missing projects, journal continuity, and recent pipeline failures. Daily and weekly indexes are rebuilt inside bounded generated sections.

## 8. Use the natural-language interface and local dashboard

Users do not need to remember the commands in this guide. When an agent is opened in the vault workspace, a plain request such as “create a resume for this job,” “what changed this week,” or “open my dashboard” routes to the matching internal operation. Commands remain available for maintainers, tests, automation, and recovery.

An explicit request to forget a project first produces an exact-match impact preview. Confirmation creates a private state/file backup, protects named successor projects, removes active canonical/state/derived project knowledge, adds the old source path to collection exclusions, rebuilds review/search/dashboard outputs, and leaves the source repository untouched. Durable skills and capabilities supported by the retired work remain, but their retained evidence is reduced to a content-free support stub when necessary. This operation is intentionally separate from hiding a dashboard card or marking a source temporarily missing. A missing folder is reported and excluded from the active catalog, but it is never forgotten automatically because the protocol cannot safely distinguish deletion from a move or temporarily unavailable storage.

The local dashboard has two modes. Its generated HTML file is a read-only fallback. Normal use starts a loopback-only server bound to `127.0.0.1`, which rebuilds the same private view on request and enables allowlisted owner actions for Knowledge Deck confirmation/retraction/undo and individual question answers. Its populated output lives under the machine-local runtime and is never committed. The dashboard contains canonical knowledge, structured daily and weekly summary logs, safe question text, aggregate run metadata, and a sanitized owner-only browser for pending public and private claims.

The latest daily briefing separates original work time from discovery time. Only evidence that occurred on the Daily's own date appears as “today”; delayed backlog discoveries are counted as historical recovery and retain their original dates. This rule applies to every delayed project and session, not a topic-specific denylist: an archive section backed entirely by older evidence is labeled as historical recovery and its stale learning cards stay hidden unless newer evidence advances them. The summary archive defaults closed, lists project-linked sessions only, and replaces individual profile-only/unattributed cards with one compact coverage explanation. It shows quick points first and reveals the complete generated section on demand without inventing interpretation beyond canonical notes. Summary counts, groups, and claims carry sanitized provenance descriptors. Designed evidence popovers are portaled to the viewport, remain anchored beside their exact source chip across transformed cards, scroll, and resize, animate open and closed, support hover and keyboard focus, and open canonical Obsidian notes from their rows. Raw conversations, paths, machine identifiers, and individual unattributed-session cards remain excluded. The question deck shows one clarification at a time with its category explanation, answer guidance, and deterministic choices where a category has useful standard states. Saving records an explicit private answer; Answer later makes no change.

The Knowledge Deck shows promoted canonical observations, never pending claim text. Pending public and private observations appear separately in a read-only review modal with sanitized claim text and human labels; they do not become knowledge merely by being visible. No action or Skip leaves knowledge unchanged. Confirm records explicit owner approval. Retract removes only matching lines inside valid `sb:generated` sections and tombstones the observation while preserving evidence, provenance, and all manual prose. Undo restores the most recent retraction and its previous confirmation state. Publication failures roll the action back; semantic-search refresh is derived work queued durably after the decision, performed in the background for changed notes only, and retried without reversing the owner's choice.

Confirm and Remove also update a bounded implicit feedback profile. No reason prompt is shown. The system compares aggregate categories across confirmed and removed cards—such as durable career facts, one-off task instructions, temporary status snapshots, or implementation mechanics generalized into personality—and feeds only stable ratios into later synthesis. A single removal cannot establish a preference by itself.

The deck opens on **About the person**, not on a mixed chronological feed. Layer tabs separate personal identity, professional evidence, operating preferences, and project knowledge. Kind-based deterministic classification reorganizes existing observations immediately; daily and weekly prompts maintain the same boundary for future observations. Narrow technical instructions remain useful operating or project context without being presented as personality.

`Memory/Learning.md` is the canonical longitudinal learning view. It is separate from stable identity and lists each tracked topic's current state, assessment, signal history, and breadth across sessions, dates, known projects, and profile-only contexts. Treat an open learning edge as temporary evidence, not a lasting limitation.

When the owner marks a historical topic as no longer useful to surface, `sb learning suppress <topic-key>` records its current evidence boundary, refreshes the canonical learning view and dashboard, and leaves the underlying dated evidence intact. The topic automatically becomes visible again only if later evidence advances its `last_seen` timestamp.

Install the optional Windows launcher once through the dashboard install action. It deterministically generates a local `.ico`, creates Desktop and current-user Start-menu shortcuts, and points both at the root BAT. The Start-menu entry can then be right-clicked and pinned normally. The BAT uses ASCII-only source so Windows command-shell code pages cannot reinterpret decorative characters as commands. A vault may copy `templates/entrypoints/start-dashboard.bat` to its root and personalize the banner. Normal daily use is then a shortcut or BAT file, not a remembered terminal command.

The launcher is deliberately on-demand; this protocol does not add a Windows-logon startup task. It first checks the loopback health endpoint. If the server is already running, it opens the browser immediately and exits. Otherwise the dashboard command reuses the existing hardened runtime and avoids repeating isolated-account configuration and Windows ACL setup before serving.

## 9. Configure Git safely

Use a repo-local identity for generated commits. Prefer a repo-scoped SSH deploy key for private unattended pushes so normal GitHub CLI account switching cannot redirect automation.

Scheduled Daily and Weekly publication privacy-scan and commit allowlisted private-vault changes before pushing the configured private remote. A deliberately local test Daily skips that Git stage, so temporary tracked edits are expected until a later scoped snapshot. The dashboard distinguishes tracked edits awaiting that snapshot from untracked owner files, which remain local until an allowlisted privacy-scanned commit includes them. Reusable protocol changes live under `Protocol/` and flow through the separate sanitized draft-PR exporter so the public example cannot receive private vault content.

Private publication must:

- snapshot safe tracked edits before generated changes;
- scan for secrets and privacy violations;
- refuse remote divergence and merge conflicts;
- never force-push;
- exclude raw evidence, credentials, staging, runtime logs, and absolute machine mappings.

Public publication must:

- export only the allowlisted protocol tree;
- genericize private configuration;
- scan personal names, paths, secrets, and private project identifiers;
- fingerprint the sanitized export and skip all GitHub access when it is unchanged;
- run the canonical protocol tests, then run the exact sanitized export's tests from inside its own root before any GitHub access;
- push an automation branch;
- open or update a draft pull request;
- retain the previous fingerprint and retry after test, privacy, authentication, network, or push failures;
- require a human to merge it.

The Windows scheduled pipeline performs this change-detected public sync after its normal second-brain processing and dashboard refresh. This keeps the example repository current without exposing private vault content or publishing directly to `main`.

## 10. Verify the installation

```powershell
uv run --locked pytest
uv run --locked sb health
uv run --locked sb reindex
```

Also verify:

- a second run over identical evidence creates no duplicate notes or commits;
- hostile instructions inside collected text remain inert quoted evidence;
- manual Markdown outside generated markers survives publication;
- malformed markers go to review rather than being overwritten;
- project source manifests are unchanged;
- the public draft contains no private vault content;
- scheduled task is enabled, interactive, single-instance, and start-when-available;
- private pushes use the intended repository-specific SSH identity.

## 11. Operate the brain

Useful recurring commands:

```powershell
uv run --locked sb review digest
uv run --locked sb search "recent project progress"
uv run --locked sb write-as-me "draft a short professional bio"
uv run --locked sb career "prepare a role-specific summary"
uv run --locked sb refresh-project "Project Name"
uv run --locked sb health
```

Keep public-facing claims under review. Let repeated work-style and voice observations accumulate across independent contexts and dates before treating them as stable. Unmatched or ambiguous sessions may contribute only through the profile-only lane; publication must reject any attempt to turn them into project knowledge. Let topic-level learning events accumulate and evolve rather than converting a single question into a knowledge-gap claim.

## Recovery

Do not reset checkpoints manually. Fix the failing dependency, account, Git state, marker, or source parser, then rerun the same incremental command. The previous checkpoint remains valid because it advances only after successful publication.

See the [Recovery runbook](../runbooks/Recovery.md) for specific failure modes.
