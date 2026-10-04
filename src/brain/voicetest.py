"""Blind tests of the writing-style profile, and their history.

A test pairs real messages the owner wrote with messages a model wrote from the profile alone,
given only a one-line description of each situation. The owner picks which one is theirs; the
share of pairs where the model fooled them is the score. Tests run once after the profile is
written (`sbrain voice test`) and then weekly on messages from the week just finished
(`sbrain voice weekly`, also run after the scheduled update when [voice] weekly_test is true).

Each test's pairs are kept in <vault>/.brain/voice/tests/<id>.json (never committed: they hold the
owner's messages). The results are kept in <vault>/brain/voice-tests.jsonl and rendered to
<vault>/brain/voice-tests.md, both committed: they hold only ids, scores, and the owner's notes.
A message used in a test is never shown to the model that writes the profile.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import random
import re
from datetime import datetime, timedelta
from pathlib import Path

from brain import corpus
from brain.config import Config
from brain.llm import ModelError, make_model

WEEKLY_N = 10
RESULTS = "brain/voice-tests.jsonl"
HISTORY = "brain/voice-tests.md"
_WORD = re.compile(r"[A-Za-z][A-Za-z'/]*")

TEST_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["messages"],
    "properties": {"messages": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "raw", "like_owner"],
        "properties": {"id": {"type": "string"}, "raw": {"type": "string"}, "like_owner": {"type": "string"}}}}},
}

TEST_TASK = """\
profile.md describes how {owner} writes. Using only that profile, write each message described in
situations.md. For each id return two versions:

- "raw": exactly how {owner} would type it in a chat with an AI assistant, following the profile's
  description of his real typing: his shortcuts and apostrophe-less forms (u, ur, dont, im, w/e,
  ofc, abit, alot), lowercase starts, missing final punctuation, run-ons, and recurring slips, at the
  frequency the profile reports. Type it as one block the way he does: no blank lines, no lists, and
  no line breaks unless the situation says the message had them.
- "like_owner": the same message as the profile's output layer would write it.

