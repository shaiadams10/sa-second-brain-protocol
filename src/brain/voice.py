"""The writing-style study: how the owner writes, not what they write about.

    sbrain corpus          every message the owner typed, no AI (brain/corpus.py)
    sbrain voice mark      a model reads every message in full and marks it: typed or pasted,
                           what kind of message, every spelling and grammar error with its fix,
                           habits, and characteristic wording. Cached per message, so re-running
                           after new exports marks only what is new.
    sbrain voice combine   code turns all marks into counts across conversations, sources, and
                           months, sets aside held-out messages for the blind test, and draws a
                           spread sample of real messages.
    sbrain voice write     a second model reads the combined findings and the sample and writes
                           the draft profile.
    sbrain voice test      a fresh model writes the held-out situations from the profile alone;
                           the result is a blind side-by-side page.
    sbrain voice status    where the study stands.

Settings live in the vault's config.toml under [voice]: `exports` (folders of web chat exports),
`background` (the owner's language background, given to both models), and `mark_cli`,
`mark_model`, `mark_effort`, `write_cli`, `write_model`, `write_effort`.
Every file lives in <vault>/.brain/voice/, which is never committed. The profile is a draft
until the owner reviews it and it is copied into the vault by hand.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import statistics
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from brain import corpus
from brain.config import Config
from brain.llm import ModelError, make_model
from brain.text import clip

MESSAGE_LIMIT = 12000  # longer messages keep their start and end: the typed framing around a paste
BATCH_CHARS = 24000
BATCH_MESSAGES = 40
HOLDOUT = 15
SAMPLE = 350
SAMPLE_CLIP = 700

MODES = ("quick_question", "how_it_works_question", "new_request", "detailed_spec", "added_constraint",
         "correction_or_pushback", "feedback_on_result", "approval_or_go_ahead", "frustration",
         "context_or_explanation", "planning_discussion", "thinking_out_loud", "message_for_a_person",
         "other")
TONES = ("neutral", "excited", "approving", "polite", "confused", "frustrated", "urgent")
ERROR_KINDS = ("spelling", "typo", "shortcut", "apostrophe", "capitalization", "article", "preposition",
               "verb_form", "tense", "plural_or_agreement", "word_order", "missing_word", "extra_word",
               "wrong_word", "run_on", "punctuation", "other")
HABITS = (
    "main_request_first", "constraints_added_after", "restates_requirement", "self_correction",
    "thinks_out_loud", "rhetorical_question", "several_questions", "multiple_question_marks",
    "exclamation", "no_final_punctuation", "lowercase_sentence_start", "lowercase_i", "run_on",
    "comma_splice", "starts_with_also", "starts_with_so", "starts_with_ok", "starts_with_and",
    "starts_with_but", "starts_with_wait", "direct_imperative", "polite_please", "asks_opinion",
    "hedging", "praise", "emoji", "caps_for_emphasis", "parenthetical_aside", "quotes_for_terms",
    "numbered_or_bulleted_list", "line_breaks_between_ideas", "one_long_paragraph", "ellipsis",
    "colon_before_list", "no_greeting", "ofc", "abit_or_alot",
)


def _string(**extra) -> dict:
    return {"type": "string", **extra}


MARK_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["marks"],
    "properties": {"marks": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["id", "authorship", "authorship_reason", "owner_text", "language", "mode", "mode_note",
                     "situation", "tone", "errors", "habits", "phrases", "notable_words"],
        "properties": {
            "id": _string(),
            "authorship": _string(enum=["typed", "mixed", "pasted", "not_prose"]),
            "authorship_reason": _string(),
            "owner_text": _string(),
            "language": _string(enum=["english", "first_language", "mixed", "other"]),
            "mode": _string(enum=list(MODES)),
            "mode_note": _string(),
            "situation": _string(),
            "tone": _string(enum=list(TONES)),
            "errors": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["wrong", "right", "kind", "first_language_influence"],
                "properties": {"wrong": _string(), "right": _string(), "kind": _string(enum=list(ERROR_KINDS)),
                               "first_language_influence": {"type": "boolean"}}}},
            "habits": {"type": "array", "items": _string()},
            "phrases": {"type": "array", "items": _string()},
            "notable_words": {"type": "array", "items": _string()},
        }}}},
}

MARK_TASK = """\
You are studying how {owner} writes in English: the mechanics, not the topics. {background}

The file messages.md holds {count} messages {owner} sent to AI assistants, in conversation order,
each under a heading `### <id>` with its source, date, and the owner's previous message in that
conversation (for context only; do not mark it). Read every message completely. Return exactly
one mark per message, with the same id, for all {count} ids.

## authorship
A message sent by {owner} is not always written by {owner}. Decide for each one:
- typed: {owner} wrote all of it.
- mixed: {owner} wrote part of it and pasted the rest. Copy every part {owner} wrote, verbatim and
  in order, into owner_text, joining separate parts with " ... ". Leave owner_text empty otherwise.
