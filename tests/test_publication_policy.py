import pytest

from second_brain_protocol.publication_policy import (
    PublicationPolicyContext,
    StagedCanonicalFile,
    validate_staged_publication,
)


CONTEXT = PublicationPolicyContext(
    run_kind="daily",
    period="2026-08-01",
    owned_sections=("preferences",),
)


BEFORE = """---
title: Preferences
---

<!-- sb:generated preferences:start -->
old
<!-- sb:generated preferences:end -->

## Manual notes

keep me
"""


def test_existing_note_can_change_only_inside_generated_section() -> None:
    after = BEFORE.replace("\nold\n", "\nnew\n")

    manifest = validate_staged_publication(
        (
            StagedCanonicalFile(
                path="Identity/Preferences.md",
                before_text=BEFORE,
                after_text=after,
            ),
        ),
        context=CONTEXT,
    )

    assert manifest.changed_paths == ("Identity/Preferences.md",)
    assert len(manifest.files[0].content_hash) == 64


def test_existing_note_rejects_manual_prose_change() -> None:
    with pytest.raises(RuntimeError, match="only sb:generated"):
        validate_staged_publication(
            (
                StagedCanonicalFile(
                    path="Identity/Preferences.md",
                    before_text=BEFORE,
                    after_text=BEFORE.replace("keep me", "overwrite me"),
                ),
            ),
            context=CONTEXT,
        )


def test_protocol_markdown_is_not_writable_canon() -> None:
    with pytest.raises(RuntimeError, match="not writable canon"):
        validate_staged_publication(
            (
                StagedCanonicalFile(
                    path="Protocol/OperatingContract.md",
                    before_text=BEFORE,
                    after_text=BEFORE.replace("\nold\n", "\nnew\n"),
                ),
            ),
            context=CONTEXT,
        )


def test_new_daily_note_requires_governed_markers() -> None:
    after = """---
id: daily-2026-08-01
type: daily
created: 2026-08-01T10:00:00+00:00
---

<!-- sb:generated daily-summary:start -->
summary
<!-- sb:generated daily-summary:end -->
"""

    manifest = validate_staged_publication(
        (
            StagedCanonicalFile(
                path="Journal/Daily/2026-08-01.md",
                before_text=None,
                after_text=after,
            ),
        ),
        context=PublicationPolicyContext(
            run_kind="daily",
            period="2026-08-01",
            owned_sections=("daily-summary",),
        ),
    )

    assert manifest.changed_paths == ("Journal/Daily/2026-08-01.md",)


def test_generated_secret_fails_final_publication_preflight() -> None:
    after = BEFORE.replace(
        "\nold\n",
        "\npassword = supersecretvalue123\n",
    )

    with pytest.raises(RuntimeError, match="privacy preflight"):
        validate_staged_publication(
            (
                StagedCanonicalFile(
                    path="Identity/Preferences.md",
                    before_text=BEFORE,
                    after_text=after,
                ),
            ),
            context=CONTEXT,
        )


def test_new_note_scaffold_fails_privacy_preflight() -> None:
    after = """# C:\\Users\\private-owner\\secret

<!-- sb:generated daily-summary:start -->
summary
<!-- sb:generated daily-summary:end -->
"""

    with pytest.raises(RuntimeError, match="privacy preflight"):
        validate_staged_publication(
            (
                StagedCanonicalFile(
                    path="Journal/Daily/2026-08-01.md",
                    before_text=None,
                    after_text=after,
                ),
            ),
            context=PublicationPolicyContext(
                run_kind="daily",
                period="2026-08-01",
                owned_sections=("daily-summary",),
            ),
        )


def test_daily_policy_cannot_create_another_period_or_weekly_note() -> None:
    after = """<!-- sb:generated daily-summary:start -->
summary
<!-- sb:generated daily-summary:end -->
"""

    with pytest.raises(RuntimeError, match="authorized run period"):
        validate_staged_publication(
            (
                StagedCanonicalFile(
                    path="Journal/Daily/2026-08-02.md",
                    before_text=None,
                    after_text=after,
                ),
            ),
            context=PublicationPolicyContext(
                run_kind="daily",
                period="2026-08-01",
                owned_sections=("daily-summary",),
            ),
        )


def test_daily_policy_cannot_edit_an_existing_note_from_another_period() -> None:
    before = """<!-- sb:generated daily-summary:start -->
old
<!-- sb:generated daily-summary:end -->
"""

    with pytest.raises(RuntimeError, match="authorized run period"):
        validate_staged_publication(
            (
                StagedCanonicalFile(
                    path="Journal/Daily/2026-07-31.md",
                    before_text=before,
                    after_text=before.replace("\nold\n", "\nnew\n"),
                ),
            ),
            context=PublicationPolicyContext(
                run_kind="daily",
                period="2026-08-01",
                owned_sections=("daily-summary",),
            ),
        )


def test_policy_cannot_change_another_generated_section_owner() -> None:
    with pytest.raises(RuntimeError, match="unowned generated section"):
        validate_staged_publication(
            (
                StagedCanonicalFile(
                    path="Identity/Preferences.md",
                    before_text=BEFORE,
                    after_text=BEFORE.replace("\nold\n", "\nnew\n"),
                ),
            ),
            context=PublicationPolicyContext(
                run_kind="daily",
                period="2026-08-01",
                owned_sections=("daily-summary",),
            ),
        )
