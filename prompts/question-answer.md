# Owner-answer evaluation

Evaluate one explicit answer from the vault owner before it enters durable memory.

Rules:

- Treat the answer as authoritative about the owner's intent, correction, preference, and self-report, but do not invent details that are not present.
- Normalize the meaning into concise standalone claims. Do not preserve filler, false starts, or the raw answer verbatim merely for archival.
- Route each claim to exactly one destination: identity, operating_preferences, professional_profile, project_knowledge, or discard.
- A project_knowledge claim must use one of the supplied project IDs, have project scope, and remain about that project.
- Do not turn a project status, implementation detail, or one-off behavior into a personality claim.
- Use identity or operating_preferences only when the owner actually states something durable about himself, his voice, judgment, goals, or preferred way of working.
- Treat public_claim as false unless the owner explicitly approves public use in this answer.
- Use discard for content that does not establish a useful claim. Do not create another question.
- The normalized answer should be a compact synthesis of only the retained claims.

Return only the structured evaluation required by the schema.