- pasted: none of it is {owner}'s own writing.
- not_prose: code, logs, error output, file paths, commands, or data with no prose of {owner}'s.
Pasted text is often unmarked and sometimes short. Judge it by context and by the English itself:
compare each message with the rest of the batch, which is mostly the same person typing.
Text pasted from another AI (prompts written for {owner}, plans, reports), documentation, an
assignment, or another person usually shows polish far above {owner}'s typing: consistent
capitalization and apostrophes, markdown headers, numbered sections, bold labels, formal
connectors, phrases like "You are", "Your task", "Mission:", "Deliverables". The words around it
("here is the prompt", "what do u think about this") are {owner}'s and belong in owner_text.
`[pasted: N characters]` marks where a tagged paste was already removed. Say why in
authorship_reason, in a few words.

## errors (only in {owner}'s own words; empty for pasted and not_prose)
List every error, each with `wrong` copied exactly as written (the word plus up to five words
around it when needed to show the problem) and `right` as correct, natural English. Count:
- shortcuts (u, ur, r, w/e, pls, idk, bc, cuz, gonna, wanna, abit, alot, ofc) as "shortcut",
- missing apostrophes (dont, im, thats) as "apostrophe"; lowercase "i" and lowercase sentence
  starts as "capitalization",
- misspellings as "spelling", or "typo" when it is clearly a slip of the fingers (letters swapped,
  a neighbouring key, a doubled or dropped letter in a word spelled right elsewhere).
Do not count technical names, code identifiers, product names, or quoted text. Do not count
informal but correct English. Set first_language_influence to true only when the structure
plausibly comes from {owner}'s first language, not for typos.

