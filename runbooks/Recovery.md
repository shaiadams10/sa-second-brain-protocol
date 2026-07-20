---
title: Recovery
type: note
permalink: personal-vault/protocol/runbooks/recovery
---

# Recovery Runbook

- Failed runs do not advance checkpoints. Read the newest machine-local run receipt and rerun the same command.
- Every daily or weekly attempt records its trigger and current pipeline stage before collection begins. A failure must retain the stage and sanitized error in machine-local state, appear in dashboard system health and recent runs, and leave an immediately created scheduled log. A scheduler exit without a completed pipeline receipt is treated as an interrupted run, never as success.
- A retry preserves each evidence item's original occurrence timestamp, but the current daily publisher writes one catch-up summary under the retry's local calendar date. It does not automatically create a separate backfilled daily note for every missed date.
- If authentication fails, reauthenticate only inside the isolated automation `CODEX_HOME`.
- If a model is unavailable, do not change roles automatically; run `sb models check` after access returns.
- If a generated section is malformed, repair the markers manually and resolve its review item.
- If Git has diverged, stop automation and resolve it interactively; never force-push.
- If the local state database is lost, restore its machine backup or recollect evidence. Deterministic evidence IDs prevent duplicate promotion.
- If a JSONL file is truncated or rewritten, the prefix-hash check automatically falls back to a full deduplicated read. Do not edit checkpoints manually.
- If an Antigravity DB/WAL snapshot fails, the collector uses the immutable main database and catches up after SQLite checkpoints the WAL. Source-side database files remain untouched.
- If an Antigravity conversation has no usable project metadata, retrying collection cannot create the missing association. Prefer explicit Projects in the standalone Antigravity application for future sessions; leave legacy sessions unmatched or use one previewed owner-confirmed session link. Never rewrite Antigravity history databases or infer ownership from chat text.
- A recurring pattern marked `conflict` requires category clarification; do not force it across the promotion threshold.
- A completed bootstrap is not rerun. Use `sb refresh-project`, `sb daily`, `sb weekly`, or `sb interview`.
