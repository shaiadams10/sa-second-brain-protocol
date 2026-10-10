"""Phrase it better: where the owner could have said it shorter, or with the right term.

Each week, code collects the owner's own typed prompts to coding agents (Codex, Claude Code,
Antigravity), each with the agent's first reply (where agents usually say back what they
understood) and the owner's next message (where a misunderstanding shows). A model then writes up
to ten cards:

- term     a word that means something else, or a long description where a term exists
- shorter  the same request in far fewer words
- misread  the agent misunderstood because of the wording, and the owner had to correct it

In the same call it confirms which terms from earlier cards the owner now uses, counts the rounds
lost to wording, and, once a month, names habits that recur across recent cards. Up to two older
cards whose terms are not in use yet come back each week. A grammar note on a card comes from the
writing-style study's marks, with no model call.

    sbrain phrasing weekly        build every finished week not built yet (after the scheduled
                                  update when [phrasing] weekly is true)
    sbrain phrasing week --week W one week again (W = 2026-W40, "last", or "this")
    sbrain phrasing status

Committed: brain/phrasing.jsonl (one line per week: cards with short excerpts, stats, terms in
use), brain/phrasing-feedback.jsonl (the owner's answers on each card), and brain/phrasing.md (both,
readable). Full prompts stay in .brain/phrasing/, never committed. Cards never reach the
writing-style profile: they describe how the owner could write, not how they do.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import statistics
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from brain import corpus
from brain.config import Config
from brain.digest import resolve_week, week_bounds, week_label
from brain.llm import ModelError, make_model
from brain.text import clip

RESULTS = "brain/phrasing.jsonl"
FEEDBACK = "brain/phrasing-feedback.jsonl"
HISTORY = "brain/phrasing.md"

CARDS = 10
MIN_WORDS = 6  # shorter prompts rarely hold a lesson worth a card
LONG_WORDS = 60  # a "long prompt" in the weekly stats
PROMPT_LIMIT = 2000
OPENING_LIMIT = 600
REPLY_LIMIT = 300
NEXT_LIMIT = 300
BUDGET = 220_000  # characters of prompts.md; past it, corrected prompts first, then long and short in turn
CATCH_UP = 4  # weeks: Claude Code keeps about 30 days of history
RETURNING = 2  # older cards shown again per week
RETURN_AFTER = 3  # weeks before a card comes back
RETURN_TIMES = 2
PATTERN_CARDS = 8  # cards in the last six weeks before a monthly pattern note is worth writing
KINDS = ("term", "shorter", "misread")
AREAS = ("ui", "code", "git", "data", "agents", "tools", "english")
VERDICTS = ("got", "knew", "not_useful")
# Error kinds from the writing-style marks worth a note. Shortcuts, apostrophes, capitals, and
# typos are how the owner types fast, not what this page teaches.
GRAMMAR_KINDS = {"spelling", "article", "preposition", "verb_form", "tense", "plural_or_agreement",
                 "word_order", "missing_word", "extra_word", "wrong_word"}

_WORD = re.compile(r"[A-Za-z][A-Za-z'/]*")
_TAGGED = re.compile(r"<([\w-]+)>.*?</\1>", re.S)  # app events a client logs as a user turn


def _s(**extra) -> dict:
    return {"type": "string", **extra}


SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["cards", "terms_used", "rounds_lost", "patterns"],
    "properties": {
        "cards": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "kind", "said", "better", "why", "agent_put_it", "terms"],
            "properties": {
                "id": _s(), "kind": _s(enum=list(KINDS)), "said": _s(), "better": _s(), "why": _s(),
                "agent_put_it": _s(),
                "terms": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False, "required": ["term", "meaning", "area"],
                    "properties": {"term": _s(), "meaning": _s(), "area": _s(enum=list(AREAS))}}},
            }}},
        "terms_used": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["term", "id", "quote"],
            "properties": {"term": _s(), "id": _s(), "quote": _s()}}},
        "rounds_lost": {"type": "integer"},
        "patterns": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["pattern", "example"],
            "properties": {"pattern": _s(), "example": _s()}}},
    },
}

TASK = """\
{owner} gives instructions to AI coding agents in English. {background}
You are {owner}'s coach for those instructions. The goal: {owner} learns to say what they mean in
fewer words and with the right terms, so an agent understands at once.

