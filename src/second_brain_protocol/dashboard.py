from __future__ import annotations

import json
import os
import re
import subprocess
import webbrowser
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .activity import session_recap
from .codex_account import read_codex_rate_limits
from .config import RuntimePaths, load_defaults, load_runtime_config, protocol_root
from .curate import (
    KNOWLEDGE_LAYER_KINDS,
    KNOWLEDGE_LAYER_NOTES,
    KNOWLEDGE_LAYER_ORDER,
)
from .markdown import slugify
from .model_runner import TOKEN_USAGE_FIELDS, usage_from_receipt
from .notifications import obsidian_uri
from .project_catalog import project_catalog_health
from .review import build_review_groups, review_summary
from .scheduler import task_details
from .security import sanitize_text
from .state import StateStore


VISIBLE_PROJECT_CLASSES = {
    "first-party",
    "fork",
    "modified-fork",
    "experiment",
    "review",
}
MODEL_PRICING_USD_PER_MTOK = {
    "gpt-5.6-luna": {"input": 1.0, "cached_input": 0.10, "output": 6.0},
    "gpt-5.6-terra": {"input": 2.5, "cached_input": 0.25, "output": 15.0},
    "gpt-5.6-sol": {"input": 5.0, "cached_input": 0.50, "output": 30.0},
    "deepseek/deepseek-v4-flash": {
        "input": 0.098,
        "cached_input": 0.0196,
        "output": 0.196,
    },
    "openai/gpt-5.6-luna": {"input": 1.0, "cached_input": 0.10, "output": 6.0},
    "openai/gpt-5.6-terra": {"input": 2.5, "cached_input": 0.25, "output": 15.0},
    "openai/gpt-5.6-sol": {"input": 5.0, "cached_input": 0.50, "output": 30.0},
}
PRICING_AS_OF = "2026-07-24"
INSIGHT_KINDS = {
    "decision",
    "lesson",
    "personality",
    "preference",
    "project_fact",
    "skill",
    "voice_style",
    "work_style",
}
KIND_LABELS = {
    "decision": "Decision",
    "education": "Education",
    "experience": "Experience",
    "explicit_fact": "Personal fact",
    "goal": "Goal",
    "lesson": "Lesson",
    "military": "Military service",
    "personality": "Personal pattern",
    "preference": "Preference",
    "project_fact": "Project knowledge",
    "skill": "Capability",
    "voice_style": "Voice",
    "work_style": "Work style",
}
PERSONAL_PATTERN_KINDS = {"personality", "preference", "voice_style", "work_style"}
PROJECT_CONTEXT_MARKERS = (
    "this demo",
    "the animated demo",
    "this project",
    "the current project",
    "the referenced repository",
    "the linked figma",
    "dynamicremotion protocol",
    "for remotion workflow research",
    "when stopping local ai work",
    "home-network sharing",
    "native video-input path",
    "lm studio-based solution",
    "slow dequantization",
    "earlier attempt",
)
GLOBAL_PATTERN_MARKERS = (
    "shai prefers",
    "shai explicitly prefers",
    "prefers ",
    "actively rejects",
    "repeatedly asks",
    "asks for a plan",
    "across multiple",
    "every relevant reference",
    "multiple ai coding-agent environments",
    "project-local skill and tooling installations",
)
IMPORTANT_PROJECT_FACT_TERMS = (
    "authorship",
    "ownership",
    "first-party",
    "third-party",
    "deployed",
    "deployment",
    "production release",
    "went live",
    "archived",
    "paused",
    "blocked",
    "incomplete",
    "failed validation",
    "security boundary",
    "privacy boundary",
)
MATT_SKILL_GUIDE = (
    {
        "name": "setup-matt-pocock-skills",
        "group": "Setup",
        "when": "Once per repository, before the first engineering flow.",
        "outcome": "Connects the issue tracker, triage labels, and domain docs.",
        "requires": [],
    },
    {
        "name": "ask-matt",
        "group": "Router",
        "when": "You are unsure which skill or workflow fits.",
        "outcome": "Routes the situation without doing the work itself.",
        "requires": ["setup-matt-pocock-skills"],
    },
    {
        "name": "grill-with-docs",
        "group": "Main flow",
        "when": "An idea in a codebase needs decisions resolved before building.",
        "outcome": "One-question-at-a-time alignment with Protocol docs updated inline.",
        "requires": ["grilling", "domain-modeling"],
    },
    {
        "name": "to-spec",
        "group": "Main flow",
        "when": "The conversation is clear and a multi-session build needs a durable spec.",
        "outcome": "Synthesizes the current context into a GitHub issue; no new interview.",
        "requires": ["setup-matt-pocock-skills"],
    },
    {
        "name": "to-tickets",
        "group": "Main flow",
        "when": "An approved spec or plan needs context-sized implementation slices.",
        "outcome": "Publishes tracer-bullet tickets with explicit blocking edges.",
        "requires": ["to-spec"],
    },
    {
        "name": "implement",
        "group": "Main flow",
        "when": "A spec or ready ticket is approved for implementation.",
        "outcome": "Builds, verifies, reviews, and commits the requested slice.",
        "requires": ["tdd", "code-review"],
    },
    {
        "name": "tdd",
        "group": "Main flow",
        "when": "A feature or fix should be built test-first at agreed seams.",
        "outcome": "Runs one vertical red → green slice at a time.",
        "requires": ["codebase-design"],
    },
    {
        "name": "code-review",
        "group": "Main flow",
        "when": "A branch or diff needs review against a fixed point.",
        "outcome": "Separates Standards findings from Spec findings.",
        "requires": ["setup-matt-pocock-skills"],
    },
    {
        "name": "triage",
        "group": "On-ramp",
        "when": "Raw bug reports or external requests need evaluation.",
        "outcome": "Moves issues through canonical states and writes durable briefs.",
        "requires": ["setup-matt-pocock-skills", "grilling", "domain-modeling"],
    },
    {
        "name": "diagnosing-bugs",
        "group": "On-ramp",
        "when": "A hard bug, flake, failure, or regression resists a first look.",
        "outcome": "Builds a tight red-capable loop, isolates the cause, and locks the fix.",
        "requires": ["tdd"],
    },
    {
        "name": "wayfinder",
        "group": "On-ramp",
        "when": "A huge effort is too foggy for one session or one linear plan.",
        "outcome": "Maps decision tickets until the route to a spec is clear.",
        "requires": [
            "setup-matt-pocock-skills",
            "grilling",
            "domain-modeling",
            "research",
        ],
    },
    {
        "name": "improve-codebase-architecture",
        "group": "Maintenance",
        "when": "Recurring friction suggests shallow modules or weak test seams.",
        "outcome": "Produces a visual deepening report, then explores one candidate.",
        "requires": ["codebase-design", "grilling", "domain-modeling"],
    },
    {
        "name": "domain-modeling",
        "group": "Foundation",
        "when": "Terminology is fuzzy, overloaded, contradictory, or decision-worthy.",
        "outcome": "Sharpens Protocol language and records durable decisions sparingly.",
        "requires": [],
    },
    {
        "name": "codebase-design",
        "group": "Foundation",
        "when": "A module interface, seam, adapter, or test surface needs design.",
        "outcome": "Applies deep-module vocabulary for leverage and locality.",
        "requires": [],
    },
    {
        "name": "grilling",
        "group": "Foundation",
        "when": "A plan or decision needs every branch resolved with the user.",
        "outcome": "Asks one recommended question at a time and waits for confirmation.",
        "requires": [],
    },
    {
        "name": "prototype",
        "group": "Detour",
        "when": "Logic or UI cannot be settled confidently on paper.",
        "outcome": "Creates throwaway code that answers one design question.",
        "requires": [],
    },
    {
        "name": "research",
        "group": "Detour",
        "when": "A decision needs primary-source reading while other work continues.",
        "outcome": "Delegates research and leaves one cited Markdown artifact.",
        "requires": [],
    },
    {
        "name": "handoff",
        "group": "Bridge",
        "when": "A fresh session is needed without losing the current context.",
        "outcome": "Writes a redacted temporary handoff with suggested next skills.",
        "requires": [],
    },
    {
        "name": "resolving-merge-conflicts",
        "group": "Standalone",
        "when": "A merge or rebase is already in conflict.",
        "outcome": "Resolves by original intent, verifies, and completes the operation.",
        "requires": [],
    },
    {
        "name": "grill-me",
        "group": "Standalone",
        "when": "A plan outside a codebase needs a relentless interview.",
        "outcome": "Runs the stateless grilling loop without writing project docs.",
        "requires": ["grilling"],
    },
    {
        "name": "teach",
        "group": "Standalone",
        "when": "A concept should be learned across multiple sessions.",
        "outcome": "Builds a stateful teaching workspace around a learner mission.",
        "requires": [],
    },
    {
        "name": "writing-great-skills",
        "group": "Reference",
        "when": "A skill needs to be created, edited, or made more predictable.",
        "outcome": "Supplies invocation, hierarchy, pruning, and leading-word discipline.",
        "requires": [],
    },
)
MATT_SKILL_LANES = (
    {
        "id": "orient",
        "label": "Start & route",
        "hint": "Set up the system, choose a workflow, or triage incoming work.",
    },
    {
        "id": "discover",
        "label": "Clarify & investigate",
        "hint": "Resolve uncertainty before committing to a direction.",
    },
    {
        "id": "define",
        "label": "Shape & plan",
        "hint": "Turn understanding into durable language, specs, and tickets.",
    },
    {
        "id": "build",
        "label": "Build",
        "hint": "Design and implement small, verifiable vertical slices.",
    },
    {
        "id": "validate",
        "label": "Fix & review",
        "hint": "Diagnose failures, review changes, and integrate safely.",
    },
    {
        "id": "evolve",
        "label": "Maintain & extend",
        "hint": "Improve architecture, transfer context, teach, and create skills.",
    },
)
MATT_SKILL_DETAILS = {
    "setup-matt-pocock-skills": {
        "lane": "orient",
        "steps": [
            "Choose the issue tracker and canonical triage labels.",
            "Point domain guidance at the repository's existing documentation.",
            "Record the configuration in the agent instructions.",
        ],
        "avoid": "Do not rerun it casually after setup; review existing configuration before replacing anything.",
        "example": "A repository adopts GitHub issues, maps Matt's five triage labels, and keeps Protocol as its single domain-documentation home.",
    },
    "ask-matt": {
        "lane": "orient",
        "steps": [
            "Describe the situation and the outcome you need.",
            "Receive the smallest matching skill or workflow.",
            "Start that suggested skill in the appropriate context.",
        ],
        "avoid": "Do not expect it to implement, research, or diagnose; it is a router.",
        "example": "You have a vague refactor request and it directs you to improve-codebase-architecture before implementation.",
    },
    "triage": {
        "lane": "orient",
        "steps": [
            "Inspect the report and gather missing reproduction facts.",
            "Apply one canonical state label.",
            "Write a bounded brief when the issue is ready.",
        ],
        "avoid": "Do not implement the issue during triage or mark ambiguous work ready.",
        "example": "A vague crash report becomes a reproducible issue with needs-info cleared and a ready-for-agent brief attached.",
    },
    "grill-with-docs": {
        "lane": "discover",
        "steps": [
            "Read the existing project and domain documentation.",
            "Resolve one dependent decision at a time with a recommendation.",
            "Update the documentation until shared understanding is confirmed.",
        ],
        "avoid": "Do not use it when requirements are already settled or project docs should not change.",
        "example": "A dashboard redesign is grilled from goals through interactions, with every accepted decision recorded before coding.",
    },
    "grill-me": {
        "lane": "discover",
        "steps": [
            "State the idea, plan, or decision.",
            "Answer one recommended question at a time.",
            "Confirm shared understanding before action begins.",
        ],
        "avoid": "Use grill-with-docs instead when accepted decisions must update repository documentation.",
        "example": "A launch idea is pressure-tested across audience, scope, risks, and success criteria without touching a codebase.",
    },
    "grilling": {
        "lane": "discover",
        "steps": [
            "Map the unresolved decision tree.",
            "Ask exactly one dependent question with a recommendation.",
            "Continue until the user confirms shared understanding.",
        ],
        "avoid": "Do not ask a batch of questions or act before the design is confirmed.",
        "example": "An unclear permissions model becomes a sequence of explicit owner decisions instead of an assumption-heavy implementation.",
    },
    "wayfinder": {
        "lane": "discover",
        "steps": [
            "Identify major unknowns and decision frontiers.",
            "Create small decision tickets with explicit relationships.",
            "Resolve them until the work can become a coherent spec.",
        ],
        "avoid": "Do not disguise a multi-frontier problem as one giant implementation ticket.",
        "example": "A platform migration becomes a map of authentication, data, rollout, and compatibility decisions that converge into one spec.",
    },
    "research": {
        "lane": "discover",
        "steps": [
            "Define the decision the research must inform.",
            "Investigate authoritative primary sources.",
            "Capture findings, uncertainty, and citations in one artifact.",
        ],
        "avoid": "Do not return an uncited web summary or research without a decision target.",
        "example": "Official platform documentation is compared in a cited brief before an authentication flow is chosen.",
    },
    "domain-modeling": {
        "lane": "define",
        "steps": [
            "Collect the terms people and code currently use.",
            "Expose contradictions, overloaded words, and missing distinctions.",
            "Update canonical language and record durable decisions sparingly.",
        ],
        "avoid": "Do not invent abstractions before evidence exists or document language that is already stable.",
        "example": "A team separates observation, claim, and evidence so APIs, docs, and review states stop contradicting each other.",
    },
    "prototype": {
        "lane": "define",
        "steps": [
            "Name the single uncertainty the prototype must resolve.",
            "Build the smallest disposable experiment.",
            "Record the result and discard or isolate the prototype.",
        ],
        "avoid": "Do not let exploratory code quietly become production architecture.",
        "example": "A throwaway interaction proves whether a metro-map layout stays readable on mobile before the dashboard is changed.",
    },
    "to-spec": {
        "lane": "define",
        "steps": [
            "Use the already-resolved conversation and project context.",
            "Write the problem, constraints, design, and acceptance criteria.",
            "Publish one durable issue that can survive a fresh session.",
        ],
        "avoid": "Do not use it to discover missing requirements; return to grilling when decisions remain.",
        "example": "An approved interactive guide becomes a self-contained GitHub issue with behavior and responsive acceptance criteria.",
    },
    "to-tickets": {
        "lane": "define",
        "steps": [
            "Read the approved spec and identify end-to-end slices.",
            "Create independently verifiable context-sized tickets.",
            "Connect blockers and mark only ready work actionable.",
        ],
        "avoid": "Do not ticket unresolved specifications or split work only by technical layer.",
        "example": "A dashboard feature becomes data, interaction, accessibility, and visual-QA tickets connected by explicit blockers.",
    },
    "codebase-design": {
        "lane": "build",
        "steps": [
            "Sketch at least two credible interface designs.",
            "Compare information hiding, depth, locality, and test seams.",
            "Choose the smallest interface that hides the most complexity.",
        ],
        "avoid": "Do not multiply thin wrappers or expose implementation detail through broad interfaces.",
        "example": "Two dashboard data APIs are compared; the deeper one wins because rendering never sees filesystem details.",
    },
    "tdd": {
        "lane": "build",
        "steps": [
            "Agree on the observable seam and write one failing test.",
            "Implement the smallest vertical behavior that makes it pass.",
            "Refactor only after green, then repeat.",
        ],
        "avoid": "Do not mock internal details or write a large test batch before feedback.",
        "example": "A route-selection test fails first, then the smallest dashboard behavior is added before styling.",
    },
    "implement": {
        "lane": "build",
        "steps": [
            "Read one approved ticket or bounded specification.",
            "Implement and verify the requested behavior.",
            "Review the diff and create a focused commit.",
        ],
        "avoid": "Do not combine unrelated tickets or invent product decisions while implementing.",
        "example": "One ready dashboard ticket is implemented test-first, visually checked, reviewed, and committed.",
    },
    "diagnosing-bugs": {
        "lane": "validate",
        "steps": [
            "Create the smallest reliable reproduction loop.",
            "Test competing hypotheses and narrow the failure boundary.",
            "Fix the cause and preserve a regression test.",
        ],
        "avoid": "Do not stack speculative fixes before identifying which hypothesis is true.",
        "example": "A sporadic route failure is reduced to one resize sequence, isolated, fixed, and locked with a regression test.",
    },
    "code-review": {
        "lane": "validate",
        "steps": [
            "Choose the exact commit, branch, tag, or merge base.",
            "Review standards separately from requested behavior.",
            "Report prioritized findings with tight locations.",
        ],
        "avoid": "Do not review an undefined moving target or mix preferences with correctness defects.",
        "example": "A branch is checked against main and its spec, revealing one accessibility defect and one missing criterion.",
    },
    "resolving-merge-conflicts": {
        "lane": "validate",
        "steps": [
            "Inspect the conflicting histories and both intended changes.",
            "Construct the integrated result instead of choosing one side.",
            "Run focused verification and complete the operation.",
        ],
        "avoid": "Do not default to ours or theirs without understanding the intended combined behavior.",
        "example": "Two dashboard changes touch one renderer; both intentions are preserved and verified before the rebase continues.",
    },
    "improve-codebase-architecture": {
        "lane": "evolve",
        "steps": [
            "Find repeated friction, leakage, and shallow-module symptoms.",
            "Rank deepening candidates in a visual report.",
            "Choose and explore one bounded improvement.",
        ],
        "avoid": "Do not launch a repository-wide refactor from aesthetic discomfort alone.",
        "example": "Repeated rendering friction identifies one leaky data boundary, which is redesigned before migration.",
    },
    "handoff": {
        "lane": "evolve",
        "steps": [
            "Summarize the goal, decisions, state, and blockers.",
            "Remove secrets, transcripts, and irrelevant history.",
            "Name the next action and likely next skills.",
        ],
        "avoid": "Do not copy the conversation wholesale or treat a temporary handoff as canonical documentation.",
        "example": "A nearly full session leaves a compact handoff that lets a fresh agent resume the exact ticket safely.",
    },
    "teach": {
        "lane": "evolve",
        "steps": [
            "Define the learner mission and current level.",
            "Sequence explanations, exercises, resources, and vocabulary.",
            "Persist progress so the next session continues.",
        ],
        "avoid": "Do not build a stateful curriculum when one concise explanation is enough.",
        "example": "A developer learns deep-module design across sessions with exercises and a durable glossary.",
    },
    "writing-great-skills": {
        "lane": "evolve",
        "steps": [
            "Choose whether invocation is user-led or model-led.",
            "Write precise triggers and a lean instruction hierarchy.",
            "Prune ambiguity and validate realistic prompts.",
        ],
        "avoid": "Do not create a universal skill with vague triggers and an oversized description.",
        "example": "A noisy review prompt becomes a focused skill with explicit triggers and progressive references.",
    },
}
MATT_SKILL_CONNECTIONS = (
    {"from": "setup-matt-pocock-skills", "to": "triage", "kind": "supports"},
    {"from": "grilling", "to": "grill-with-docs", "kind": "supports"},
    {"from": "domain-modeling", "to": "grill-with-docs", "kind": "supports"},
    {"from": "research", "to": "wayfinder", "kind": "supports"},
    {"from": "wayfinder", "to": "grill-with-docs", "kind": "feeds"},
    {"from": "grill-with-docs", "to": "to-spec", "kind": "feeds"},
    {"from": "to-spec", "to": "to-tickets", "kind": "feeds"},
    {"from": "to-tickets", "to": "implement", "kind": "feeds"},
    {"from": "domain-modeling", "to": "codebase-design", "kind": "supports"},
    {"from": "prototype", "to": "codebase-design", "kind": "supports"},
    {"from": "codebase-design", "to": "tdd", "kind": "feeds"},
    {"from": "tdd", "to": "implement", "kind": "feeds"},
    {"from": "diagnosing-bugs", "to": "tdd", "kind": "feeds"},
    {"from": "implement", "to": "code-review", "kind": "feeds"},
    {
        "from": "codebase-design",
        "to": "improve-codebase-architecture",
        "kind": "supports",
    },
    {"from": "grill-me", "to": "grill-with-docs", "kind": "alternative"},
    {"from": "prototype", "to": "research", "kind": "alternative"},
)
MATT_SKILL_SPARKS = (
    {
        "id": "fuzzy",
        "label": "I have an idea, but it is still fuzzy.",
        "description": "Resolve the important choices, preserve them, then turn the result into buildable work.",
        "routes": [
            {
                "label": "Primary · guided idea to delivery",
                "skills": [
                    "grill-with-docs",
                    "domain-modeling",
                    "to-spec",
                    "to-tickets",
                    "implement",
                    "code-review",
                ],
            },
            {
                "label": "Alternative · the effort is bigger than one plan",
                "skills": ["wayfinder", "research", "grill-with-docs", "to-spec"],
            },
            {
                "label": "Alternative · explore before committing",
                "skills": [
                    "grill-with-docs",
                    "prototype",
                    "codebase-design",
                    "to-spec",
                ],
            },
        ],
    },
    {
        "id": "structure",
        "label": "I know what to build, but not how to structure it.",
        "description": "Clarify the language and seams before implementation hardens the wrong design.",
        "routes": [
            {
                "label": "Primary · design the seam",
                "skills": [
                    "domain-modeling",
                    "codebase-design",
                    "prototype",
                    "tdd",
                    "implement",
                ],
            },
            {
                "label": "Alternative · recurring architectural friction",
                "skills": [
                    "improve-codebase-architecture",
                    "codebase-design",
                    "prototype",
                ],
            },
            {
                "label": "Alternative · validate the uncertainty first",
                "skills": ["research", "prototype", "codebase-design"],
            },
        ],
    },
    {
        "id": "broken",
        "label": "Something is broken and the cause is unclear.",
        "description": "Move from a reproducible signal to an isolated cause and a locked regression fix.",
        "routes": [
            {
                "label": "Primary · isolate and lock the fix",
                "skills": ["diagnosing-bugs", "tdd", "implement", "code-review"],
            },
            {
                "label": "Alternative · the report is not actionable yet",
                "skills": ["triage", "diagnosing-bugs", "tdd"],
            },
            {
                "label": "Alternative · the breakage is a merge conflict",
                "skills": ["resolving-merge-conflicts", "code-review"],
            },
        ],
    },
    {
        "id": "wrong-shape",
        "label": "The code works, but the design feels wrong.",
        "description": "Turn recurring friction into evidence, compare deeper modules, and test one candidate.",
        "routes": [
            {
                "label": "Primary · deepen the architecture",
                "skills": [
                    "improve-codebase-architecture",
                    "domain-modeling",
                    "codebase-design",
                    "prototype",
                ],
            },
            {
                "label": "Alternative · terminology is the real problem",
                "skills": ["domain-modeling", "grilling", "codebase-design"],
            },
            {
                "label": "Alternative · inspect the current change first",
                "skills": ["code-review", "codebase-design"],
            },
        ],
    },
    {
        "id": "too-large",
        "label": "The work is too large to see clearly.",
        "description": "Map decision frontiers first; create a spec only after the landscape becomes coherent.",
        "routes": [
            {
                "label": "Primary · find the route through the fog",
                "skills": [
                    "wayfinder",
                    "research",
                    "grill-with-docs",
                    "to-spec",
                    "to-tickets",
                ],
            },
            {
                "label": "Alternative · understanding exists, context does not",
                "skills": ["handoff", "to-spec", "to-tickets"],
            },
            {
                "label": "Alternative · one uncertain branch blocks everything",
                "skills": ["research", "prototype", "grill-with-docs"],
            },
        ],
    },
    {
        "id": "confidence",
        "label": "I need confidence before I ship.",
        "description": "Make behavior observable, verify the requested slice, then review it against the right baseline.",
        "routes": [
            {
                "label": "Primary · test, implement, review",
                "skills": ["codebase-design", "tdd", "implement", "code-review"],
            },
            {
                "label": "Alternative · a suspicious failure remains",
                "skills": ["diagnosing-bugs", "tdd", "code-review"],
            },
            {
                "label": "Alternative · integration is conflicted",
                "skills": ["resolving-merge-conflicts", "tdd", "code-review"],
            },
        ],
    },
    {
        "id": "transfer",
        "label": "I need to preserve or transfer understanding.",
        "description": "Choose the durable format that matches the lifespan: session, specification, curriculum, or reusable skill.",
        "routes": [
            {
                "label": "Primary · continue in a fresh session",
                "skills": ["handoff", "ask-matt"],
            },
            {
                "label": "Alternative · preserve product intent",
                "skills": ["grill-with-docs", "to-spec", "to-tickets"],
            },
            {
                "label": "Alternative · teach reusable understanding",
                "skills": ["teach", "writing-great-skills"],
            },
        ],
    },
)
LOW_VALUE_KNOWLEDGE_TERMS = (
    "windows icons",
    "secondary iconography",
    "svgl.app",
    "svg logo",
    "linked figma design",
    "preview server available over the local network",
)
LOW_VALUE_QUESTION_TERMS = (
    "future packets",
    "trajectory excerpts",
    "what user request initiated",
    "master duration",
    "system-prompt size",
    "system prompt size",
    "caption identity count",
    "session timeline",
    "chat recovery outcome",
    "canonical project ids",
    "repository names",
    "folder project boundaries",
    "for each major project",
)
GENERIC_PROJECT_NAMES = {
    "app",
    "apps",
    "client",
    "code",
    "frontend",
    "lib",
    "libs",
    "packages",
    "server",
    "source",
    "src",
}


