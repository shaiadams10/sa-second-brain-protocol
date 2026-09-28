"""Prompts and output schemas. Written for a fast, literal model: short numbered rules,
concrete good and bad examples, and a fixed JSON shape so the model never writes files."""

from __future__ import annotations

PROJECT_WEEK_TASK = """\
You maintain a private second brain about {owner}. {owner} works with AI coding assistants.
`week.md` holds one week of {owner}'s conversations in ONE folder of their projects directory:
each exchange is what {owner} wrote, then the assistant's final reply. `context.md` holds what
the brain already knows. Return what this week shows, using the JSON schema.

RULES

1. Facts only from `week.md`. Never guess. If something is unclear, leave it out.
2. `blurb`: what this folder is, in at most 15 plain words, neutral. Keep the existing blurb
   unless this week clearly shows it is wrong or incomplete.
3. `week_summary`: 1 to 3 sentences on what {owner} did or tried this week. Concrete, past tense,
   no praise, no filler. Say "explored" or "tried" for experiments; say "built" or "shipped" only
   when the conversation shows it working.
4. `project_notes`: at most 5 durable facts about the project itself: a decision, a milestone
   reached, the stack, a goal, an open problem. Skip anything already in context.md.
   Skip routine steps ("fixed a bug", "ran tests").
5. `observations`: what this week reveals about {owner} as a person and worker. At most 5.
   Look at how they react: praise ("gj", "perfect") shows what they value; corrections, "nope",
   "why did u", redoing work, or interrupting show what they dislike. Every observation needs a
   short `evidence` quote of {owner}'s own words.
   Choose `scope` carefully:
   - "me": true about {owner} in any project. Write it in its general form.
   - "project": only makes sense inside this project.
   - "ephemeral": a one-off instruction for one task. These are dropped, so use this freely.
   GOOD "me": "Wants to review visual options as images before anything is implemented."
   GOOD "me": "Expects agents to finish and verify work without asking permission for routine steps."
   BAD  "me": "For SVG logos, prefer svgl.app."  -> too specific: "ephemeral", or generalize to
        "Prefers real brand assets over invented placeholders" only if the week shows that.
   BAD  "me": "{owner} is the owner of <project>."  -> not an observation. Never write ownership claims.
   BAD  "me": "Likes the Street Press UI style."  -> "project".
6. If an observation says the same as an item listed in context.md, put that item's id in
   `reinforces` instead of repeating it in new words. Otherwise `reinforces` is "".
7. Never propose anything similar to the REMOVED items in context.md. {owner} deleted those.
8. An empty list is a good answer. Most weeks reveal little that is new about a person.
9. Sensitive matters stay high level everywhere (blurb, summary, notes, observations): school
   work and grades, how assignments or tests are completed, health, money, legal matters,
   relationships, passwords and accounts. Write "worked on statistics coursework", never how.
   Write nothing that would embarrass {owner} if an employer read it.
"""

WEEK_SYNTHESIS_TASK = """\
You maintain a private second brain about {owner}. `projects.md` summarizes their week project
by project, ordered by how much attention each got. `candidates.md` lists observations about
{owner} proposed this week (ids starting "new-") and ones already known (other ids).
Return the week's log entry and the de-duplication, using the JSON schema.

RULES

1. `headline`: one plain sentence naming the main thing {owner} worked on this week.
2. `summary`: 2 to 4 sentences covering the week, biggest work first. Mention small
   experiments briefly as a group ("also tried X and Y"). Neutral tone, no praise, no hype.
3. `highlights`: at most 4 things that will still matter in a month: a milestone reached, an
   important decision, a new direction. Not failed attempts, small fixes, or trivia such as a
   flag that did not work. An empty list is fine.
4. `merges`: for each "new-" candidate that means the same thing as another candidate (new or
   known), give {{"id": the new id, "same_as": the other id}}. Only merge real duplicates.
5. Use only the input files. Never invent anything.
6. Sensitive matters stay high level: school work and how it is completed, grades, health,
   money, legal matters, relationships, accounts. Write "worked on coursework", never how.
"""

PROJECT_WEEK_SCHEMA = {
    "type": "object",
    "properties": {
        "blurb": {"type": "string"},
        "week_summary": {"type": "string"},
        "subprojects_touched": {"type": "array", "items": {"type": "string"}},
        "project_notes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["decision", "milestone", "stack", "goal", "open-problem"]},
                    "text": {"type": "string"},
                },
                "required": ["kind", "text"],
                "additionalProperties": False,
            },
        },
        "observations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "scope": {"type": "string", "enum": ["me", "project", "ephemeral"]},
                    "kind": {"type": "string", "enum": ["preference", "dislike", "work-style", "skill", "interest"]},
                    "text": {"type": "string"},
                    "evidence": {"type": "string"},
                    "reinforces": {"type": "string"},
                },
                "required": ["scope", "kind", "text", "evidence", "reinforces"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["blurb", "week_summary", "subprojects_touched", "project_notes", "observations"],
    "additionalProperties": False,
}

WEEK_SYNTHESIS_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "highlights": {"type": "array", "items": {"type": "string"}},
        "merges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "same_as": {"type": "string"}},
                "required": ["id", "same_as"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["headline", "summary", "highlights", "merges"],
    "additionalProperties": False,
}
