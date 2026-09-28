---
name: Second Brain Logbook
description: A pilot's logbook for a self-maintaining knowledge vault; night cockpit by default, cool ruled paper by day.
colors:
  ground: "#0b0e13"
  panel: "#111722"
  panel-2: "#0e131c"
  rule: "#1f2a3b"
  rule-strong: "#2c3a50"
  ink: "#e6ebf2"
  ink-2: "#97a4b6"
  ink-3: "#8390a3"
  blue-ink: "#5aa2ff"
  blue-wash: "rgba(90, 162, 255, 0.14)"
  pencil: "#a7b0bd"
  warn: "#e5a54b"
  bad: "#ff7a6b"
  bar: "#3d7fe0"
  bar-track: "#18202d"
  day-ground: "#eef2f7"
  day-panel: "#fbfcfe"
  day-panel-2: "#f3f6fa"
  day-rule: "#c9d9ef"
  day-rule-strong: "#9fbbe2"
  day-ink: "#111a26"
  day-ink-2: "#4b5a6e"
  day-ink-3: "#586577"
  day-blue-ink: "#1c5fd0"
  day-blue-wash: "rgba(28, 95, 208, 0.09)"
  day-pencil: "#5e6878"
  day-warn: "#9a5b00"
  day-bad: "#b3261e"
  day-bar: "#2f6fdc"
  day-bar-track: "#e3e9f1"
typography:
  headline:
    fontFamily: "Barlow, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "22px"
    fontWeight: 600
    lineHeight: 1.25
    letterSpacing: "-0.01em"
  title:
    fontFamily: "Barlow Semi Condensed, Barlow, system-ui, sans-serif"
    fontSize: "17px"
    fontWeight: 600
    lineHeight: 1
    letterSpacing: "0.02em"
  body:
    fontFamily: "Barlow, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.5
    fontFeature: "tnum"
  body-sm:
    fontFamily: "Barlow, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "13.5px"
    fontWeight: 400
    lineHeight: 1.5
    fontFeature: "tnum"
  caption:
    fontFamily: "Barlow, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "12.5px"
    fontWeight: 400
    lineHeight: 1.4
    fontFeature: "tnum"
  label-tab:
    fontFamily: "Barlow Semi Condensed, Barlow, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 600
    lineHeight: 1
    letterSpacing: "0.07em"
  label:
    fontFamily: "Barlow Semi Condensed, Barlow, system-ui, sans-serif"
    fontSize: "11.5px"
    fontWeight: 600
    lineHeight: 1.15
    letterSpacing: "0.08em"
  mono:
    fontFamily: "ui-monospace, Cascadia Mono, Consolas, monospace"
    fontSize: "12.5px"
    fontWeight: 400
    lineHeight: 1.6
rounded:
  hair: "1px"
  sm: "3px"
spacing:
  xs: "4px"
  sm: "8px"
  cell: "10px"
  md: "14px"
  lg: "18px"
  xl: "24px"
  section: "30px"
  row-pitch: "44px"
components:
  button:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "0 12px"
    height: "32px"
  button-primary:
    backgroundColor: "{colors.blue-ink}"
    textColor: "{colors.ground}"
    rounded: "{rounded.sm}"
    padding: "0 12px"
    height: "32px"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.sm}"
    height: "32px"
  button-small:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "0 9px"
    height: "26px"
  tab:
    textColor: "{colors.ink-2}"
    typography: "{typography.label-tab}"
    padding: "12px 14px 11px"
  tab-active:
    textColor: "{colors.ink}"
    typography: "{typography.label-tab}"
  segmented-option-active:
    backgroundColor: "{colors.blue-wash}"
    textColor: "{colors.blue-ink}"
    height: "26px"
    padding: "0 9px"
  filter-input:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    height: "30px"
    width: "220px"
  sheet:
    backgroundColor: "{colors.panel}"
    rounded: "{rounded.sm}"
  log-column-head:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink-3}"
    typography: "{typography.label}"
    padding: "11px 10px 9px"
  log-row:
    height: "{spacing.row-pitch}"
    padding: "0 10px"
---

# Design System: Second Brain Logbook

## Overview

**Creative North Star: "The Night Cockpit Logbook"**

The interface is a pilot's logbook kept by the machine. Each week is one ruled page: a line per item logged, numbers in fixed columns, totals and brought-forward figures closing the page under double accounting rules, and prose remarks in a margin beside it. The night theme is a cockpit at rest: near-black blue ground, hairline blue-grey rules, paper-white type. The day theme is cool white logbook paper with light blue rules. Both themes are the same page, only the lighting changes.