prompts.md holds {count} messages {owner} typed this week. Each comes with the agent's first reply
(where agents usually say back what they understood), its final reply when that differs, and
{owner}'s next message in the conversation (where a misunderstanding shows).

## cards
Pick up to {n} cards, the most useful first. Only real lessons; fewer cards is fine. Kinds:
- term: {owner} used a word that means something else ("make the header wider" for a top bar that
  should be taller, where the right word is "height"), or described something at length where a
  term exists ("the small box that shows when you hover" is a "tooltip"). The agent's reply often
  uses the right term; take it from there.
- shorter: the message could say the same thing in far fewer words, leaving out nothing the agent
  needs. Context, constraints, and examples the agent needs are not wordiness. Repetition, false
  starts, thinking out loud, and filler ("like", "I don't know", "basically") are.
- misread: the agent misunderstood because of how {owner} worded it, and the next message had to
  correct it. Not when the agent made its own mistake.

For each card:
- id: the message id from prompts.md.
- said: the words from {owner}'s message the lesson is about, copied exactly: the phrase for a
  term; the whole message or its core for shorter, at most 400 characters, with "…" where you cut.
- better: what {owner} could type next time, in plain, correct English at their level: simple words
  plus the right term, never formal or corporate. For shorter, one or two sentences when possible.
  For misread, the first message that would have avoided the misunderstanding, using what the next
  message made clear. Add nothing {owner} did not mean.
- why: one or two short sentences, to {owner} as "you", on what makes it better. For a term, say
  what it means.
- agent_put_it: the agent's words that restate what {owner} meant in the better way (the right term,
  or the request in one sentence), copied exactly from its reply, at most 200 characters. "" when
  the reply does not restate it; an opening like "I'll take a look first" does not count.
- terms: zero to three terms worth learning from this card, each with a plain one-line meaning and
  an area (ui: layout and visual design; code; git; data; agents: AI models and agents; tools:
  setup, terminals, apps; english: everyday words).

Prefer lessons that carry over to future prompts (a term, a habit of wording) over one-off fixes.
Skip grammar and spelling on their own (another step covers them), messages that are already short
and clear, pasted text, and code. Do not invent problems. Never repeat a lesson from earlier.md,
and skip what {owner} marked there as already known or not useful.

## terms_used
terms-to-check.md lists terms from earlier cards and the places this week where the word appears.
List each term {owner} used correctly in their own words, with the message id and a short quote.
Leave out the rest.

## rounds_lost
Across all messages in prompts.md, how many times {owner}'s next message had to correct a
misunderstanding caused by the wording, not by the agent's own mistake.

## patterns
{patterns}
"""

PATTERNS_DUE = """\
recent-cards.md holds the cards of the last weeks. Name up to two habits that recur across several
of them. pattern: the habit in one sentence to {owner} as "you". example: one short case from the
cards, written as what {owner} said and the better wording ("the toaster that confirms" -> "a
confirmation toast"), not the pattern again."""


def _setting(cfg: Config, key: str, override: str | None) -> str | None:
    p, v = cfg.phrasing, cfg.voice
    return override or p.get(key) or v.get(f"write_{key}") or {"cli": cfg.model_cli, "model": cfg.model_name,
                                                                 "effort": cfg.model_effort}[key]


def work_dir(cfg: Config) -> Path:
    return cfg.work_dir / "phrasing"


def _log(cfg: Config, line: str, log=print) -> None:
    stamped = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}"
    log(stamped)
    work_dir(cfg).mkdir(parents=True, exist_ok=True)
    with (work_dir(cfg) / "phrasing.log").open("a", encoding="utf-8") as fh:
        fh.write(stamped + "\n")


def words(text: str) -> int:
    return len(_WORD.findall(text))


# ----- storage


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def load_weeks(cfg: Config) -> list[dict]:
    return sorted(_read_jsonl(cfg.vault / RESULTS), key=lambda r: r["week"])


def load_feedback(cfg: Config) -> dict[str, dict]:
    return {r["card"]: r for r in _read_jsonl(cfg.vault / FEEDBACK)}


def _prompts_file(cfg: Config, week: str) -> Path:
    return work_dir(cfg) / "prompts" / f"{week}.json"