## the rest
- language: english; first_language (mostly {owner}'s first language); mixed; other.
- mode: what the message is doing, from the fixed list; add nuance in mode_note when the list
  does not fit well.
- situation: one neutral sentence describing what {owner} is doing in this message and roughly how
  long it is, without quoting it, e.g. "Asks the agent to add an on/off toggle for map zones in an
  editor, with a default and a keybind; about 60 words." This is used later to test whether a
  model can write the message from a description alone.
- tone: the main tone.
- habits: tags from this list that clearly apply: {habits}. You may add a new
  short snake_case tag for a clear habit the list misses.
- phrases: up to 5 verbatim chunks of 2 to 6 words that show how {owner} says things (for example
  how they ask, push back, add a requirement, or approve), never topic words or names.
- notable_words: up to 5 of {owner}'s words that stand out as above or below their usual level.
For pasted and not_prose messages, leave errors, habits, phrases, and notable_words empty.
"""

WRITE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["profile", "voice_contradictions", "open_questions"],
    "properties": {
        "profile": _string(),
        "voice_contradictions": {"type": "array", "items": _string()},
        "open_questions": {"type": "array", "items": _string()},
    },
}

WRITE_TASK = """\
You are deciding what goes into {owner}'s writing-style profile: a Markdown file another AI loads
when {owner} asks it to "write this like me". {background}

Inputs:
- findings.md: counts computed from every message {owner} typed to AI assistants, after a first
  model marked each one (authorship, errors with fixes, habits, wording). Pasted text is already
  excluded. These numbers cover the whole corpus; trust them over impressions.
- vocabulary.md: the words and word sequences {owner} uses, ranked by how many conversations they
  appear in. Topic words are mixed in; tell them apart from everyday vocabulary.
- sample.md: a spread sample of {owner}'s own words, across sources, months, and kinds of message.
- owner_guidance.md: what {owner} said he wants from the profile. It outranks your own judgment.
- blind_tests.md: results of blind tests, where {owner} picked his real message over one a model
  wrote from an earlier profile, with what gave the model away.
- current_voice.md: the hand-written voice rules in use today.

The goal: anything written with this profile should sound like {owner} wrote it, wherever it is
going: messages to friends and to people he does not know well, emails, posts, comments, replies,
short bios, and descriptions of his work. The data is his chats with AI assistants because that is
what exists, but mimicking how he talks to an AI is NOT the goal. Use the chats as evidence of his
English (vocabulary, level, sentence construction, how he connects thoughts, how direct he is, how
he shows feeling) and translate that into writing for people. Leave out what only makes sense when
instructing an agent (constraint lists for a model, "tell me before you do it", task hand-offs),
except where it reflects how he naturally thinks.

The study is about HOW {owner} writes, not WHAT about. Leave out projects, tools, school, people,
and anything personal unless it changes how {owner} writes.

Structure:

1. "How to use this": short.
2. "Part 1: How {owner} writes": descriptive and honest, built only on the evidence, and compact
   (about a third of the file). English level, where it differs from polished native English and
   which structures come from his first language, sentence shape and rhythm, grammar patterns,
   typing habits with frequencies, how he asks, explains, disagrees, approves, and shows emotion.
   Separate stable patterns from situational ones and random slips.
3. "Vocabulary": what kind of English he uses, so a model can pick words he would pick. Not a full
   word list. Describe the register and band (for example everyday, practical words; technical
   terms only where the subject needs them), list the everyday words and phrases he reaches for
   most (40 to 80, grouped by job: connecting ideas, agreeing, disagreeing, asking, intensifying,
   approximating, approving), and name the kinds of words he does not use (with examples of the
   more polished or literary words a model tends to reach for instead and what he would say).
4. "Part 2: Writing as {owner}": the output layer. Always correctly spelled and grammatical, full
   words, no shortcuts, proper capitalization and apostrophes. Keep his level, vocabulary, and
   construction; never more advanced, formal, or polished than him; never a parody. Follow
   owner_guidance.md: tidy the order so a person can follow it (state the point, then the
   details; do not reproduce stream-of-thought), keep his directness, allow blunt or dark wording
   and capitals for emphasis when he is frustrated, no emojis unless asked, greetings as he
   described. Include rules as MUST / SHOULD / SOMETIMES / AVOID, guidance per kind of writing
   (message to a friend, message to someone he does not know well, email, public post, comment or
   reply, short bio or project description), what makes text sound unlike him, and how to clean up
   his own draft without changing his voice.
5. "Examples": 8 sets, each a NEW invented piece (never a real one) in two versions: "like
   {owner}" (the target) and "too polished" (what to avoid). Cover a message to a friend, a message
   to someone he does not know well, a short email, a public post, a reply that disagrees, a reply
   that thanks or approves, a short bio, and a frustrated message. None of them addressed to an AI.
6. "Evidence": corpus size, sources, date range, key counts, coverage gaps (writing to people is
   inferred from chats with assistants until web exports and real messages are added), and how
   confident each major rule is.

Keep it practical and loadable: rules before explanation, roughly 250 to 350 lines. Start with
"# Writing Style".

Also return voice_contradictions: every rule in current_voice.md the evidence contradicts or does
not support, with the reason; and open_questions: things only {owner} can answer that the guidance
and tests have not already settled.
"""


# ----- files


def _dir(cfg: Config) -> Path:
    return corpus.voice_dir(cfg)


def _log(cfg: Config, line: str) -> None:
    stamped = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}"
    print(stamped, flush=True)
    with (_dir(cfg) / "voice.log").open("a", encoding="utf-8") as fh:
        fh.write(stamped + "\n")


def load_marks(cfg: Config) -> dict[str, dict]:
    path = _dir(cfg) / "marks.jsonl"
    marks: dict[str, dict] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                mark = json.loads(line)
                marks[mark["id"]] = mark
    return marks


def _setting(cfg: Config, args: argparse.Namespace, step: str) -> tuple[str, str | None, str | None]:
    v = cfg.voice
    cli = args.cli or v.get(f"{step}_cli") or cfg.model_cli
    model = args.model or v.get(f"{step}_model") or cfg.model_name
    effort = args.effort or v.get(f"{step}_effort") or cfg.model_effort
    return cli, model, effort


def _fill(template: str, cfg: Config, **values) -> str:
    background = cfg.voice.get("background", "")
    return template.format(owner=cfg.owner, background=background, **values)


# ----- mark


def batches(records: list[dict]) -> list[list[dict]]:
    out: list[list[dict]] = []
    current: list[dict] = []
    size = 0
    for record in records:
        length = min(record["chars"], MESSAGE_LIMIT)
        if current and (size + length > BATCH_CHARS or len(current) >= BATCH_MESSAGES):
            out.append(current)
            current, size = [], 0
        current.append(record)
        size += length
    if current:
        out.append(current)
    return out


def render_batch(batch: list[dict]) -> str:
    parts = []
    for r in batch:
        head = f"### {r['id']}\nsource: {r['source']} · {r['at'][:10]}"
        if r.get("prev"):
            head += f"\nprevious message (context only): {r['prev']!r}"
        parts.append(f"{head}\n\n{clip(r['text'], MESSAGE_LIMIT)}\n")
    return "\n".join(parts)


# A model call that failed because the account is out of usage or signed out. Retrying cannot
# help until the owner signs in (possibly with another account), so the step stops cleanly.
ACCOUNT_PROBLEM = re.compile(
    r"(?i)usage.?limit|rate.?limit|quota|too many requests|\b429\b|\b401\b|unauthori[sz]ed|"
    r"not (?:logged|signed) in|log ?in again|sign ?in again|credits|limit reached|try again (?:at|in)")
EXIT_ACCOUNT = 3


def _title(text: str) -> None:
    """Show progress in the console window's title bar, where it stays visible."""
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleTitleW(text)
    except (AttributeError, OSError):
        pass


def _duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    return f"{minutes // 60}h{minutes % 60:02d}" if minutes >= 60 else f"{minutes}m"


def _account_stop(cfg: Config, step: str, error: str) -> int:
    _log(cfg, f"{step}: stopped, the model account is out of usage or signed out: {error[:300]}")
    print("\n  Everything finished so far is saved. To continue with another account:\n"
          "    1. codex logout\n    2. codex login   (sign in with the other account)\n"
          "    3. run the voice study again (it picks up where it stopped)\n", flush=True)
    _title(f"Voice study - stopped at {step}: out of usage")
    return EXIT_ACCOUNT


def mark(cfg: Config, args: argparse.Namespace) -> int:
    records = corpus.load(cfg)
    done = load_marks(cfg)
    todo = [r for r in records if r["id"] not in done]
    work = batches(todo)
    if args.limit:
        work = work[:args.limit]
    cli, name, effort = _setting(cfg, args, "mark")
    workers = max(1, args.workers or int(cfg.voice.get("mark_workers", 3)))
    _log(cfg, f"mark: {len(done)} of {len(records)} messages already marked; {len(todo)} to go in "
              f"{len(work)} batches, {cli} {name or '(default)'} {effort or ''}, {workers} in parallel")
    if not work:
        return 0
    print(f"  Working on {min(workers, len(work))} batches at once. Each takes several minutes, so the first "
          f"results take a while; after that a line appears as each batch finishes.", flush=True)
    _title(f"Voice study - marking {len(done)}/{len(records)}")
    lock = threading.Lock()
    stop = threading.Event()
    marks_path = _dir(cfg) / "marks.jsonl"
    usage = Counter()
    failed = finished = marked = 0
    account_error = ""
    started = time.time()

    def run(batch: list[dict]) -> tuple[int, int]:
        if stop.is_set():
            return -1, len(batch)
        model = make_model(cfg.work_dir, cli=cli, name=name, effort=effort, log=lambda _l: None)
        task = _fill(MARK_TASK, cfg, count=len(batch), habits=", ".join(HABITS))
        try:
            answer = model.ask(task, {"messages.md": render_batch(batch)}, MARK_SCHEMA, attempts=2,
                               timeout_minutes=45)
        finally:
            with lock:
                for key, value in model.usage.items():
                    if isinstance(value, int):
                        usage[key] += value
        ids = {r["id"] for r in batch}
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        good = []
        for m in answer.get("marks", []):
            if m.get("id") in ids:
                ids.discard(m["id"])
                good.append({**m, "marked_by": f"{model.cli}:{model.name}:{effort or ''}", "marked_at": stamp})
        with lock:
            with marks_path.open("a", encoding="utf-8") as fh:
                for m in good:
                    fh.write(json.dumps(m, ensure_ascii=False) + "\n")
        return len(good), len(ids)

    def progress() -> str:
        elapsed = time.time() - started
        total_done = len(done) + marked
        left = len(work) - finished - failed
        eta = _duration(elapsed / finished * left) if finished and left else ""
        _title(f"Voice study - marking {100 * total_done // len(records)}%" + (f" - about {eta} left" if eta else ""))
        return (f"{finished + failed}/{len(work)} batches, {total_done:,}/{len(records):,} messages marked "
                f"({100 * total_done / len(records):.0f}%), {_duration(elapsed)} so far"
                + (f", about {eta} left" if eta else "")
                + (f", {failed} failed" if failed else "")
                + f"; tokens {usage['input_tokens']:,} in / {usage['output_tokens']:,} out")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run, b): n for n, b in enumerate(work, 1)}
        for future in as_completed(futures):
            n = futures[future]
            try:
                got, missing = future.result()
            except ModelError as exc:
                if ACCOUNT_PROBLEM.search(str(exc)):
                    if not stop.is_set():
                        account_error = str(exc)
                        stop.set()
                        for f in futures:
                            f.cancel()
                        _log(cfg, "mark: the model account is out of usage or signed out; finishing the "
                                  "batches already running, then stopping")
                    continue
                failed += 1
                _log(cfg, f"mark: batch {n} failed, it will be retried: {str(exc)[:200]}")
                continue
            except Exception as exc:  # cancelled or unexpected: retried on the next run
                if not stop.is_set():
                    failed += 1
                    _log(cfg, f"mark: batch {n} failed, it will be retried: {exc!r}"[:300])
                continue
            if got < 0:
                continue
            finished += 1
            marked += got
            note = f" ({missing} left for the next run)" if missing else ""
            _log(cfg, f"mark: batch {n} done{note}. {progress()}")
    total = len(load_marks(cfg))
    _log(cfg, f"mark: this run ended. {total:,} of {len(records):,} messages marked, {failed} batches failed, "
              f"{_duration(time.time() - started)}. Tokens: {usage['input_tokens']:,} in "
              f"({usage['cached_input_tokens']:,} cached), {usage['output_tokens']:,} out")
    if account_error:
        return _account_stop(cfg, "mark", account_error)
    if total >= len(records):
        _title("Voice study - marking done")
    # Non-zero until every message is marked, so a wrapper can simply run this again.
    return 0 if failed == 0 and total >= len(records) else 1


