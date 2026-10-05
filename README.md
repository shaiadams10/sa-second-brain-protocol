# Second Brain Protocol v2

A weekly job that reads your AI coding conversations and your projects folder, and keeps a private Markdown vault about you up to date: what you worked on each week, which projects are real and which were experiments, and what your conversations show about how you work.

It runs on its own. Nothing waits for approval. You watch it from a local dashboard and strike anything it got wrong; struck items are remembered and never proposed again.

Version 2 is a rewrite. Version 1 was a large governed pipeline that grew hard to run and had stopped producing anything useful.

## How it works

1. **Collect, without AI.** Parsers read the local history of Codex, Claude Code, and the Antigravity app. They keep what you said and each assistant's final reply, and drop tool calls, tool output, reasoning, and injected context (environment blocks, IDE context, instruction files). A 170 MB session shrinks to a few kilobytes. API keys, tokens, and passwords are masked.
2. **Find your projects, without AI.** A scanner classifies every folder under your projects root:
   - **project**: has its own project files. Its subfolders are parts of it.
   - **project with sub-projects**: one project whose parts you want tracked by name.
   - **group**: a folder that holds several projects, each tracked separately.
   - **loose**: files but no project structure. Tracked as one small item.
   - **copy**: worktrees, backups, and legacy copies. Their activity counts toward the original.

   When files alone can't tell a monorepo from a collection of separate projects, the dashboard asks you once and remembers. Folders outside the projects root can be added as named places.
3. **Keep history intact when folders move.** A conversation in a folder that has since moved is matched by name to where that folder lives now. A folder that no longer exists becomes a historical project under its old name, and one rule (`"Old Name": "part-of:New Name"`) merges its history into its successor.
4. **Build the weekly digest, without AI.** Each project gets an attention score from active days, conversations, file changes, and commits (git is optional). Exchanges where you praised, corrected, or interrupted the assistant are always kept, because that's where your preferences show, and so are your questions about how something works, because that's where the edge of what you know shows. Pasted text is marked as pasted, and attached terminal output and agent handoff briefs are dropped: they aren't your words. Sessions another agent started (Codex subagent threads and `codex exec` runs) count as activity on the days they ran, but their prompts were written by that agent, so they are not shown as you speaking and are not counted as conversations or exchanges.
5. **Summarize with a model you choose.** Each project's week goes to a coding-agent CLI you already use, answering in JSON against a fixed schema. Code, not the model, writes the vault:
   - **Antigravity** (`agy -p`): with no model named, the newest Gemini Flash your account offers is picked at run time.
   - **Codex** (`codex exec`): read-only, ephemeral sandbox so the brain's own calls never show up as conversations; with no model named, your Codex default is used.
6. **Only what recurs becomes part of you.** An observation about you stays a pencilled candidate until it shows up in two different projects or two different weeks. One-off instructions are dropped, and anything you struck is never proposed again. Sensitive matters (coursework, grades, health, money, accounts) stay high level.
7. **Skills come from how you talk, not from what the assistant did.** Each week, every topic you touched is noted as one you *directed* (specific direction, catching mistakes, using its terms without asking), were *learning* (asking how it works), or *struggled* with. From that history the brain computes where each skill stands: strong, growing, shown once, learning, or struggled. What you stopped asking about and now direct is what you've learned.
8. **Conversations about you count directly.** Folders listed under `about_me` (usually the vault itself) are where you talk about yourself. There the model also records what you state about yourself, corrections to what the brain believed (the wrong belief is struck), goals you are working out, and advice you took or turned down. These count at once, without waiting to recur.
9. **Themes across projects.** At the end of each run, one call reads the catalog, the skill map, and what you've stated, and names the areas you keep coming back to. Which projects you return to after a break is counted without AI.
10. **Projects earn their status.** Ongoing means real attention in 3 of the last 8 weeks, or marked by you. A project that goes quiet is shown neutrally as "on hold since", never as abandoned.

Every run and every edit you make on the dashboard is committed to the vault's git history. Each run records how many model calls it made and the tokens they used.