Match the described length. Never reuse wording from the profile's examples.
{notes}"""


def tests_dir(cfg: Config) -> Path:
    return cfg.work_dir / "voice" / "tests"


def profile_path(cfg: Config) -> Path | None:
    approved = cfg.vault / "me" / "writing-style.md"
    draft = cfg.work_dir / "voice" / "writing-style.draft.md"
    return approved if approved.exists() else draft if draft.exists() else None


def _version(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


def load_results(cfg: Config) -> list[dict]:
    path = cfg.vault / RESULTS
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def tested_ids(cfg: Config) -> set[str]:
    ids: set[str] = set()
    for path in tests_dir(cfg).glob("*.json"):
        ids.update(item["id"] for item in json.loads(path.read_text(encoding="utf-8")).get("items", []))
    return ids


def load_test(cfg: Config, test_id: str | None = None) -> dict | None:
    """A test by id, else the newest unanswered one, else the newest."""
    paths = sorted(tests_dir(cfg).glob("*.json"))
    if test_id:
        path = tests_dir(cfg) / f"{test_id}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() and path.parent == tests_dir(cfg) else None
    answered = {r["test"] for r in load_results(cfg)}
    tests = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    open_tests = [t for t in tests if t["id"] not in answered]
    return (open_tests or tests or [None])[-1]


def past_notes(cfg: Config) -> str:
    """What gave earlier generated messages away, in the owner's words, with the message each note
    is about, for the next writer."""
    notes = []
    for r in load_results(cfg)[-6:]:
        if r.get("note"):
            notes.append(f"- Overall: {r['note']}")
        for i in r.get("items", []):
            if i.get("note"):
                about = f' (about the model message: "{i["model_message"][:300]}")' if i.get("model_message") else ""
                verdict = "it fooled him" if i.get("fooled") else "he spotted it"
                notes.append(f"- {i['note']}{about}; {verdict}")
    return "\n".join(notes)


def create(cfg: Config, held: list[dict], kind: str, log=print, cli=None, name=None, effort=None) -> dict | None:
    """Write the generated side of each held-out message and save the test. `held` items need
    id, mode, situation, text (and optionally at, source)."""
    profile = profile_path(cfg)
    if not profile or not held:
        log("test: no profile or no messages to test")
        return None
    text = profile.read_text(encoding="utf-8")
    v = cfg.voice
    model = make_model(cfg.work_dir, cli=cli or v.get("write_cli") or cfg.model_cli,
                       name=name or v.get("write_model") or cfg.model_name,
                       effort=effort or v.get("write_effort") or cfg.model_effort, log=lambda _l: None)
    notes = past_notes(cfg)
    task = TEST_TASK.format(owner=cfg.owner, notes=(
        f"\nIn earlier blind tests, {cfg.owner} spotted the generated messages by these tells. Avoid them:\n{notes}\n"
        if notes else ""))
    situations = "\n".join(f"- {h['id']} ({h['mode']}): {h['situation']}" for h in held)
    log(f"test: writing {len(held)} held-out situations with {model.cli} {model.name}")
    answer = model.ask(task, {"profile.md": text, "situations.md": situations}, TEST_SCHEMA, timeout_minutes=30)
    generated = {m["id"]: m for m in answer.get("messages", [])}
    rng = random.Random()
    items = []
    for h in held:
        g = generated.get(h["id"])
        if not g or not g.get("raw"):
            continue
        items.append({"id": h["id"], "mode": h.get("mode"), "situation": h.get("situation"), "at": h.get("at"),
                      "source": h.get("source"), "real": h["text"], "generated": g["raw"],
                      "generated_clean": g.get("like_owner", ""), "real_side": rng.choice("AB")})
    now = datetime.now().astimezone()
    test = {"id": f"{now:%Y-%m-%d-%H%M}-{kind}", "kind": kind, "created": now.isoformat(timespec="seconds"),
            "profile": str(profile.relative_to(cfg.vault)).replace("\\", "/"), "profile_version": _version(text),
            "model": f"{model.cli}:{model.name}:{model.effort or ''}", "items": items}
    tests_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tests_dir(cfg) / f"{test['id']}.json").write_text(json.dumps(test, indent=1, ensure_ascii=False) + "\n",
                                                       encoding="utf-8")
    render_history(cfg)
    from brain.run import commit

    commit(cfg, f"Voice blind test {test['id']} ready ({len(items)} pairs)", [HISTORY])
    log(f"test: saved {test['id']} with {len(items)} pairs; take it on the dashboard (Guide) or at /voice/test")
    return test


def _sample_ids(cfg: Config) -> set[str]:
    path = cfg.work_dir / "voice" / "sample-ids.json"
    return set(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else set()


def pick(cfg: Config, since: datetime | None, n: int = WEEKLY_N) -> list[dict]:
    """Up to n typed English messages never tested and never shown to the profile writer, spread over
    kinds of message. With `since`, only messages written after it; without, any such message."""
    from brain.voice import load_marks, owner_text

    marks = load_marks(cfg)
    used = tested_ids(cfg) | _sample_ids(cfg)
    pool = []
    for r in corpus.load(cfg):
        m = marks.get(r["id"])
        if not m or r["id"] in used or m.get("authorship") != "typed" or r.get("repeats", 1) > 1:
            continue
        if since and datetime.fromisoformat(r["at"]) < since:
            continue
        text = owner_text(r, m)
        if text and 8 <= len(_WORD.findall(text)) <= 140:
            pool.append({"id": r["id"], "mode": m.get("mode"), "situation": m.get("situation"), "text": text,
                         "at": r["at"], "source": r["source"], "session": r["session"]})
    random.Random().shuffle(pool)
    # Writing meant for people (samples the owner added) comes first: that is what the profile is for.
    pool.sort(key=lambda item: item["source"] != "samples")
    by_mode: dict[str, list] = {}
    for item in pool:
        by_mode.setdefault(item["mode"], []).append(item)
    chosen, sessions = [], set()
    all_sessions = {p["session"] for p in pool}
    while len(chosen) < n and any(by_mode.values()):
        for mode in sorted(by_mode, key=lambda k: -len(by_mode[k])):
            while by_mode[mode]:
                item = by_mode[mode].pop()
                if item["session"] not in sessions or len(sessions) >= len(all_sessions):
                    chosen.append(item)
                    sessions.add(item["session"])
                    break
            if len(chosen) >= n:
                break
    return chosen


def weekly(cfg: Config, log=print, on_demand: bool = False) -> dict | None:
    """Collect and mark new messages, then build a blind test. Weekly tests use messages written
    since the last test; an on-demand test tops up with older messages never tested."""
    from brain import voice

    if not profile_path(cfg):
        log("test: no profile yet, skipped")
        return None
    stamps = [r.get("created") for r in load_results(cfg)] + [(load_test(cfg) or {}).get("created")]
    created = [datetime.fromisoformat(t) for t in stamps if t]
    since = max(created) if created else datetime.now().astimezone() - timedelta(days=7)
    log("test: collecting and marking new messages")
    exports = [Path(p).expanduser() for p in cfg.voice.get("exports", []) if Path(p).expanduser().is_dir()]
    corpus.write(cfg, corpus.collect(cfg, exports))
    args = argparse.Namespace(cli=None, model=None, effort=None, workers=None, limit=None)
    if voice.mark(cfg, args) == voice.EXIT_ACCOUNT:
        log("test: not built, the model account is out of usage")
        return None
    held = pick(cfg, since)
    if on_demand and len(held) < WEEKLY_N:
        taken = {h["id"] for h in held}
        held += [h for h in pick(cfg, None, WEEKLY_N * 2) if h["id"] not in taken][:WEEKLY_N - len(held)]
    if len(held) < 4:
        log(f"test: not built, only {len(held)} untested messages")
        return None
    try:
        return create(cfg, held, "on-demand" if on_demand else "weekly", log)
    except ModelError as exc:
        log(f"test: not built: {str(exc)[:300]}")
        return None


def save_result(cfg: Config, body: dict) -> dict:
    """Record the owner's picks for one test: append to the results log, re-render the history, commit."""
    test = load_test(cfg, str(body.get("test", "")))
    if not test:
        raise KeyError("test")
    picks = {p.get("id"): p for p in body.get("picks", []) if isinstance(p, dict)}
    items = []
    for item in test["items"]:
        p = picks.get(item["id"])
        if not p or p.get("picked") not in ("A", "B"):
            continue
        note = str(p.get("note") or "")[:500]
        entry = {"id": item["id"], "mode": item.get("mode"), "words": len(_WORD.findall(item["real"])),
                 "picked": p["picked"], "fooled": p["picked"] != item["real_side"], "note": note}
        if note:  # the model's message the note is about, so the next writer knows what "this" means
            entry["model_message"] = item["generated"][:600]
        items.append(entry)
    if not items:
        raise ValueError("no picks")
    result = {"test": test["id"], "kind": test["kind"], "created": test["created"],
              "answered": datetime.now().astimezone().isoformat(timespec="seconds"),
              "profile": test["profile"], "profile_version": test["profile_version"], "model": test["model"],
              "pairs": len(items), "fooled": sum(i["fooled"] for i in items),
              "note": str(body.get("note") or "")[:2000], "items": items}
    results = [r for r in load_results(cfg) if r["test"] != test["id"]] + [result]
    path = cfg.vault / RESULTS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results), encoding="utf-8")
    render_history(cfg, results)
    from brain.run import commit

    commit(cfg, f"Voice blind test {test['id']}: fooled {result['fooled']} of {result['pairs']}", [RESULTS, HISTORY])
    return result


