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

- Sunday through Friday: daily incremental pipeline.
- Saturday: daily pipeline followed by weekly synthesis.
- Empty day: record the run without a model call.
- Missed run: start as soon as possible after the user logs in.
- Existing run: ignore the overlapping instance.
- Failed run: preserve evidence and checkpoints for retry.

Daily output focuses on activity, implementations, decisions, blockers, candidate skills, and sanitized voice samples. Weekly output connects trajectories, wins, lessons, repeated work patterns, stale claims, contradictions, and next-focus suggestions.

## 8. Configure Git safely

Use a repo-local identity for generated commits. Prefer a repo-scoped SSH deploy key for private unattended pushes so normal GitHub CLI account switching cannot redirect automation.

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
- push an automation branch;
- open or update a draft pull request;
- require a human to merge it.

## 9. Verify the installation

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

## 10. Operate the brain

Useful recurring commands:

```powershell
uv run --locked sb review digest
uv run --locked sb search "recent project progress"
uv run --locked sb write-as-me "draft a short professional bio"
uv run --locked sb career "prepare a role-specific summary"
uv run --locked sb refresh-project "Project Name"
uv run --locked sb health
```

Keep public-facing claims under review. Let repeated work-style and voice observations accumulate across projects and dates before treating them as stable.

## Recovery

Do not reset checkpoints manually. Fix the failing dependency, account, Git state, marker, or source parser, then rerun the same incremental command. The previous checkpoint remains valid because it advances only after successful publication.

See the [Recovery runbook](../runbooks/Recovery.md) for specific failure modes.