## Install

```
uv tool install --editable <path to this repo>
sbrain --vault <path to your vault> install     # Windows: weekly task + desktop shortcut
```

The weekly task runs on the day and time in `[schedule]` (Monday 09:00 unless set), in the PC's local time. Change it, and the CLI, model and effort scheduled runs use, from **Weekly run** on the dashboard's Runs page; saving rewrites `config.toml` and re-registers the task. If the machine was off, Windows starts the run at the next opportunity and every missed week is caught up.

## Commands

```
sbrain run                        catch up every finished week not yet logged
sbrain run --current              also write the week in progress
sbrain run --week 2026-W39        (re)write one week; re-running replaces its earlier result
sbrain run --backfill             walk all history from your first conversation, oldest first
sbrain run --learn                read logged weeks again for skills, communication, and what you stated,
                                  without rewriting their logs (resumes where it stopped)
sbrain run --cli codex --model <id> --effort medium
                                  pick the CLI, model and reasoning effort for this run
sbrain dashboard                  open the logbook dashboard
sbrain install / uninstall        Windows scheduled run and desktop shortcut
sbrain scan                       show how your projects folder is classified
sbrain digest --week last         build a week's no-AI digest, for inspection
sbrain corpus [--exports DIR]     collect every message you typed, plus web chat exports (no AI)
sbrain voice mark|combine|write|test|weekly|newtest|status
                                  the writing-style study, one step at a time (see below)
```

Run inside your vault, or pass `--vault` or set `BRAIN_VAULT`.

A backfill resumes where it stopped: finished weeks are skipped. For a one-time backfill answered by hand (or by another agent reading files), use `--model manual-<name>`: each request is written to `.brain/manual/<n>/request.md` with its schema, and the run waits for `answer.json` next to it.

## Writing-style study

A separate, on-demand study of how you write: your level, grammar, sentence shape, rhythm, wording, shortcuts, and recurring mistakes, not your topics. It ends in a profile another AI can load when you ask it to "write this like me": one part describes how you really type, and an output layer writes correct English at your level and in your shape.

1. `sbrain corpus` collects every message you typed, from every session the sources hold, plus web chat exports (ChatGPT and Claude.ai `conversations.json`, Gemini Takeout `My Activity.json`, or any `{"messages": [...]}` file, loose or still zipped) from the folders under `[voice] exports`, plus writing you did for people from `[voice] samples` (default `brain/voice-samples`). When an export holds an assistant's reply and a coding-agent message contains 12 or more words of it in a row, that stretch is replaced with a pasted placeholder: you had a web chat write it. No AI. Sessions another agent started (Codex subagent threads, `codex exec` runs) are left out, because their prompts were written by an agent. So are injected context, slash commands, and tagged pastes.
2. `sbrain voice mark` has a model read every message in full and mark it: typed, mixed, pasted, or not prose (judged from context and the quality of the English, since many pastes are untagged), what kind of message it is, every error with its fix, habits, and characteristic wording. Marks are cached per message, so new exports only cost what is new.
3. `sbrain voice combine` turns the marks into counts across conversations, sources, and months, holds out 15 messages for a blind test, and draws a spread sample.
4. `sbrain voice write` has a second model write the draft profile from the counts and the sample, and list what the evidence contradicts in your current voice notes.
5. `sbrain voice test` has a fresh model write the held-out situations from the profile alone, and builds a blind page where you pick which message is really yours.

6. `sbrain voice weekly` repeats the blind test every week on messages from the week just finished, which no profile has seen. With `weekly_test = true` under `[voice]` it runs after the scheduled update. Picks and the owner's notes on what gave the model away are saved from the dashboard to `brain/voice-tests.jsonl` and `brain/voice-tests.md` in the vault (scores and notes only, no message text), and the next profile and test read those notes, each with the model message it is about. `sbrain voice newtest` (or Start a new blind test on the Guide card) builds one now, topped up with older untested messages. The test page keeps progress if it is closed, saves itself at the end, and finished tests open as a read-only review. The Guide tab and each week in the Log show the open test and the scores.

