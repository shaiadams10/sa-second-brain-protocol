# Second Brain Protocol v2

A weekly job that reads your AI coding conversations and your projects folder, and keeps a private Markdown vault about you up to date: what you worked on each week, which projects are real and which were experiments, and what your conversations show about how you work.

It runs on its own. Nothing waits for approval. You watch it from a local dashboard and strike anything it got wrong; struck items are remembered and never proposed again.

Version 2 is a rewrite. Version 1 was a large governed pipeline that grew hard to run and had stopped producing anything useful. It remains in the history of `main`.

## How it works

1. **Collect, without AI.** Parsers read the local history of Codex, Claude Code, and the Antigravity app. They keep what you said and each assistant's final reply, and drop tool calls, tool output, reasoning, and injected context. A 170 MB session shrinks to a few kilobytes. API keys, tokens, and passwords are masked.
2. **Find your projects, without AI.** A scanner classifies every folder under your projects root:
   - **project**: has its own project files. Its subfolders are parts of it.
   - **project with sub-projects**: one project whose parts you want tracked by name.
   - **group**: a folder like `Utilities & Automation` that holds several projects, each tracked separately.
   - **loose**: files but no project structure (homework, a scenes folder). Tracked as one small item.
   - **copy**: worktrees, backups, and legacy copies. Their activity counts toward the original.

   When files alone can't tell a monorepo from a collection of separate projects, the dashboard asks you once and remembers.
3. **Build the weekly digest, without AI.** Each project gets an attention score from active days, conversations, file changes, and commits (git is optional). Exchanges where you praised, corrected, or interrupted the assistant are always kept, because that's where your preferences show.
4. **Summarize, with a cheap model.** The Antigravity CLI (`agy -p`) reads each project's week and returns JSON against a fixed schema; the newest Gemini Flash your account offers is picked automatically. Code, not the model, writes the vault.
5. **Only what recurs becomes part of you.** An observation about you stays a pencilled candidate until it shows up in two different projects or two different weeks. One-off instructions are dropped. Sensitive matters (coursework, grades, health, money, accounts) stay high level.
6. **Projects earn their status.** Ongoing means real attention in 3 of the last 8 weeks, or marked by you. A project that goes quiet is shown neutrally as "on hold since", never as abandoned.

Every run and every edit you make on the dashboard is committed to the vault's git history.

## Commands

```
sbrain run                   catch up every finished week not yet logged
sbrain run --current         also write the week in progress
sbrain run --week 2026-W39   (re)write one week
sbrain dashboard             open the logbook dashboard
sbrain install               Windows: weekly scheduled run + desktop shortcut
sbrain uninstall             remove both
sbrain scan                  show how your projects folder is classified
sbrain digest --week last    build a week's no-AI digest, for inspection
```

Install with `uv tool install --editable <this repo>`. Run inside your vault, or pass `--vault` or set `BRAIN_VAULT`.

## The dashboard

A local page on `127.0.0.1` in the shape of a pilot's logbook:

- **Log**: each week is a ruled page, one line per project with its remark, totals carried forward, and the week's headline, highlights, and what was learned in the margin.
- **Projects**: ongoing, exploring, on hold, and earlier projects with an 8-week attention strip; mark a folder as a real project or not; answer folder questions; set how any folder is treated.
- **About you**: inked (confirmed) and pencilled (candidate) entries with the quotes behind them. Strike one and it is gone for good, with undo.
- **Runs**: run history, the next scheduled run, and Run now with live progress.

## Vault setup

The engine holds no personal data. Everything about you lives in your private vault:

```
<vault>/brain/config.toml      owner name, paths, git identities
<vault>/brain/projects.json    your decisions about folders and real projects
<vault>/brain/knowledge.json   everything the brain has learned (rendered to Markdown)
<vault>/log/, projects/, me/learned.md   the generated notes
<vault>/.brain/                working files: digests, run log (keep out of git)
```

Example `brain/config.toml`:

```toml
owner = "Alex"
projects_root = "D:/Projects"
git_authors = ["you@example.com", "noreply@anthropic.com"]
auto_commit = true

[sources]
codex = "~/.codex/sessions"
claude_code = "~/.claude/projects"
antigravity = "~/.gemini/antigravity/brain"
```

## Privacy

- Collection happens on your machine. Only each week's bounded digest goes to the model, with secrets masked.
- The Antigravity CLI keeps its own history separately, so the brain's model runs are never read back in.
- Never commit a vault, digest, or config with personal data to this repository.

## Development

```
python -m unittest discover -s tests
```

No dependencies beyond Python 3.12+.

## License

MIT
