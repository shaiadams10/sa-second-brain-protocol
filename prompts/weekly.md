---
title: weekly
type: note
permalink: personal-vault/protocol/prompts/weekly
---

# Weekly Synthesis

Connect the week's approved and new evidence across projects. Use `project_delta`, `session_digest`, and `recurring_pattern` records to distinguish genuinely new activity from previously evaluated history. Identify major wins, trajectories, capability evidence, cross-project lessons, stable work patterns, stable voice patterns, stale claims, and suggested next focus areas without overstating skills, personality, or voice.

Re-emit a normalized `pattern_signal` when the week supplies additional evidence for a recurring preference, work style, voice style, personality trait, or preferred protocol. Preserve the established kebab-case key from a `recurring_pattern` record when it represents the same behavior. Do not merge materially different behaviors merely to reach the promotion threshold.

Emit `voice_style` observations only when the same user-authored language pattern is supported across at least three sessions, two dates, and two projects; the deterministic publisher will enforce this gate. Separate deliberate tone and phrasing from typos, one-off expressions, assistant wording, and project-specific vocabulary. Put contradictions and unresolved gaps in `review_items`.

When the packet supplies `pending_questions`, use `question_resolutions` to close only objective questions that the week's evidence conclusively answers, such as verified deployment state, branch status, dates, counts, completed validation, or canonical repository metadata. Copy the supplied `obs-*` ID exactly. Set `authoritative` only when the answer is directly established by an authoritative artifact or at least two consistent signals. Do not guess, express a recommendation, or answer a question that requires the user's judgment; leave those questions untouched.