def full_prompts(cfg: Config, week: str) -> dict[str, str]:
    path = _prompts_file(cfg, week)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def terms(cfg: Config, records: list[dict] | None = None) -> list[dict]:
    """Every term the cards taught, first card first, with the week the owner first used it."""
    records = records if records is not None else load_weeks(cfg)
    out: dict[str, dict] = {}
    for r in records:
        for card in r.get("cards", []):
            for t in card.get("terms", []):
                key = t["term"].strip().lower()
                if key and key not in out:
                    out[key] = {**t, "key": key, "card": card["id"], "week": r["week"], "used": None}
    for r in records:
        for u in r.get("terms_used", []):
            t = out.get(u["term"].strip().lower())
            if t and not t["used"] and r["week"] > t["week"]:
                t["used"] = {"week": r["week"], "quote": u.get("quote", "")}
    return list(out.values())


# ----- collecting the week


def _grammar(mark: dict | None) -> list[dict]:
    notes = []
    for e in (mark or {}).get("errors", []):
        if e.get("kind") in GRAMMAR_KINDS and e.get("wrong") and e.get("right") and e["wrong"] != e["right"]:
            notes.append({"wrong": e["wrong"], "right": e["right"]})
    return notes[:2]


def collect(cfg: Config, week: str) -> tuple[list[dict], dict, list[str]]:
    """The week's candidate prompts, the week's stats, and every typed prompt (for term checks)."""
    from brain.projects import scan
    from brain.sources import load_sources_for
    from brain.voice import load_marks, owner_text

    start, end = week_bounds(week)
    marks = load_marks(cfg)
    decisions = cfg.load_decisions()
    catalog = scan(cfg.projects_root, decisions.get("folders", {}), cfg.places)
    found: dict[str, dict] = {}
    typed: list[str] = []
    counts: list[int] = []
    for session in load_sources_for(cfg.sources, start, end):
        if session.by_agent:
            continue
        location = session.cwd or (session.path_hints.most_common(1)[0][0] if session.path_hints else None)
        pid = catalog.resolve(location)
        project = catalog.projects[pid].name if pid in catalog.projects else (Path(location).name if location else "")
        cleaned = [_TAGGED.sub("", corpus.clean(ex.user)[0]).strip() for ex in session.exchanges]
        for i, ex in enumerate(session.exchanges):
            text = cleaned[i]
            if not text:
                continue
            mid = corpus.message_id(text)
            mark = marks.get(mid)
            if mark:
                own = owner_text({"text": text}, mark)
            else:  # not marked yet: keep it unless it is mostly another language; the model skips pastes
                own = text if corpus.non_latin_share(text) < 0.5 else None
            if not own:
                continue
            n = words(own)
            if mid in found or n == 0:
                continue
            typed.append(own)
            counts.append(n)
            if n < MIN_WORDS:
                continue
            nxt = next((c for c in cleaned[i + 1:] if c), "")
            next_mark = marks.get(corpus.message_id(nxt)) if nxt else None
            found[mid] = {
                "mid": mid, "at": ex.at.isoformat(timespec="minutes"), "source": session.tool,
                "project": project, "text": own, "words": n,
                "opening": ex.opening.strip(), "reply": ex.reply.strip(), "next": nxt,
                "corrected": bool(next_mark and next_mark.get("mode") == "correction_or_pushback"),
                "grammar": _grammar(mark),
            }
    long = [n for n in counts if n >= LONG_WORDS]
    stats = {"prompts": len(counts), "long": len(long),
             "avg_long_words": round(statistics.mean(long)) if long else 0,
             "median_words": round(statistics.median(counts)) if counts else 0}
    return sorted(found.values(), key=lambda c: c["at"]), stats, typed


def _size(c: dict) -> int:
    reply = min(len(c["reply"]), REPLY_LIMIT) if c["reply"] != c["opening"] else 0
    return (min(len(c["text"]), PROMPT_LIMIT) + min(len(c["opening"]), OPENING_LIMIT) + reply
            + min(len(c["next"]), NEXT_LIMIT) + 160)


