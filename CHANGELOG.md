# 📝 Changelog

## Unreleased

- Added exact-match project forgetting with preview, successor protection, backup, source-ignore protection, canonical/state/evidence cleanup, derived-artifact removal, and search/dashboard rebuilds while leaving source repositories untouched.
- Added implicit learning from Knowledge Deck confirmations/removals: aggregate feedback categories now guide daily, weekly, and bootstrap synthesis, and strongly repeated negative categories are deterministically suppressed without asking the owner for a reason.
- Excluded generic source-folder names such as `src` from project discovery and attribution, and blocked machine-resolvable or omnibus review questions before they enter the owner queue.
- Cut dashboard refresh latency by batching project/evidence reads and checking independent scheduler/account status concurrently; explicit owner project corrections now override noisy source-session attribution for both display and saved answers.
- Made dashboard questions destination-aware: project answers now become project evidence, cards show project/profile scope, tailored editable answer starters support Tab completion, and low-value technical questions stay deferred.
- Reduced Curate noise by excluding routine stacks, feature inventories, narrow asset choices, and one-off project instructions while restoring genuinely reusable preferences to How I Work.
- Rebuilt the Today briefing as a compact visual scan with an at-a-glance summary, emoji metrics, titled signal groups, and bounded chips.
- Separated contextual project instructions from global personal preferences in the Knowledge Deck, added diagonal project-attribution stamps, and changed Confirmed-card controls to Remove, Previous, and Next.
- Added synthesis scope metadata so a direct instruction within one task no longer promotes automatically as a global personality or work-style claim.
- Added a paper-and-pixel local dashboard with daily briefing, project momentum, learning, pattern maturity, review, run, schedule, and health views.
- Structured daily briefings into named activity and learning sections, and added expandable daily/weekly summary history with quick points and complete canonical detail.
- Replaced duplicated summary bullet blocks with visual metric tiles, project chips, human-readable change badges, session coverage, and pattern-status cards.
- Added per-summary model-usage receipts with exact input, cached-input, output, and total token counts for new runs plus legacy total-token recovery.
- Added bounded marker-based discovery for nested non-Git projects inside grouping folders while retaining the parent collection.
- Added Knowledge Deck review-state emphasis and consolidated the patterns, review, and health cards into one dashboard row.
- Added an owner-only dashboard question deck with category explanations, suggested choices, custom answers, and durable explicit-answer evidence.
- Standardized Knowledge Deck presentation on the owner's preferred first name and instructed future synthesis to use `the user` instead of generic `the user` wording.
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
