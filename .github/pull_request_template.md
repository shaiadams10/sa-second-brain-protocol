## Summary

Describe the protocol behavior changed and why.

## Trust boundary

Explain whether this affects collection, sanitization, model input, publishing, review, Git, scheduling, search, or recovery.

## Verification

- [ ] Tests were added or updated.
- [ ] `uv run --locked pytest` passes.
- [ ] Fixtures contain synthetic data only.
- [ ] Read-only source guarantees remain intact.
- [ ] Public-export allowlisting and redaction remain intact.
- [ ] Failure behavior preserves checkpoints and evidence.

## Migration and recovery

Document any state migration, rollback, or recovery step. Write `None` when not applicable.