def _render_prompts(items: list[dict], seed: str = "") -> tuple[str, dict[str, dict]]:
    """prompts.md within BUDGET, in time order. When the week holds more, corrected prompts go first
    (they are where misreads are), then long and short prompts in turn: long ones hold the shorter
    lessons, short ones the wrong terms."""
    long = sorted((c for c in items if not c["corrected"] and c["words"] >= LONG_WORDS), key=lambda c: -c["words"])
    short = [c for c in items if not c["corrected"] and c["words"] < LONG_WORDS]
    random.Random(seed).shuffle(short)
    ranked = [c for c in items if c["corrected"]]
    while long or short:
        ranked += [lst.pop(0) for lst in (long, short) if lst]
    chosen, used = [], 0
    for c in ranked:
        if used + _size(c) > BUDGET and chosen:
            continue
        chosen.append(c)
        used += _size(c)
    chosen.sort(key=lambda c: c["at"])
    ids, blocks = {}, []
    for n, c in enumerate(chosen, 1):
        pid = f"p{n}"
        ids[pid] = c
        when = datetime.fromisoformat(c["at"])
        parts = [f"### {pid} · {c['source']} · {c['project'] or 'no project'} · {when:%a %b} {when.day} · {c['words']} words",
                 "Owner wrote:", clip(c["text"], PROMPT_LIMIT), "",
                 "Agent's first reply:", clip(c["opening"], OPENING_LIMIT) or "(none)"]
        if c["reply"] and c["reply"] != c["opening"]:
            parts += ["", "Agent's final reply:", clip(c["reply"], REPLY_LIMIT)]
        parts += ["", "Owner's next message:", clip(c["next"], NEXT_LIMIT) or "(none, the conversation ended)"]
        blocks.append("\n".join(parts))
    return "\n\n".join(blocks), ids


def _earlier(cfg: Config, records: list[dict], feedback: dict[str, dict]) -> str:
    lines = []
    for r in records[-8:]:
        for card in r.get("cards", []):
            fb = feedback.get(card["id"], {})
            tag = {"knew": " (already known)", "not_useful": " (marked not useful)"}.get(fb.get("verdict"), "")
            note = f" Note: {fb['note']}" if fb.get("note") else ""
            lines.append(f"- {card['kind']}: \"{card['said'][:160]}\" -> \"{card['better'][:160]}\"{tag}{note}")
    return "\n".join(lines) or "(no earlier cards)"


def _terms_to_check(pending: list[dict], items: list[dict], typed: list[str]) -> str:
    by_text = {c["text"]: c for c in items}
    blocks = []
    for t in pending[:40]:
        pattern = re.compile(rf"(?<!\w){re.escape(t['term'])}(?!\w)", re.I)
        hits = []
        for text in typed:
            m = pattern.search(text)
            if m:
                pid = by_text.get(text, {}).get("pid", "short message")
                hits.append(f"  - {pid}: …{text[max(0, m.start() - 90):m.end() + 90]}…")
        if hits:
            blocks.append(f"- {t['term']} ({t['meaning']})\n" + "\n".join(hits[:4]))
    return "\n".join(blocks)


def _patterns_due(records: list[dict], week: str) -> bool:
    month = week_bounds(week)[0].strftime("%Y-%m")
    if any(r.get("patterns_month") == month for r in records if r["week"] != week):
        return False
    return sum(len(r.get("cards", [])) for r in records[-6:]) >= PATTERN_CARDS


def _returning(records: list[dict], week: str, feedback: dict[str, dict], used: set[str]) -> list[str]:
    """Older cards whose terms the owner does not use yet, oldest first, at most RETURN_TIMES each."""
    cutoff = week_label(week_bounds(week)[0].date() - timedelta(weeks=RETURN_AFTER))
    shown: dict[str, int] = {}
    for r in records:
        if r["week"] != week:
            for cid in r.get("returning", []):
                shown[cid] = shown.get(cid, 0) + 1
    picks = []
    for r in records:
        if r["week"] > cutoff:
            break
        for card in r.get("cards", []):
            keys = {t["term"].strip().lower() for t in card.get("terms", [])}
            if (keys and not keys & used and shown.get(card["id"], 0) < RETURN_TIMES
                    and feedback.get(card["id"], {}).get("verdict") not in ("knew", "not_useful")):
                picks.append(card["id"])
    return picks[:RETURNING]


# ----- building a week


