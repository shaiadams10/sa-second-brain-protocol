---
title: Recovery
type: note
permalink: personal-vault/protocol/runbooks/recovery
---

# Recovery Runbook

- Failed runs do not advance checkpoints. Read the newest machine-local run receipt and rerun the same command.
- If authentication fails, reauthenticate only inside the isolated automation `CODEX_HOME`.
- If a model is unavailable, do not change roles automatically; run `sb models check` after access returns.
- If a generated section is malformed, repair the markers manually and resolve its review item.
- If Git has diverged, stop automation and resolve it interactively; never force-push.
- If the local state database is lost, restore its machine backup or recollect evidence. Deterministic evidence IDs prevent duplicate promotion.
- If a JSONL file is truncated or rewritten, the prefix-hash check automatically falls back to a full deduplicated read. Do not edit checkpoints manually.
- If an Antigravity DB/WAL snapshot fails, the collector uses the immutable main database and catches up after SQLite checkpoints the WAL. Source-side database files remain untouched.
- A recurring pattern marked `conflict` requires category clarification; do not force it across the promotion threshold.
- A completed bootstrap is not rerun. Use `sb refresh-project`, `sb daily`, `sb weekly`, or `sb interview`.
