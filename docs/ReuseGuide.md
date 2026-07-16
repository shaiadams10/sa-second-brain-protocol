# ♻️ Reuse guide

The protocol is designed to be reusable without copying a person's private vault or machine state.

## Keep these boundaries

- The protocol repository contains generic code, schemas, prompts, templates, tests, and runbooks.
- The private vault contains personal identity, experience, projects, skills, memories, goals, journals, and review notes.
- Machine-local runtime contains paths, credentials, raw evidence, checkpoints, indexes, graphs, staging packets, and logs.
- Source projects remain read-only and independent.

## New personal vault

1. Clone or fork the protocol repository.
2. Create a private Obsidian vault using the note templates.
3. Configure the machine-local runtime with vault and source roots.
4. Add confirmed Git owners, author names, author emails, and explicit exclusions.
5. Authenticate an isolated automation account.
6. Run model canaries before starting bootstrap.
7. Complete bootstrap review before enabling schedules or Git publication.

## Project-local adaptation

For a project-specific brain, reuse only the relevant operating rules, generated-section publisher, review records, and note templates. Do not copy personal identity data into the project. The project brain should understand that project; the personal brain may later observe it through the read-only scanner.

## Agent entrypoints

Keep wrappers thin:

- Codex: an `AGENTS.md` contract plus task-specific skills.
- Antigravity: shared rules, skills, and workflows.

Wrappers should invoke the common `sb` CLI and must not duplicate ingestion, promotion, privacy, or Git logic.

## Public sharing

Publish only through the allowlist exporter. Its output should include generic implementation files and exclude vault notes, local paths, credentials, evidence, session identifiers, indexes, and runtime state. Open a draft pull request and merge manually after inspection.