def build(cfg: Config, week: str, log=print, cli=None, name=None, effort=None) -> dict | None:
    records = [r for r in load_weeks(cfg) if r["week"] < week]
    feedback = load_feedback(cfg)
    items, stats, typed = collect(cfg, week)
    if not items:
        _log(cfg, f"{week}: no typed prompts long enough for a card", log)
        return None
    prompts_md, ids = _render_prompts(items, seed=week)
    for pid, c in ids.items():
        c["pid"] = pid
    known = terms(cfg, records)
    pending = [t for t in known if not t["used"]]
    due = _patterns_due(records + [{"week": week}], week)
    files = {"prompts.md": prompts_md, "earlier.md": _earlier(cfg, records, feedback),
             "terms-to-check.md": _terms_to_check(pending, items, typed) or "(no terms to check)"}
    if due:
        files["recent-cards.md"] = "\n".join(
            f"- {c['kind']}: \"{c['said'][:200]}\" -> \"{c['better'][:200]}\"" for r in records[-6:] for c in r.get("cards", []))
    background = cfg.voice.get("background", "")
    task = TASK.format(owner=cfg.owner, background=background, count=len(ids),
                       n=int(cfg.phrasing.get("per_week", CARDS)),
                       patterns=PATTERNS_DUE.format(owner=cfg.owner) if due else "Return an empty list.")
    model = make_model(cfg.work_dir, cli=_setting(cfg, "cli", cli), name=_setting(cfg, "model", name),
                       effort=_setting(cfg, "effort", effort), log=lambda _l: None)
    _log(cfg, f"{week}: reading {len(ids)} prompts with {model.cli} {model.name}", log)
    answer = model.ask(task, files, SCHEMA, timeout_minutes=40)

    cards, per_kind = [], {}
    for raw in answer.get("cards", [])[: int(cfg.phrasing.get("per_week", CARDS))]:
        c = ids.get(raw.get("id"))
        if not c or raw.get("kind") not in KINDS or not raw.get("said", "").strip() or not raw.get("better", "").strip():
            continue
        k = per_kind[(c["mid"], raw["kind"])] = per_kind.get((c["mid"], raw["kind"]), 0) + 1
        cards.append({
            "id": f"{week}-{c['mid']}-{raw['kind']}-{k}", "prompt": c["mid"], "kind": raw["kind"], "at": c["at"],
            "source": c["source"], "project": c["project"], "words": c["words"],
            "said": raw["said"].strip()[:600], "better": raw["better"].strip(), "why": raw["why"].strip(),
            "agent_put_it": raw.get("agent_put_it", "").strip()[:300],
            "terms": [t for t in raw.get("terms", []) if t.get("term", "").strip()][:3],
            "grammar": c["grammar"],
        })
    pending_keys = {t["key"] for t in pending}
    used_now = []
    for u in answer.get("terms_used", []):
        key = u.get("term", "").strip().lower()
        if key in pending_keys and key not in {x["term"].lower() for x in used_now}:
            used_now.append({"term": u["term"].strip(), "quote": u.get("quote", "")[:200]})
    used_keys = {t["key"] for t in known if t["used"]} | {u["term"].lower() for u in used_now}
    start, end = week_bounds(week)
    record = {
        "week": week, "built": datetime.now().astimezone().isoformat(timespec="seconds"),
        "partial": datetime.now().astimezone() < end,
        "model": f"{model.cli}:{model.name}:{model.effort or ''}",
        "stats": {**stats, "candidates": len(ids), "rounds_lost": max(0, int(answer.get("rounds_lost") or 0))},
        "cards": cards, "terms_used": used_now,
        "returning": _returning(records + [{"week": week}], week, feedback, used_keys),
    }
    patterns = [{"pattern": p["pattern"].strip(), "example": "" if p.get("example", "").strip() == p["pattern"].strip()
                 else p.get("example", "").strip()} for p in answer.get("patterns", []) if p.get("pattern", "").strip()] if due else []
    if patterns:
        record["patterns"], record["patterns_month"] = patterns[:2], start.strftime("%Y-%m")

    full = {c["mid"]: c["text"] for c in ids.values() if any(card["prompt"] == c["mid"] for card in cards)}
    _prompts_file(cfg, week).parent.mkdir(parents=True, exist_ok=True)
    _prompts_file(cfg, week).write_text(json.dumps(full, ensure_ascii=False, indent=1), encoding="utf-8")
    all_records = [r for r in load_weeks(cfg) if r["week"] != week] + [record]
    _write_jsonl(cfg.vault / RESULTS, sorted(all_records, key=lambda r: r["week"]))
    render(cfg)
    from brain.run import commit

    commit(cfg, f"Phrase it better {week}: {len(cards)} cards", [RESULTS, HISTORY])
    usage = getattr(model, "usage", {}) or {}
    _log(cfg, f"{week}: {len(cards)} cards, {len(used_now)} terms now in use, {record['stats']['rounds_lost']} rounds "
              f"lost to wording ({usage.get('input_tokens', 0):,} input / {usage.get('output_tokens', 0):,} output tokens)", log)
    return record


