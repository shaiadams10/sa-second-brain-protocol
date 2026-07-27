# Natural-language interface

The user should never need to remember a command, skill name, workflow name, or argument syntax. When an agent is opened in the second-brain workspace, ordinary language is the primary interface. Commands and skills are internal implementation details.

## Routing contract

1. Infer the user's intent from the request and choose the narrowest matching brain operation.
2. Use the matching skill or CLI operation internally. Do not ask the user to repeat the request using special syntax.
3. Present the useful result, not the command that produced it. Show implementation details only when the user asks or recovery requires action from them.
4. Preserve approval boundaries. Natural language does not imply approval of public claims, review items, bootstrap completion, publication, model substitution, conflict resolution, or destructive actions.
5. Distinguish reading from updating. A question such as “What did I work on this week?” reads canonical activity; it does not rerun weekly synthesis. A request such as “Update the brain with this week’s work” explicitly asks for an update.
6. If multiple operations are plausible and the choice would materially change state or cost, ask one narrow clarification. Otherwise choose the safest read-only interpretation.

## Intent map

| Natural request | Internal route | Behavior |
| --- | --- | --- |
| Create a resume, LinkedIn section, application, or interview brief | Career | Build from verified canonical evidence and require review before public use. |
| Write a message, post, biography, or explanation in my voice | Write as me | Use verified voice and identity context; never send automatically. |
| What do you know about me, a skill, or a project? | Search | Answer from canonical notes with provenance and acknowledge gaps. |
| What changed recently or what did I work on? | Search and recent activity | Read existing daily, weekly, and project knowledge without starting ingestion. |
| What was my latest Codex or Antigravity session or last message? | Recent sessions | Query the bounded local session view by surface, project, timestamp, and user role without publishing raw conversations. |
| Reconcile agent sessions with the project index, including older moved folders | Project-session reconciliation | Read bounded session metadata first and match at most one known leaf project by current path, historical path, or unique Git identity. Matched sessions enter the full lane; unmatched or ambiguous sessions may enter the isolated profile-only lane without creating project knowledge. Reconciliation itself makes no model call or publication. |
| This Codex or Antigravity session belongs to a named project | Session attribution correction | Preview one exact session-to-project change, require confirmation, persist the stable project mapping in machine-local configuration, and reconcile without using conversational keyword guesses. |
| Analyze the accepted historical sessions project by project | Project-session analysis | Send one known project's indexed digests per bounded model packet, publish only that project's dossier and audit note, and discard personal-profile, skill, voice, and question output. |
| Update the brain with today’s work | Daily | Run the incremental daily pipeline. |
| Synthesize or update this week | Weekly | Run weekly synthesis after the daily evidence is current. |
| Refresh what we know about a named project | Project refresh | Resolve the known project and scan it read-only. |
| Refresh, repair, or reconcile the project index | Project catalog sync | Re-scan configured project roots, rebuild the generated project index from scanner truth, and refresh local search without a model call or Git publication. |
| Forget or remove everything about a named project | Project forgetting | Preview exact matches, protect similarly named projects, back up active state, remove project-specific canonical/state/derived knowledge, and ignore the source path so it is not re-ingested. Source repositories remain untouched. |
| Show or open the dashboard | Dashboard | Start or reuse the private loopback dashboard and open it. No knowledge changes occur without an explicit card action. |
| What needs my approval? | Review | Start with the short grouped review dashboard, never the machine ledger. |
| Correct or add a fact about me | Interview or reviewed note update | Capture the explicit fact without inventing missing details. |
| Is the brain healthy or did the schedule run? | Health | Diagnose first and use the recovery runbook only when needed. |
| Publish the reusable protocol | Protocol publish | Create or update a sanitized draft PR; never merge automatically. |

## Agent presentation

- Say what was learned, created, updated, found, or blocked.
- Do not lead with terminal syntax or ask the user to invoke a skill.
- When the protocol returns a local note or dashboard, open it when safe or provide a direct link.
- Keep machine IDs, evidence IDs, run IDs, and raw ledger details in the background unless the user requests provenance.

Explicit commands remain documented for maintainers, automation, tests, and recovery. They are not the normal human interface.