def _project_is_attributable(project: dict[str, Any]) -> bool:
    name = str(project.get("name") or project.get("logical_name") or "").strip()
    if not name or name.casefold() in GENERIC_PROJECT_NAMES:
        return False
    return str(project.get("classification") or "") not in {"collection", "duplicate"}


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _clean_markdown(text: str, *, max_chars: int = 520) -> str:
    text = sanitize_text(text, max_chars=max_chars * 3)
    text = re.sub(r"^---\s.*?\s---\s*", "", text, flags=re.DOTALL)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(
        r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", lambda m: m.group(2) or m.group(1), text
    )
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    text = re.sub(r"\^(?:obs|ev)-[a-zA-Z0-9-]+", "", text)
    text = re.sub(r"[`*>#]", "", text)
    text = re.sub(r"^\s*[-+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    shortened = text[: max_chars - 1].rsplit(" ", 1)[0].rstrip(" ,.;:")
    return shortened + "…"


def _generated_section(path: Path, section: str) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    pattern = re.compile(
        rf"<!-- sb:generated {re.escape(section)}:start -->\s*(.*?)\s*"
        rf"<!-- sb:generated {re.escape(section)}:end -->",
        re.DOTALL,
    )
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def _latest_note(folder: Path, pattern: str) -> Path | None:
    candidates = [
        path for path in folder.glob(pattern) if path.name.casefold() != "index.md"
    ]
    return max(candidates, key=lambda path: path.stem) if candidates else None


def _summary_sections(raw: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] = {"title": "Summary", "items": [], "paragraphs": []}
    paragraph_lines: list[str] = []

    def flush_paragraph() -> None:
        if not paragraph_lines:
            return
        paragraph = _clean_markdown(" ".join(paragraph_lines), max_chars=4000)
        if paragraph:
            current["paragraphs"].append(paragraph)
        paragraph_lines.clear()

    def flush_section() -> None:
        flush_paragraph()
        if current["items"] or current["paragraphs"]:
            sections.append(
                {
                    "title": current["title"],
                    "items": list(current["items"]),
                    "paragraphs": list(current["paragraphs"]),
                }
            )

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("<!--"):
            flush_paragraph()
            continue
        heading = re.match(r"^#{1,6}\s+(.+)$", stripped)
        if heading:
            flush_section()
            current = {
                "title": _clean_markdown(heading.group(1), max_chars=120),
                "items": [],
                "paragraphs": [],
            }
            continue
        bullet = re.match(r"^[-+*]\s+(.+)$", stripped)
        if bullet:
            flush_paragraph()
            item = _clean_markdown(bullet.group(1), max_chars=1200)
            if item:
                current["items"].append(item)
            continue
        paragraph_lines.append(stripped)
    flush_section()
    return sections


CHANGE_LABELS = {
    "added": "Project added",
    "classification_changed": "Classification",
    "commits_added": "Commits",
    "files_changed": "Files",
    "head_changed": "Git head",
    "removed": "Removed",
    "stack_changed": "Tech stack",
    "project_brain_changed": "Project brain",
    "working_tree_changed": "Working tree",
}


def _count_chips(text: str) -> list[dict[str, Any]]:
    return [
        {
            "label": CHANGE_LABELS.get(name.casefold(), name.replace("_", " ").title()),
            "value": int(value),
        }
        for name, value in re.findall(
            r"([a-z][a-z0-9_]*)\s*\((\d+)\)", text, re.IGNORECASE
        )
    ]


def _status_chips(text: str) -> list[dict[str, Any]]:
    chips = []
    for value, label in re.findall(r"(\d+)\s+([^,]+)", text):
        chips.append(
            {"label": label.strip().replace("_", " ").title(), "value": int(value)}
        )
    return chips


def _evidence_for(
    evidence: list[dict[str, Any]], *categories: str
) -> list[dict[str, Any]]:
    allowed = set(categories)
    return [item for item in evidence if str(item.get("category")) in allowed]


def _summary_visuals(
    sections: list[dict[str, Any]], *, evidence: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Turn deterministic activity bullets into compact visual dashboard data."""

    stats: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    evidence = evidence or []
    project_evidence = _evidence_for(evidence, "project_change")
    session_evidence = [
        item
        for item in _evidence_for(evidence, "session")
        if str(item.get("lane")) == "full"
    ]
    pattern_evidence = _evidence_for(evidence, "pattern")
    items = [str(item) for section in sections for item in section.get("items", [])]
    for item in items:
        label, separator, detail = item.partition(":")
        normalized = label.strip().casefold()
        detail = detail.strip() if separator else item
        if normalized in {"project deltas", "projects with detected changes"}:
            if normalized == "project deltas":
                match = re.match(r"(\d+)\s+across\s+(.+)", detail, re.IGNORECASE)
            else:
                match = re.match(r"(\d+)(?:\s+-\s+(.+))?$", detail, re.IGNORECASE)
            if not match:
                continue
            count = int(match.group(1))
            names_text = match.group(2) or ""
            names = [name.strip() for name in names_text.split(",") if name.strip()]
            stats.append(
                {
                    "value": count,
                    "label": "Projects changed",
                    "icon": "🚀",
                    "tone": "pink",
                    "evidence": project_evidence,
                }
            )
            groups.append(
                {
                    "title": "Projects with detected changes",
                    "icon": "🗂️",
                    "tone": "pink",
                    "description": "Projects with new file, Git, stack, lifecycle, or working-tree changes in this run",
                    "evidence": project_evidence,
                    "chips": [
                        {
                            "label": name,
                            "evidence": [
                                row
                                for row in project_evidence
                                if row.get("label") == name
                            ],
                        }
                        for name in names
                    ],
                }
            )
        elif normalized in {"change types", "change signals"}:
            chips = _count_chips(detail)
            if chips:
                for chip in chips:
                    chip["evidence"] = [
                        row
                        for row in project_evidence
                        if str(chip["label"]).casefold()
                        in {
                            str(
                                CHANGE_LABELS.get(
                                    str(signal),
                                    str(signal).replace("_", " ").title(),
                                )
                            ).casefold()
                            for signal in row.get("signals", [])
                        }
                    ]
                groups.append(
                    {
                        "title": "Change signals",
                        "icon": "🧩",
                        "tone": "purple",
                        "description": f"{sum(chip['value'] for chip in chips)} signals across {len(chips)} types",
                        "evidence": project_evidence,
                        "chips": chips,
                    }
                )
        elif normalized == "agent sessions reviewed":
            match = re.match(
                r"(\d+)\s+total\s+-\s+(\d+)\s+linked\s+to\s+(\d+)\s+projects?;\s+(\d+)\s+(?:not\s+yet\s+linked|analyzed\s+profile-only)(?:;\s*(.*))?",
                detail,
                re.IGNORECASE,
            )
            if not match:
                continue
            sessions, linked, projects, unlinked = (
                int(value) for value in match.groups()[:4]
            )
            stats.extend(
                [
                    {
                        "value": sessions,
                        "label": "Sessions reviewed",
                        "icon": "🤖",
                        "tone": "purple",
                        "evidence": session_evidence,
                    },
                    {
                        "value": projects,
                        "label": "Projects represented in sessions",
                        "icon": "🔗",
                        "tone": "green",
                        "evidence": session_evidence,
                    },
                ]
            )
            groups.append(
                {
                    "title": "Session analysis coverage",
                    "icon": "🤖",
                    "tone": "green",
                    "description": "Reviewed sessions are either project-linked or safely restricted to person-level analysis",
                    "evidence": session_evidence,
                    "chips": [
                        {
                            "label": "Reviewed sessions",
                            "value": sessions,
                            "evidence": session_evidence,
                        },
                        {
                            "label": "Linked sessions",
                            "value": linked,
                            "evidence": session_evidence,
                        },
                        {
                            "label": "Projects represented",
                            "value": projects,
                            "evidence": session_evidence,
                        },
                        {"label": "Profile-only sessions", "value": unlinked},
                    ],
                }
            )
        elif normalized == "agent sessions evaluated":
            match = re.match(
                r"(\d+)\s+across\s+(\d+)\s+attributed projects?", detail, re.IGNORECASE
            )
            if not match:
                continue
            sessions, projects = (int(value) for value in match.groups())
            stats.extend(
                [
                    {
                        "value": sessions,
                        "label": "Sessions reviewed",
                        "icon": "🤖",
                        "tone": "purple",
                        "evidence": session_evidence,
                    },
                    {
                        "value": projects,
                        "label": "Projects represented in sessions",
                        "icon": "🔗",
                        "tone": "green",
                        "evidence": session_evidence,
                    },
                ]
            )
            groups.append(
                {
                    "title": "Session-to-project coverage",
                    "icon": "🤖",
                    "tone": "green",
                    "description": "How many reviewed agent sessions could be connected to a known project",
                    "evidence": session_evidence,
                    "chips": [
                        {
                            "label": "Reviewed sessions",
                            "value": sessions,
                            "evidence": session_evidence,
                        },
                        {
                            "label": "Projects represented",
                            "value": projects,
                            "evidence": session_evidence,
                        },
                    ],
                }
            )
        elif normalized in {"recurring patterns", "knowledge signals"}:
            chips = _status_chips(detail)
            if not chips:
                continue
            promoted = next(
                (
                    chip["value"]
                    for chip in chips
                    if chip["label"].casefold() == "promoted"
                ),
                0,
            )
            stats.append(
                {
                    "value": promoted,
                    "label": "Patterns promoted",
                    "icon": "🔁",
                    "tone": "yellow",
                    "evidence": pattern_evidence,
                }
            )
            groups.append(
                {
                    "title": "Pattern status",
                    "icon": "🔁",
                    "tone": "yellow",
                    "description": "Recurring signals moving through evidence gates",
                    "evidence": pattern_evidence,
                    "chips": [dict(chip, evidence=pattern_evidence) for chip in chips],
                }
            )

    if not groups and items:
        groups = [
            {
                "title": f"Update {index}",
                "icon": "✦",
                "tone": "purple",
                "description": item,
                "chips": [],
                "evidence": evidence,
            }
            for index, item in enumerate(items[:6], 1)
        ]
    for stat in stats:
        if not stat.get("evidence"):
            stat.pop("evidence", None)
    for group in groups:
        if not group.get("evidence"):
            group.pop("evidence", None)
        for chip in group.get("chips", []):
            if not chip.get("evidence"):
                chip.pop("evidence", None)
    return {"available": bool(stats or groups), "stats": stats[:4], "groups": groups}


def _period_for_run(run: dict[str, Any], kind: str) -> str | None:
    timestamp = _parse_datetime(run.get("completed_at") or run.get("started_at"))
    if timestamp is None:
        return None
    local = timestamp.astimezone()
    if kind == "daily":
        return local.date().isoformat()
    week = local.isocalendar()
    return f"{week.year}-W{week.week:02d}"


def _summary_cost(iterations: list[dict[str, Any]]) -> dict[str, Any]:
    rates = []
    low = 0.0
    high = 0.0
    exact = True
    for iteration in iterations:
        model = str(iteration.get("model") or "")
        pricing = MODEL_PRICING_USD_PER_MTOK.get(model)
        if pricing is None:
            return {"available": False, "rates": []}
        if not any(item["model"] == model for item in rates):
            rates.append({"model": model, **pricing})
        scale = 1_000_000
        if iteration.get("details_available"):
            input_tokens = int(iteration.get("input_tokens") or 0)
            cached_tokens = min(
                input_tokens, int(iteration.get("cached_input_tokens") or 0)
            )
            uncached_tokens = max(0, input_tokens - cached_tokens)
            cache_write_tokens = int(iteration.get("cache_write_input_tokens") or 0)
            output_tokens = int(iteration.get("output_tokens") or 0)
            long_context = input_tokens > 272_000
            input_multiplier = 2.0 if long_context else 1.0
            output_multiplier = 1.5 if long_context else 1.0
            cost = (
                uncached_tokens * pricing["input"] * input_multiplier
                + cached_tokens * pricing["cached_input"] * input_multiplier
                + cache_write_tokens * pricing["input"] * 1.25 * input_multiplier
                + output_tokens * pricing["output"] * output_multiplier
            ) / scale
            low += cost
            high += cost
        else:
            exact = False
            total = int(iteration.get("total_tokens") or 0)
            low += total * pricing["input"] / scale
            high += total * pricing["output"] / scale
    return {
        "available": bool(rates),
        "exact_split": exact,
        "estimate_low_usd": round(low, 6),
        "estimate_high_usd": round(high, 6),
        "rates": rates,
        "pricing_as_of": PRICING_AS_OF,
        "basis": "api-equivalent",
    }


def _summary_codex_impact(
    usage: dict[str, Any], codex_usage: dict[str, Any]
) -> dict[str, Any]:
    """Estimate one run's percentage-point impact on the active Codex window."""

    if not usage.get("available") or not codex_usage.get("available"):
        return {"available": False}
    windows = codex_usage.get("windows") or []
    if not windows:
        return {"available": False}
    window = windows[0]
    observed_tokens = int(window.get("observed_tokens") or 0)
    used_percent = float(window.get("used_percent") or 0)
    run_tokens = int(usage.get("total_tokens") or 0)
    completed_values = [
        _parse_datetime(item.get("completed_at"))
        for item in usage.get("iterations") or []
        if item.get("completed_at")
    ]
    completed = max(
        (item for item in completed_values if item is not None), default=None
    )
    starts_at = _parse_datetime(window.get("starts_at"))
    resets_at = _parse_datetime(window.get("resets_at"))
    if (
        observed_tokens <= 0
        or used_percent <= 0
        or run_tokens <= 0
        or (completed and starts_at and completed < starts_at)
        or (completed and resets_at and completed > resets_at)
    ):
        return {"available": False}
    token_share_percent = run_tokens / observed_tokens * 100
    percentage_points = run_tokens / observed_tokens * used_percent
    return {
        "available": True,
        "estimated": True,
        "percentage_points": round(percentage_points, 4),
        "token_share_percent": round(token_share_percent, 4),
        "run_tokens": run_tokens,
        "observed_window_tokens": observed_tokens,
        "window_used_percent": used_percent,
        "window_label": window.get("label") or "Codex window",
        "method": "token-share-scaled-by-window-usage",
    }


def _summary_usage(store: StateStore, kind: str, period: str) -> dict[str, Any]:
    run_ids = store.summary_run_ids(kind, period)
    runs = {run["id"]: run for run in store.runs(limit=250)}
    if not run_ids:
        run_ids = [
            run["id"]
            for run in runs.values()
            if run.get("kind") == kind
            and run.get("status") == "completed"
            and _period_for_run(run, kind) == period
        ]
    stored = {item["run_id"]: item for item in store.usage_for_runs(run_ids)}
    iterations = []
    for run_id in sorted(run_ids):
        usage = stored.get(run_id) or usage_from_receipt(
            runs.get(run_id, {}).get("receipt_path")
        )
        if usage is None:
            continue
        iterations.append(
            {field: int(usage.get(field) or 0) for field in TOKEN_USAGE_FIELDS}
            | {
                "model": runs.get(run_id, {}).get("model"),
                "completed_at": runs.get(run_id, {}).get("completed_at"),
                "model_calls": int(usage.get("model_calls") or 0),
                "cached_result": bool(usage.get("cached_result")),
                "details_available": bool(usage.get("details_available")),
            }
        )
    if not iterations:
        return {"available": False, "iterations": []}
    totals = {
        field: sum(int(item.get(field) or 0) for item in iterations)
        for field in TOKEN_USAGE_FIELDS
    }
    result = {
        "available": True,
        **totals,
        "model_calls": sum(item["model_calls"] for item in iterations),
        "cached_results": sum(int(item["cached_result"]) for item in iterations),
        "details_available": all(item["details_available"] for item in iterations),
        "iterations": iterations,
    }
    result["pricing"] = _summary_cost(iterations)
    return result


def _cached_summary_evidence_ids(
    paths: RuntimePaths, sections: list[dict[str, Any]]
) -> list[str]:
    """Recover legacy summary provenance without publishing raw model cache data."""

    recap = next(
        (
            section
            for section in sections
            if "recap" in str(section.get("title") or "").casefold()
        ),
        sections[0] if sections else {"items": []},
    )
    expected = [
        _clean_markdown(str(item), max_chars=1200) for item in recap.get("items", [])
    ]
    if not expected:
        return []
    cache_root = paths.runs / "model-cache"
    if not cache_root.is_dir():
        return []
    candidates = sorted(
        cache_root.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True
    )
    for path in candidates[:200]:
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        result = cached.get("result") if isinstance(cached, dict) else None
        if not isinstance(result, dict):
            continue
        cached_sections = _summary_sections(str(result.get("summary") or ""))
        actual = [
            _clean_markdown(str(item), max_chars=1200)
            for section in cached_sections
            for item in section.get("items", [])
        ]
        if actual == expected:
            return [str(value) for value in cached.get("evidence_ids") or []]
    return []


def _summary_evidence_rows(
    store: StateStore,
    paths: RuntimePaths,
    kind: str,
    period: str,
    sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    evidence_ids = store.summary_evidence_ids(kind, period)
    if not evidence_ids:
        evidence_ids = _cached_summary_evidence_ids(paths, sections)
    return store.evidence_by_ids(evidence_ids)


def _summary_evidence_cards(
    vault: Path,
    store: StateStore,
    rows: list[dict[str, Any]],
    *,
    kind: str,
) -> list[dict[str, Any]]:
    projects = {
        str(project["id"]): str(project.get("name") or project["id"])
        for project in store.projects()
    }
    cards: list[dict[str, Any]] = []
    for row in rows:
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if row.get("kind") == "project_delta":
            name = str(
                payload.get("project_name")
                or projects.get(str(row.get("project_id")))
                or "Project"
            )
            signals = [str(value) for value in payload.get("change_types") or []]
            detail = (
                ", ".join(
                    CHANGE_LABELS.get(signal, signal.replace("_", " ").title())
                    for signal in signals
                )
                or "Project activity"
            )
            cards.append(
                {
                    "category": "project_change",
                    "label": name,
                    "meta": "Project change",
                    "detail": detail,
                    "signals": signals,
                    "url": obsidian_uri(vault, _project_note(vault, name)),
                }
            )
        elif row.get("kind") == "session_digest":
            project_ids = [str(value) for value in payload.get("project_ids") or []]
            if row.get("project_id") and str(row["project_id"]) not in project_ids:
                project_ids.append(str(row["project_id"]))
            name = next(
                (projects[value] for value in project_ids if value in projects),
                "Unattributed session",
            )
            lane = (
                "full"
                if project_ids or row.get("project_id")
                else str(payload.get("analysis_lane") or "profile_only")
            )
            occurred = _parse_datetime(
                str(payload.get("started_at") or row.get("occurred_at") or "")
            )
            time_label = (
                occurred.astimezone().strftime("%b %-d · %H:%M")
                if occurred and os.name != "nt"
                else occurred.astimezone().strftime("%b %#d · %H:%M")
                if occurred
                else "Time unavailable"
            )
            source = str(payload.get("source") or "agent").title()
            cards.append(
                {
                    "evidence_id": str(row.get("id") or ""),
                    "category": "session",
                    "label": name,
                    "occurred_at": str(
                        payload.get("started_at") or row.get("occurred_at") or ""
                    ),
                    "meta": f"{source} · {time_label}",
                    "detail": session_recap(payload),
                    "attribution": (
                        "Project-linked session"
                        if lane == "full" and project_ids
                        else "Profile-only session"
                    ),
                    "lane": lane,
                    "signals": [],
                    "url": obsidian_uri(vault, _project_note(vault, name)),
                }
            )
        elif row.get("kind") == "recurring_pattern":
            cards.append(
                {
                    "category": "pattern",
                    "label": str(payload.get("label") or "Recurring pattern"),
                    "meta": "Knowledge signal",
                    "detail": sanitize_text(
                        str(payload.get("claim") or "Pattern evidence"), max_chars=500
                    ),
                    "signals": [],
                    "url": obsidian_uri(vault, vault / "Memory" / "Patterns.md"),
                }
            )
    if kind == "weekly" and (vault / "System" / "Stewardship.md").is_file():
        cards.append(
            {
                "category": "stewardship",
                "label": "Second-brain stewardship",
                "meta": "Weekly system review",
                "detail": "Coverage, continuity, review load, and recovery checks.",
                "signals": [],
                "url": obsidian_uri(vault, vault / "System" / "Stewardship.md"),
            }
        )
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for card in cards:
        key = (
            str(card["category"]),
            str(card.get("evidence_id") or card["label"]),
            "" if card.get("evidence_id") else str(card["meta"]),
        )
        if key not in seen:
            unique.append(card)
            seen.add(key)
    return unique


def _attach_section_evidence(
    sections: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    *,
    period: str | None = None,
) -> None:
    session_evidence = _evidence_for(evidence, "session")
    linked_sessions = [
        item for item in session_evidence if str(item.get("lane")) == "full"
    ]
    profile_only_count = len(session_evidence) - len(linked_sessions)
    historical_profile_only = len(
        [
            item
            for item in session_evidence
            if str(item.get("lane")) != "full"
            and period
            and str(item.get("occurred_at") or "")[:10] != period
        ]
    )
    for section in sections:
        title = str(section.get("title") or "").casefold()
        if "session" in title:
            selected = linked_sessions
            section["session_coverage"] = {
                "reviewed": len(session_evidence),
                "linked": len(linked_sessions),
                "profile_only": profile_only_count,
                "historical_profile_only": historical_profile_only,
            }
        elif "project" in title or "change" in title or "activity" in title:
            selected = _evidence_for(evidence, "project_change") + linked_sessions
        elif "pattern" in title or "learn" in title:
            selected = _evidence_for(evidence, "pattern")
        elif "steward" in title or "system" in title:
            selected = _evidence_for(evidence, "stewardship")
        else:
            selected = evidence
        section["evidence"] = selected


def _apply_canonical_evidence_details(
    evidence: list[dict[str, Any]], sections: list[dict[str, Any]]
) -> None:
    session_items = [
        str(item)
        for section in sections
        if "session" in str(section.get("title") or "").casefold()
        for item in section.get("items", [])
    ]
    used_items: set[int] = set()
    for card in evidence:
        if card.get("category") != "session":
            continue
        label = str(card.get("label") or "")
        occurred_date = str(card.get("occurred_at") or "")[:10]
        time_match = re.search(r"\b\d{2}:\d{2}\b", str(card.get("meta") or ""))
        occurred_time = time_match.group(0) if time_match else ""
        match_index = next(
            (
                index
                for index, item in enumerate(session_items)
                if index not in used_items
                and label.casefold() in item.casefold()
                and (not occurred_time or occurred_time in item)
                and (
                    not occurred_date
                    or f"backfill from {occurred_date}" in item
                    or "backfill from" not in item
                )
            ),
            None,
        )
        match = session_items[match_index] if match_index is not None else None
        if match_index is not None:
            used_items.add(match_index)
        if match and "—" in match:
            card["detail"] = sanitize_text(
                match.split("—", 1)[1].strip(), max_chars=800
            )


def _summary_timeline_context(
    sections: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
    *,
    kind: str,
    period: str,
) -> dict[str, Any]:
    """Keep delayed evidence on its original timeline in the derived Daily view."""

    if kind != "daily":
        return {}
    dates: list[str] = []
    for row in evidence_rows:
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        occurred = str(
            payload.get("started_at")
            or row.get("occurred_at")
            or row.get("created_at")
            or ""
        )[:10]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", occurred):
            dates.append(occurred)
    if not dates:
        return {}
    historical_dates = sorted(value for value in dates if value != period)
    current_count = len(dates) - len(historical_dates)
    context = {
        "current_count": current_count,
        "historical_count": len(historical_dates),
        "earliest": historical_dates[0] if historical_dates else "",
        "latest": historical_dates[-1] if historical_dates else "",
        "all_historical": bool(historical_dates) and current_count == 0,
    }
    for section in sections:
        title = str(section.get("title") or "").casefold()
        if not any(
            word in title
            for word in ("activity", "recap", "learn", "synthesis", "reflection")
        ):
            continue
        section_context = dict(context)
        if context["historical_count"] and any(
            word in title for word in ("learn", "synthesis", "reflection")
        ):
            section_context["learning_cards_hidden"] = True
            section["historical_item_count"] = len(section.get("items") or [])
            section["items"] = []
            section["paragraphs"] = []
        section["timeline_context"] = section_context
    return context


def _summary_entry(
    vault: Path,
    path: Path,
    kind: str,
    *,
    store: StateStore,
    paths: RuntimePaths,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    raw = _generated_section(path, kind)
    if not raw or "no automated run yet" in raw.casefold():
        return None
    sections = _summary_sections(raw)
    if not sections:
        return None
    evidence_rows = _summary_evidence_rows(store, paths, kind, path.stem, sections)
    evidence = _summary_evidence_cards(vault, store, evidence_rows, kind=kind)
    _apply_canonical_evidence_details(evidence, sections)
    _attach_section_evidence(sections, evidence, period=path.stem)
    timeline_context = _summary_timeline_context(
        sections, evidence_rows, kind=kind, period=path.stem
    )

    synthesis = next(
        (
            section
            for section in sections
            if any(
                word in section["title"].casefold()
                for word in ("synthesis", "learn", "reflection")
            )
        ),
        sections[-1],
    )
    summary_candidates = list(synthesis["paragraphs"]) or list(synthesis["items"])
    if not summary_candidates:
        summary_candidates = [
            value
            for section in sections
            for value in (*section["paragraphs"], *section["items"])
        ]
    summary = summary_candidates[0] if summary_candidates else ""
    if timeline_context.get("historical_count"):
        summary = (
            "Historical recovery only: delayed evidence kept its original work dates, "
            "and no same-day personal learning was claimed."
            if timeline_context.get("all_historical")
            else "This Daily includes delayed evidence kept on its original work dates; "
            "verified same-day personal learning appears only in Today."
        )
    highlights = [item for section in sections for item in section["items"]]
    if not highlights:
        highlights = [
            paragraph for section in sections for paragraph in section["paragraphs"]
        ]
    period = path.stem
    label = "Daily" if kind == "daily" else "Weekly"
    return {
        "available": True,
        "kind": kind,
        "period": period,
        "title": f"{label} summary — {period}",
        "summary": summary,
        "timeline_context": timeline_context,
        "highlights": highlights[:6],
        "sections": sections,
        "visuals": _summary_visuals(sections, evidence=evidence),
        "evidence": evidence,
        "evidence_ids": [str(row.get("id")) for row in evidence_rows if row.get("id")],
        "usage": usage or {"available": False, "iterations": []},
        "url": obsidian_uri(vault, path),
    }


def _summary_history(
    vault: Path,
    kind: str,
    store: StateStore,
    paths: RuntimePaths,
    *,
    limit: int = 24,
) -> list[dict[str, Any]]:
    folder = vault / "Journal" / ("Daily" if kind == "daily" else "Weekly")
    pattern = "20??-??-??.md" if kind == "daily" else "*.md"
    candidates = sorted(
        (path for path in folder.glob(pattern) if path.name.casefold() != "index.md"),
        key=lambda path: path.stem,
        reverse=True,
    )
    result = []
    for path in candidates:
        entry = _summary_entry(
            vault,
            path,
            kind,
            store=store,
            paths=paths,
            usage=_summary_usage(store, kind, path.stem),
        )
        if entry:
            result.append(entry)
        if len(result) >= limit:
            break
    return result


def personalize_knowledge_text(text: str, first_name: str) -> str:
    """Use the vault owner's preferred name in cards without rewriting canonical notes."""

    personalized = re.sub(
        r"\bthe user['’]s\b", f"{first_name}'s", text, flags=re.IGNORECASE
    )
    return re.sub(r"\bthe user\b", first_name, personalized, flags=re.IGNORECASE)


def _project_note(vault: Path, name: str) -> Path:
    candidate = vault / "Projects" / f"{slugify(name)}.md"
    return candidate if candidate.is_file() else vault / "Projects" / "Index.md"


def _observation_note(vault: Path, kind: str) -> Path:
    mapping = {
        "explicit_fact": vault / "Memory" / "LongTermMemory.md",
        "decision": vault / "Memory" / "Decisions.md",
        "lesson": vault / "Memory" / "Lessons.md",
        "project_fact": vault / "Memory" / "LongTermMemory.md",
        "skill": vault / "Skills" / "Index.md",
        "voice_style": vault / "Identity" / "Voice.md",
        "work_style": vault / "Identity" / "WorkStyle.md",
        "preference": vault / "Identity" / "Preferences.md",
        "personality": vault / "Identity" / "Persona.md",
        "experience": vault / "Experience" / "Employment.md",
        "education": vault / "Experience" / "Education.md",
        "military": vault / "Experience" / "MilitaryService.md",
        "goal": vault / "Goals" / "ActiveGoals.md",
    }
    return mapping.get(kind, vault / "Home.md")


def knowledge_layer_for(
    observation: dict[str, Any], *, project_scoped: bool = False
) -> str:
    """Place promoted knowledge in one stable human-facing layer."""

    if project_scoped and str(observation.get("kind") or "") in PERSONAL_PATTERN_KINDS:
        return "project_knowledge"
    declared = str((observation.get("payload") or {}).get("knowledge_layer") or "")
    if declared in KNOWLEDGE_LAYER_ORDER:
        return declared
    kind = str(observation.get("kind") or "")
    for layer, kinds in KNOWLEDGE_LAYER_KINDS.items():
        if kind in kinds:
            return layer
    return "project_knowledge"


def _observation_context(
    store: StateStore,
    observation: dict[str, Any],
    *,
    evidence_by_id: dict[str, dict[str, Any]] | None = None,
    projects_by_id: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = (
        observation.get("payload")
        if isinstance(observation.get("payload"), dict)
        else {}
    )
    evidence_refs = list(observation.get("evidence_refs") or [])
    evidence_rows = (
        [
            evidence_by_id[evidence_id]
            for evidence_id in evidence_refs
            if evidence_id in evidence_by_id
        ]
        if evidence_by_id is not None
        else store.evidence_by_ids(evidence_refs)
    )
    override_ids = payload.get("project_ids_override")
    attribution_overridden = isinstance(override_ids, list) and bool(override_ids)
    project_ids = (
        {str(project_id) for project_id in override_ids if project_id}
        if attribution_overridden
        else {
            str(project_id)
            for project_id in (payload.get("project_ids") or [])
            if project_id
        }
    )
    if not attribution_overridden and payload.get("project_id"):
        project_ids.add(str(payload["project_id"]))
    sessions: set[str] = set()
    dates: set[str] = set()
    session_scoped = False
    trusted_profile_evidence = False
    for row in evidence_rows:
        row_payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if not attribution_overridden:
            if row.get("project_id"):
                project_ids.add(str(row["project_id"]))
            project_ids.update(
                str(item) for item in row_payload.get("project_ids", []) if item
            )
        session_id = row_payload.get("session_id")
        if session_id:
            sessions.add(str(session_id))
            session_scoped = True
        if row.get("kind") == "session_digest" or row.get("source_type") in {
            "codex",
            "session-digest",
            "antigravity",
        }:
            session_scoped = True
        occurred = str(row.get("occurred_at") or row.get("created_at") or "")[:10]
        if occurred:
            dates.add(occurred)
        if row.get("source_type") in {"interview", "linkedin"}:
            trusted_profile_evidence = True

    projects = (
        projects_by_id
        if projects_by_id is not None
        else {
            str(item["id"]): item
            for item in store.projects()
            if _project_is_attributable(item)
        }
    )
    project_names = sorted(
        {
            str(
                projects[project_id].get("name")
                or projects[project_id].get("logical_name")
                or project_id
            )
            for project_id in project_ids
            if project_id in projects
        },
        key=str.casefold,
    )
    return {
        "project_ids": sorted(project_ids),
        "project_names": project_names,
        "sessions": sessions,
        "dates": dates,
        "session_scoped": session_scoped,
        "trusted_profile_evidence": trusted_profile_evidence,
        "attribution_overridden": attribution_overridden,
    }


def _focused_project_names(
    store: StateStore,
    candidates: list[str],
    text: str,
    *,
    all_project_names: list[str] | None = None,
) -> list[str]:
    """Prefer a project named by the card over noisy multi-project evidence context."""

    def normalized(value: str) -> str:
        expanded = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
        return " ".join(re.findall(r"[a-z0-9]+", expanded.casefold()))

    folded = normalized(text)
    all_names = (
        all_project_names
        if all_project_names is not None
        else sorted(
            {
                str(item.get("name") or item.get("logical_name") or "").strip()
                for item in store.projects()
                if _project_is_attributable(item)
                if item.get("name") or item.get("logical_name")
            },
            key=str.casefold,
        )
    )
    direct = [
        name for name in all_names if len(name) >= 5 and normalized(name) in folded
    ]
    if direct:
        return [max(direct, key=len)]
    stopwords = {
        "project",
        "version",
        "status",
        "current",
        "ownership",
        "repository",
        "application",
        "system",
        "shai",
        "adams",
        "agent",
        "agents",
        "assisted",
        "demo",
        "demos",
    }
    text_tokens = set(folded.split()) - stopwords
    scored: list[tuple[int, str]] = []
    for name in all_names:
        tokens = {
            token
            for token in normalized(name).split()
            if len(token) >= 4 and token not in stopwords
        }
        scored.append((len(tokens & text_tokens), name))
    best = max((score for score, _name in scored), default=0)
    winners = [name for score, name in scored if score == best and score > 0]
    if len(winners) == 1:
        return winners
    return candidates


def _knowledge_scope(
    store: StateStore,
    observation: dict[str, Any],
    *,
    context: dict[str, Any] | None = None,
) -> tuple[str, list[str]]:
    """Separate durable personal patterns from contextual project instructions."""

    payload = (
        observation.get("payload")
        if isinstance(observation.get("payload"), dict)
        else {}
    )
    declared = str(
        payload.get("scope") or payload.get("knowledge_scope") or ""
    ).casefold()
    context = context or _observation_context(store, observation)
    project_names = list(context["project_names"])
    kind = str(observation.get("kind") or "")
    if kind not in PERSONAL_PATTERN_KINDS:
        return (
            "project" if kind in {"decision", "lesson", "project_fact"} else "global"
        ), project_names
    if declared == "project":
        return "project", project_names
    if declared == "global" or context["trusted_profile_evidence"]:
        return "global", project_names

    observed_project_count = (
        len(context["project_ids"])
        if context["attribution_overridden"]
        else max(
            len(context["project_ids"]), int(observation.get("project_count") or 0)
        )
    )
    stable_cross_project = (
        len(context["sessions"]) >= 3
        and len(context["dates"]) >= 2
        and observed_project_count >= 2
    )
    if stable_cross_project:
        return "global", project_names
    pattern_text = " ".join(
        str(value)
        for value in (
            observation.get("subject"),
            observation.get("claim"),
        )
        if value
    ).casefold()
    explicitly_contextual = any(
        marker in pattern_text for marker in PROJECT_CONTEXT_MARKERS
    )
    explicitly_reusable = any(
        marker in pattern_text for marker in GLOBAL_PATTERN_MARKERS
    )
    if explicitly_reusable and not explicitly_contextual:
        return "global", project_names
    if context["session_scoped"] or observed_project_count > 0:
        return "project", project_names
    return "global", project_names


def _knowledge_card_is_useful(
    observation: dict[str, Any], *, scope: str, subject: str, claim: str
) -> bool:
    """Keep Curate focused on durable owner judgment instead of routine inventory."""

    kind = str(observation.get("kind") or "")
    text = f"{subject} {claim}".casefold()
    if any(term in text for term in LOW_VALUE_KNOWLEDGE_TERMS):
        return False
    if scope == "project" and kind in PERSONAL_PATTERN_KINDS:
        return False
    if kind != "project_fact":
        return True
    return any(term in text for term in IMPORTANT_PROJECT_FACT_TERMS)


def default_knowledge_layer(layers: list[dict[str, Any]]) -> str:
    """Open Curate on the first layer that has unreviewed knowledge."""
    return next(
        (str(layer["key"]) for layer in layers if int(layer.get("new") or 0) > 0),
        "about_shai",
    )


def _knowledge_deck(store: StateStore, vault: Path) -> dict[str, Any]:
    feedback = store.knowledge_feedback()
    rows = sorted(
        store.observations("promoted"),
        key=lambda item: item["updated_at"],
        reverse=True,
    )
    projects = [item for item in store.projects() if _project_is_attributable(item)]
    projects_by_id = {str(item["id"]): item for item in projects}
    all_project_names = sorted(
        {
            str(item.get("name") or item.get("logical_name") or "").strip()
            for item in projects
            if item.get("name") or item.get("logical_name")
        },
        key=str.casefold,
    )
    evidence_by_id = {
        str(item["id"]): item
        for item in store.evidence_by_ids(
            sorted(
                {
                    str(evidence_id)
                    for row in rows
                    for evidence_id in (row.get("evidence_refs") or [])
                    if evidence_id
                }
            )
        )
    }
    display_name = vault.name.removesuffix(" Second Brain").strip() or "Second Brain"
    first_name = display_name.split()[0]
    cards = []
    for item in rows:
        if item.get("sensitivity") == "sensitive":
            continue
        claim = _clean_markdown(str(item.get("claim") or ""), max_chars=420)
        if not claim:
            continue
        claim = personalize_knowledge_text(claim, first_name)
        decision = feedback.get(item["id"], {}).get("decision")
        if decision == "disliked":
            continue
        context = _observation_context(
            store,
            item,
            evidence_by_id=evidence_by_id,
            projects_by_id=projects_by_id,
        )
        scope, project_names = _knowledge_scope(store, item, context=context)
        project_names = _focused_project_names(
            store,
            project_names,
            f"{item.get('subject', '')} {item.get('claim', '')}",
            all_project_names=all_project_names,
        )
        layer = knowledge_layer_for(item, project_scoped=scope == "project")
        subject = _clean_markdown(str(item.get("subject") or "Knowledge"), max_chars=96)
        if not _knowledge_card_is_useful(
            item, scope=scope, subject=subject, claim=claim
        ):
            continue
        if scope == "project":
            project_stamp = (
                " + ".join(project_names) if project_names else "Project not attributed"
            )
            note = (
                _project_note(vault, project_names[0])
                if len(project_names) == 1
                else vault / "Projects" / "Index.md"
            )
        else:
            project_stamp = ""
            note = _observation_note(vault, item["kind"])
        cards.append(
            {
                "id": item["id"],
                "layer": layer,
                "kind": KIND_LABELS.get(
                    item["kind"], item["kind"].replace("_", " ").title()
                ),
                "subject": subject,
                "claim": claim,
                "confidence": round(float(item.get("confidence", 0)) * 100),
                "source_count": int(item.get("source_count") or 0),
                "project_count": int(item.get("project_count") or 0),
                "scope": scope,
                "project_names": project_names,
                "project_stamp": project_stamp,
                "updated_at": item.get("updated_at"),
                "feedback": decision,
                "url": obsidian_uri(vault, note),
            }
        )
    confirmed = sum(1 for card in cards if card["feedback"] == "liked")
    removed = sum(1 for item in feedback.values() if item.get("decision") == "disliked")
    layer_content = {
        "about_shai": {
            "label": f"About {first_name}",
            "short_label": f"About {first_name}",
            "description": "Identity, voice, work style, personality, values, and goals.",
        },
        "professional_profile": {
            "label": "Professional profile",
            "short_label": "Professional",
            "description": "Capabilities, skills, experience, education, service, and verified technical range.",
        },
        "operating_preferences": {
            "label": "Operating preferences",
            "short_label": "How I work",
            "description": "Reusable cross-project behavior, preferred formats, validation rules, design taste, and protocols.",
        },
        "project_knowledge": {
            "label": "Project knowledge",
            "short_label": "Projects",
            "description": "Project-specific facts, architecture, decisions, implementations, lessons, and contextual instructions—not personality.",
        },
    }
    layers = []
    for key in KNOWLEDGE_LAYER_ORDER:
        layer_cards = [card for card in cards if card["layer"] == key]
        layer_confirmed = sum(1 for card in layer_cards if card["feedback"] == "liked")
        layers.append(
            {
                "key": key,
                **layer_content[key],
                "count": len(layer_cards),
                "new": len(layer_cards) - layer_confirmed,
                "confirmed": layer_confirmed,
                "url": obsidian_uri(vault, vault / KNOWLEDGE_LAYER_NOTES[key]),
            }
        )
    return {
        "cards": cards,
        "default_layer": default_knowledge_layer(layers),
        "layers": layers,
        "counts": {
            "new": len(cards) - confirmed,
            "all": len(cards),
            "confirmed": confirmed,
            "removed": removed,
        },
        "undo_available": store.last_knowledge_dislike() is not None,
    }


def _question_is_owner_worthy(
    group_key: str, item: dict[str, Any], question: str
) -> bool:
    """Surface questions that need the user's judgment, not agent-resolvable trivia."""

    text = f"{item.get('subject', '')} {question}".casefold()
    if group_key == "questions-technical":
        return False
    if any(term in text for term in LOW_VALUE_QUESTION_TERMS):
        return False
    if group_key == "questions-profile-privacy":
        return True
    if group_key == "questions-attribution":
        return any(
            term in text
            for term in (
                "owner",
                "ownership",
                "author",
                "attribution",
                "contribution",
                "original",
                "first-party",
                "third-party",
                "fork",
                "collaboration",
                "boundaries",
                "belong",
                "separate projects",
            )
        )
    if group_key == "questions-project-state":
        return any(
            term in text
            for term in (
                "status",
                "current state",
                "deployed",
                "deployment",
                "working",
                "prototype",
                "paused",
                "archived",
                "release",
                "timeline",
                "public status",
                "validation status",
            )
        )
    return False


def _question_deck(store: StateStore, vault: Path) -> dict[str, Any]:
    pending = store.observations("pending")
    groups = build_review_groups(pending)
    projects = [item for item in store.projects() if _project_is_attributable(item)]
    projects_by_id = {str(item["id"]): item for item in projects}
    all_project_names = sorted(
        {
            str(item.get("name") or item.get("logical_name") or "").strip()
            for item in projects
            if item.get("name") or item.get("logical_name")
        },
        key=str.casefold,
    )
    evidence_by_id = {
        str(item["id"]): item
        for item in store.evidence_by_ids(
            sorted(
                {
                    str(evidence_id)
                    for row in pending
                    for evidence_id in (row.get("evidence_refs") or [])
                    if evidence_id
                }
            )
        )
    }
    cards: list[dict[str, Any]] = []
    category_counts: dict[str, int] = defaultdict(int)
    total_questions = 0
    for group in groups:
        if group["section"] != "questions":
            continue
        total_questions += int(group["count"])
        group_path = vault / "Inbox" / "Review" / "Groups" / group["filename"]
        for item in group["items"]:
            payload = item.get("payload") or {}
            nested_payload = (
                payload.get("payload")
                if isinstance(payload.get("payload"), dict)
                else {}
            )
            question = _clean_markdown(
                str(
                    payload.get("question")
                    or nested_payload.get("question")
                    or item.get("claim")
                    or ""
                ),
                max_chars=1600,
            )
            if not question:
                continue
            if not _question_is_owner_worthy(group["key"], item, question):
                continue
            context = _observation_context(
                store,
                item,
                evidence_by_id=evidence_by_id,
                projects_by_id=projects_by_id,
            )
            subject = _clean_markdown(
                str(item.get("subject") or "Open question"), max_chars=140
            )
            project_names = _focused_project_names(
                store,
                list(context["project_names"]),
                f"{subject} {question}",
                all_project_names=all_project_names,
            )
            scope = (
                "profile" if group["key"] == "questions-profile-privacy" else "project"
            )
            if scope == "project":
                project_stamp = (
                    " + ".join(project_names[:2])
                    + (
                        f" + {len(project_names) - 2} more"
                        if len(project_names) > 2
                        else ""
                    )
                    if project_names
                    else "Project not attributed"
                )
                destination = "Project knowledge"
                destination_detail = "This answer stays with project knowledge and is not saved as personality."
            else:
                project_stamp = "Professional profile"
                destination = "Professional profile"
                destination_detail = "This answer supports verified profile facts; it does not become a personality trait."
            category_counts[group["title"]] += 1
            cards.append(
                {
                    "id": item["id"],
                    "category": group["title"],
                    "subject": subject,
                    "question": question,
                    "explanation": group["description"],
                    "guidance": group["answer_template"],
                    "scope": scope,
                    "project_names": project_names,
                    "project_stamp": project_stamp,
                    "destination": destination,
                    "destination_detail": destination_detail,
                    "url": obsidian_uri(vault, group_path),
                }
            )
    cards.sort(
        key=lambda card: (
            0 if card["scope"] == "project" and len(card["project_names"]) == 1 else 1,
            0 if card["scope"] == "profile" else 1,
            card["subject"].casefold(),
        )
    )
    categories = [
        {"title": title, "count": count} for title, count in category_counts.items()
    ]
    return {
        "cards": cards,
        "count": len(cards),
        "deferred_count": max(0, total_questions - len(cards)),
        "categories": categories,
        "undo_available": store.last_question_dismissal() is not None,
    }


def _recent_activity(
    store: StateStore, vault: Path, *, now: datetime, days: int = 7
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    cutoff = now.astimezone(UTC) - timedelta(days=days)
    projects = {
        item["id"]: item for item in store.projects() if _project_is_attributable(item)
    }
    presence: dict[str, bool] = {}
    with store.connect() as connection:
        for row in connection.execute(
            "SELECT project_id,present FROM project_presence"
        ).fetchall():
            presence[str(row["project_id"])] = bool(row["present"])
        digest_rows = connection.execute(
            """SELECT source_type,project_id,occurred_at,payload_json FROM evidence
            WHERE kind='session_digest' AND COALESCE(occurred_at,created_at) >= ?""",
            (cutoff.isoformat(),),
        ).fetchall()
        delta_rows = connection.execute(
            """SELECT project_id,COALESCE(occurred_at,created_at) AS activity_at FROM evidence
            WHERE kind='project_delta' AND COALESCE(occurred_at,created_at) >= ?""",
            (cutoff.isoformat(),),
        ).fetchall()
        all_digests = connection.execute(
            "SELECT source_type,payload_json FROM evidence WHERE kind='session_digest'"
        ).fetchall()

    sessions_by_day: dict[str, set[str]] = defaultdict(set)
    sessions_by_project: dict[str, set[str]] = defaultdict(set)
    last_activity: dict[str, datetime] = {}
    for row in digest_rows:
        try:
            payload = json.loads(row["payload_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        session_id = str(payload.get("session_id") or "")
        if not session_id:
            continue
        key = f"{row['source_type']}:{session_id}"
        occurred = _parse_datetime(row["occurred_at"]) or cutoff
        local_day = occurred.astimezone(now.tzinfo).date().isoformat()
        sessions_by_day[local_day].add(key)
        project_ids = set(str(item) for item in payload.get("project_ids", []) if item)
        if row["project_id"]:
            project_ids.add(str(row["project_id"]))
        for project_id in project_ids:
            project = projects.get(project_id)
            if not project or not presence.get(project_id, True):
                continue
            if project.get("classification") not in VISIBLE_PROJECT_CLASSES:
                continue
            sessions_by_project[project_id].add(key)
            last_activity[project_id] = max(
                last_activity.get(project_id, occurred), occurred
            )

    changes: dict[str, int] = defaultdict(int)
    for row in delta_rows:
        project_id = str(row["project_id"] or "")
        project = projects.get(project_id)
        if not project or not presence.get(project_id, True):
            continue
        if project.get("classification") not in VISIBLE_PROJECT_CLASSES:
            continue
        changes[project_id] += 1
        occurred = _parse_datetime(row["activity_at"])
        if occurred:
            last_activity[project_id] = max(
                last_activity.get(project_id, occurred), occurred
            )

    activity = []
    active_ids = set(sessions_by_project) | set(changes)
    for project_id in active_ids:
        project = projects[project_id]
        sessions = len(sessions_by_project.get(project_id, set()))
        change_count = changes.get(project_id, 0)
        activity.append(
            {
                "name": _clean_markdown(
                    str(project.get("name") or "Untitled project"), max_chars=80
                ),
                "classification": str(
                    project.get("classification") or "review"
                ).replace("-", " "),
                "sessions": sessions,
                "changes": change_count,
                "score": sessions * 3 + change_count,
                "last_activity": last_activity.get(project_id).isoformat()
                if project_id in last_activity
                else None,
                "url": obsidian_uri(
                    vault, _project_note(vault, str(project.get("name") or ""))
                ),
            }
        )
    activity.sort(
        key=lambda item: (item["score"], item["last_activity"] or ""), reverse=True
    )
    activity = activity[:6]
    max_score = max((item["score"] for item in activity), default=1)
    for item in activity:
        item["relative"] = max(8, round(item["score"] / max_score * 100))

    trend = []
    for offset in range(days - 1, -1, -1):
        day = (now.date() - timedelta(days=offset)).isoformat()
        trend.append({"date": day, "sessions": len(sessions_by_day.get(day, set()))})

    all_session_keys: set[str] = set()
    for row in all_digests:
        try:
            payload = json.loads(row["payload_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        session_id = payload.get("session_id")
        if session_id:
            all_session_keys.add(f"{row['source_type']}:{session_id}")
    return activity, trend, len(all_session_keys)


def _recent_insights(store: StateStore, vault: Path) -> list[dict[str, Any]]:
    rows = sorted(
        store.observations("promoted"),
        key=lambda item: item["updated_at"],
        reverse=True,
    )
    insights = []
    for item in rows:
        if item["kind"] not in INSIGHT_KINDS or item.get("sensitivity") == "sensitive":
            continue
        claim = _clean_markdown(str(item.get("claim") or ""), max_chars=240)
        if not claim:
            continue
        insights.append(
            {
                "kind": KIND_LABELS.get(
                    item["kind"], item["kind"].replace("_", " ").title()
                ),
                "subject": _clean_markdown(
                    str(item.get("subject") or "New insight"), max_chars=80
                ),
                "claim": claim,
                "confidence": round(float(item.get("confidence", 0)) * 100),
                "updated_at": item.get("updated_at"),
                "url": obsidian_uri(vault, _observation_note(vault, item["kind"])),
            }
        )
        if len(insights) == 5:
            break
    return insights


def _forming_patterns(store: StateStore, vault: Path) -> list[dict[str, Any]]:
    rows = store.pattern_signals("tracking")
    patterns = []
    for item in rows:
        sessions = int(item.get("session_count", 0))
        dates = int(item.get("date_count", 0))
        projects = int(item.get("project_count", 0))
        contexts = int(((item.get("payload") or {}).get("context_count")) or projects)
        progress = round(
            (min(sessions / 3, 1) + min(dates / 2, 1) + min(contexts / 2, 1)) / 3 * 100
        )
        patterns.append(
            {
                "label": _clean_markdown(
                    str(item.get("label") or "Emerging pattern"), max_chars=80
                ),
                "claim": _clean_markdown(str(item.get("claim") or ""), max_chars=190),
                "sessions": sessions,
                "dates": dates,
                "projects": projects,
                "contexts": contexts,
                "progress": progress,
                "last_seen": item.get("last_seen"),
                "url": obsidian_uri(vault, vault / "Memory" / "Patterns.md"),
            }
        )
    patterns.sort(
        key=lambda item: (item["progress"], item["last_seen"] or ""), reverse=True
    )
    return patterns[:4]


def _group_runs(store: StateStore, *, limit: int = 7) -> list[dict[str, Any]]:
    pipeline_rows = store.pipeline_runs(limit=30)
    grouped: list[dict[str, Any]] = []
    if pipeline_rows:
        oldest_pipeline = min(str(row["started_at"]) for row in pipeline_rows)
        source_rows = [
            {
                **row,
                "pipeline": True,
                "model": None,
                "reasoning": None,
                "evidence_count": 0,
            }
            for row in pipeline_rows
        ]
        source_rows.extend(
            {**row, "pipeline": False}
            for row in store.runs(limit=30)
            if str(row.get("started_at") or "") < oldest_pipeline
        )
        source_rows.sort(key=lambda row: str(row.get("started_at") or ""), reverse=True)
    else:
        source_rows = [{**row, "pipeline": False} for row in store.runs(limit=30)]

    for run in source_rows:
        started = _parse_datetime(run.get("started_at"))
        completed = _parse_datetime(run.get("completed_at"))
        error = sanitize_text(str(run.get("error") or ""), max_chars=4000)
        status = (
            "completed"
            if run.get("status") == "validated"
            else str(run.get("status") or "unknown")
        )
        candidate = {
            "kind": str(run.get("kind") or "operation").replace("-", " ").title(),
            "status": status,
            "started_at": run.get("started_at"),
            "completed_at": run.get("completed_at"),
            "model": run.get("model"),
            "reasoning": run.get("reasoning"),
            "evidence_count": int(run.get("evidence_count") or 0),
            "duration_seconds": max(0, round((completed - started).total_seconds()))
            if started and completed
            else None,
            "batch_count": 1,
            "stage": str(run.get("stage") or "").replace("_", " ").title(),
            "error": error,
            "error_summary": sanitize_text(error, max_chars=240),
            "trigger": str(run.get("trigger") or ""),
            "source": "Pipeline" if run.get("pipeline") else "Model run",
        }
        previous = grouped[-1] if grouped else None
        previous_started = (
            _parse_datetime(previous.get("started_at")) if previous else None
        )
        if (
            previous
            and previous["kind"] == candidate["kind"]
            and previous["status"] == candidate["status"]
            and started
            and previous_started
            and abs((previous_started - started).total_seconds()) <= 8
        ):
            previous["batch_count"] += 1
            previous["evidence_count"] += candidate["evidence_count"]
            if (
                previous["duration_seconds"] is not None
                and candidate["duration_seconds"] is not None
            ):
                previous["duration_seconds"] = max(
                    previous["duration_seconds"], candidate["duration_seconds"]
                )
            continue
        grouped.append(candidate)
        if len(grouped) == limit:
            break
    return grouped


def _git_summary(vault: Path) -> dict[str, Any]:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=vault,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        return {"state": "unknown", "detail": "Git status unavailable"}
    remote = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=vault,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if remote.returncode != 0 or not remote.stdout.strip():
        return {"state": "attention", "detail": "Private Git remote is not configured"}
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    tracked = [line for line in lines if not line.startswith("??")]
    untracked = [line for line in lines if line.startswith("??")]
    if tracked:
        return {
            "state": "attention",
            "detail": (
                f"{len(tracked)} tracked edit{'s are' if len(tracked) != 1 else ' is'} "
                "awaiting the next scoped, privacy-scanned private snapshot"
            ),
        }
    if untracked:
        return {
            "state": "neutral",
            "detail": (
                "Private remote connected; "
                f"{len(untracked)} untracked owner file{'s remain' if len(untracked) != 1 else ' remains'} "
                "local until a privacy-scanned snapshot includes them"
            ),
        }
    return {"state": "good", "detail": "Private remote connected; working tree clean"}


def _cached_search_health(paths: RuntimePaths, store: StateStore) -> dict[str, Any]:
    refresh = store.search_refresh_status()
    pending = int(refresh.get("pending") or 0)
    if pending:
        if refresh.get("state") == "failed":
            return {
                "state": "attention",
                "detail": f"{pending} note update{'s' if pending != 1 else ''} waiting for search repair",
            }
        return {
            "state": "neutral",
            "detail": f"Updating {pending} changed note{'s' if pending != 1 else ''} in the background",
        }
    report_path = paths.runs / "health-report.json"
    if report_path.is_file():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            if bool(report.get("basic_memory", {}).get("ok")):
                return {"state": "good", "detail": "Local semantic index ready"}
        except (json.JSONDecodeError, OSError, TypeError):
            pass
    if (paths.basic_memory / "vault-mirror").is_dir():
        return {"state": "neutral", "detail": "Local index available"}
    return {"state": "attention", "detail": "Index needs a health check"}


def _focus(vault: Path) -> dict[str, str]:
    path = vault / "Goals" / "ActiveGoals.md"
    raw = _generated_section(path, "goal-suggestions")
    title_match = re.search(r"\*\*([^:*]+):\*\*", raw)
    return {
        "title": _clean_markdown(title_match.group(1), max_chars=80)
        if title_match
        else "Current direction",
        "summary": _clean_markdown(raw, max_chars=380),
        "url": obsidian_uri(vault, path),
    }


def _system_status(
    store: StateStore, schedule: dict[str, Any], *, now: datetime
) -> dict[str, str]:
    bootstrap = store.bootstrap_state().get("state")
    pipeline_runs = store.pipeline_runs(limit=30)
    latest_pipeline = pipeline_runs[0] if pipeline_runs else None
    runs = [
        run for run in store.runs(limit=30) if run.get("kind") in {"daily", "weekly"}
    ]
    latest = runs[0] if runs else None
    if bootstrap != "completed":
        return {
            "tone": "attention",
            "label": "Setup needs attention",
            "detail": "Bootstrap is not complete.",
        }
    if not schedule.get("installed"):
        return {
            "tone": "attention",
            "label": "Schedule needs attention",
            "detail": "The daily task is not installed.",
        }
    if latest_pipeline and latest_pipeline.get("status") == "failed":
        stage = str(latest_pipeline.get("stage") or "unknown stage").replace("_", " ")
        error = sanitize_text(str(latest_pipeline.get("error") or ""), max_chars=180)
        detail = f"Failed during {stage}. Evidence is preserved for retry."
        if error:
            detail += f" {error}"
        return {"tone": "danger", "label": "Last daily run failed", "detail": detail}
    schedule_result = int(schedule.get("last_result") or 0)
    scheduled_at = _parse_datetime(schedule.get("last_run"))
    successful_at = _parse_datetime(
        (latest_pipeline or {}).get("completed_at")
        if latest_pipeline and latest_pipeline.get("status") == "completed"
        else (latest or {}).get("completed_at")
    )
    if schedule_result != 0 and (
        scheduled_at is None or successful_at is None or successful_at < scheduled_at
    ):
        return {
            "tone": "danger",
            "label": "Last scheduled run failed",
            "detail": f"Scheduler exit code {schedule_result}. Evidence is preserved for retry.",
        }
    if latest and latest.get("status") == "failed":
        return {
            "tone": "danger",
            "label": "Last run failed safely",
            "detail": "Evidence is preserved for retry.",
        }
    if latest is None:
        return {
            "tone": "ready",
            "label": "Ready for the first daily run",
            "detail": "The system is installed and waiting for its first real daily briefing.",
        }
    completed = _parse_datetime(latest.get("completed_at") or latest.get("started_at"))
    if completed and now.astimezone(UTC) - completed > timedelta(hours=48):
        return {
            "tone": "attention",
            "label": "A refresh is due",
            "detail": "The latest successful update is more than two days old.",
        }
    return {
        "tone": "good",
        "label": "Brain is up to date",
        "detail": "Recent evidence has been processed successfully.",
    }


def _learning_snapshot(
    store: StateStore,
    daily: dict[str, Any],
) -> dict[str, Any]:
    period = str(daily.get("period") or "")
    evidence_ids = {str(value) for value in daily.get("evidence_ids") or [] if value}
    visible_topics = store.visible_learning_topics()
    personal_topic_keys = {
        str(topic.get("topic_key") or "")
        for topic in visible_topics
        if int(topic.get("context_count") or 0) >= 2
        or str(topic.get("current_state") or "") == "verified"
    }
    evidence_cache: dict[str, dict[str, Any]] = {}

    def observed_date(refs: list[str]) -> str:
        missing = [ref for ref in refs if ref not in evidence_cache]
        for row in store.evidence_by_ids(missing):
            evidence_cache[str(row["id"])] = row
        dates = [
            str(
                evidence_cache[ref].get("occurred_at")
                or evidence_cache[ref].get("created_at")
                or ""
            )[:10]
            for ref in refs
            if ref in evidence_cache
        ]
        return max((value for value in dates if value), default="")

    candidates: list[dict[str, str]] = []
    today_evidence_ids: set[str] = set()
    historical_count = 0
    historical_dates: set[str] = set()
    for event in store.learning_signal_events():
        refs = [str(value) for value in event.get("evidence_refs") or []]
        if not evidence_ids or not (set(refs) & evidence_ids):
            continue
        signal_type = str(event.get("signal_type") or "")
        if signal_type in {"learning_edge", "counterevidence"}:
            continue
        occurred = observed_date(refs) or str(event.get("occurred_at") or "")[:10]
        if occurred != period:
            historical_count += 1
            if occurred:
                historical_dates.add(occurred)
            continue
        if str(event.get("topic_key") or "") not in personal_topic_keys:
            continue
        label = {
            "demonstrated_understanding": "Demonstrated understanding",
            "applied_learning": "Applied learning",
            "architectural_judgment": "Architectural judgment",
            "operational_capability": "Operational capability",
            "validated_outcome": "Validated capability",
        }.get(signal_type, "Learning movement")
        candidates.append(
            {
                "kind": label,
                "tone": "capability"
                if signal_type in {"operational_capability", "validated_outcome"}
                else "learning",
                "detail": _clean_markdown(str(event.get("claim") or ""), max_chars=700),
                "observed_at": occurred,
            }
        )
        today_evidence_ids.update(refs)

    personal_kinds = {
        "explicit_fact",
        "goal",
        "personality",
        "preference",
        "voice_style",
        "work_style",
    }
    for observation in store.observations():
        if observation.get("status") not in {"approved", "promoted"}:
            continue
        if str(observation.get("kind") or "") not in personal_kinds:
            continue
        payload = (
            observation.get("payload")
            if isinstance(observation.get("payload"), dict)
            else {}
        )
        if payload.get("scope") == "project":
            continue
        refs = [str(value) for value in observation.get("evidence_refs") or []]
        if not evidence_ids or not (set(refs) & evidence_ids):
            continue
        occurred = observed_date(refs)
        if occurred != period:
            historical_count += 1
            if occurred:
                historical_dates.add(occurred)
            continue
        candidates.append(
            {
                "kind": "About the user",
                "tone": "person",
                "detail": _clean_markdown(
                    str(observation.get("claim") or ""), max_chars=700
                ),
                "observed_at": occurred,
            }
        )
        today_evidence_ids.update(refs)

    learned_items: list[dict[str, str]] = []
    seen_details: set[str] = set()
    for candidate in candidates:
        key = str(candidate.get("detail") or "").casefold()
        if not key or key in seen_details:
            continue
        learned_items.append(candidate)
        seen_details.add(key)

    topics = []
    eligible_topics = [
        topic
        for topic in visible_topics
        if str(topic.get("current_state") or "exploring") != "exploring"
        and str(topic.get("topic_key") or "") in personal_topic_keys
    ]
    for topic in sorted(
        eligible_topics,
        key=lambda item: str(item.get("last_seen") or ""),
        reverse=True,
    )[:6]:
        topics.append(
            {
                "label": _clean_markdown(
                    str(topic.get("label") or topic.get("topic_key") or "Learning"),
                    max_chars=120,
                ),
                "state": str(topic.get("current_state") or "exploring").replace(
                    "_", " "
                ),
                "assessment": _clean_markdown(
                    str(topic.get("assessment") or ""), max_chars=420
                ),
                "sessions": int(topic.get("session_count") or 0),
                "projects": int(topic.get("project_count") or 0),
                "last_seen": topic.get("last_seen"),
            }
        )
    return {
        "today": learned_items[:8],
        "historical_count": historical_count,
        "historical_dates": sorted(historical_dates),
        "evidence_ids": sorted(today_evidence_ids),
        "topics": topics,
        "quiet": not learned_items,
    }


def _review_browser(
    store: StateStore,
    vault: Path,
    pending: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build an owner-only, sanitized view of claims awaiting judgment."""

    display_name = vault.name.removesuffix(" Second Brain").strip() or "Second Brain"
    first_name = display_name.split()[0]
    cards: list[dict[str, Any]] = []
    for group in build_review_groups(pending):
        if group["section"] not in {"public", "private"}:
            continue
        group_note = vault / "Inbox" / "Review" / "Groups" / group["filename"]
        if not group_note.is_file():
            group_note = vault / "Inbox" / "Review" / "Index.md"
        for item in group["items"]:
            claim = _clean_markdown(str(item.get("claim") or ""), max_chars=520)
            if not claim:
                continue
            cards.append(
                {
                    "section": group["section"],
                    "group": group["title"],
                    "kind": KIND_LABELS.get(
                        str(item.get("kind") or ""),
                        str(item.get("kind") or "Observation")
                        .replace("_", " ")
                        .title(),
                    ),
                    "subject": _clean_markdown(
                        str(item.get("subject") or "Pending observation"),
                        max_chars=120,
                    ),
                    "claim": personalize_knowledge_text(claim, first_name),
                    "confidence": round(float(item.get("confidence") or 0) * 100),
                    "source_count": int(
                        item.get("source_count") or len(item.get("evidence_refs") or [])
                    ),
                    "project_count": int(item.get("project_count") or 0),
                    "url": obsidian_uri(vault, group_note),
                }
            )
    return cards


def _matt_skills_catalog(vault: Path) -> dict[str, Any]:
    """Return a safe, installed-only guide to the Matt Pocock skill set."""

    skills: list[dict[str, Any]] = []
    skills_root = vault / ".agents" / "skills"
    for guide in MATT_SKILL_GUIDE:
        skill_dir = skills_root / str(guide["name"])
        skill_path = skill_dir / "SKILL.md"
        if not skill_path.is_file():
            continue
        skill_text = skill_path.read_text(encoding="utf-8", errors="replace")
        openai_path = skill_dir / "agents" / "openai.yaml"
        openai_text = (
            openai_path.read_text(encoding="utf-8", errors="replace")
            if openai_path.is_file()
            else ""
        )
        manual = (
            "disable-model-invocation: true" in skill_text
            or "allow_implicit_invocation: false" in openai_text
        )
        source_body = skill_text.strip()
        if source_body.startswith("---"):
            frontmatter_end = source_body.find("\n---", 3)
            if frontmatter_end >= 0:
                source_body = source_body[frontmatter_end + 4 :].lstrip()
        package_files = [
            {
                "path": markdown_path.relative_to(skill_dir).as_posix(),
                "content": (
                    source_body
                    if markdown_path == skill_path
                    else markdown_path.read_text(
                        encoding="utf-8", errors="replace"
                    ).strip()
                ),
                "kind": (
                    "instructions" if markdown_path == skill_path else "reference"
                ),
            }
            for markdown_path in sorted(
                skill_dir.rglob("*.md"),
                key=lambda path: (
                    path.name != "SKILL.md",
                    path.relative_to(skill_dir).as_posix().casefold(),
                ),
            )
            if markdown_path.is_file()
        ]
        skills.append(
            {
                **guide,
                **MATT_SKILL_DETAILS.get(str(guide["name"]), {}),
                "mode": "manual" if manual else "automatic",
                "mode_label": (
                    "Codex · You invoke" if manual else "Codex · Model can invoke"
                ),
                "antigravity_mode": "discoverable",
                "source_body": source_body,
                "package_files": package_files,
            }
        )

    manual_count = sum(skill["mode"] == "manual" for skill in skills)
    installed_names = {str(skill["name"]) for skill in skills}
    installed_lanes = {str(skill.get("lane", "")) for skill in skills}
    connections = [
        connection
        for connection in MATT_SKILL_CONNECTIONS
        if connection["from"] in installed_names and connection["to"] in installed_names
    ]
    sparks = []
    for spark in MATT_SKILL_SPARKS:
        routes = []
        for route in spark["routes"]:
            route_skills = [name for name in route["skills"] if name in installed_names]
            if route_skills:
                routes.append({**route, "skills": route_skills})
        if routes:
            sparks.append({**spark, "routes": routes})
    return {
        "available": bool(skills),
        "source": "mattpocock/skills",
        "expected_count": len(MATT_SKILL_GUIDE),
        "installed_count": len(skills),
        "manual_count": manual_count,
        "automatic_count": len(skills) - manual_count,
        "antigravity_discoverable_count": len(skills),
        "agents": ["Codex", "Antigravity"],
        "lanes": [lane for lane in MATT_SKILL_LANES if lane["id"] in installed_lanes],
        "connections": connections,
        "sparks": sparks,
        "skills": skills,
    }


def build_snapshot(
    paths: RuntimePaths,
    vault: Path,
    *,
    now: datetime | None = None,
    schedule: dict[str, Any] | None = None,
    codex_usage_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = now or datetime.now().astimezone()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    store = StateStore(paths.state)
    runtime_config = load_runtime_config(paths)
    defaults = load_defaults(runtime_config)
    if schedule is None and codex_usage_snapshot is None:
        with ThreadPoolExecutor(max_workers=2) as executor:
            schedule_future = executor.submit(
                task_details, str(runtime_config["task_name"])
            )
            codex_usage_future = executor.submit(read_codex_rate_limits, paths)
            schedule = schedule_future.result()
            codex_usage_snapshot = codex_usage_future.result()
    else:
        schedule = (
            schedule
            if schedule is not None
            else task_details(str(runtime_config["task_name"]))
        )
        codex_usage_snapshot = (
            codex_usage_snapshot
            if codex_usage_snapshot is not None
            else read_codex_rate_limits(paths)
        )

    daily_path = _latest_note(vault / "Journal" / "Daily", "20??-??-??.md")
    weekly_path = _latest_note(vault / "Journal" / "Weekly", "*.md")
    daily_history = _summary_history(vault, "daily", store, paths)
    weekly_history = _summary_history(vault, "weekly", store, paths)
    for entry in (*daily_history, *weekly_history):
        entry["usage"]["codex_impact"] = _summary_codex_impact(
            entry["usage"], codex_usage_snapshot
        )
    empty_summary = {
        "available": False,
        "summary": "",
        "highlights": [],
        "sections": [],
        "url": "",
    }
    daily = daily_history[0] if daily_history else dict(empty_summary)
    weekly = weekly_history[0] if weekly_history else dict(empty_summary)
    activity, trend, _historical_sessions = _recent_activity(store, vault, now=now)
    session_coverage = store.session_coverage()
    total_sessions = int(session_coverage["total"])
    pending = store.observations("pending")
    reviews = review_summary(pending)
    runs = _group_runs(store)
    status = _system_status(store, schedule, now=now)

    runtime_projects = store.present_projects()
    catalog_health = project_catalog_health(vault, runtime_projects)

    latest_review = _latest_note(vault / "Inbox" / "Review", "Review-*.md")
    dashboard_links = {
        "home": obsidian_uri(vault, vault / "Home.md"),
        "projects": obsidian_uri(vault, vault / "Projects" / "Index.md"),
        "skills": obsidian_uri(vault, vault / "Skills" / "Index.md"),
        "memory": obsidian_uri(vault, vault / "Memory" / "LongTermMemory.md"),
        "goals": obsidian_uri(vault, vault / "Goals" / "ActiveGoals.md"),
        "review": obsidian_uri(
            vault, latest_review or vault / "Inbox" / "Review" / "Index.md"
        ),
        "daily": obsidian_uri(
            vault, daily_path or vault / "Journal" / "Daily" / "Index.md"
        ),
        "weekly": obsidian_uri(
            vault, weekly_path or vault / "Journal" / "Weekly" / "Index.md"
        ),
        "roadmap": obsidian_uri(vault, vault / "System" / "Roadmap.md"),
    }

    cached_search = _cached_search_health(paths, store)
    git = _git_summary(vault)
    schedule_state = str(schedule.get("state") or "unknown")
    scheduler_good = bool(schedule.get("installed")) and schedule_state.casefold() in {
        "ready",
        "running",
    }
    next_scheduled = _parse_datetime(schedule.get("next_run"))
    scheduler_detail = (
        "Installed; first run pending"
        if not schedule.get("last_run") and scheduler_good
        else schedule_state.title()
    )
    if scheduler_good and next_scheduled:
        next_label = next_scheduled.astimezone(now.tzinfo).strftime("%b %d, %I:%M %p")
        scheduler_detail = (
            f"{schedule_state.title()}; next {next_label.replace(' 0', ' ')}"
        )
    latest_pipeline = next(iter(store.pipeline_runs(limit=1)), None)
    if latest_pipeline and latest_pipeline.get("status") == "failed":
        daily_outcome = {
            "state": "attention",
            "detail": status["detail"],
        }
    elif latest_pipeline and latest_pipeline.get("status") == "completed":
        daily_outcome = {
            "state": "good",
            "detail": "Latest pipeline completed and published",
        }
    else:
        daily_outcome = {
            "state": "neutral",
            "detail": "No completed pipeline receipt yet",
        }
    checks = [
        {"label": "Vault", "state": "good", "detail": "Canonical notes available"},
        {
            "label": "Scheduler",
            "state": "good" if scheduler_good else "attention",
            "detail": scheduler_detail,
        },
        {"label": "Last Daily outcome", **daily_outcome},
        {"label": "Search", **cached_search},
        {"label": "Private Git", **git},
        {
            "label": "Evidence queue",
            "state": "neutral" if store.evidence_count(status="new") else "good",
            "detail": f"{store.evidence_count(status='new')} waiting for the next run"
            if store.evidence_count(status="new")
            else "Nothing waiting",
        },
        {
            "label": "Session analysis lanes",
            "state": "good" if session_coverage["total"] else "neutral",
            "detail": (
                f"{session_coverage['attributed']} of {session_coverage['total']} sessions linked to projects; "
                f"{session_coverage['unattributed']} safely analyzed profile-only"
                if session_coverage["total"]
                else "No session evidence collected yet"
            ),
        },
        {
            "label": "Project catalog",
            "state": "good" if catalog_health["ok"] else "attention",
            "detail": (
                f"{catalog_health['projects']} projects; "
                f"{catalog_health['collections']} collections kept separate"
                if catalog_health["ok"]
                else str(catalog_health["reason"])
            ),
        },
    ]

    display_name = vault.name.removesuffix(" Second Brain").strip() or "Second Brain"
    first_name = display_name.split()[0]
    schedule_defaults = defaults.get("schedule", {})
    return {
        "schema_version": 2,
        "generated_at": now.isoformat(),
        "display_name": display_name,
        "first_name": first_name,
        "status": status,
        "briefing": {
            "date": now.date().isoformat(),
            "daily": daily,
            "weekly": weekly,
            "pending_evidence": store.evidence_count(status="new"),
        },
        "learning": _learning_snapshot(store, daily),
        "summaries": {
            "daily": daily_history,
            "weekly": weekly_history,
            "counts": {"daily": len(daily_history), "weekly": len(weekly_history)},
            "codex_usage": codex_usage_snapshot,
        },
        "metrics": [
            {
                "label": "Learning topics",
                "value": len(store.learning_topics()),
                "detail": "Understanding tracked over time",
            },
            {
                "label": "Skills evidenced",
                "value": len(
                    [
                        path
                        for path in (vault / "Skills").glob("*.md")
                        if path.name != "Index.md"
                    ]
                ),
                "detail": "Canonical skill notes",
            },
            {
                "label": "Sessions understood",
                "value": total_sessions,
                "detail": (
                    f"{session_coverage['attributed']} linked to projects, "
                    f"{session_coverage['unattributed']} profile-only"
                ),
            },
            {
                "label": "Knowledge promoted",
                "value": len(store.observations("promoted")),
                "detail": "Evidence-backed observations",
            },
        ],
        "activity": {"projects": activity, "trend": trend, "days": 7},
        "project_catalog": catalog_health,
        "session_coverage": session_coverage,
        "insights": _recent_insights(store, vault),
        "knowledge": _knowledge_deck(store, vault),
        "questions": _question_deck(store, vault),
        "patterns": _forming_patterns(store, vault),
        "matt_skills": _matt_skills_catalog(vault),
        "review": {
            "pending": reviews["pending"],
            "questions": reviews["needs_answers"],
            "public": reviews["public_claims"],
            "private": reviews["private_review"],
            "cards": _review_browser(store, vault, pending),
            "groups": [
                {
                    "title": group["title"],
                    "count": group["count"],
                    "section": group["section"],
                }
                for group in reviews["groups"]
            ],
            "url": dashboard_links["review"],
        },
        "runs": runs,
        "schedule": {
            "daily_time": schedule_defaults.get("daily_time", "22:30"),
            "weekly_day": schedule_defaults.get("weekly_day", "Saturday"),
            "last_run": schedule.get("last_run"),
            "next_run": schedule.get("next_run"),
            "state": schedule_state,
            "missed_runs": int(schedule.get("missed_runs") or 0),
        },
        "health": {"checks": checks},
        "focus": _focus(vault),
        "links": dashboard_links,
        "actions": {"enabled": False, "csrf_token": ""},
        "privacy": "Canonical knowledge and safe operational summaries only. Raw evidence stays out of this view.",
    }


def render_dashboard(snapshot: dict[str, Any]) -> str:
    packaged_template = (
        Path(__file__).resolve().parent / "assets" / "dashboard" / "index.html"
    )
    source_template = protocol_root() / "assets" / "dashboard" / "index.html"
    template_path = (
        packaged_template if packaged_template.is_file() else source_template
    )
    template = template_path.read_text(encoding="utf-8")
    payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).replace(
        "</", "<\\/"
    )
    if template.count("__DASHBOARD_DATA__") != 1:
        raise RuntimeError(
            "Dashboard template must contain exactly one data placeholder."
        )
    return template.replace("__DASHBOARD_DATA__", payload)


def build_dashboard(paths: RuntimePaths, vault: Path) -> Path:
    paths.dashboard.mkdir(parents=True, exist_ok=True)
    rendered = render_dashboard(build_snapshot(paths, vault))
    destination = paths.dashboard / "index.html"
    temporary = destination.with_suffix(".html.tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(destination)
    return destination


def open_dashboard(paths: RuntimePaths, vault: Path) -> str:
    from .dashboard_server import ensure_dashboard_server

    url = ensure_dashboard_server(paths)
    webbrowser.open(url)
    return url


def install_dashboard_shortcut(paths: RuntimePaths, vault: Path) -> Path:
    if os.name != "nt":
        raise RuntimeError(
            "Desktop shortcut installation is currently supported on Windows only."
        )
    _ = paths
    owner = vault.name.removesuffix(" Second Brain").strip()
    words = re.findall(r"[A-Za-z0-9]+", owner)
    initials = "".join(word[0] for word in words[:2]).upper() or "SB"
    shortcut_name = f"{initials} Second Brain"
    installer = protocol_root() / "scripts" / "install-dashboard-launcher.ps1"
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(installer),
            "-VaultRoot",
            str(vault),
            "-LauncherName",
            shortcut_name,
            "-Initials",
            initials,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr or result.stdout or "Dashboard shortcut installation failed"
        )
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        shortcut = Path(payload["StartMenuShortcut"])
    except (IndexError, KeyError, json.JSONDecodeError) as error:
        raise RuntimeError(
            "Dashboard shortcut installer returned an invalid receipt"
        ) from error
    if not shortcut.is_file():
        raise RuntimeError("Dashboard shortcut was not created.")
    return shortcut
