---
title: daily
type: note
permalink: personal-vault/protocol/prompts/daily
---

# Daily Synthesis

Summarize only new evidence. Use `project_delta` records as the authoritative description of what changed since the previous validated run. Identify new or moved projects, project progress, successful implementations, decisions, blockers, unresolved work, candidate skills, candidate work patterns, and safe voice excerpts.

Emit `pattern_signals` for normalized, reusable behavior that may recur across sessions, such as a preferred answer format, review style, tool boundary, implementation protocol, communication habit, or working preference. Use a stable kebab-case `pattern_key` that describes the behavior rather than the current project. A signal may be emitted from one session; the deterministic registry will accumulate evidence and enforce the three-session, two-date, two-project promotion rule. Do not inflate evidence by treating repeated lines within one session as separate support.

Preserve the natural wording of useful voice excerpts after privacy sanitization. Emit `voice_style` observations only for concrete language patterns supported by the supplied user-authored evidence; do not confuse spelling mistakes, one-off phrasing, assistant wording, or project vocabulary with a stable personal voice. Put contradictions, missing facts, and unclear attribution in `review_items`. Keep ambiguous or public-facing claims reviewable.

Keep four knowledge roles distinct through the existing output types:

- About the person: use `explicit_fact`, `goal`, `personality`, `voice_style`, or `work_style` only for identity, enduring values, direction, communication, or stable personal patterns supported by user evidence.
- Professional profile: use experience, education, military, and `skill_updates` for attributable capabilities and verified technical range. A manifest or dependency alone is never skill evidence.
- Operating preferences: use `preference` or a `protocol_preference` pattern signal for reusable agent behavior, answer formats, validation rules, design taste, and working protocols.
- Project knowledge: use `project_fact`, `decision`, `lesson`, or `project_updates` for architecture, implementations, local constraints, and project-specific learning.

Do not turn a technology found in a repository, a one-project instruction, or project vocabulary into personality. A project-specific preference remains project knowledge unless the evidence explicitly makes it reusable beyond that project.
