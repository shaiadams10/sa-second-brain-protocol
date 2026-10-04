"""The blind test page: one pair at a time, pick with a click or a key, say what gave the model away,
and the result saves itself to the vault's test history at the end.

Served by the dashboard at /voice/test, so saving goes to its API. The test's pairs are embedded as
JSON; nothing loads from elsewhere except the dashboard's own web font.
"""

from __future__ import annotations

import html
import json

from brain.config import Config

TAGS = ["Missing my shortcuts", "Too tidy", "Words I don't use", "Too long", "Too short",
        "Line breaks or format", "Not how I ask", "Wrong tone"]


def render(cfg: Config, test: dict) -> str:
    from brain.voicetest import load_results

    done = next((r for r in load_results(cfg) if r["test"] == test["id"]), None)
    pairs = [{"id": i["id"], "mode": (i.get("mode") or "").replace("_", " "), "situation": i.get("situation") or "",
              "a": i["real"] if i["real_side"] == "A" else i["generated"],
              "b": i["generated"] if i["real_side"] == "A" else i["real"],
              "real": i["real_side"], "clean": i.get("generated_clean") or ""} for i in test["items"]]
    review = {}
    if done:
        sides = {i["id"]: i["real_side"] for i in test["items"]}
        for item in done.get("items", []):
            real = sides.get(item["id"], "A")
            picked = item.get("picked") or (real if not item.get("fooled") else ("B" if real == "A" else "A"))
            review[item["id"]] = {"picked": picked, "note": item.get("note", "")}
    data = json.dumps({"test": test["id"], "kind": test["kind"], "created": test["created"], "pairs": pairs,
                       "tags": TAGS, "done": {"fooled": done["fooled"], "pairs": done["pairs"], "answered": done["answered"],
                                              "note": done.get("note", ""), "review": review} if done else None},
                      ensure_ascii=False).replace("</", "<\\/")
    title = {"weekly": "This week's blind test", "on-demand": "Blind test", "study": "Blind test"}.get(test["kind"], "Blind test")
    return PAGE.replace("__TITLE__", html.escape(title)).replace("__DATA__", data)


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ · Logbook</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600;700&display=swap">
<script>
  try { const t = new URLSearchParams(location.search).get("theme") || localStorage.getItem("brain-theme");
        if (t === "light" || t === "dark") document.documentElement.dataset.theme = t; } catch (e) {}
</script>
<style>
:root { --ground:#f5f5f3; --panel:#ffffff; --panel-2:#eeeeea; --rule:#dcdcd8; --ink:#1d1d1c; --ink-2:#4e4e49; --ink-3:#6b6b64;
  --accent:#a52c19; --action:#c13822; --on-action:#fff; --good:#1f7a45; --good-soft:#e3f2e8; --bad:#b3261e; --bad-soft:#f8e4e1;
  --shadow:0 1px 2px rgba(20,20,15,.06), 0 12px 32px -12px rgba(20,20,15,.18); --lift:0 2px 4px rgba(20,20,15,.06), 0 22px 44px -16px rgba(20,20,15,.28); color-scheme:light; }
:root[data-theme="dark"] { --ground:#161615; --panel:#212120; --panel-2:#2a2a28; --rule:#3a3a37; --ink:#f3f3ec; --ink-2:#cfcfc5; --ink-3:#a3a399;
  --accent:#ffa18d; --action:#c13a25; --good:#7fd19b; --good-soft:#1e3326; --bad:#f5a1a1; --bad-soft:#3a2220;
  --shadow:0 1px 2px rgba(0,0,0,.3), 0 14px 34px -14px rgba(0,0,0,.7); --lift:0 2px 4px rgba(0,0,0,.35), 0 26px 50px -18px rgba(0,0,0,.85); color-scheme:dark; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --ground:#161615; --panel:#212120; --panel-2:#2a2a28; --rule:#3a3a37; --ink:#f3f3ec; --ink-2:#cfcfc5; --ink-3:#a3a399;
  --accent:#ffa18d; --action:#c13a25; --good:#7fd19b; --good-soft:#1e3326; --bad:#f5a1a1; --bad-soft:#3a2220;
  --shadow:0 1px 2px rgba(0,0,0,.3), 0 14px 34px -14px rgba(0,0,0,.7); --lift:0 2px 4px rgba(0,0,0,.35), 0 26px 50px -18px rgba(0,0,0,.85); color-scheme:dark; } }
