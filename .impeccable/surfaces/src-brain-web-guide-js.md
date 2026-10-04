---
version: 1
slug: "src-brain-web-guide-js"
primary_target: "src/brain/web/guide.js"
related_targets: ["src/brain/web/guide.css","src/brain/web/guide-render.js"]
---

# Guide tab surface brief

Scope: the dashboard's Guide tab (src/brain/web/guide.js, guide.css, guide-render.js; served by dashboard.py and guide.py). Mode: Read, with one live Operate element (the study's progress and Resume). Build path: comp-led; approved comp `.impeccable/mocks/decision/kneeboard.jpg` (generated with the Antigravity CLI's image generator).

Audience and job: the vault owner at their desk opens Guide to understand how the system works, find and read any important vault document, copy a command, and see how the running voice study is doing without opening a terminal.

## Direction contract

THESIS: The Guide is a pilot's kneeboard: the owner's documents are sheets clipped to a board, tabbed by folder, typeset as designed pages; a laminated quick-reference card rides beside them. Refuses the docs-site default of sidebar tree plus plain rendered Markdown.

OWN-WORLD: The logbook's graphite world: board #232322 with a soft lifted edge on the #1a1a19 ground, a dark metal clip at top centre, a stack of three offset sheets #1f1f1e with hairline rules #494945, index tabs above the stack (active tab outlined in vermilion #c13a25 with vermilion label), Georgia for document titles and outlined numerals, Barlow for body, monospace for commands with dotted leaders and COPY. The quick-reference card is a darker laminated plate with a small clip, a vermilion progress bar, and checkbox rows.

STORY: The owner opens Guide, sees at a glance that marking is 46% with 1h40 left, flips the tab to the document they need, reads it as a typeset sheet (ledgers, numbered rules, timelines), copies a command from the card, and leaves knowing how to resume.

FIRST VIEWPORT: Masthead and tabs unchanged. Centered board (about 1080px) with the clip overlapping its top edge; index tabs for each document group along the top-left of the stack; the open sheet fills the board's left two-thirds with a 44px Georgia title, then designed content; the laminated card sits to the right of the board, clipped, holding LIVE status and the command checklist. A document picker for the active group sits on the sheet's top edge.

FORM: Kneeboard, position 7 on the re-roll's ordered list, seed key 05ba9500.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Decisions recorded after the finish review

- Header: the owner answered "Keep my real header (Recommended)" when asked whether to match the comp's one-row header; the dashboard keeps its two-row masthead and tab row, so the Guide sits about 60px lower than the comp. The hero gate stays open at 0.646 (structure 0.61) because of that offset and because real document content (a ledger) sits where the comp shows placeholder rules; force was refused since the answer was a selected option. Disclosed, not hidden.
- Clip gap: the tabs split around a 180px gap under the clip because labels covered by the clip's legs could not be read or clicked; the clip still bites the sheet's top edge.
- Live counts line and five-stage stepper: carry the request's "voice study's progress" (messages marked, batch, time left, which step is running).
- Command group labels: 24 real commands against the comp's 11; the groups (Everyday, Deeper passes, Inspect, Voice study, Codex account, Setup) keep the card scannable. Rows stay one line and truncate with the full command on hover and in the copy.
- Sheet picker: a "2 of 4" stepper on the meta line plus an "On this tab" list at the sheet's foot, instead of a strip above the title (which pushed the title down).
- Mobile: the card follows the sheet; ledgers stack into labelled rows under 600px.
