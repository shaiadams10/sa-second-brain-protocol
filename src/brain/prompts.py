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
   Text between [pasted text] and [end of pasted text] may be someone else's words (another
   agent's report, a log); treat it as material {owner} shared, and quote it as theirs only
   when it is plainly their own writing.
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
   Choose `kind`: "preference", "dislike", "work-style", "interest" (what visibly excites or
   absorbs {owner}: excitement, "love this", going deep on a topic for fun), or "communication"
   (how {owner} talks to assistants: how they phrase requests, ask for help, give feedback, or
   hand off decisions). GOOD "communication": "Asks for a verdict and a recommendation rather
   than a list of options."
6. `skills`: the technologies, tools, and domains this week shows, at most 6, one per topic.
   - `topic`: a technology, tool, or domain at a useful size, such as "Cloudflare Pages",
     "game server scripting", "microcontroller firmware", "Docker Compose", "local LLM inference",
     "visual design direction". Not "coding" (too broad), not "the retry timeout in the upload
     form" (too narrow). Name the technology or field, not a feature of one project: one topic
     "game server scripting" rather than separate topics for its menus, zones, and markers. If context.md lists the topic under Skills, copy its name exactly and
     put its id in `reinforces`; otherwise `reinforces` is "".
   - `level`, judged only from {owner}'s own words:
     "directs": gives specific technical direction, chooses between technical options with a
       reason, catches or corrects a technical mistake, or uses the topic's terms without asking.
     "learning": asks what something is, how or why it works, or asks for an explanation.
       Exchanges marked `signal: learning` are such questions.
     "struggled": the topic kept failing and {owner} could not tell why, or said they were lost.
   - `evidence`: a short quote of {owner}'s own words that shows the level.
   Handing work to the assistant ("do it for me") shows neither skill nor its lack. A topic the
   assistant handled alone, with no sign from {owner}, is not a skill: leave it out. Skip
   schoolwork.
7. If an observation says the same as an item listed in context.md, put that item's id in
   `reinforces` instead of repeating it in new words. Otherwise `reinforces` is "".
8. Never propose anything similar to the REMOVED items in context.md. {owner} deleted those.
9. An empty list is a good answer. Most weeks reveal little that is new about a person.
10. Sensitive matters stay high level everywhere (blurb, summary, notes, observations, skills):
   school work and grades, how assignments or tests are completed, health, money, legal matters,
   relationships, passwords and accounts. Write "worked on statistics coursework", never how.
   Write nothing that would embarrass {owner} if an employer read it.
"""

# Appended to PROJECT_WEEK_TASK for folders listed under `about_me` in config.toml.
ABOUT_ME_RULES = """
THIS FOLDER IS ABOUT {owner}

In this folder {owner} talks about themself: who they are, what they want, the advice they ask
for, and where the brain has them wrong. Fill the fields above as usual, and also:

11. `said`: what {owner} stated directly this week, at most 10, each with a short `evidence`
    quote of their own words. Only what {owner} said counts, never what an assistant suggested.
    - "stated": a fact about themself: interests, background, situation, what they will or won't
      do. Third person, general form. GOOD: "Photography is their main hobby."
      BAD: "Wants the dashboard button moved." -> a project instruction, not a fact about them.
    - "correction": {owner} said the brain or an assistant had them wrong. `text` is what is
      true; `wrong` is the belief they rejected, in general form.
    - "goal": something {owner} is trying to decide or reach, bigger than one task. GOOD: "Wants
      to move from a salaried job toward independent client work."
    - "advice": advice an assistant gave that {owner} accepted or turned down, with their reason
      when they gave one. GOOD: "Turned down a daily planning checklist; prefers a short weekly
      review."
    `wrong` is "" except for corrections. If context.md already lists the same thing, skip it.
12. `resolved_goals`: ids of goals listed in context.md that this week shows reached or settled.
    Usually empty.
13. Here, career direction and wanting to earn income may be written at a high level. Amounts,
    accounts, debts, and other money details are still left out.
"""

THEMES_TASK = """\
You maintain a private second brain about {owner}. `projects.md` lists the projects {owner} has
worked on, with attention and dates. `skills.md` lists the technologies and domains their
conversations show, with levels. `about.md` lists interests and facts {owner} stated.
Return the themes in their work, using the JSON schema.

RULES

1. A theme is an area {owner} keeps coming back to across projects: a domain, a kind of problem,
   or a family of technology. GOOD: "Home automation", "Developer tooling".
   BAD: "Software" (too broad). BAD: "the login page" (one project).
2. 3 to 7 themes. Put first the ones with the most attention and the most returns after a break.
3. `name`: 2 to 5 words. `summary`: 1 to 2 sentences on what {owner} does in this area and how
   deep it goes, using skill levels from skills.md where they fit.
4. `projects`: at least 2 project names, copied exactly from projects.md.
5. Facts only from the input files. No praise and no advice.
6. Sensitive matters stay high level: schoolwork, grades, health, money, relationships.
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
                    "kind": {"type": "string", "enum": ["preference", "dislike", "work-style", "interest", "communication"]},
                    "text": {"type": "string"},
                    "evidence": {"type": "string"},
                    "reinforces": {"type": "string"},
                },
                "required": ["scope", "kind", "text", "evidence", "reinforces"],
                "additionalProperties": False,
            },
        },
        "skills": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "level": {"type": "string", "enum": ["directs", "learning", "struggled"]},
                    "evidence": {"type": "string"},
                    "reinforces": {"type": "string"},
                },
                "required": ["topic", "level", "evidence", "reinforces"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["blurb", "week_summary", "subprojects_touched", "project_notes", "observations", "skills"],
    "additionalProperties": False,
}

ABOUT_ME_SCHEMA = {
    **PROJECT_WEEK_SCHEMA,
    "properties": {
        **PROJECT_WEEK_SCHEMA["properties"],
        "said": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["stated", "correction", "goal", "advice"]},
                    "text": {"type": "string"},
                    "wrong": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["kind", "text", "wrong", "evidence"],
                "additionalProperties": False,
            },
        },
        "resolved_goals": {"type": "array", "items": {"type": "string"}},
    },
    "required": [*PROJECT_WEEK_SCHEMA["required"], "said", "resolved_goals"],
}

THEMES_SCHEMA = {
    "type": "object",
    "properties": {
        "themes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "summary": {"type": "string"},
                    "projects": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "summary", "projects"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["themes"],
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
