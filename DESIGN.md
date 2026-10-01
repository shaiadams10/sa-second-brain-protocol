---
name: Signal Reading Room
description: Editorial weekly review with a graphite masthead, neutral ledger and vermilion actions.
colors:
  ground: "#1a1a19"
  panel: "#232322"
  panel-2: "#2e2e2b"
  rule: "#494945"
  rule-strong: "#8e8e86"
  ink: "#f5f5ee"
  ink-2: "#d2d2c8"
  ink-3: "#b4b4a9"
  blue: "#ffa18d"
  blue-soft: "#363632"
  pencil: "#b4b4a9"
  bar: "#d4d4c8"
  bar-track: "#505049"
  action: "#c13a25"
  on-action: "#ffffff"
  top-ground: "#111110"
  top-ink: "#f5f5ee"
  top-muted: "#b4b4a9"
  day-ground: "#f5f5f3"
  day-panel: "#ffffff"
  day-panel-2: "#eaeae7"
  day-rule: "#cdcdca"
  day-rule-strong: "#85857f"
  day-ink: "#20201f"
  day-ink-2: "#4e4e49"
  day-ink-3: "#64645d"
  day-blue: "#a52c19"
  day-blue-soft: "#e5e5e0"
  day-pencil: "#64645d"
  day-bar: "#373734"
  day-bar-track: "#d8d8d2"
  day-action: "#c13822"
  day-on-action: "#ffffff"
  day-top-ground: "#242423"
  day-top-ink: "#ffffff"
  day-top-muted: "#cecec8"
typography:
  headline:
    fontFamily: "Georgia, serif"
    fontSize: "31px"
    fontWeight: 400
    lineHeight: 1.3
  body:
    fontFamily: "Barlow, Segoe UI, sans-serif"
    fontSize: "15px"
    lineHeight: 1.5
rounded:
  control: "6px"
  sheet: "0"
---

# Signal Reading Room

The approved design combines the Reading Room composition with Signal colors. The primary task is reviewing weekly work and what the brain learned. The implementation is plain HTML/CSS/JavaScript inside the Python package; shared components remain in app.css and the selected layout and theme live in reading-room.css.

## Color roles

Light uses a neutral off-white canvas, white ledger, graphite masthead and vermilion primary action. Dark uses near-black and charcoal surfaces with a darker masthead. Reading text, project states and attention measurements use neutral colors. The legacy --blue variable carries the action/link/focus accent; it does not prescribe a blue color. Hover and expanded surfaces use neutral --blue-soft. Warning and failure tokens remain semantic. Root tokens cover native scrollbars, selection and caret as well as page content.

## Layout and typography

Desktop has a 92px masthead, centered four-view navigation, and content capped at 1480px. The weekly ledger sits left (1.65fr) and narrative right (.8fr, minimum 330px), separated by a fine rule and 45px gap. Georgia supplies the brand and 31px headline; Barlow supplies body, controls and tabular figures. Body copy is 15px and the narrative line height is 1.65. The ledger ends with its totals; it is not padded to the narrative height.

At 1150px the narrative moves above the ledger. At 760px the header wraps, all four tabs keep their counts, and the ledger, project register and run table scroll within their containers. Every data column remains available. Filters wrap and project details become a single column.

## Controls and states

Primary actions use --action and --on-action, distinct from readable link text. Standard controls are 36px tall with 6px corners; inline controls remain compact. Navigation uses a fine accent underline. Sheets are flat with square corners and neutral separators. Floating menus and status toasts use soft offset shadows. Keyboard focus is visible; the dark masthead uses a contrasting focus ring. Reduced motion keeps correction actions readable without animation.

Pressing the week number opens a non-modal annual selector with 52 or 53 ISO weeks, date labels, current-week outline, year controls, and Latest entry. Weeks without saved data are disabled with a dashed rule. Click outside, Close or Escape dismisses it. Left/right keys move between enabled weeks while the selector is open; normal week-arrow navigation remains available otherwise. Mobile uses a scrollable four-column grid with sticky year controls.

## Complete information coverage

Log retains partial-week state, headline, summary, highlights, learned entries, project summaries, subprojects, days, conversations, exchanges, commits, files, fixed-scale attention, page totals and brought-forward totals. It also shows skill activity in the selected week.

Projects retains state groups, eight-week history, last-active dates, real-project classification, notes, struck-note restoration, subprojects, timeline links, folder questions, folder search and every folder-rule option.

About retains stated facts, preferences, dislikes, work style, communication, interests, skills, goals and advice, with evidence and reversible Strike/Undo. Skills are grouped by the engine-computed Strong, Growing, Shown once, Learning and Struggled levels with trends and per-week evidence. Missing or future progress levels remain visible under Recorded skills. Goals show Open/Settled and their recorded dates. Direct statements have distinct confirmation rules from recurring observations.

Runs retains scheduling, live output, errors, results, duration, model, CLI, effort, calls and input/output tokens, including the distinction between measured and unmeasured runs. Run now retains CLI/model/effort/default selection and loading, unavailable-model and running states.

## Boundaries

All existing API-backed correction behavior remains reversible. This change does not modify the learning algorithm, launch a learning run, or change vault content. Private data and visual captures stay outside the public repository.
