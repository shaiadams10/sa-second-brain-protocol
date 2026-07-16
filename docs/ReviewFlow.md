# 👀 Review flow

The review system deliberately separates the human experience from the machine provenance ledger.

## Three layers

1. **Dashboard** — a short Obsidian page showing counts and topic links.
2. **Topic pages** — focused questions or claims with plain-language guidance.
3. **Machine ledger** — the complete `obs-*` and `ev-*` trail for audits and debugging.

Most people should use only the first two layers.

## Review categories

### Questions

Questions resolve missing ownership, attribution, timelines, technical state, privacy, and disclosure boundaries. They cannot be approved in bulk. Answer one by number:

```powershell
sb review answer <group-token> <number> --answer "first-party; I directed the architecture and implemented the API"
```

It is safe to answer `defer` or leave a question pending.

### Public-facing claims

These can support resumes, LinkedIn, portfolios, service descriptions, or biographies. Review both factual accuracy and wording. Confidential or uncertain project details should remain pending or be rejected.

### Private observations

These include durable project lessons, work patterns, preferences, and context useful inside the private brain. They may be approved after a spot-check, rejected with a reason, or left pending.

## Snapshot-safe group decisions

Each reviewable group receives a token derived from the exact IDs visible in that group. For example:

```text
rvg-private-projects-0123456789
```

If a later run adds or removes an item, the token changes. An old command then fails instead of applying to unseen records.

```powershell
sb review approve-group <group-token>
sb review reject-group <group-token> --reason "Temporary implementation detail"
```

Clarification groups are answer-only and reject group approval attempts.

## Individual decisions

Machine-level commands remain available for precise operation:

```powershell
sb review approve <observation-id>
sb review reject <observation-id> --reason "Incorrect attribution"
sb review resolve <observation-id> --answer "Corrected fact"
```

Rejections create durable tombstones. Approved claims are promoted through generated-section boundaries; user-authored prose is preserved.

## Bootstrap relationship

The review backlog does not need to be empty before bootstrap approval. Reviewing the canonical Identity, Experience, Projects, and Skills notes is the main bootstrap gate. Pending observations stay pending and cannot promote themselves.
