---
title: Setup
type: note
permalink: personal-vault/protocol/runbooks/setup
---

# Setup Runbook

1. Run `uv sync --all-groups` from the standalone protocol repository. When `Protocol/` is embedded in a private vault, use `uv sync --project Protocol --all-groups`.
2. Run `uv run sb setup` to create machine-local runtime state.
3. Edit the generated runtime configuration with vault and project roots, Codex and Antigravity history roots, private/public repository names, task name, and confirmed identity aliases used for Git attribution.
4. Authenticate the standalone Codex CLI inside the isolated runtime home using a dedicated ChatGPT account.
5. Run `uv run sb models check`. Every locked role must pass without fallback.
6. Start bootstrap with `sb bootstrap --linkedin-export <PDF, ZIP, or directory>` and answer one narrow question at a time with `sb interview next` and `sb interview answer`.
7. Resume `uv run sb bootstrap` until the canonical notes and final review packet are ready.
8. Run `sb review digest`. Review important topic pages; the complete pending queue does not need to be cleared.
9. Approve bootstrap only after the canonical notes and health gates are acceptable.
10. Only then create/push GitHub repositories, open the sanitized public draft PR, and install the scheduled task.

Never use real source projects as the protocol workspace. The scanner reads them; it does not install or execute them.