* { box-sizing: border-box; }
html { background: var(--ground); }
body { margin: 0; min-height: 100vh; background: var(--ground); color: var(--ink);
  font: 400 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI Variable Text", "Segoe UI", "Barlow", system-ui, sans-serif;
  -webkit-font-smoothing: antialiased; }
::selection { background: var(--action); color: var(--on-action); }
button { font: inherit; color: inherit; cursor: pointer; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; border-radius: 10px; }

.bar { position: sticky; top: 0; z-index: 5; display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: 16px;
  padding: 14px 24px; background: color-mix(in srgb, var(--ground) 86%, transparent); backdrop-filter: saturate(1.4) blur(14px); -webkit-backdrop-filter: saturate(1.4) blur(14px);
  border-bottom: 1px solid color-mix(in srgb, var(--rule) 60%, transparent); }
.brand { display: flex; flex-direction: column; gap: 2px; }
.brand b { font: 400 21px/1.1 Georgia, serif; letter-spacing: -.01em; }
.brand span { font-size: 12.5px; color: var(--ink-3); }
.dots { display: flex; gap: 6px; align-items: center; }
.dots i { width: 8px; height: 8px; border-radius: 50%; background: var(--rule); transition: transform .3s cubic-bezier(.2,.8,.2,1), background .3s; }
.dots i.now { background: var(--ink); transform: scale(1.35); }
.dots i.right { background: var(--ink-3); }
.dots i.fooled { background: var(--action); }
.back { justify-self: end; font-size: 14px; font-weight: 500; color: var(--ink-2); text-decoration: none; padding: 8px 12px; border-radius: 10px; }
.back:hover { background: var(--panel-2); color: var(--ink); }

.stage { max-width: 1040px; margin: 0 auto; padding: 48px 24px 120px; }
.screen { animation: rise .45s cubic-bezier(.16,1,.3,1); }
@keyframes rise { from { opacity: 0; transform: translateY(14px); } }

.intro { max-width: 620px; margin: 8vh auto 0; text-align: center; }
.intro h1 { margin: 0 0 14px; font: 400 clamp(36px, 5vw, 54px)/1.08 Georgia, serif; letter-spacing: -.02em; }
.intro p { margin: 0 auto 10px; max-width: 46ch; font-size: 18px; color: var(--ink-2); }
.intro .note { margin-top: 18px; max-width: none; font-size: 14px; color: var(--ink-3); }
.cta { display: inline-flex; align-items: center; gap: 10px; margin-top: 30px; padding: 15px 30px; border: 0; border-radius: 999px;
  background: var(--action); color: var(--on-action); font-size: 17px; font-weight: 600; box-shadow: var(--shadow); transition: transform .2s, box-shadow .2s; }
.cta:hover { transform: translateY(-1px); box-shadow: var(--lift); }
.cta:active { transform: translateY(0) scale(.98); }
.ghost { display: inline-flex; align-items: center; padding: 13px 22px; border: 1px solid var(--rule); border-radius: 999px; background: var(--panel); color: var(--ink); font-size: 15px; font-weight: 500; }
.ghost:hover { border-color: var(--ink-3); }
kbd { display: inline-block; min-width: 22px; padding: 1px 6px; border: 1px solid var(--rule); border-bottom-width: 2px; border-radius: 6px;
  font: 600 12px/1.5 inherit; color: var(--ink-2); background: var(--panel); text-align: center; }

