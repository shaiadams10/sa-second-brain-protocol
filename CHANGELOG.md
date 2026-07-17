# 📝 Changelog

## Unreleased

- Added a paper-and-pixel local dashboard with daily briefing, project momentum, learning, pattern maturity, review, run, schedule, and health views.
- Added a swipeable Knowledge Deck for promoted observations: no action keeps, Confirm records approval, and Remove safely retracts generated claims.
- Added durable feedback events, generated-section-only retraction, rejection tombstones, and last-removal undo.
- Made Knowledge Deck decisions immediate: changed notes enter a durable, debounced background search-refresh queue; index failures remain retryable without undoing the canonical decision.
- Added incremental Basic Memory mirror updates, pending-tombstone search filtering, and visible background-index health.
- Added an on-demand dashboard fast path that reuses a running server and skips repeated automation-account setup and ACL work.
- Split promoted knowledge into personal, professional, operating-preference, and project layers; the personal layer is now the default Knowledge Deck view.
- Tightened daily and weekly synthesis guidance so project technology and one-off instructions do not become personality or unverified skill claims.
- Added scheduled public-protocol synchronization with sanitized-tree fingerprinting, a test gate, no-network unchanged runs, durable retry behavior, and draft-PR-only publication.
- Added a loopback-only action server with same-origin token validation plus ASCII-safe BAT launchers and deterministic custom-icon Desktop/Start-menu shortcuts.
- Added natural-language agent routing so commands and skill names remain internal details.
- Added a reusable roadmap with explicit read-only MCP safety gates.
- Renamed the public distribution to `sa-second-brain-protocol`.
- Expanded the public landing page and added a complete implementation guide.
- Added Windows protocol checks plus privacy-safe issue and pull-request templates.
- Added protected-main publication guidance while preserving manual draft-PR review.

## 0.1.0

- Added resumable one-time bootstrap and incremental daily/weekly pipelines.
- Added read-only project, Git, Codex, and Antigravity evidence collection.
- Added deterministic evidence IDs, checkpoints, observations, and rejection tombstones.
- Added bounded cloud model roles with structured output and no silent fallback.
- Added Obsidian canonical publication, Basic Memory indexing, and Graphify-derived code graphs.
- Added a short grouped review dashboard with snapshot-safe batch decisions.
- Added private Git safety, sanitized public export, and draft-PR publication.
- Reserved a disabled read-only MCP service boundary.