State is written, not painted. Two writing instruments carry certainty: blue ink underlines what is confirmed, graphite pencil (lighter, dashed underline) marks what is only a candidate. A removal follows logbook law: never erase; a single ruled line is drawn through the entry, which dims but stays legible and restorable. The same principle runs through every status marker: a short rule whose line pattern (solid, dashed, dotted, hairline) carries the meaning, with colour only supporting it.

The system is dense and quiet. Surfaces are flat, square-cornered (3px), and separated by 1px rules rather than shadows or fills. There are no stat cards, no sidebar, no charts beyond single-scale bars. A healthy page has almost no colour on it except blue ink.

**Key Characteristics:**
- Ruled table grammar everywhere: hairline rows, vertical column rules, double rules at head and foot.
- Barlow for reading, Barlow Semi Condensed uppercase with tracking for column heads, tabs, and section labels.
- Tabular numerals on the whole page.
- Blue is the only accent; amber and red appear only for warnings and failures.
- Line pattern, never colour alone, distinguishes states.
- One fixed attention scale and one week axis across every view.

## Colors

A cool, low-chroma blue-grey neutral family with one blue accent that plays the role of ink.

### Primary
- **Blue Ink** (`blue-ink`; day `day-blue-ink`): confirmed entries' underline, the active tab rule, the primary button, links, focus rings, selection, the caret, the "ongoing" state rule, and highlight bullets. It is the pen; anything written in it is settled.
- **Blue Wash** (`blue-wash`; day `day-blue-wash`): the translucent tint behind a hovered or expanded log row, an open register row, and a pressed segmented option. Never used as a surface fill at rest.
- **Gauge Blue** (`bar`; day `day-bar`): the fill of attention bars and weekly history bars, deliberately a step deeper than the ink so bars read as instruments, not text.

### Secondary
- **Graphite Pencil** (`pencil`; day `day-pencil`): candidate entries only, always paired with a dashed underline in the same tone.

### Tertiary
- **Amber Caution** (`warn`; day `day-warn`): the partial-week banner icon and the small attention dot on a tab. Nothing else.
- **Correction Red** (`bad`; day `day-bad`): failed runs (with a dashed rule), error text, and the hover state of a Strike control.

### Neutral
- **Cockpit Ground** (`ground`; day `day-ground`): the page background and the sticky header.
- **Panel** (`panel`; day `day-panel`): the logbook page, sheets, buttons, inputs, toast.
- **Margin Panel** (`panel-2`; day `day-panel-2`): the remarks margin, the tab strip, table footers, register header rows, expanded detail, and the legend; the recessed second paper.
- **Hairline Rule** (`rule`; day `day-rule`): row and column rules, sheet borders.
- **Strong Rule** (`rule-strong`; day `day-rule-strong`): header underlines, the double accounting rules, control borders, the margin's dividing rule, scrollbar thumbs.
- **Paper White / Logbook Black** (`ink`; day `day-ink`): primary text.
- **Secondary Ink** (`ink-2`; day `day-ink-2`): summaries, remarks, secondary values.
- **Tertiary Ink** (`ink-3`; day `day-ink-3`): column heads, captions, zero values, struck entry text, metadata. Kept above AA contrast on both panels.
- **Bar Track** (`bar-track`; day `day-bar-track`): the empty channel behind attention bars and empty history weeks.

### Named Rules
**The One Pen Rule.** Blue is the only accent. Amber and red are signals, not decoration; a healthy week shows neither.

**The Cool Paper Rule.** The day theme is cool white paper with blue rules. Never cream, beige, or warm off-white.

## Typography

**Display Font:** none; the largest type is the 22px margin headline.
**Body Font:** Barlow (with system-ui, -apple-system, Segoe UI, sans-serif), weights 400, 500, 600 and italic 400.
**Label Font:** Barlow Semi Condensed (with Barlow), weights 500 and 600.
**Mono:** ui-monospace stack, used only for the live run output.

**Character:** An instrument-panel sans in two widths. The condensed cut, set uppercase and tracked, does the job of stencilled column heads; regular Barlow carries remarks and names at comfortable reading size.

