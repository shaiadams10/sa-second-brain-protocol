---
title: daily
type: note
permalink: personal-vault/protocol/prompts/daily
---

# Daily Synthesis

Summarize only new evidence. Use `project_delta` records as the authoritative description of what changed since the previous validated run. Identify new or moved projects, project progress, successful implementations, decisions, blockers, unresolved work, candidate skills, candidate work patterns, and safe voice excerpts.

Make the top-level `summary` a quick activity recap, not a protocol-status report. Use 2-6 short Markdown bullets, normally one per active project. Start each bullet with the project name and state the concrete work completed, changed, decided, or blocked in about 18 words or fewer. Omit a project when the packet contains only an unchanged inventory. Do not spend the recap on evidence counts, ingestion mechanics, lookup limitations, or claims that the refresh succeeded; deterministic coverage is published separately.

Treat setup and operating activity as real activity when it has an observable result. A connectivity check, new project-to-agent connection, environment validation, migration, or access test should be summarized even when it changes no source file. Do not let a low-level file timestamp or classification signal displace a more meaningful session outcome.

Emit one `session_summaries` item for every attributed `session_digest` in the packet. Cite that digest's evidence ID, preserve its exact project ID and name, and state the useful outcome in one short sentence. Include brief tests and no-op sessions so the daily note remains a complete session ledger. If the digest contains only replayed metadata, say that it was backfilled and that no user-authored outcome was available. Do not quote raw conversation text or expose paths, session IDs, or tool-call details.

Emit `pattern_signals` for normalized, reusable behavior that may recur across sessions, such as a preferred answer format, review style, tool boundary, implementation protocol, communication habit, or working preference. Use a stable kebab-case `pattern_key` that describes the behavior rather than the current project. Set `scope` to `project` for a one-project or one-task instruction and to `global` only for an explicitly general statement. A signal may be emitted from one session; the deterministic registry will accumulate evidence and enforce the three-session, two-date, two-project promotion rule. Do not inflate evidence by treating repeated lines within one session as separate support.

Preserve the natural wording of useful voice excerpts after privacy sanitization. Emit `voice_style` observations only for concrete language patterns supported by the supplied user-authored evidence; do not confuse spelling mistakes, one-off phrasing, assistant wording, or project vocabulary with a stable personal voice. Put contradictions, missing facts, and unclear attribution in `review_items`. Keep ambiguous or public-facing claims reviewable.

Keep four knowledge roles distinct through the existing output types:

- About the person: use `explicit_fact`, `goal`, `personality`, `voice_style`, or `work_style` only for identity, enduring values, direction, communication, or stable personal patterns supported by user evidence.
- Professional profile: use experience, education, military, and `skill_updates` for attributable capabilities and verified technical range. A manifest or dependency alone is never skill evidence.
- Operating preferences: use `preference` or a `protocol_preference` pattern signal for reusable agent behavior, answer formats, validation rules, design taste, and working protocols.
- Project knowledge: use `project_fact`, `decision`, `lesson`, or `project_updates` for architecture, implementations, local constraints, and project-specific learning.

For `project_updates`, use the scanner-provided project ID and project name exactly. The deterministic catalog owns project identity; do not rename a project, create aliases, or emit updates for collection containers or content-free folders.

Do not turn a technology found in a repository, a one-project instruction, or project vocabulary into personality. A project-specific preference remains project knowledge unless the evidence explicitly makes it reusable beyond that project.

Project classification controls ownership and public-attribution language, not whether a session can teach the brain about the user's technical capabilities. A session in a fork or third-party repository may support a narrow `skill_update` when its user messages show the user directing the work and its assistant results or artifacts show successful implementation or validation. Never credit the user with the upstream project or infer a skill from its inventory alone.

Keep Curate high-signal: routine technology stacks, feature inventories, visual implementation details, numeric counts, and one-off task instructions belong in project summaries rather than standalone knowledge cards. Ask clarification questions only when the user's judgment is truly required; defer objective technical gaps until the related project is refreshed.

When the evidence packet includes an aggregate `feedback_profile`, treat its avoid/prefer signals as ranking guidance, not biographical evidence. Use it to reduce categories the user repeatedly removes and emphasize categories he confirms. Do not quote the profile, infer a reason for a single removal, or turn feedback metadata into a claim.
