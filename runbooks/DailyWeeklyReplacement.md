---
title: Daily and Weekly replacement
type: note
permalink: personal-vault/protocol/runbooks/daily-weekly-replacement
---

# Daily and Weekly replacement

## Operational status

The owner-authorized cutover is complete. `sb daily`, `sb weekly`, and
`sb scheduled` use `governed-extraction-v3` as their only Daily/Weekly engine.
There is no alternate publication mode. Replay artifacts are authoritative,
authorization-bound, and stored in the governed artifact registry.

The authoritative pipeline now uses an exact canonical Markdown change set for search refresh. Its result contains:

- `changed_index_paths`: added, changed, or deleted canonical Markdown paths;
- `index_refresh`: `changed-only` when a refresh ran, otherwise `none`.

## Replacement behavior

The governed engine:

- splits long sessions into bounded, fully accounted episodes;
- validates each model response independently so an invalid candidate does not erase valid siblings;
- keeps project attribution authoritative and isolates profile-only evidence;
- records exact evidence IDs, source roots, content hashes, checkpoint intent, prompt/schema policy, model contract, and usage;
- blocks checkpoint eligibility on any failed episode, malformed/omitted message, incomplete provenance, or changed evidence snapshot;
- creates deterministic memory entity/version plans for review;
- accepts reusable procedure proposals only from owner-directed, validated full-lane outcomes;
- derives privacy-safe session and period receipts without rereading raw sessions;
- reuses an identical eligible durable artifact on replay; a blocked same-input attempt is retried and may be replaced only until an eligible artifact is stored.

Authorized apply:

- requires a period- and artifact-bound cutover authorization;
- permits edits only inside owned generated sections and only in the authorized Daily/Weekly journal period;
- checks authoritative evidence hashes and persists artifact-bound sanitized episode evidence before committing files;
- recovers through `prepared -> files_committed -> completed`;
- stages memory and procedure review proposals, checkpoints, evidence status, changed-note refresh work, and the receipt in one SQLite transaction;
- leaves canonical memory and executable skills unchanged until their separate review workflows succeed.

## What to inspect after a scheduled run

Run the read-only health check:

```powershell
uv run --project Protocol sb health
```

Inspect:

- `schedule.last_result` and `schedule.next_run`;
- the newest `recent_pipeline_runs` entry, especially `status`, `stage`, and sanitized `error`;
- the newest `recent_runs` model receipt;
- `pending_evidence` and `pending_review`;
- Basic Memory health.

Then inspect the expected canonical output:

- `Journal/Daily/YYYY-MM-DD.md` after a successful Daily;
- `Journal/Weekly/YYYY-Www.md` after Saturday or missing-week catch-up;
- `Journal/Daily/Index.md` and `Journal/Weekly/Index.md`;
- `Inbox/Review/` or the dashboard for review-only observations.

The machine-local scheduled result also reports changed-only index paths. These fields are not necessarily rendered into the Daily note.

## Model-free verification

```powershell
uv run --project Protocol pytest -q
uv run --project Protocol sb harness evaluate
uv run --project Protocol sb harness evaluate --corpus quality
uv run --project Protocol sb harness recall
```

These commands do not run a Daily, call an external model, publish notes, advance checkpoints, or authorize cutover.

## Failure and recovery

A failed governed run follows [Recovery](Recovery.md): fix the recorded stage
failure and let the next scheduled window retry unchanged evidence and
checkpoints. Do not restore the retired execution path. Historical pre-cutover
rows and notes may remain in private backups for audit, but the dashboard and
current journal surface only governed outputs.
