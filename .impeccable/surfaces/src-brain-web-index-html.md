---
version: 1
slug: "src-brain-web-index-html"
primary_target: "src/brain/web/index.html"
related_targets: []
---

# Dashboard surface brief

Scope: the whole local dashboard (src/brain/web/). Mode: Operate. Build path: code-led (no image generation on this machine).

Audience and job: the vault owner, at their desk in the evening on a three-monitor setup, opens this every week or two to check what the brain logged and strike anything wrong. Nothing is approved; subtraction is the only edit and it is always reversible.

## Direction contract

THESIS: The brain keeps a pilot's logbook of your work. Each week is a ruled logbook page: one line per project flown, totals carried forward, remarks in the margin. Refuses the admin-dashboard default of sidebar, stat cards, and charts.

OWN-WORLD: Night cockpit logbook. Ground ink #0b0e13, panel #111722, hairline rules #1f2a3b, paper-white type #e6ebf2, secondary #97a4b6. Two writing instruments carry state: blue ink #5aa2ff for confirmed entries, graphite for pencilled candidates (dashed underline, lighter). Corrections follow logbook law: never erase, strike through with a single ruled line; struck entries stay legible and restorable. Day theme is cool white logbook paper with blue rules, never cream. Barlow family: Semi Condensed small caps for column heads, Barlow for body, tabular numerals everywhere numbers sit in columns.

STORY: The owner opens this week's page, reads the headline in the remarks margin, scans which projects got real time, strikes one wrong remark, and leaves in under two minutes trusting the log.

FIRST VIEWPORT: 52px page header: logbook title left, week pager centred (arrows, "Week 39 · Sep 21 – 27"), last entry status and Run now right. Index tabs below: Log, Projects, About <owner>, Runs. Body: ruled log table on the left ~62% (Project, Status, Days, Conv., Exch., Commits, Files, Attention bar on one fixed scale), "Totals this page" and "Brought forward" rows closing it; remarks margin on the right ~38% with headline, summary, highlights, and learned-this-week entries in ink or pencil.

FORM: Pilot Logbook, the model's top-ranked grounded candidate (#1 on the ordered list), seed key 0302ee04. Raises carried from the roll: state by line pattern not colour alone (jackfield); one row grid (pixorama); fixed attention scale across weeks (botanical); one week axis across every history view (deep dive).

SIGNATURE INTERACTION: the correction stroke. Striking an entry draws one ruled line across it left to right, the entry dims but stays legible, and an inline Undo appears; restoring lifts the line.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
