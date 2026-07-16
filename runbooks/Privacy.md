---
title: Privacy
type: note
permalink: personal-vault/protocol/runbooks/privacy
---

# Privacy Runbook

- Raw conversation text, hidden reasoning, tool-output dumps, tokens, credentials, and local paths remain outside Git.
- Cloud packets contain deterministic redactions and bounded visible evidence only.
- Antigravity database fallback decodes only fields proven to be visible user or assistant messages plus tool names; system, reasoning, permission, and raw tool-output payloads are excluded.
- Live database/WAL files are copied to temporary machine-runtime snapshots before non-immutable SQLite reads, preventing source-side writes.
- The public exporter is allowlist-based and scans every exported text file.
- A privacy finding stops commit, push, protocol PR creation, and checkpoint advancement.