.round-head { margin: 0 auto 28px; max-width: 760px; text-align: center; }
.round-head small { display: block; margin-bottom: 8px; font-size: 13px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; color: var(--ink-3); }
.round-head h2 { margin: 0 0 8px; font: 400 clamp(26px, 3.4vw, 34px)/1.2 Georgia, serif; letter-spacing: -.01em; }
.round-head p { margin: 0; font-size: 16px; color: var(--ink-2); }
.pair { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; align-items: stretch; }
.msg { position: relative; display: flex; flex-direction: column; gap: 14px; min-height: 180px; padding: 22px 24px 24px; text-align: left;
  background: var(--panel); border: 1.5px solid var(--rule); border-radius: 22px; box-shadow: var(--shadow);
  transition: transform .25s cubic-bezier(.2,.8,.2,1), box-shadow .25s, border-color .25s, opacity .35s; }
.msg:not([disabled]):hover { transform: translateY(-3px); box-shadow: var(--lift); border-color: var(--ink-3); }
.msg[disabled] { cursor: default; }
.msg .side { display: flex; align-items: center; justify-content: space-between; font-size: 13px; font-weight: 700; letter-spacing: .08em; color: var(--ink-3); }
.msg .side kbd { font-size: 11px; }
.msg .text { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 17px; line-height: 1.55; color: var(--ink); }
.msg .tag { display: none; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; font-size: 12.5px; font-weight: 600; letter-spacing: 0; }
.revealed .msg .tag { display: inline-flex; }
.revealed .msg.real .tag { background: var(--good-soft); color: var(--good); }
.revealed .msg.model .tag { background: var(--panel-2); color: var(--ink-2); }
.revealed .msg.model { opacity: .72; }
.revealed .msg.picked { border-color: var(--ink); }
.revealed .msg.picked.model { border-color: var(--action); opacity: 1; }

.verdict { display: flex; flex-direction: column; align-items: center; gap: 6px; margin: 28px auto 0; text-align: center; }
.verdict b { font: 400 30px/1.2 Georgia, serif; }
.verdict.right b { color: var(--ink); }
.verdict.fooled b { color: var(--accent); }
.verdict span { color: var(--ink-2); }
.why { max-width: 760px; margin: 22px auto 0; padding: 20px 22px; background: var(--panel); border: 1px solid var(--rule); border-radius: 20px; }
.why h3 { margin: 0 0 12px; font-size: 15px; font-weight: 600; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; }
.chip { padding: 8px 14px; border: 1px solid var(--rule); border-radius: 999px; background: var(--ground); font-size: 14px; font-weight: 500; color: var(--ink-2);
  transition: background .15s, border-color .15s, color .15s; }
.chip:hover { border-color: var(--ink-3); color: var(--ink); }
.chip[aria-pressed="true"] { background: var(--ink); border-color: var(--ink); color: var(--ground); }
.why input, .final textarea { width: 100%; margin-top: 12px; padding: 12px 14px; border: 1px solid var(--rule); border-radius: 14px; background: var(--ground);
  color: var(--ink); font: inherit; }
.why input:focus, .final textarea:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 22%, transparent); }
.clean { margin-top: 14px; font-size: 14px; color: var(--ink-3); }
.clean summary { cursor: pointer; }
.clean p { margin: 8px 0 0; white-space: pre-wrap; color: var(--ink-2); }
.next { display: flex; justify-content: center; align-items: center; gap: 14px; margin-top: 26px; color: var(--ink-3); font-size: 14px; }

.final { max-width: 720px; margin: 4vh auto 0; text-align: center; }
.final .score { font: 400 clamp(64px, 11vw, 112px)/1 Georgia, serif; letter-spacing: -.03em; }
.final .score span { font-size: .32em; letter-spacing: 0; color: var(--ink-3); }
.final h1 { margin: 10px 0 8px; font: 400 26px/1.3 Georgia, serif; }
.final p { margin: 0 auto; max-width: 52ch; color: var(--ink-2); font-size: 17px; }
.rounds { display: flex; justify-content: center; flex-wrap: wrap; gap: 8px; margin: 28px 0; }
.rounds button { display: grid; border: 0; cursor: pointer; place-items: center; width: 34px; height: 34px; border-radius: 50%; font: 600 13px/1 inherit; font-style: normal;
  background: var(--panel-2); color: var(--ink-2); }