def _when(iso: str) -> str:
    dt = datetime.fromisoformat(iso)
    return f"{dt:%b} {dt.day}, {dt:%H:%M}"


def _week(iso: str) -> str:
    y, w, _ = datetime.fromisoformat(iso).isocalendar()
    return f"{y}-W{w:02d}"


def render_history(cfg: Config, results: list[dict] | None = None) -> None:
    results = results if results is not None else load_results(cfg)
    answered = {r["test"]: r for r in results}
    tests = sorted((json.loads(p.read_text(encoding="utf-8")) for p in tests_dir(cfg).glob("*.json")),
                   key=lambda t: t["created"])
    rows = []
    for t in tests:
        r = answered.get(t["id"])
        if r:
            rows.append(f"| {_week(t['created'])} | {_when(t['created'])} | {_when(r['answered'])} | "
                        f"{t['kind']} | {t['profile_version']} | {r['pairs']} | {r['fooled']} | {100 * r['fooled'] // max(r['pairs'], 1)}% | "
                        f"[Review](/voice/test?id={t['id']}) |")
        else:
            rows.append(f"| {_week(t['created'])} | {_when(t['created'])} | waiting | {t['kind']} | "
                        f"{t['profile_version']} | {len(t['items'])} | | | [Take it](/voice/test?id={t['id']}) |")
    for r in results:  # results whose pairs are no longer on this machine
        if r["test"] not in {t["id"] for t in tests}:
            rows.append(f"| {_week(r['created'])} | {_when(r['created'])} | {_when(r['answered'])} | "
                        f"{r['kind']} | {r['profile_version']} | {r['pairs']} | {r['fooled']} | {100 * r['fooled'] // max(r['pairs'], 1)}% | |")
    lines = ["# Voice blind tests", "",
             f"Each test pairs real messages {cfg.owner} wrote with messages a model wrote from the writing-style "
             "profile alone. The score is how often the model fooled him: higher means the profile captures him better. "
             "Weekly tests use messages from the week just finished, which no profile has seen.", "",
             "| Week | Created | Taken | Kind | Profile | Pairs | Fooled | Score | Open |",
             "| --- | --- | --- | --- | --- | ---: | ---: | ---: | --- |", *rows]
    notes = [(r["answered"][:10], r["note"]) for r in results if r.get("note")]
    item_notes = [(r["answered"][:10], i) for r in results for i in r.get("items", []) if i.get("note")]
    if notes or item_notes:
        lines += ["", "## What gave it away", ""]
        lines += [f"- **{d}:** {n}" for d, n in notes]
        lines += [f"- **{d}** ({i.get('mode')}, {i['words']} words, {'fooled him' if i.get('fooled') else 'spotted'}): {i['note']}"
                  + (f"  \n  Model's message: *{i['model_message'][:200]}*" if i.get("model_message") else "") for d, i in item_notes]
    (cfg.vault / HISTORY).write_text("\n".join(lines) + "\n", encoding="utf-8")


def summary(cfg: Config) -> dict:
    results = load_results(cfg)
    current = load_test(cfg)
    answered = {r["test"] for r in results}
    open_test = current if current and current["id"] not in answered else None
    return {
        "open": open_test["id"] if open_test else None,
        "open_pairs": len(open_test["items"]) if open_test else 0,
        "open_week": _week(open_test["created"]) if open_test else None,
        "open_created": open_test["created"] if open_test else None,
        "history": [{"test": r["test"], "date": r["answered"][:10], "week": _week(r["created"]), "kind": r["kind"],
                     "pairs": r["pairs"], "fooled": r["fooled"], "note": r.get("note", "")} for r in results],
    }


def page(cfg: Config, test: dict) -> str:
    from brain.voicetest_page import render

    return render(cfg, test)