### Hierarchy
- **Headline** (600, 22px, 1.25, -0.01em, balanced wrap): the week's headline at the top of the remarks margin. One per page.
- **Title** (Semi Condensed 600, 17px, 1, uppercase, 0.02em): the logbook wordmark. The week pager label uses the same face at 15px, mixed case, with a 12px Barlow date line beneath.
- **Body** (400, 15px, 1.5, tabular numerals): row names (600), numbers, summaries. Summaries cap at 70ch, remarks at 92ch, timeline entries at 72ch.
- **Body small** (400, 13.5px): remarks under log rows, register blurbs, legend and question text, state labels, buttons (13.5px, 500).
- **Caption** (400, 12–12.5px): sub-lines under names, provenance lines ("seen in … · week"), last-run status.
- **Tab label** (Semi Condensed 600, 13px, uppercase, 0.07em): index tabs, with an 11.5px Barlow count in tertiary ink.
- **Label** (Semi Condensed 600, 10.5–12px, uppercase, 0.08–0.1em, tertiary ink): table column heads, footer row labels, margin and section headings, note kinds, entry kinds.
- **Mono** (400, 12.5px, 1.6): the streaming run log only.

### Named Rules
**The Tabular Rule.** `font-variant-numeric: tabular-nums` is set on the body; every number on every view sits in a fixed-width column.

**The Stencil Rule.** Uppercase tracked text is reserved for the Semi Condensed label roles (column heads, tabs, section headings, kind tags). Body copy, names, and headlines are never set uppercase.

## Layout

The page is a sticky 56px header (wordmark left, week pager centred, last-run status and actions right, on a 1fr / auto / 1fr grid), an index tab strip on the margin panel beneath it, then a main area padded 24px 24px 64px and capped at 1480px.

The week view is a single framed page split into a ruled log (62fr) and a remarks margin (38fr, minimum 300px) divided by a strong rule. Log rows sit on a 44px pitch; each project row is followed by a two-line remark row sharing its tint. When the margin runs longer than the log, the log is padded with blank ruled rows (44px plus 1px rule) and the final blank row absorbs the remainder, so the totals land flush with the foot of the page frame. The page is always ruled to its foot.

The projects register is a five-column row grid (name, description, eight-week history, last active, classification) inside sheets, grouped by state under section heads (30px above, 10px below). The learned-entries view is a list sheet plus a 300px sticky legend. Runs is a plain ruled table.

Spacing is small-step and dense: 4, 6, 8, 10, 14, 18, 24px, with 30px between sections. Cell padding is 10px horizontal, 18px at the leading edge.

**Responsive.** At 1100px the page stacks (margin below the log, rule moves to its top, ruled fill switches off), the legend unsticks, the register drops its last-active column, and detail panels go single-column. At 760px the header wraps with the pager on its own row, tabs share the width equally without counts, the log keeps three columns on a fixed layout (50% / 16% / 34%) with the state shown under the name, register rows fold to name + classification over a full-width history strip with its own week labels, and main padding drops to 14px 10px 48px.

### Named Rules
**The Ruled To The Foot Rule.** A logbook page never ends in empty panel: blank ruled rows carry the rules down until the totals sit flush on the page frame.

**The One Axis Rule.** Every history view uses the same eight-week axis, oldest left, and one fixed attention scale shared across weeks and projects. Bars are never normalised per page.

## Elevation & Depth

Flat. Depth is conveyed by the two paper tones (panel over ground, margin panel recessed) and by rule weight: hairline, strong, and 3px double. The single shadow in the system belongs to the toast, the only element that floats above the page.

### Shadow Vocabulary
- **Toast float** (`box-shadow: 0 10px 30px -12px rgba(0, 0, 0, 0.65), 0 0 0 1px` strong rule; day `0 10px 30px -14px rgba(20, 40, 70, 0.28)`): the undo / status toast at the bottom centre.

### Named Rules
**The Double Accounting Rule.** The log's header and its totals are separated from the body by a 3px double rule in the strong rule colour, as in a ledger. Ordinary rows use 1px hairlines.

**The Flat Page Rule.** Pages, sheets, and panels never cast shadows; separation is a rule or a paper-tone step.

## Shapes

Square and ruled. Containers, buttons, inputs, chips, and segmented controls share one 3px radius; bars use 1px; the only circle is the 6px amber attention dot. Status and state markers are short horizontal rules (14–18px long) rather than dots, pills, or badges. Icons are 16px stroked line drawings (1.6px stroke, round caps) inlined from an SVG sprite.

## Components

### Buttons
Compact instrument switches.
- **Shape:** 3px corners, 32px tall, 1px strong-rule border, 13.5px Barlow 500, 7px icon gap.
- **Default:** panel fill, primary ink; hover lifts the border to tertiary ink.
- **Primary:** blue ink fill and border, ground-coloured text at 600; hover brightens 8%. One per view (Run now).
- **Ghost:** transparent until hover, secondary ink; used for pager arrows and the theme toggle (square 32px icon buttons).
- **Small:** 26px tall, 9px padding, 12.5px; used for inline answers.
- **Disabled:** 55% opacity. **Running:** the leading icon spins (1s linear).
- **Focus:** 2px blue-ink outline, 2px offset, everywhere.