.rounds button.fooled { background: var(--action); color: var(--on-action); }
.final label { display: block; margin-top: 8px; text-align: left; font-weight: 600; font-size: 15px; }
.saved { margin-top: 14px; min-height: 24px; font-size: 14px; color: var(--ink-3); }
.saved.ok { color: var(--good); }
.saved.err { color: var(--bad); }
.actions { display: flex; justify-content: center; align-items: center; gap: 12px; margin-top: 18px; flex-wrap: wrap; }

.round-nav { display: flex; justify-content: space-between; align-items: center; max-width: 1040px; margin: 0 auto 10px; min-height: 36px; }
.linkbtn { display: inline-flex; align-items: center; gap: 6px; padding: 8px 12px; border: 0; border-radius: 10px; background: none; color: var(--ink-2); font-size: 14px; font-weight: 500; text-decoration: none; }
.linkbtn:hover { background: var(--panel-2); color: var(--ink); }
.linkbtn[disabled] { visibility: hidden; }
.resume { display: flex; justify-content: center; gap: 10px; flex-wrap: wrap; }
.review { max-width: 1040px; margin: 0 auto; }
.review-head { text-align: center; margin-bottom: 34px; }
.review-head .score { font: 400 clamp(56px, 9vw, 88px)/1 Georgia, serif; letter-spacing: -.03em; }
.review-head .score span { font-size: .32em; letter-spacing: 0; color: var(--ink-3); }
.review-head h1 { margin: 10px 0 6px; font: 400 26px/1.3 Georgia, serif; }
.review-head p { margin: 0; color: var(--ink-2); }
.rv { margin: 0 0 22px; padding: 22px; background: var(--panel); border: 1px solid var(--rule); border-radius: 22px; }
.rv-top { display: flex; justify-content: space-between; gap: 12px; align-items: baseline; margin-bottom: 14px; }
.rv-top small { font-size: 12.5px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; color: var(--ink-3); }
.rv-top b { font-size: 14px; }
.rv-top b.fooled { color: var(--accent); }
.rv-sit { margin: 0 0 14px; color: var(--ink-2); }
.rv .pair .msg { min-height: 0; box-shadow: none; cursor: default; }
.rv .msg.real .tag { display: inline-flex; background: var(--good-soft); color: var(--good); }
.rv .msg.model .tag { display: inline-flex; background: var(--panel-2); color: var(--ink-2); }
.rv .msg.picked { border-color: var(--ink); }
.rv .msg.picked.model { border-color: var(--action); }
.rv-note { margin: 14px 0 0; padding: 12px 14px; border-radius: 14px; background: var(--ground); color: var(--ink-2); font-size: 14.5px; }
.rv-note b { color: var(--ink); }
.pending { max-width: 620px; margin: 0 auto 26px; padding: 14px 16px; border-radius: 14px; background: var(--bad-soft); color: var(--ink); font-size: 14.5px; text-align: center; }
.pending.ok { background: var(--good-soft); }
@media (max-width: 760px) {
  .bar { grid-template-columns: 1fr auto; padding: 12px 16px; }
  .dots { grid-column: 1 / -1; grid-row: 2; justify-content: center; }
  .stage { padding: 28px 16px 90px; }
  .pair { grid-template-columns: 1fr; gap: 14px; }
  .msg { min-height: 0; border-radius: 18px; }
}
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation: none !important; transition: none !important; } }
</style>
</head>
<body>
<header class="bar">
  <div class="brand"><b>__TITLE__</b><span id="sub"></span></div>
  <div class="dots" id="dots" aria-hidden="true"></div>
  <a class="back" href="/#guide">Back to the dashboard</a>