# ----- combine

_SENTENCE = re.compile(r"[^.!?\n]+[.!?]*")
_WORD = re.compile(r"[A-Za-z][A-Za-z'/]*")
_PLACEHOLDER = re.compile(r"\[pasted: \d+ characters\]")
_EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")
SHORTCUTS = ("u", "ur", "r", "w/e", "pls", "plz", "thx", "ty", "idk", "imo", "btw", "tbh", "bc", "cuz", "coz",
             "gonna", "wanna", "gotta", "lemme", "ya", "yea", "k", "gj", "ofc", "abit", "alot", "dont", "cant",
             "wont", "im", "ive", "isnt", "doesnt", "didnt", "wasnt", "arent", "couldnt", "shouldnt",
             "wouldnt", "thats", "whats", "theres", "youre", "u're", "i'm", "i")


def owner_text(record: dict, mark: dict) -> str | None:
    if mark.get("language") not in ("english", "mixed"):
        return None
    if mark.get("authorship") == "typed":
        text = record["text"]
    elif mark.get("authorship") == "mixed":
        text = mark.get("owner_text") or ""
    else:
        return None
    text = _PLACEHOLDER.sub(" ", text).strip()
    return text or None


def _month(record: dict) -> str:
    return record["at"][:7]


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}%" if d else "-"


def _table(head: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(head) + " |", "| " + " | ".join("---" for _ in head) + " |"]
    lines += ["| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ") for c in row) + " |" for row in rows]
    return "\n".join(lines)


