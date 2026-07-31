# Natural-language interface

The user should never need to remember a command, skill name, workflow name, or argument syntax. When an agent is opened in the second-brain workspace, ordinary language is the primary interface. Commands and skills are internal implementation details.

## Routing contract

1. Infer the user's intent from the request and choose the narrowest matching brain operation.
2. Use the matching skill or CLI operation internally. Do not ask the user to repeat the request using special syntax.
3. Present the useful result, not the command that produced it. Show implementation details only when the user asks or recovery requires action from them.
4. Preserve approval boundaries. Natural language does not imply approval of public claims, review items, bootstrap completion, publication, model substitution, conflict resolution, or destructive actions.
5. Distinguish reading from updating. A question such as “What did I work on this week?” reads canonical activity; it does not rerun weekly synthesis. A request such as “Update the brain with this week’s work” explicitly asks for an update.
6. If multiple operations are plausible and the choice would materially change state or cost, ask one narrow clarification. Otherwise choose the safest read-only interpretation.
7. During every interaction in the main second-brain vault, notice durable information about the owner even when it appears inside another task and the owner does not say "update the brain."
8. Classify each insight into exactly one Knowledge Deck layer: About the user (personal facts, goals, personality, voice, or work style), Professional profile (experience, education, service, or capability), How I work (reusable preferences), or Project knowledge (project facts, decisions, or lessons with one exact project attribution).
9. A clear, durable, non-sensitive, non-conflicting owner statement may be captured as confirmed knowledge. A merely implied insight normally becomes an unconfirmed Curate candidate without asking a follow-up question. Exceptionally high-confidence implied knowledge may be confirmed immediately when it is broadly reusable and its meaning and classification are unambiguous.
10. Lead with an auditable capture notice, then continue the original task. The notice must state the exact canonical note, Knowledge Deck layer, global or project scope, confirmation state, concise distilled claim, and a short classification reason. Project knowledge must name the exact project and explicitly distinguish the capture from personal, professional-profile, and operating-preference knowledge. Never use a standalone "Brain updated with..." message that hides the destination.
11. This immediate write route exists only inside the main vault. Agents in other projects never write canonical private notes or submit candidates; their sessions remain read-only evidence and enter through Daily ingestion.

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
| Update the brain with today’s work | Daily | A direct request authorizes one manual Daily. Use the explicit owner-request authorization; if the owner calls it a test, publish locally without Git snapshot, commit, or push. Otherwise the 10:30 PM schedule remains the default. |
| Synthesize or update this week | Weekly | Run weekly synthesis after the daily evidence is current. |
| Refresh what we know about a named project | Project refresh | Resolve the known project and scan it read-only. |
| Refresh, repair, or reconcile the project index | Project catalog sync | Re-scan configured project roots, rebuild the generated project index from scanner truth, and refresh local search without a model call or Git publication. |
| Forget or remove everything about a named project | Project forgetting | Preview exact matches, protect similarly named projects, back up active state, remove project-specific canonical/state/derived knowledge, and ignore the source path so it is not re-ingested. Source repositories remain untouched. |
| Show or open the dashboard | Dashboard | Start or reuse the private loopback dashboard and open it. No knowledge changes occur without an explicit card action. |
| What needs my approval? | Review | Start with the short grouped review dashboard, never the machine ledger. |
| Correct or add a fact about me | Interview or reviewed note update | Capture the explicit fact without inventing missing details. |
| State a clear reusable insight during normal work in the main vault | Confirmed knowledge capture | Classify it, publish it through the deterministic capture route, and confirm it when it is durable, non-sensitive, non-conflicting, and unambiguous. |
| Imply a potentially durable insight during normal work in the main vault | Curate candidate capture | Do not interrupt the task with a clarification. Distill and classify one atomic candidate, add it to Curate as New, announce it first, and continue the request. |
| Is the brain healthy or did the schedule run? | Health | Diagnose first and use the recovery runbook only when needed. |
| Publish the reusable protocol | Protocol publish | Create or update a sanitized draft PR; never merge automatically. |

## Agent presentation

- Say what was learned, created, updated, found, or blocked.
- When knowledge was captured, make its structured Brain or Curate notice the first part of the response. State the exact note, layer, scope, confirmation state, claim, and classification reason; include the exact project for project knowledge.
- Do not lead with terminal syntax or ask the user to invoke a skill.
- When the protocol returns a local note or dashboard, open it when safe or provide a direct link.
- Keep machine IDs, evidence IDs, run IDs, and raw ledger details in the background unless the user requests provenance.

Explicit commands remain documented for maintainers, automation, tests, and recovery. They are not the normal human interface.