</header>
<main class="stage" id="stage" aria-live="polite"></main>
<script>
const D = __DATA__;
const n = D.pairs.length;
const KEY = "bt-progress-" + D.test, PENDING = "bt-pending-" + D.test;
const store = {
  get(k) { try { return JSON.parse(localStorage.getItem(k)); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
  drop(k) { try { localStorage.removeItem(k); } catch (e) {} },
};
const params = new URLSearchParams(location.search);
let st = { i: -1, picks: [], notes: [] };
const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const day = (iso) => new Date(iso).toLocaleDateString([], { month: "long", day: "numeric" });
$("#sub").textContent = `${n} rounds · made ${day(D.created)}`;
const saveProgress = () => store.set(KEY, st);

function dots() {
  $("#dots").innerHTML = D.pairs.map((p, k) => {
    const pick = st.picks[k];
    const cls = k === st.i ? "now" : pick == null ? "" : (pick === p.real ? "right" : "fooled");
    return `<i class="${cls}"></i>`;
  }).join("");
}

function noteText(k) {
  const nt = st.notes[k] || {};
  return [...(nt.tags || []), nt.text || ""].filter(Boolean).join("; ");
}

/* ---------- review of a finished test (read only) */

function review() {
  const r = D.done;
  st.i = -1;
  $("#dots").innerHTML = "";
  $("#stage").innerHTML = `<section class="screen review">
    <div class="review-head">
      <div class="score">${r.fooled}<span> of ${r.pairs}</span></div>
      <h1>The model fooled you ${r.fooled} ${r.fooled === 1 ? "time" : "times"}</h1>
      <p>Taken ${esc(day(r.answered))}. This is a read-only record of every round.</p>
      ${r.note ? `<p class="rv-note" style="max-width:640px;margin:16px auto 0"><b>Your note:</b> ${esc(r.note)}</p>` : ""}
      <div class="resume" style="margin-top:22px"><a class="ghost" href="?id=${encodeURIComponent(D.test)}&retake=1" style="text-decoration:none">Take it again</a>
        <a class="cta" href="/#guide" style="text-decoration:none;margin-top:0">Back to the dashboard</a></div>
    </div>
    ${D.pairs.map((p, k) => {
      const rv = r.review[p.id] || {};
      const fooled = rv.picked && rv.picked !== p.real;
      return `<article class="rv">
        <div class="rv-top"><small>Round ${k + 1}${p.mode ? " · " + esc(p.mode) : ""}</small><b class="${fooled ? "fooled" : ""}">${!rv.picked ? "Not answered" : fooled ? "It fooled you" : "You spotted it"}</b></div>
        <p class="rv-sit">${esc(p.situation)}</p>
        <div class="pair">${["A", "B"].map((s) => `<div class="msg ${s === p.real ? "real" : "model"} ${rv.picked === s ? "picked" : ""}">
          <span class="side">${s}<span class="tag">${s === p.real ? "You wrote this" : "Model"}</span>${rv.picked === s ? "<span>Your pick</span>" : "<span></span>"}</span>
          <p class="text">${esc(s === "A" ? p.a : p.b)}</p></div>`).join("")}</div>
        ${rv.note ? `<p class="rv-note"><b>Your note:</b> ${esc(rv.note)}</p>` : ""}
      </article>`;
    }).join("")}
  </section>`;
}

/* ---------- taking the test */

function intro() {
  st.i = -1;
  const saved = store.get(KEY);
  const answered = saved && saved.picks ? saved.picks.filter((x) => x != null).length : 0;
  const again = D.done ? `<p class="note">You took this test on ${esc(day(D.done.answered))}: the model fooled you ${D.done.fooled} of ${D.done.pairs} times. Finishing it again replaces that result.</p>` : "";
  const resume = answered && answered < n
    ? `<div class="resume"><button class="cta" id="cont" type="button">Continue · round ${Math.min(answered + 1, n)} of ${n}</button><button class="ghost" id="over" type="button" style="margin-top:30px">Start over</button></div>`
    : `<button class="cta" id="go" type="button">Start · ${n} rounds</button>`;
  $("#stage").innerHTML = `<section class="screen intro">
    <h1>Which one did you write?</h1>
    <p>Each round shows two messages for the same situation. One is yours. The other was written by a model that only read your writing profile.</p>
    <p>The more often it fools you, the better the profile sounds like you.</p>
    ${resume}
    <p class="note">Pick with <kbd>A</kbd> <kbd>B</kbd> or <kbd>←</kbd> <kbd>→</kbd>, then <kbd>Enter</kbd> for the next round. Your progress is kept if you close this page.</p>
    ${again}
  </section>`;
  if ($("#go")) $("#go").onclick = () => { st = { i: 0, picks: [], notes: [] }; saveProgress(); round(0); };
  if ($("#cont")) $("#cont").onclick = () => { st = saved; round(Math.min(answered, n - 1)); };
  if ($("#over")) $("#over").onclick = () => { st = { i: 0, picks: [], notes: [] }; saveProgress(); round(0); };
  dots();
}

function round(k) {
  st.i = k;
  saveProgress();
  const p = D.pairs[k];
  $("#stage").innerHTML = `<div class="round-nav">
      <button type="button" class="linkbtn" id="prev" ${k === 0 ? "disabled" : ""}>← Previous round</button>
      <span></span></div>
    <section class="screen" id="round">
    <div class="round-head"><small>Round ${k + 1} of ${n}${p.mode ? " · " + esc(p.mode) : ""}</small>
      <h2>Which one did you write?</h2><p>${esc(p.situation)}</p></div>
    <div class="pair">
      ${["A", "B"].map((s) => `<button type="button" class="msg ${s === p.real ? "real" : "model"}" data-side="${s}">
        <span class="side">${s}<span class="tag">${s === p.real ? "You wrote this" : "Model"}</span><kbd>${s}</kbd></span>
        <p class="text">${esc(s === "A" ? p.a : p.b)}</p></button>`).join("")}
    </div>
    <div id="after"></div>
  </section>`;
  $("#prev").onclick = () => k > 0 && round(k - 1);
  document.querySelectorAll(".msg").forEach((b) => (b.onclick = () => pick(b.dataset.side)));
  if (st.picks[k] != null) reveal(k);
  dots();
}

function pick(side) {
  const k = st.i;
  if (k < 0 || k >= n || st.picks[k] != null) return;
  st.picks[k] = side;
  st.notes[k] = st.notes[k] || { tags: [], text: "" };
  saveProgress();
  reveal(k, true);
}

function reveal(k, fresh) {
  const p = D.pairs[k], side = st.picks[k];
  const right = side === p.real;
  const nt = st.notes[k] || (st.notes[k] = { tags: [], text: "" });
  const r = $("#round");
  r.classList.add("revealed");
  r.querySelector(`.msg[data-side="${side}"]`).classList.add("picked");
  r.querySelectorAll(".msg").forEach((b) => (b.disabled = true));
  const last = k === n - 1;
  $("#after").innerHTML = `
    <div class="verdict ${fresh ? "screen" : ""} ${right ? "right" : "fooled"}"><b>${right ? "Right, that one is yours." : "It fooled you."}</b>
      <span>${right ? "What gave the model away?" : "The model's message passed for yours. Anything still off about it?"}</span></div>
    <div class="why ${fresh ? "screen" : ""}">
      <h3>${right ? "Pick what gave it away, write it, or both" : "Optional"}</h3>
      <div class="chips">${D.tags.map((t) => `<button type="button" class="chip" aria-pressed="${nt.tags.includes(t)}">${esc(t)}</button>`).join("")}</div>
      <input type="text" id="why" maxlength="300" placeholder="Anything else, in your words. A model reads this." value="${esc(nt.text)}">
      ${p.clean ? `<details class="clean"><summary>How the model would write it cleaned up</summary><p>${esc(p.clean)}</p></details>` : ""}
    </div>
    <div class="next"><button type="button" class="cta" id="next">${last ? "See your score" : "Next round"}</button><span><kbd>Enter</kbd></span></div>`;
  $("#after").querySelectorAll(".chip").forEach((c) => (c.onclick = () => {
    const on = c.getAttribute("aria-pressed") !== "true";
    c.setAttribute("aria-pressed", String(on));
    if (on) nt.tags.push(c.textContent); else nt.tags.splice(nt.tags.indexOf(c.textContent), 1);
    saveProgress();
  }));
  $("#why").oninput = (e) => { nt.text = e.target.value; saveProgress(); };
  $("#next").onclick = () => {
    if (last) return st.picks.filter((x) => x != null).length === n ? finish() : round(st.picks.findIndex((x) => x == null));
    round(k + 1);
  };
  if (fresh) {
    $("#next").focus({ preventScroll: true });
    $("#after").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "nearest" });
  }
  dots();
}