def _pick_holdout(items: list[tuple[dict, dict, str]], rng: random.Random) -> list[tuple[dict, dict, str]]:
    pool = [it for it in items if it[1].get("authorship") == "typed" and it[0].get("repeats", 1) == 1
            and 12 <= len(_WORD.findall(it[2])) <= 140]
    by_mode: dict[str, list] = defaultdict(list)
    for it in pool:
        by_mode[it[1].get("mode", "other")].append(it)
    for group in by_mode.values():
        rng.shuffle(group)
        # Writing samples and web chats are closer to writing for people: picked first (popped from the end).
        group.sort(key=lambda it: it[0]["source"] in ("samples", "chatgpt", "claude-ai", "gemini", "web"))
    chosen, used_sessions = [], set()
    modes = sorted(by_mode, key=lambda m: -len(by_mode[m]))
    while len(chosen) < HOLDOUT and any(by_mode[m] for m in modes):
        for m in modes:
            while by_mode[m]:
                it = by_mode[m].pop()
                if it[0]["session"] not in used_sessions:
                    chosen.append(it)
                    used_sessions.add(it[0]["session"])
                    break
            if len(chosen) >= HOLDOUT:
                break
    return chosen


def combine(cfg: Config, _args: argparse.Namespace) -> int:
    records = {r["id"]: r for r in corpus.load(cfg)}
    marks = load_marks(cfg)
    unmarked = len([i for i in records if i not in marks])
    rng = random.Random(7)
    folder = _dir(cfg)

    authorship = defaultdict(Counter)
    items: list[tuple[dict, dict, str]] = []
    for mid, record in records.items():
        mark = marks.get(mid)
        if not mark:
            continue
        authorship[record["source"]][mark.get("authorship", "?")] += 1
        text = owner_text(record, mark)
        if text:
            items.append((record, mark, text))

    # Messages already used in a blind test never go into a profile or a new test.
    from brain.voicetest import tested_ids

    used = tested_ids(cfg)
    items = [it for it in items if it[0]["id"] not in used]
    holdout_path = folder / "holdout.json"
    if holdout_path.exists():
        held_ids = {h["id"] for h in json.loads(holdout_path.read_text(encoding="utf-8"))}
    elif unmarked > len(records) // 100:
        held_ids = set()  # the held-out set is chosen once, from the (almost) fully marked corpus
    else:
        chosen = _pick_holdout(items, rng)
        held = [{"id": r["id"], "source": r["source"], "at": r["at"], "mode": m.get("mode"),
                 "situation": m.get("situation"), "text": t} for r, m, t in chosen]
        holdout_path.write_text(json.dumps(held, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        held_ids = {h["id"] for h in held}
    items = [it for it in items if it[0]["id"] not in held_ids]

    n = len(items)
    sessions = {r["session"] for r, _, _ in items}
    words_per_msg, words_per_sentence = [], []
    flags = Counter()
    shortcut_msgs, shortcut_sessions = Counter(), defaultdict(set)
    first_words, first_two, sentence_openers = Counter(), Counter(), Counter()
    ngram_sessions: dict[str, set] = defaultdict(set)
    monthly = defaultdict(lambda: Counter())
    total_words = 0
    for record, mark, text in items:
        words = _WORD.findall(text)
        lower = [w.lower() for w in words]
        total_words += len(words)
        words_per_msg.append(len(words))
        sentences = [s.strip() for s in _SENTENCE.findall(text) if _WORD.search(s)]
        words_per_sentence += [len(_WORD.findall(s)) for s in sentences]
        letters = re.search(r"[A-Za-z]", text)
        if letters and text[letters.start()].islower():
            flags["starts lowercase"] += 1
        if not re.search(r"[.!?)\]:]\s*$", text):
            flags["no final punctuation"] += 1
        for label, pattern in (("'??' or more", r"\?\?"), ("'!'", r"!"), ("'...'", r"\.\.\."),
                               ("parentheses", r"\("), ("' - ' dash", r" [-–—] "), ("colon", r":"),
                               ("line breaks", r"\n"), ("ALL-CAPS word", r"\b[A-Z]{3,}\b"),
                               ("semicolon", r";"), ("question mark", r"\?")):
            if re.search(pattern, text):
                flags[label] += 1
        if _EMOJI.search(text):
            flags["emoji"] += 1
        if words and words[0] == "i" or re.search(r"(^|[^A-Za-z])i([^A-Za-z']|$)", text):
            flags["lowercase 'i' as a word"] += 1
        for token in set(lower) & set(SHORTCUTS):
            if token == "i":
                continue
            shortcut_msgs[token] += 1
            shortcut_sessions[token].add(record["session"])
        if lower:
            first_words[lower[0]] += 1
            first_two[" ".join(lower[:2])] += 1
        for s in sentences:
            sw = [w.lower() for w in _WORD.findall(s)]
            if sw:
                sentence_openers[sw[0]] += 1
        for size in (2, 3, 4):
            for i in range(len(lower) - size + 1):
                ngram_sessions[" ".join(lower[i:i + size])].add(record["session"])
        month = _month(record)
        monthly[month]["messages"] += 1
        monthly[month]["words"] += len(words)
        monthly[month]["errors"] += len(mark.get("errors", []))
        monthly[month]["shortcuts"] += sum(1 for e in mark.get("errors", []) if e.get("kind") in ("shortcut", "apostrophe"))
        monthly[month]["lowercase start"] += 1 if letters and text[letters.start()].islower() else 0

    errors_by_kind = Counter()
    first_language_by_kind = Counter()
    grouped: dict[tuple, dict] = {}
    habits, phrases, notable = Counter(), defaultdict(set), Counter()
    habit_sessions = defaultdict(set)
    modes, tones = Counter(), Counter()
    mode_words = defaultdict(list)
    mode_habits = defaultdict(Counter)
    mode_errors = Counter()
    for record, mark, text in items:
        modes[mark.get("mode")] += 1
        tones[mark.get("tone")] += 1
        mode_words[mark.get("mode")].append(len(_WORD.findall(text)))
        for e in mark.get("errors", []):
            kind = e.get("kind", "other")
            errors_by_kind[kind] += 1
            mode_errors[mark.get("mode")] += 1
            if e.get("first_language_influence"):
                first_language_by_kind[kind] += 1
            key = (kind, e.get("wrong", "").strip().lower(), e.get("right", "").strip().lower())
            g = grouped.setdefault(key, {"messages": 0, "sessions": set(), "sources": set(), "months": set(),
                                         "first_language": 0})
            g["messages"] += 1
            g["sessions"].add(record["session"])
            g["sources"].add(record["source"])
            g["months"].add(_month(record))
            g["first_language"] += 1 if e.get("first_language_influence") else 0
        for h in set(mark.get("habits", [])):
            habits[h] += 1
            habit_sessions[h].add(record["session"])
            mode_habits[mark.get("mode")][h] += 1
        for p in mark.get("phrases", []):
            phrases[p.strip().lower()].add(record["session"])
        for w in mark.get("notable_words", []):
            notable[w.strip().lower()] += 1

    def med(values: list[int]) -> str:
        return f"{statistics.median(values):.0f}" if values else "-"

    def quart(values: list[int]) -> str:
        if len(values) < 4:
            return "-"
        q = statistics.quantiles(values, n=4)
        return f"{q[0]:.0f}–{q[2]:.0f}"

    out = [f"# Findings: how {cfg.owner} writes",
           "",
           f"Built {datetime.now().astimezone():%Y-%m-%d %H:%M} from {len(records)} unique messages "
           f"({len(marks)} marked{f', {unmarked} not yet marked' if unmarked else ''}). "
           f"{n} messages hold {cfg.owner}'s own English words ({total_words:,} words in {len(sessions)} "
           f"conversations); {len(held_ids)} more are held out for the blind test and left out of everything below.",
           "", "## Authorship by source", "",
           _table(["source", "typed", "mixed", "pasted", "not prose"],
                  [[s, c["typed"], c["mixed"], c["pasted"], c["not_prose"]] for s, c in sorted(authorship.items())]),
           "", "## Length", "",
           _table(["measure", "median", "middle half"],
                  [["words per message", med(words_per_msg), quart(words_per_msg)],
                   ["words per sentence", med(words_per_sentence), quart(words_per_sentence)]]),
           "", "## Punctuation and capitalization (share of messages)", "",
           _table(["feature", "messages", "share"], [[k, v, _pct(v, n)] for k, v in flags.most_common()]),
           "", "## Shortcuts and apostrophe-less forms", "",
           _table(["form", "messages", "share of messages", "conversations"],
                  [[k, v, _pct(v, n), len(shortcut_sessions[k])] for k, v in shortcut_msgs.most_common(40)]),
           "", "## Message openers", "",
           _table(["first word", "messages", "share"], [[k, v, _pct(v, n)] for k, v in first_words.most_common(30)]),
           "",
           _table(["first two words", "messages"], [[k, v] for k, v in first_two.most_common(30)]),
           "",
           _table(["sentence opener", "sentences"], [[k, v] for k, v in sentence_openers.most_common(30)]),
           "", "## Word sequences used in the most conversations", "",
           "Topic words are mixed in; judge which are style.", "",
           _table(["sequence", "conversations"],
                  [[k, len(v)] for k, v in sorted(ngram_sessions.items(), key=lambda kv: -len(kv[1]))
                   if len(v) >= 5][:120]),
           "", "## Kinds of message", "",
           _table(["mode", "messages", "share", "median words", "errors per message", "top habits"],
                  [[m, c, _pct(c, n), med(mode_words[m]), f"{mode_errors[m] / c:.1f}",
                    ", ".join(h for h, _ in mode_habits[m].most_common(6))] for m, c in modes.most_common()]),
           "",
           _table(["tone", "messages", "share"], [[t, c, _pct(c, n)] for t, c in tones.most_common()]),
           "", "## Habits marked", "",
           _table(["habit", "messages", "share", "conversations"],
                  [[h, c, _pct(c, n), len(habit_sessions[h])] for h, c in habits.most_common(60)]),
           "", "## Errors by kind", "",
           f"{sum(errors_by_kind.values())} errors in {total_words:,} words "
           f"({1000 * sum(errors_by_kind.values()) / max(total_words, 1):.0f} per 1,000 words).", "",
           _table(["kind", "errors", "per 1,000 words", "marked first-language influence"],
                  [[k, c, f"{1000 * c / max(total_words, 1):.1f}", first_language_by_kind[k]]
                   for k, c in errors_by_kind.most_common()]),
           "", "## Recurring errors (in 2 or more conversations)", "",
           "Capitalization is left out here; see the punctuation and capitalization table.", "",
           _table(["kind", "as written", "correct", "messages", "conversations", "sources", "months", "first language"],
                  [[k[0], k[1], k[2], g["messages"], len(g["sessions"]), len(g["sources"]), len(g["months"]),
                    g["first_language"]]
                   for k, g in sorted(grouped.items(), key=lambda kv: -len(kv[1]["sessions"]))
                   if len(g["sessions"]) >= 2 and k[0] != "capitalization"][:200]),
           "",
           f"Errors seen in only one conversation: {sum(1 for g in grouped.values() if len(g['sessions']) == 1)}.",
           "", "## First-language influence: examples", "",
           _table(["kind", "as written", "correct", "conversations"],
                  [[k[0], k[1], k[2], len(g["sessions"])]
                   for k, g in sorted(grouped.items(), key=lambda kv: -len(kv[1]["sessions"]))
                   if g["first_language"]][:60]),
           "", "## Characteristic phrases (marked by the first model)", "",
           _table(["phrase", "conversations"],
                  [[p, len(s)] for p, s in sorted(phrases.items(), key=lambda kv: -len(kv[1]))[:100]]),
           "", "## Words that stand out for level", "",
           _table(["word", "times marked"], [[w, c] for w, c in notable.most_common(60)]),
           "", "## By month", "",
           _table(["month", "messages", "words", "errors per 100 words", "shortcuts per 100 words", "starts lowercase"],
                  [[m, c["messages"], c["words"], f"{100 * c['errors'] / max(c['words'], 1):.1f}",
                    f"{100 * c['shortcuts'] / max(c['words'], 1):.1f}", _pct(c["lowercase start"], c["messages"])]
                   for m, c in sorted(monthly.items())]),
           ""]
    (folder / "findings.md").write_text("\n".join(out), encoding="utf-8")

    # A spread sample of real messages: across sources, months, and kinds of message.
    groups: dict[tuple, list] = defaultdict(list)
    for it in items:
        groups[(it[0]["source"], _month(it[0]), it[1].get("mode"))].append(it)
    for g in groups.values():
        rng.shuffle(g)
    sample = []
    keys = sorted(groups)
    while len(sample) < SAMPLE and any(groups[k] for k in keys):
        for k in keys:
            if groups[k] and len(sample) < SAMPLE:
                sample.append(groups[k].pop())
    sample.sort(key=lambda it: it[0]["at"])
    lines = [f"# Sample of {cfg.owner}'s own words", "",
             f"{len(sample)} messages spread across sources, months, and kinds of message. Pasted parts are removed.", ""]
    for record, mark, text in sample:
        lines += [f"## {record['source']} · {record['at'][:10]} · {mark.get('mode')} · {mark.get('tone')}", "",
                  clip(text, SAMPLE_CLIP), ""]
    (folder / "sample.md").write_text("\n".join(lines), encoding="utf-8")
    (folder / "sample-ids.json").write_text(json.dumps([it[0]["id"] for it in sample]), encoding="utf-8")

    # Vocabulary: every word and short sequence by how many conversations use it.
    word_sessions: dict[str, set] = defaultdict(set)
    word_count: Counter = Counter()
    for record, _mark, text in items:
        for w in (t.lower() for t in _WORD.findall(text)):
            word_sessions[w].add(record["session"])
            word_count[w] += 1
    ranked = sorted(word_sessions, key=lambda w: (-len(word_sessions[w]), w))
    vocab = [f"# Vocabulary of {cfg.owner}'s own words", "",
             f"{len(word_count):,} distinct words in {sum(word_count.values()):,} words of his own writing, "
             f"ranked by the number of conversations (of {len(sessions)}) that use them. Topic words are mixed in.", "",
             _table(["word", "conversations", "times"],
                    [[w, len(word_sessions[w]), word_count[w]] for w in ranked[:900] if len(word_sessions[w]) >= 3]),
             "", "## Word sequences", "",
             _table(["sequence", "conversations"],
                    [[k, len(v)] for k, v in sorted(ngram_sessions.items(), key=lambda kv: -len(kv[1])) if len(v) >= 8][:400]),
             ""]
    (folder / "vocabulary.md").write_text("\n".join(vocab), encoding="utf-8")

    # Everything judged pasted or mixed, so the owner can check the first model's calls.
    review = ["# Messages judged pasted or mixed", ""]
    for mid, mark in marks.items():
        if mark.get("authorship") in ("pasted", "mixed") and mid in records:
            r = records[mid]
            review += [f"## {mark['authorship']} · {r['source']} · {r['at'][:10]} · {mark.get('authorship_reason', '')}", "",
                       clip(r["text"], 600), ""]
    (folder / "pasted-review.md").write_text("\n".join(review), encoding="utf-8")
    _log(cfg, f"combine: {n} messages of the owner's own words, {len(held_ids)} held out; wrote findings.md, "
              f"sample.md, pasted-review.md" + (f". {unmarked} messages are not marked yet" if unmarked else ""))
    return 0


# ----- write and test


def write(cfg: Config, args: argparse.Namespace) -> int:
    folder = _dir(cfg)
    findings, sample = folder / "findings.md", folder / "sample.md"
    if not findings.exists():
        raise SystemExit("Run `sbrain voice combine` first.")
    voice_md = cfg.vault / "me" / "voice.md"
    cli, name, effort = _setting(cfg, args, "write")
    model = make_model(cfg.work_dir, cli=cli, name=name, effort=effort)
    _log(cfg, f"write: {model.cli} {model.name} {effort or ''}")
    vocabulary = folder / "vocabulary.md"
    guidance = cfg.vault / cfg.voice.get("guidance", "brain/voice-guidance.md")
    tests = cfg.vault / "brain" / "voice-tests.md"
    files = {"findings.md": findings.read_text(encoding="utf-8"),
             "vocabulary.md": vocabulary.read_text(encoding="utf-8") if vocabulary.exists() else "(not built yet)",
             "sample.md": sample.read_text(encoding="utf-8"),
             "owner_guidance.md": guidance.read_text(encoding="utf-8") if guidance.exists() else "(none)",
             "blind_tests.md": tests.read_text(encoding="utf-8") if tests.exists() else "(no tests yet)",
             "current_voice.md": voice_md.read_text(encoding="utf-8") if voice_md.exists() else "(none)"}
    _title("Voice study - writing the profile")
    try:
        answer = model.ask(_fill(WRITE_TASK, cfg), files, WRITE_SCHEMA, attempts=2, timeout_minutes=60)
    except ModelError as exc:
        if ACCOUNT_PROBLEM.search(str(exc)):
            return _account_stop(cfg, "write", str(exc))
        raise
    stamp = f"<!-- Draft written by `sbrain voice write` ({model.cli} {model.name} {effort or ''}) on " \
            f"{datetime.now():%Y-%m-%d}. Not reviewed yet. -->\n\n"
    (folder / "writing-style.draft.md").write_text(stamp + answer["profile"].strip() + "\n", encoding="utf-8")
    notes = ["# Notes from the writer", "", "## Rules in voice.md the evidence does not support", ""]
    notes += [f"- {c}" for c in answer["voice_contradictions"]] or ["- none"]
    notes += ["", "## Open questions", ""] + ([f"- {q}" for q in answer["open_questions"]] or ["- none"])
    (folder / "write-notes.md").write_text("\n".join(notes) + "\n", encoding="utf-8")
    _log(cfg, f"write: wrote writing-style.draft.md and write-notes.md; tokens {model.usage}")
    return 0


def test(cfg: Config, args: argparse.Namespace) -> int:
    from brain import voicetest

    holdout = _dir(cfg) / "holdout.json"
    if not voicetest.profile_path(cfg) or not holdout.exists():
        raise SystemExit("Run `sbrain voice combine` and `sbrain voice write` first.")
    held = json.loads(holdout.read_text(encoding="utf-8"))
    cli, name, effort = _setting(cfg, args, "write")
    _title("Voice study - blind test")
    try:
        made = voicetest.create(cfg, held, "study", log=lambda line: _log(cfg, line), cli=cli, name=name, effort=effort)
    except ModelError as exc:
        if ACCOUNT_PROBLEM.search(str(exc)):
            return _account_stop(cfg, "test", str(exc))
        raise
    if made:
        (_dir(cfg) / "blind-test.html").write_text(voicetest.page(cfg, made), encoding="utf-8")
    return 0


def weekly(cfg: Config, _args: argparse.Namespace) -> int:
    from brain import voicetest

    made = voicetest.weekly(cfg, log=lambda line: _log(cfg, line), on_demand=getattr(_args, "step", "") == "newtest")
    if made:
        (_dir(cfg) / "blind-test.html").write_text(voicetest.page(cfg, made), encoding="utf-8")
    return 0


def status(cfg: Config, _args: argparse.Namespace) -> int:
    folder = _dir(cfg)
    summary_path = folder / "corpus-summary.json"
    if not summary_path.exists():
        print("No corpus yet. Run `sbrain corpus`.")
        return 0
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    records = corpus.load(cfg)
    marks = load_marks(cfg)
    marked = sum(1 for r in records if r["id"] in marks)
    print(f"Corpus built {summary['built']}: {summary['messages']} messages {summary['by_source']}")
    print(f"Marked: {marked} of {len(records)} ({len(records) - marked} to go)")
    for name in ("findings.md", "holdout.json", "writing-style.draft.md", "blind-test.html"):
        path = folder / name
        when = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M") if path.exists() else "not yet"
        print(f"  {name}: {when}")
    return 0


def main(cfg: Config, args: argparse.Namespace) -> int:
    _dir(cfg).mkdir(parents=True, exist_ok=True)
    steps = {"mark": mark, "combine": combine, "write": write, "test": test, "weekly": weekly, "newtest": weekly, "status": status}
    if args.step == "status":
        return status(cfg, args)
    # While a step runs, the dashboard can tell it is alive; a closed window or crash leaves a dead pid.
    active = _dir(cfg) / "active.json"
    active.write_text(json.dumps({"pid": os.getpid(), "step": args.step,
                                  "started": datetime.now().astimezone().isoformat(timespec="seconds")}), encoding="utf-8")
    try:
        return steps[args.step](cfg, args)
    finally:
        active.unlink(missing_ok=True)