An optional `guidance` file (default `brain/voice-guidance.md`) holds what the owner wants from the profile; the writer follows it over its own judgment.

Each step's CLI, model, and effort come from `[voice]` in `config.toml` (`mark_cli`, `mark_model`, `mark_effort`, `mark_workers`, `write_cli`, `write_model`, `write_effort`), with `background` describing your language background for both models. Everything stays in `<vault>/.brain/voice/`; the profile is a draft until you copy it into the vault yourself.

## The dashboard

A local page on `127.0.0.1` with the Signal color theme and Reading Room layout, in light and dark modes. Press the week number to browse the year's saved weeks directly.

The desktop shortcut opens it from a normal terminal window titled "Second Brain dashboard": close that window to stop the dashboard. (Started without a window, it stops itself after three idle hours.) Helper programs it starts (git, PowerShell, the model CLIs) never open windows of their own, here or in the scheduled run.

- **Log**: each week has the complete project ledger, totals carried forward, headline, highlights, observations and skill activity. Wide tables scroll within the page on smaller screens.
- **Projects**: ongoing, exploring, on hold, and earlier projects with an 8-week attention strip; mark a folder as a real project or not; answer folder questions; set how any folder is treated.
- **About you**: inked (confirmed) and pencilled (candidate) entries with evidence quotes and reversible Strike/Undo. Filters include stated facts, communication, goals and advice. Skills show computed progress and evidence history; goals show whether they are open or settled.
- **Runs**: the **Weekly run** settings (CLI, model, effort, day, time for every scheduled run), run history with model and token usage, the next scheduled run, and **Run now** with live progress. The arrow next to Run now picks the CLI, model and effort for that one run. Beside Run now, every page shows the next weekly run's date and time in the browser's local timezone, with a live countdown from Windows Task Scheduler.

## Vault setup

The engine holds no personal data. Everything about you lives in your private vault:

```
<vault>/brain/config.toml      owner, paths, git identities, model settings, extra places
<vault>/brain/projects.json    your decisions about folders and real projects
<vault>/brain/knowledge.json   everything the brain has learned (rendered to Markdown)
<vault>/log/, projects/       the generated weekly logs and project notes
<vault>/me/learned.md, skills.md, themes.md, open-questions.md   generated notes about you
<vault>/.brain/                working files: digests, run log, locks (keep out of git)
```

Example `brain/config.toml`:

```toml
owner = "Alex"
projects_root = "D:/Projects"
git_authors = ["you@example.com", "noreply@anthropic.com"]
auto_commit = true

# Folders whose conversations are about you, not only project work.
about_me = ["Notes Vault"]

# Which CLI answers scheduled runs, and Run now unless you pick something else there.
[model]
cli = "agy"      # "agy" or "codex"
name = ""        # a model id, or "" for the newest Gemini Flash (agy) / your Codex default
effort = ""      # e.g. "low", "medium", "high"; "" for the CLI's default

# When the weekly run starts, in the PC's local time.
[schedule]
day = "Monday"
time = "22:00"

# Project folders outside projects_root.
[places]
"Notes Vault" = "D:/Notes"

[sources]
codex = "~/.codex/sessions"
claude_code = "~/.claude/projects"
antigravity = "~/.gemini/antigravity/brain"
```

Example `brain/projects.json`:

```json
{
  "folders": {
    "Arduino Projects": "project+subprojects",
    "Old App": "part-of:New App"
  },
  "marked": ["New App"],
  "unmarked": []
}
```

Folder rules: `project`, `project+subprojects`, `collection`, `ignore`, `part-of:<id>`.

## Privacy

- Collection happens on your machine. Only each week's bounded digest goes to the model, with secrets masked.
- The brain's own model calls are never read back in as conversations.
- Never commit a vault, digest, or config with personal data to this repository.

## Development

```
python -m unittest discover -s tests
```

No dependencies beyond Python 3.12+.

## License

MIT