function finish() {
  st.i = n;
  const fooled = D.pairs.filter((p, k) => st.picks[k] !== p.real).length;
  const pct = Math.round((100 * fooled) / n);
  const line = fooled === 0 ? "You spotted every one. The profile still has a way to go."
    : pct < 30 ? "A start. Your notes on what gave it away go straight into the next profile."
    : pct < 50 ? "Getting close. Keep the notes coming and it will keep improving."
    : "That is hard to tell apart. The profile is doing its job.";
  $("#stage").innerHTML = `<section class="screen final">
    <div class="score">${fooled}<span> of ${n}</span></div>
    <h1>The model fooled you ${fooled} ${fooled === 1 ? "time" : "times"}</h1>
    <p>${line}</p>
    <div class="rounds">${D.pairs.map((p, k) => `<button type="button" class="${st.picks[k] !== p.real ? "fooled" : ""}" data-round="${k}" title="Round ${k + 1}: ${st.picks[k] !== p.real ? "fooled you" : "you were right"}. Open it.">${k + 1}</button>`).join("")}</div>
    <label for="overall">Anything else you noticed?</label>
    <textarea id="overall" rows="3" maxlength="2000" placeholder="Optional. A model reads this when it writes the next profile and test.">${esc(st.overall || "")}</textarea>
    <div class="saved" id="saved">Saving…</div>
    <div class="actions"><button type="button" class="ghost" id="resave" hidden>Save the note</button><a class="cta" href="/#guide" style="text-decoration:none;margin-top:0">Back to the dashboard</a></div>
  </section>`;
  document.querySelectorAll(".rounds [data-round]").forEach((b) => (b.onclick = () => round(+b.dataset.round)));
  $("#overall").oninput = (e) => { st.overall = e.target.value; saveProgress(); $("#resave").hidden = false; };
  $("#resave").onclick = () => save();
  dots();
  save();
}

