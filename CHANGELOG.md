# 📝 Changelog

## Unreleased

- Separated evidence discovery date from original work date in the dashboard, limited “today” learning to same-day evidence, hid individual profile-only session cards behind compact aggregate coverage, collapsed summary archives by default, and added owner topic suppression that automatically expires when newer evidence appears.
- Hardened the project/person boundary so project-scoped explicit facts normalize to project facts, clarified private Git stewardship states, and added smoother reduced-motion-aware dashboard transitions.
- Added an isolated profile-only analysis lane for unmatched and ambiguous Codex/Antigravity sessions. Their visible interactions can improve personal, voice, preference, pattern, goal, and learning knowledge, while deterministic publication blocks project facts, updates, decisions, lessons, ownership, authorship, skill verification, validated outcomes, project summaries, and project-question resolution.
- Added longitudinal topic-level learning evidence with dated events for learning edges, demonstrated understanding, applied learning, architectural judgment, operational capability, validated outcomes, and counterevidence. The derived state and cross-session/date/project/context breadth are visible in `Memory/Learning.md`.
- Reframed daily and weekly synthesis as a dual-lens evaluation of both work outcomes and what the user's interactions demonstrate, and changed stable-pattern breadth from two known projects to two independent contexts without assigning projects to profile-only sessions.

- Documented the complete project/session lifecycle, including collection-versus-project boundaries, one-project question admission, ownership-versus-learning policy, safe forgetting, automatic incremental discovery, and the Antigravity IDE workspace-association limitation plus the explicit-Project workflow.
- Added safe Antigravity recovery from exact project-facing tool paths plus previewed owner-confirmed session links for conversations Antigravity leaves outside a project.
- Made missing project folders visible in health output while preserving the soft-missing versus explicitly-forgotten safety boundary.
- Project forgetting now removes orphaned clarification questions that name the exact retired project while retaining durable cross-project skills and capabilities.

- Recovered Antigravity session attribution from its aggregate workspace/Git metadata, added explicit owner-confirmed session mappings for conversations recorded outside a project, and deduplicated re-compacted session views by authoritative session identity.
- Separated project ownership from technical learning: user-directed, successfully validated work in forks and third-party repositories may support narrow skill evidence without implying upstream authorship.
- Fixed recent dashboard activity for valid non-Git projects whose stable IDs use the `folder-` prefix.
- Added metadata-first Codex and Antigravity session reconciliation with current/historical path and unique Git-identity resolution, one-project-only attribution, pre-ingestion skipping for unrelated or ambiguous sessions, and model-free index inspection.
- Added resumable one-project-at-a-time historical session analysis that publishes only the matching project dossier and audit note while deterministically blocking cross-project, personal-profile, skill, voice, and question output.
- Rebuilt project ingestion around explicit collection paths, authoritative workspace attribution, path-stable project identity, and a backed-up project-only reset. Review-question admission now permits at most one precise owner decision per named leaf project and rejects multi-project, mixed-decision, broad, or unattributed questions.
- Replaced append-only model-authored project indexing with a scanner-authoritative catalog that uses stable names, separates collection containers, labels content-free folders as non-projects, and checks runtime/index consistency in health and dashboard views.
- Expanded project discovery so every direct child of a configured project root is represented, and every direct child of human collection folders such as `Utilities & Automation` is represented without requiring Git or language markers.
- Added bounded `sb sessions latest` lookup with safe last-message previews, project/surface filters, Antigravity workspace enrichment, and one-transcript-per-session deduplication.
- Split daily and weekly output into a terse activity recap plus explicit coverage details, and clarified dashboard labels for changed projects, reviewed sessions, linked sessions, and represented projects.
- Added question dismissal and undo with aggregate feedback learning so repeatedly irrelevant question categories are suppressed without treating feedback as biographical evidence.
- Added exact-match project forgetting with preview, successor protection, backup, source-ignore protection, canonical/state/evidence cleanup, derived-artifact removal, and search/dashboard rebuilds while leaving source repositories untouched.
- Added implicit learning from Knowledge Deck confirmations/removals: aggregate feedback categories now guide daily, weekly, and bootstrap synthesis, and strongly repeated negative categories are deterministically suppressed without asking the owner for a reason.
- Prevented generic nested source folders such as `src` from becoming accidental child projects, while allowing an explicitly top-level folder with that name to remain a project; machine-resolvable or omnibus review questions are blocked before they enter the owner queue.
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
