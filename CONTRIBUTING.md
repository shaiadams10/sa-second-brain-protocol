# 🤝 Contributing

Contributions should preserve the protocol's evidence, privacy, and deterministic-publication boundaries.

## Good contributions

- Parser fixtures for additional visible session formats
- Deduplication and checkpoint-recovery tests
- Review usability improvements
- Privacy scanners and hostile-input fixtures
- Deterministic Markdown merge hardening
- Clear generic templates and runbooks

## Development

```powershell
uv sync --all-groups
uv run pytest
```

Add tests for behavior changes. Never place real conversations, credentials, personal paths, private project names, or personal vault content in fixtures.

## Pull requests

Describe the trust boundary affected, failure behavior, tests run, and any migration required. Changes that weaken read-only scanning, review gates, redaction, checkpoint safety, or public-export allowlisting will not be accepted.