function payload() {
  return { test: D.test, picks: D.pairs.map((p, k) => ({ id: p.id, picked: st.picks[k], note: noteText(k) })), note: st.overall || "" };
}

async function send(body) {
  const res = await fetch("/api/voice/test-result", { method: "POST", headers: { "Content-Type": "application/json", "X-Brain": "1" }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.status);
  return data;
}

async function save() {
  const out = $("#saved");
  const body = payload();
  store.set(PENDING, body);  // kept until the dashboard confirms, so a crash cannot lose the result
  try {
    await send(body);
    store.drop(PENDING);
    store.drop(KEY);
    out.textContent = "Saved to your test history.";
    out.className = "saved ok";
    $("#resave").hidden = true;
  } catch (e) {
    out.textContent = "Not saved yet: the dashboard is not answering. Your answers are kept on this computer and will be sent the next time you open this test.";
    out.className = "saved err";
    $("#resave").hidden = false;
  }
}

async function retryPending() {
  const body = store.get(PENDING);
  if (!body) return;
  const box = document.createElement("p");
  box.className = "pending";
  box.textContent = "Sending the answers from your last attempt…";
  $("#stage").prepend(box);
  try {
    await send(body);
    store.drop(PENDING);
    store.drop(KEY);
    box.className = "pending ok";
    box.textContent = "Your earlier answers were saved to the test history.";
  } catch (e) {
    box.textContent = "Your earlier answers are still waiting to be saved. Start the dashboard and reload this page.";
  }
}

document.addEventListener("keydown", (e) => {
  if (e.target.matches("input, textarea")) { if (e.key === "Enter" && e.target.id === "why") $("#next")?.click(); return; }
  const key = e.key.toLowerCase();
  if (st.i >= 0 && st.i < n && st.picks[st.i] == null) {
    if (key === "a" || e.key === "ArrowLeft") pick("A");
    if (key === "b" || e.key === "ArrowRight") pick("B");
  } else if (e.key === "Enter" && $("#next") && document.activeElement?.tagName !== "BUTTON") $("#next").click();
  else if (e.key === "Backspace" && $("#prev") && !$("#prev").disabled) $("#prev").click();
});

if (D.done && !params.get("retake")) review(); else intro();
retryPending();
</script>
</body>
</html>"""