def weeks_to_build(cfg: Config, today: date | None = None) -> list[str]:
    """Finished weeks since the newest finished week built, at most CATCH_UP; a week built while it
    was in progress is built again once it ends."""
    today = today or date.today()
    last = week_label(today - timedelta(days=7))
    done = {r["week"] for r in load_weeks(cfg) if not r.get("partial")}
    weeks, day = [], week_bounds(last)[0].date()
    for _ in range(CATCH_UP):
        label = week_label(day)
        if label in done:
            break
        weeks.append(label)
        day -= timedelta(weeks=1)
    return sorted(weeks) if done or not weeks else weeks[:1]


def weekly(cfg: Config, log=print, cli=None, name=None, effort=None) -> list[dict]:
    built = []
    for week in weeks_to_build(cfg):
        try:
            record = build(cfg, week, log, cli, name, effort)
        except ModelError as exc:
            _log(cfg, f"{week}: not built: {str(exc)[:300]}", log)
            break
        if record:
            built.append(record)
    return built


def save_feedback(cfg: Config, body: dict) -> dict:
    """The owner's answer on one card: a verdict (or none, to clear it), a note, and their own try."""
    card = str(body.get("card", ""))
    if not any(c["id"] == card for r in load_weeks(cfg) for c in r.get("cards", [])):
        raise KeyError(card)
    verdict = body.get("verdict") or ""
    if verdict and verdict not in VERDICTS:
        raise ValueError("unknown verdict")
    rows = load_feedback(cfg)
    entry = dict(rows.get(card, {"card": card}))
    for key in ("verdict", "note", "tried"):
        if key in body:
            entry[key] = str(body.get(key) or "")[: 2000 if key != "verdict" else 20]
    entry["at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    rows[card] = entry
    _write_jsonl(cfg.vault / FEEDBACK, sorted(rows.values(), key=lambda r: r["card"]))
    render(cfg)
    from brain.run import commit

    what = {"got": "got it", "knew": "knew it", "not_useful": "not useful"}.get(
        entry.get("verdict"), "answer cleared" if "verdict" in body else "note or try saved")
    commit(cfg, f"Phrase it better: {what} ({card})", [FEEDBACK, HISTORY])
    return entry


# ----- reading


KIND_LABEL = {"term": "Right term", "shorter": "Shorter", "misread": "Misread"}
SOURCE_LABEL = {"codex": "Codex", "claude-code": "Claude Code", "antigravity": "Antigravity"}


def dashboard(cfg: Config) -> dict:
    records = load_weeks(cfg)
    feedback = load_feedback(cfg)
    by_id = {c["id"]: {**c, "week": r["week"]} for r in records for c in r.get("cards", [])}
    weeks = []
    for r in reversed(records):
        prompts = full_prompts(cfg, r["week"])

        def card(c: dict, returning: bool = False) -> dict:
            out = {**c, "feedback": feedback.get(c["id"], {}), "returning": returning}
            text = prompts.get(c["prompt"]) or full_prompts(cfg, c.get("week", r["week"])).get(c["prompt"])
            if text and len(text) > len(c["said"]) + 40:
                out["full"] = text
            return out

        weeks.append({
            "week": r["week"], "built": r["built"], "partial": r.get("partial", False), "model": r.get("model"),
            "stats": r.get("stats", {}), "terms_used": r.get("terms_used", []), "patterns": r.get("patterns", []),
            "cards": [card(c) for c in r.get("cards", [])],
            "returning": [card(by_id[cid], True) for cid in r.get("returning", []) if cid in by_id],
        })
    trend = [{"week": r["week"], "cards": len(r.get("cards", [])), **r.get("stats", {})} for r in records]
    patterns = next((r["patterns"] for r in reversed(records) if r.get("patterns")), [])
    return {"weeks": weeks, "terms": terms(cfg, records), "trend": trend, "patterns": patterns}


def summary(cfg: Config) -> dict:
    """For the tab count, the Log, and the Guide: cards still unanswered in the newest week and in all
    weeks, and each week's totals."""
    records = load_weeks(cfg)
    feedback = load_feedback(cfg)
    newest = records[-1] if records else None

    def open_cards(r: dict) -> int:
        return sum(1 for c in r.get("cards", []) if not feedback.get(c["id"], {}).get("verdict"))

    return {"week": newest["week"] if newest else None, "unread": open_cards(newest) if newest else 0,
            "open": sum(open_cards(r) for r in records), "cards": sum(len(r.get("cards", [])) for r in records),
            "open_weeks": sum(1 for r in records if open_cards(r)),
            "weeks": {r["week"]: {"cards": len(r.get("cards", [])), "open": open_cards(r), "used": len(r.get("terms_used", [])),
                                  "rounds_lost": r.get("stats", {}).get("rounds_lost", 0)} for r in records}}


def render(cfg: Config) -> None:
    records = load_weeks(cfg)
    feedback = load_feedback(cfg)
    lines = ["# Phrase it better", "",
             f"Where {cfg.owner} could have told a coding agent the same thing in fewer words or with the right term, "
             "taken each week from the prompts and how the agent said them back. Answers and notes from the dashboard "
             "are kept with each card.", ""]
    known = terms(cfg, records)
    if known:
        lines += ["## Terms", "", "| Term | Meaning | Area | From | In use since |", "| --- | --- | --- | --- | --- |"]
        lines += [f"| {t['term']} | {t['meaning']} | {t['area']} | {t['week']} | {t['used']['week'] if t['used'] else ''} |"
                  for t in known]
        lines.append("")
    for r in reversed(records):
        s = r.get("stats", {})
        lines += [f"## {r['week']}{' (in progress)' if r.get('partial') else ''}", "",
                  f"{len(r.get('cards', []))} cards from {s.get('prompts', 0)} prompts; {s.get('long', 0)} long prompts "
                  f"averaging {s.get('avg_long_words', 0)} words; {s.get('rounds_lost', 0)} rounds lost to wording.", ""]
        for p in r.get("patterns", []):
            lines.append(f"- **Pattern:** {p['pattern']}" + (f" ({p['example']})" if p.get("example") and p["example"] != p["pattern"] else ""))
        if r.get("patterns"):
            lines.append("")
        for c in r.get("cards", []):
            fb = feedback.get(c["id"], {})
            when = datetime.fromisoformat(c["at"])
            lines += [f"### {KIND_LABEL[c['kind']]} · {SOURCE_LABEL.get(c['source'], c['source'])} · "
                      f"{c['project'] or 'no project'} · {when:%b} {when.day}", "",
                      f"- **You said:** {c['said']}", f"- **Better:** {c['better']}", f"- **Why:** {c['why']}"]
            if c.get("agent_put_it"):
                lines.append(f"- **The agent put it:** {c['agent_put_it']}")
            if c.get("terms"):
                lines.append("- **Terms:** " + "; ".join(f"{t['term']} ({t['meaning']})" for t in c["terms"]))
            if c.get("grammar"):
                lines.append("- **Grammar:** " + "; ".join(f"{g['wrong']} → {g['right']}" for g in c["grammar"]))
            if fb.get("tried"):
                lines.append(f"- **Your try:** {fb['tried']}")
            if fb.get("verdict"):
                lines.append(f"- **Answer:** {fb['verdict'].replace('_', ' ')}" + (f". {fb['note']}" if fb.get("note") else ""))
            elif fb.get("note"):
                lines.append(f"- **Note:** {fb['note']}")
            lines.append("")
    path = cfg.vault / HISTORY
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


# ----- command line


def main(cfg: Config, args: argparse.Namespace) -> int:
    if args.step == "status":
        records = load_weeks(cfg)
        known = terms(cfg, records)
        print(f"{len(records)} weeks built, {sum(len(r.get('cards', [])) for r in records)} cards, "
              f"{len(known)} terms ({sum(1 for t in known if t['used'])} in use)")
        for r in records[-6:]:
            s = r.get("stats", {})
            print(f"  {r['week']}{' (in progress)' if r.get('partial') else ''}: {len(r.get('cards', []))} cards, "
                  f"{s.get('prompts', 0)} prompts, {s.get('rounds_lost', 0)} rounds lost")
        print(f"Next weekly run builds: {', '.join(weeks_to_build(cfg)) or 'nothing new'}")
        return 0
    try:
        if args.step == "week":
            build(cfg, resolve_week(args.week or "last"), cli=args.cli, name=args.model, effort=args.effort)
        else:
            weekly(cfg, cli=args.cli, name=args.model, effort=args.effort)
    except ModelError as exc:
        print(f"Not built: {str(exc)[:500]}")
        return 1
    return 0
