---
title: daily
type: note
permalink: personal-vault/protocol/prompts/daily
---

# Daily Synthesis

Summarize only new evidence. Use `project_delta` records as the authoritative description of what changed since the previous validated run. Identify new or moved projects, project progress, successful implementations, decisions, blockers, unresolved work, candidate skills, candidate work patterns, learning signals, and safe voice excerpts.

Treat evidence occurrence time as authoritative for the work timeline. Evidence may be newly processed today while describing older work; call that historical recovery or backfill and never phrase it as activity, learning, or capability newly demonstrated today. A recovered signal may update longitudinal knowledge once, at its original occurrence date, but it must not be repeatedly surfaced as a new event on later runs.

Evaluate every `session_digest` twice. First extract the project outcome when the digest is in the `full` lane. Then independently inspect only the user's user-authored messages and their visible interaction outcome for evidence about how he communicates, frames problems, decomposes work, reasons about tradeoffs, delegates to agents, handles uncertainty and failure, validates quality, demonstrates understanding, or develops new understanding. Do not require the user to explicitly describe himself when repeated observable behavior supplies grounded evidence.

An insight remains eligible when the user states it while asking for unrelated work. If he explicitly describes a durable, reusable preference or personal fact, emit the narrow corresponding `observation` with `explicit: true` and the correct scope. If the behavior is implied, contextual, or not yet demonstrably reusable, emit a `pattern_signal` instead so the registry can accumulate evidence. Do not duplicate the same idea as both an observation and a pattern.

Enforce the analysis lane on every emitted evidence reference. A `profile_only` session digest may support only person observations, voice samples, global or context-scoped pattern signals, and non-validated learning signals. Never cite a `profile_only` digest in `project_updates`, `session_summaries`, `skill_updates`, project-scoped observations, or project-scoped pattern signals. When a project update is supported by both full-lane and profile-only material, cite only the full-lane evidence and omit any claim that depends on the profile-only digest. If no full-lane evidence supports a project claim, do not emit that project claim.

Make the top-level `summary` person-first, not a protocol-status report or project changelog. Lead with what the evidence newly reveals about the user: learning movement, demonstrated judgment, capabilities, preferences, voice, work patterns, or goals. Follow with a small `Work context` portion that names only the project changes needed to understand where those signals came from. Use 2-8 short Markdown bullets. Omit unchanged inventory and do not spend the recap on evidence counts, ingestion mechanics, lookup limitations, or claims that the refresh succeeded; deterministic coverage is published separately.

Treat setup and operating activity as real activity when it has an observable result. A connectivity check, new project-to-agent connection, environment validation, migration, or access test should be summarized even when it changes no source file. Do not let a low-level file timestamp or classification signal displace a more meaningful session outcome.

Emit one `session_summaries` item for every attributed `session_digest` in the `full` lane. Cite that digest's evidence ID, preserve its exact project ID and name, and state the useful outcome in one short sentence. Include brief tests and no-op sessions so the daily note remains a complete session ledger. Do not emit `session_summaries` for `profile_only` digests; deterministic coverage will list them as unattributed profile-only sessions. If a full-lane digest contains only replayed metadata, say that it was backfilled and that no user-authored outcome was available. Do not quote raw conversation text or expose paths, session IDs, or tool-call details.

Emit `pattern_signals` for normalized, reusable behavior that may recur across sessions, such as a preferred answer format, review style, tool boundary, implementation protocol, communication habit, or working preference. Use a stable kebab-case `pattern_key` that describes the behavior rather than the current project. Set `scope` to `project` for a one-project or one-task instruction, `context` for a profile-only or not-yet-generalized interaction, and `global` only for an explicitly general statement. A signal may be emitted from one session; the deterministic registry will accumulate evidence and enforce the three-session, two-date, two-independent-context promotion rule. Do not inflate evidence by treating repeated lines within one session as separate support.

Emit `learning_signals` whenever the packet contains strong evidence about a topic the user understands, is learning, applies, operates, reasons about architecturally, or has not yet resolved. Reuse one stable kebab-case `topic_key` for the same knowledge area across projects. Prefer demonstrated behavior over vocabulary: correct application, error detection, causal explanation, constraint selection, transfer to another context, troubleshooting, or validation. A request for explanation alone is not a learning edge. Describe the evidence at its observed level and do not inflate agent execution into the user's manual coding.

Preserve the natural wording of useful voice excerpts after privacy sanitization. Emit `voice_style` observations only for concrete language patterns supported by the supplied user-authored evidence; do not confuse spelling mistakes, one-off phrasing, assistant wording, or project vocabulary with a stable personal voice. Put contradictions, missing facts, and unclear attribution in `review_items`. Keep ambiguous or public-facing claims reviewable.

Keep four knowledge roles distinct through the existing output types:

- About the person: use `explicit_fact`, `goal`, `personality`, `voice_style`, or `work_style` only for identity, enduring values, direction, communication, or stable personal patterns supported by user evidence.
- Professional profile: use experience, education, military, and `skill_updates` for attributable capabilities and verified technical range. A manifest or dependency alone is never skill evidence.
- Operating preferences: use `preference` or a `protocol_preference` pattern signal for reusable agent behavior, answer formats, validation rules, design taste, and working protocols.
- Project knowledge: use `project_fact`, `decision`, `lesson`, or `project_updates` for architecture, implementations, local constraints, and project-specific learning.
- Learning state: use `learning_signals` for time-varying topic understanding, application, operational fluency, architectural judgment, validation, counterevidence, and unresolved learning edges. These signals may originate in full or profile-only sessions, but a validated outcome requires full-lane project evidence.

For `project_updates`, use the scanner-provided project ID and project name exactly. The deterministic catalog owns project identity; do not rename a project, create aliases, or emit updates for collection containers or content-free folders.

Do not turn a technology found in a repository, a one-project instruction, or project vocabulary into personality. A project-specific preference remains project knowledge unless the evidence explicitly makes it reusable beyond that project.

Project-specific limits, prompt sizes, output formats, deployment targets, folder conventions, and named-agent architecture are project knowledge even when the user chose them. Never emit them as `explicit_fact`, personality, or a global preference merely because the subject is the user.

Project classification controls ownership and public-attribution language, not whether a session can teach the brain about the user's technical capabilities. A session in a fork or third-party repository may support a narrow `skill_update` when its user messages show the user directing the work and its assistant results or artifacts show successful implementation or validation. Never credit the user with the upstream project or infer a skill from its inventory alone.

Keep Curate high-signal: routine technology stacks, feature inventories, visual implementation details, numeric counts, and one-off task instructions belong in project summaries rather than standalone knowledge cards. Ask clarification questions only when the user's judgment is truly required; defer objective technical gaps until the related project is refreshed.

When the evidence packet includes an aggregate `feedback_profile`, treat its avoid/prefer signals as ranking guidance, not biographical evidence. Use it to reduce categories the user repeatedly removes and emphasize categories he confirms. Do not quote the profile, infer a reason for a single removal, or turn feedback metadata into a claim.

Before returning JSON, perform an insight extraction check:

1. Consider every supplied session through both the work and the user lenses.
2. Capture supported explicit durable facts, goals, decisions, lessons, and reusable preferences in `observations`.
3. Capture implied or not-yet-stable behavior in `pattern_signals`, and demonstrated growth or unresolved learning in `learning_signals`.
4. Keep professional capability, project knowledge, operating preferences, and person knowledge in their narrowest correct roles.
5. Remove duplicate claims across output collections and leave arrays empty when no evidence-backed insight exists.