### Tabs
Uppercase Semi Condensed labels on the margin-panel strip. Active tab is primary ink with a 2px blue-ink rule sitting on the strip's bottom border, inset 10px each side. Counts follow in 11.5px tertiary ink; an amber 6px dot flags a tab that needs a look.

### Segmented control
A strong-rule outlined group, 26px tall, options divided by strong rules. Pressed option takes the blue wash and blue-ink text at 600. Used for kind filters and project classification.

### Inputs
The folder filter is a 30px, 220px-wide field (full width on mobile) with panel fill, strong-rule border, 3px corners, tertiary-ink placeholder. Inline selects are borderless and tertiary until hover or focus reveals a strong-rule border and panel fill.

### Sheets
Panel fill, 1px hairline border, 3px corners, clipped overflow, no internal padding: rows supply their own. Register header rows use the margin panel and label type.

### Log table (signature)
Right-aligned numerals, left-aligned name and status, vertical hairlines between columns, 44px row pitch, uppercase label heads over a double rule, zeros in tertiary ink. Each project row pairs with a remark row (13.5px, two-line clamp, full text when opened); hover or expansion tints the pair with the blue wash. Footer rows on the margin panel: "Totals this page" in primary ink at 600, "Brought forward" in secondary ink at 500.

### State marker
An inline label led by an 18px rule: **ongoing** solid 2px in blue ink; **exploring** dashed 2px; **on hold** dotted 3px; **earlier** 1px strong-rule hairline. Run results use the same device at 14px: logged is a solid blue rule, failed a dashed red rule with red text.

### Attention bar
An 8px track in bar-track colour, graduated with a hairline tick every 10%, filled in gauge blue from the left on the fixed scale, value in 13px secondary ink at 3.2ch. The eight-week history is eight 1px-rounded columns 22px tall with 3px gaps; empty weeks show a 2px track stub.

### Ink, pencil, and struck entries (signature)
- **Ink:** primary text, 1.5px blue-ink underline offset 5px.
- **Pencil:** pencil text, 1px dashed pencil underline offset 5px.
- **Struck:** tertiary text, no underline, a 1.5px secondary-ink line through at 55% height; sub-lines and quotes drop to 60% opacity.
- **The correction stroke:** striking draws the line left to right (`scaleX` 0 to 1, 0.32s, `cubic-bezier(0.16, 1, 0.3, 1)`); undo lifts it. Reduced motion removes the transition.
- **Controls:** Strike is a quiet tertiary text button with a stroke icon that turns red on hover; Undo is blue ink.
- Evidence quotes indent 14px behind a strong rule, quoted text italic in primary ink, provenance in 12px tertiary.

### Remarks margin
Margin panel, 22px 24px 26px padding. Headline, then summary in secondary ink, then label-headed lists; highlight items are hairline-separated with an 8px blue-ink dash as the bullet.

### Toast
Bottom-centred, panel fill, 3px corners, toast float shadow, 13.5px text with an inline Undo; enters by rising 16px and fading in (0.2s opacity, 0.25s transform on the same ease).

## Do's and Don'ts

### Do:
- **Do** separate everything with rules: 1px hairlines between rows and columns, strong rules under heads, a 3px double rule at the log's head and foot.
- **Do** encode every state as a line pattern first (solid, dashed, dotted, hairline, struck) and let colour only reinforce it.
- **Do** strike removed items with one ruled line and keep them legible with an inline Undo.
- **Do** keep tabular numerals and right-aligned numbers in every numeric column.
- **Do** plot attention on the one fixed scale and the one eight-week axis in every view.
- **Do** fill the log page with blank ruled rows to the foot of the frame when the margin is taller.
- **Do** keep both themes in step: every token has a night value and a cool day value.

### Don't:
- **Don't** use colour alone to distinguish states, candidates, or results.
- **Don't** delete, fade out, or hide a removed entry; strike it.
- **Don't** add stat cards, sidebars, or charts beyond single-scale bars; the page is a logbook, not an admin dashboard.
- **Don't** put shadows on pages, sheets, or panels; the toast is the only floating element.
- **Don't** introduce a second accent hue; amber and red are reserved for warnings and failures.
- **Don't** warm the day theme toward cream or beige.
- **Don't** normalise bars per week or per project.
- **Don't** set body copy, names, or headlines in uppercase; tracked uppercase belongs to the Semi Condensed label roles.
