---
title: extraction
type: note
permalink: personal-vault/protocol/prompts/extraction
---

# Extract Durable Memory Mutations

Inspect every supplied episode independently for explicit durable statements and strongly grounded reusable knowledge.

For each supported memory, emit one `memory_mutation` candidate:

- `operation`: use `create` when the packet supplies no existing target memory. Use `reinforce`, `update`, `supersede`, or `contradict` only when supplied memory context names the exact `target_memory_id`.
- `kind`: choose the narrowest allowed kind. Use `project_fact`, `decision`, or `lesson` only with authoritative project evidence.
- `subject`: a stable, concise label.
- `claim`: one atomic, evidence-backed statement; do not combine unrelated facts.
- `scope`: `global` only when explicitly general, `project` for one known project, otherwise `context`.
- `project_id`: the exact authoritative project ID for project knowledge; otherwise null.
- `evidence_refs`: one or more top-level evidence IDs from this packet only.
- `confidence`: calibrated from 0 to 1.
- `explicit`: true only when the user stated the durable meaning directly.

Do not emit question resolutions, summaries, publication instructions, or canonical-note edits from this extraction task. Do not duplicate the same semantic memory. Return an empty array when no durable memory is supported.

A `full` session lane may also emit one `procedure_candidate` for a genuinely reusable, successfully validated workflow. Include its narrow scope, prerequisites, ordered steps, failure branches, and behavioral tests. Set `owner_directed` and `validated_outcome` to true only when the episode directly supports both. Never propose a procedure from `profile_only` evidence, a failed experiment, assistant-only invention, or routine one-off mechanics. Procedure candidates always enter review and must never directly edit executable agent instructions.
