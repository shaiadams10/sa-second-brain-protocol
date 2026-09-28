# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Plain HTML, CSS, and JavaScript, served by the engine's own Python standard-library HTTP server. No build step, no npm, no framework. Ships inside the Python package.

## Users

One person: the owner of the vault, a hands-on engineer who works mostly through AI coding assistants (Codex, Claude Code, Antigravity). They open the dashboard on their own PC every week or two to see what the brain learned, and occasionally right after pressing Run now.

## Product Purpose

The brain updates itself weekly without asking for approval. The dashboard is where the owner watches that happen and corrects it: read the week, see which projects are ongoing versus exploratory, and remove anything the model got wrong. Success means a few minutes of review leaves the owner confident the vault is accurate, with nothing to approve.

## Positioning

A self-maintaining record of one person's work and working style, derived from their own AI conversations and project folders, where the only human action is subtraction.

## Operating Context

- Runs on localhost only, on the owner's Windows PC. Never exposed on the network.
- Opened from a desktop shortcut or `brain dashboard`; a scheduled weekly job updates the data in between.
- The owner removes items rather than editing them; removed items are remembered so the model never proposes them again.
- Every change, by the model or the owner, is committed to the private vault's git history.

## Capabilities and Constraints

- Views: the week (headline, summary, highlights, per-project summaries and stats), projects (ongoing, exploring, on hold, earlier, and every other folder), what was learned about the owner (confirmed items and candidates with evidence quotes), and runs (health, model, duration, failures, Run now with live progress).
- Actions: remove and restore learned items and project notes; mark or unmark a folder as a real project; answer folder questions (one project, project with sub-projects, or a collection); run now.
- Terminology: "ongoing", "exploring", "on hold", "earlier" for project states; "candidate" for an observation seen once; "confirmed" once it recurs in two projects or two weeks.
- On hold is phrased neutrally ("on hold since"), never as stopped or abandoned.
- Data comes from a local JSON API; the page must work offline with no external requests except optional web fonts.

## Evidence on Hand

Real data exists in the owner's vault (`brain/knowledge.json`, weekly logs). The public repository contains no personal data, so screenshots and examples must use neutral sample content.

## Product Principles

1. Monitoring, not approving. Nothing waits on the owner.
2. Subtraction is the only edit, and it is always reversible.
3. Show evidence next to every claim about the owner, in their own words.
4. Separate real work from experiments at a glance.
5. Calm by default: a healthy week needs no attention.

## Accessibility & Inclusion

Keyboard operable, visible focus, WCAG AA contrast in both light and dark themes, reduced-motion respected.
