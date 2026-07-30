---
title: weekly
type: note
permalink: personal-vault/protocol/prompts/weekly
---

# Weekly Synthesis

Make the top-level `summary` a quick weekly activity recap. Use 3-10 short Markdown bullets, grouped mentally by project or trajectory but without extra headings. Lead with concrete shipped work, meaningful changes, decisions, blockers, and momentum; keep each bullet to roughly 22 words or fewer. When the week contains meaningful person or learning movement, include one or two bullets beginning with `the user:` that describe the evidence-backed pattern, demonstrated understanding, applied learning, or still-open edge. Do not use the recap for ingestion mechanics, evidence counts, or generic statements that synthesis succeeded. Structured observations, patterns, learning signals, project updates, and review items continue to improve durable knowledge separately.

Emit one `session_summaries` item for every attributed `session_digest` in the `full` lane. Cite the digest evidence ID and preserve its exact project ID and name. Keep each summary to one sentence describing the useful outcome, decision, blocker, or setup result; identify metadata-only backfill honestly. Do not emit a project session summary for a `profile_only` digest. These items support drill-down and provenance, while the top-level weekly recap should still emphasize trajectories rather than enumerate sessions.

For `project_updates`, preserve the scanner-provided project ID and project name exactly. The deterministic catalog owns identity; never rename projects or emit dossiers for collection containers or content-free folders.

Connect the week's approved and new evidence across projects and profile-only contexts. Use `project_delta`, `session_digest`, `recurring_pattern`, and prior learning-registry records to distinguish genuinely new activity from previously evaluated history. Evaluate both the work outcome and what the user's own interactions demonstrate. Identify major wins, trajectories, capability evidence, cross-project lessons, stable work patterns, stable voice patterns, learning edges, later application, validated growth, counterevidence, stale claims, and suggested next focus areas without overstating skills, personality, or voice.

Re-emit a normalized `pattern_signal` when the week supplies additional evidence for a recurring preference, work style, voice style, personality trait, or preferred protocol. Preserve the established kebab-case key from a `recurring_pattern` record when it represents the same behavior. Do not merge materially different behaviors merely to reach the promotion threshold.

Emit `learning_signals` with the same stable topic key when the week adds session or explicit interview evidence to a tracked area of understanding. A prior `learning_topic` record supplies comparison context but can never support a new signal by itself. A later correct application may become `applied_learning`; an attributed successful validation may become `validated_outcome`; contradictory or weaker evidence may become `counterevidence`. Do not claim that an edge was resolved merely because the assistant supplied an answer. Resolution requires the user's later correct use, diagnosis, explanation, tradeoff, or validation.

Emit `voice_style` observations only when the same user-authored language pattern is supported across at least three sessions, two dates, and two independent contexts; the deterministic publisher will enforce this gate. Separate deliberate tone and phrasing from typos, one-off expressions, assistant wording, and project-specific vocabulary. Put contradictions and unresolved gaps in `review_items`.

When the packet supplies `pending_questions`, use `question_resolutions` to close only objective questions that the week's evidence conclusively answers, such as verified deployment state, branch status, dates, counts, completed validation, or canonical repository metadata. Copy the supplied `obs-*` ID exactly. Set `authoritative` only when the answer is directly established by an authoritative artifact or at least two consistent signals. Do not guess, express a recommendation, or answer a question that requires the user's judgment; leave those questions untouched.

Preserve the four knowledge roles during synthesis:

- About the person contains identity, enduring values, goals, voice, work style, and stable personality patterns.
- Professional profile contains attributable capabilities, verified skill evidence, experience, education, and service.
- Operating preferences contains reusable agent behavior, preferred formats, validation rules, design taste, and cross-project protocols.
- Project knowledge contains architecture, implementation facts, project decisions, and project-specific lessons.
- Learning state contains time-varying evidence about demonstrated understanding, correct application, operational capability, architectural judgment, validated outcomes, counterevidence, and unresolved edges.

Cross-project repetition may strengthen a personal or operating pattern, but it does not automatically convert project technology into personal identity or a verified skill. Prefer the narrowest correct role and leave uncertain scope reviewable.

Profile-only sessions may strengthen person, voice, pattern, goal, preference, and learning evidence. They must never produce project facts, project updates, project decisions or lessons, ownership or authorship claims, project session summaries, skill updates, validated outcomes, or project-question resolutions. Do not infer their project from conversation content.

Include attributable learning from sessions in forks, experiments, and third-party repositories. Describe only the tools the user used, systems he operated, changes he directed, or problems he demonstrably solved; keep upstream ownership and pre-existing implementation explicitly separate.

Use an aggregate `feedback_profile`, when supplied, to rank the weekly summary and candidate knowledge toward what the user repeatedly confirms and away from categories he repeatedly removes. The profile is optimization guidance only; it is not evidence for a personal fact and must never be quoted or described as a reason the user holds a view.
