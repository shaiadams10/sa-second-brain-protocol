# Second Brain Protocol v2

A weekly job that reads your AI coding conversations and your projects folder, and keeps a private Markdown vault about you up to date: who you are, how you work, what you're building, and what you learned each week.

Version 2 is a rewrite. Version 1 was a large governed pipeline that grew hard to run and had stopped producing anything useful. V2 keeps one good idea from it (the model proposes, you approve) and drops the rest.

## How it works

1. **Collect, without AI.** Parsers read the local history of Codex, Claude Code, and the Antigravity app. They keep what you said and each assistant's final reply, and drop tool calls, tool output, reasoning, and injected context. A 170 MB session shrinks to a few kilobytes.
2. **Find your projects, without AI.** A scanner classifies every folder under your projects root:
   - **project**: has its own project files. Its subfolders are parts of it.
   - **group**: a folder like `Utilities & Automation` that holds several projects. Each child is tracked separately.
   - **loose**: files but no project structure (homework, a scenes folder). Tracked as one small item.
   - **copy**: worktrees, backups, and legacy copies. Their activity counts toward the original.

   When files alone can't tell a monorepo from a collection of separate projects, the scanner asks you once and remembers the answer.
3. **Build the weekly digest, without AI.** Each project gets an attention score from active days, conversations, file changes, and commits (git is optional). Exchanges where you praised, corrected, or interrupted the assistant are always kept, because that's where your preferences show.
4. **Summarize, with a cheap model.** *(next milestone)* The Antigravity CLI (`agy -p`) turns the digest into a weekly log and proposes updates to your profile and project notes. The output is forced into a fixed JSON structure, and nothing about you changes without your approval.

## Commands

```
brain scan                  show how your projects folder is classified
brain digest --week last    build last week's digest (also: this, or 2026-W39)
```

Run from inside your vault, or pass `--vault` or set `BRAIN_VAULT`.

## Vault setup

The engine holds no personal data. Everything about you lives in your private vault:

```
<vault>/brain/config.toml      your paths and git identities
<vault>/brain/projects.json    your decisions: which folders are collections, which projects are real
<vault>/.brain/                working files such as digests (keep out of git)
```

Example `brain/config.toml`:

```toml
projects_root = "D:/Projects"
git_authors = ["you@example.com", "noreply@anthropic.com"]

[sources]
codex = "~/.codex/sessions"
claude_code = "~/.claude/projects"
antigravity = "~/.gemini/antigravity/brain"
```

## Privacy

- Your conversations never leave your machine during collection. Only the week's digest goes to the model, with API keys, tokens, and passwords masked first.
- The Antigravity CLI keeps its own history separately, so the brain's own model runs are never read back in.
- Do not commit a vault, digest, or config with personal data to this repository.

## Development

```
python -m unittest discover -s tests
```

No dependencies beyond Python 3.12+.

## License

MIT
