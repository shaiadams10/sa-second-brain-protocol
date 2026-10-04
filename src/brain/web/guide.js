"use strict";

/* Guide: the kneeboard. The vault's documents are sheets clipped to a board and tabbed by folder;
   GuideRender typesets each sheet. A laminated quick-reference card beside the board carries the
   voice study's live state and every command, copied with one click. Loaded before app.js, and
   uses its globals (S, $, esc, icon, api, toast, render, go) only when called. */

const GUIDE_TABS = [
  ["start", "Start here"], ["about", "About"], ["experience", "Experience"],
  ["career", "Career"], ["engine", "Engine"], ["results", "Results"],
];
const GUIDE_GROUP_OF = { "": "start", me: "about", experience: "experience", career: "career", brain: "engine", projects: "engine", ".brain/voice": "results" };
const GUIDE_HOW = {
  path: "guide:how-it-works", title: "How this brain works", group: "", modified: null, size: 0,
  text: `# How this brain works

Every week the engine reads your conversations with coding agents and the folders you work in, and writes what it learned into this vault. Nothing waits for approval. Your part is to read, and to strike what is wrong.

## The weekly run

1. **Collect.** Parsers read Codex, Claude Code, and Antigravity history and keep what you said and each final reply. Tool output, injected context, and secrets are dropped. No AI.
2. **Find projects.** Every folder in the projects root is classified as a project, a group, loose files, or a copy. No AI.
3. **Build the digest.** Each project gets an attention score from active days, conversations, file changes, and commits. Corrections, praise, and questions are always kept. No AI.
4. **Summarize.** A model you choose (Codex or Antigravity) answers in JSON against a fixed schema. Code, not the model, writes the vault.
5. **Learn.** An observation becomes part of you only when it recurs in two projects or two weeks. What you state about yourself counts at once.
6. **Commit.** Every run and every strike is a git commit, so anything can be traced or undone.

## The voice study

1. **Collect** every message you typed, from every local session and any web chat exports. No AI.
2. **Mark** each message in full: typed or pasted, what kind of message, every error with its fix.
3. **Combine** the marks into counts across conversations, sources, and months.
4. **Write** the profile: how you write, and an output layer that writes correct English in your shape.
5. **Test** it blind: a fresh model writes held-out messages, and you pick which one is really yours.

## Your part

- **Read the Log.** Once a week is enough. A healthy week needs nothing from you.
- **Strike what is wrong.** On the About and Projects pages. Struck items never come back.
- **Press Run now.** When you want the week in progress logged before Monday.
- **Copy a command.** From the card beside this sheet, whenever the terminal is quicker.
`,
};

const G = { data: null, loading: false, error: "", docs: {}, path: null, group: "start", timer: null, copied: new Set(), hint: "", swap: false, expanded: new Set(),
  commandsOpen: (() => { try { return localStorage.getItem("brain-guide-commands") === "open"; } catch (e) { return false; } })() };

/* ---------- data */

// One request at a time, shared by every caller: an early return here would resolve at once and
// let a caller re-render before the data exists, which re-requests forever.
function guideLoad() {
  if (G.loading) return G.loading;
  G.loading = (async () => {
    try {
      const res = await fetch("/api/guide");
      const data = await res.json();
      if (!data || !Array.isArray(data.docs)) throw new Error("bad shape");
      G.data = data;
      G.error = "";
    } catch (e) {
      if (!G.data) G.error = "The dashboard server is older than this page. Close the dashboard window and open it again to load the Guide.";
    } finally {
      G.loading = null;
    }
  })();
  return G.loading;
}

function guideDocs() {
  return [GUIDE_HOW, ...((G.data && G.data.docs) || [])];
}
function guideTabOf(doc) {
  return GUIDE_GROUP_OF[doc.group] || "engine";
}
function guideDocsIn(tab) {
  return guideDocs().filter((d) => guideTabOf(d) === tab);
}

async function guideFetchDoc(path) {
  if (path === GUIDE_HOW.path) return GUIDE_HOW;
  const meta = guideDocs().find((d) => d.path === path);
  const cached = G.docs[path];
  if (cached && meta && cached.modified === meta.modified) return cached;
  const res = await fetch(`/api/doc?path=${encodeURIComponent(path)}`);
  if (!res.ok) throw new Error("not found");
  const doc = await res.json();
  G.docs[path] = doc;
  return doc;
}

function guideRoute(arg) {
  const docs = guideDocs();
  const wanted = arg && docs.find((d) => d.path === arg);
  const doc = wanted || docs.find((d) => d.path === G.path) || GUIDE_HOW;
  if (G.path !== doc.path) G.swap = true;
  G.path = doc.path;
  G.group = guideTabOf(doc);
}

/* ---------- view */

function viewGuide() {
  if (!G.data && !G.error) {
    guideLoad().then(() => { if (S.tab === "guide") { guideRoute(guideHashArg()); render(); } });
    return guideShell(`<div class="kb-sheet kb-quiet"><p>Unclipping the sheets…</p></div>`, "");
  }
  if (G.error) return guideShell(`<div class="kb-sheet kb-quiet"><h1 class="kb-title">The Guide needs a restart</h1><p class="kb-lede">${esc(G.error)}</p></div>`, "");
  guideRoute(guideHashArg());
  guideStartPolling();
  setTimeout(guidePaintSheet, 0);
  return guideShell(`<article class="kb-sheet" id="kb-sheet" aria-live="polite"><p class="kb-quiet">Reading…</p></article>`, guideCard());
}

function guideHashArg() {
  const [tab, ...rest] = decodeURIComponent(location.hash.slice(1)).split("/");
  return tab === "guide" && rest.length ? rest.join("/") : null;
}

function guideShell(sheet, card) {
  const tabs = GUIDE_TABS.filter(([id]) => id === "start" || guideDocsIn(id).length);
  const half = Math.ceil(tabs.length / 2);
  const tabHtml = (list) => list.map(([id, label]) => {
    const n = guideDocsIn(id).length;
    return `<button type="button" class="kb-tab" role="tab" aria-selected="${G.group === id}" data-kb-tab="${id}" title="${n} ${n === 1 ? "sheet" : "sheets"}">${esc(label)}</button>`;
  }).join("");
  return `
  <div class="kb">
    <div class="kb-board">
      <img class="kb-clip kb-clip-m" src="assets/plates/clip.png" alt="" width="994" height="523" decoding="async">
      <div class="kb-tabs" role="tablist" aria-label="Document groups">
        <div class="kb-tabs-l">${tabHtml(tabs.slice(0, half))}</div>
        <div class="kb-tabs-gap" aria-hidden="true"><img class="kb-clip" src="assets/plates/clip.png" alt="" width="994" height="523" decoding="async"></div>
        <div class="kb-tabs-r">${tabHtml(tabs.slice(half))}</div>
      </div>
      <div class="kb-stack">${sheet}</div>
    </div>
    ${card ? `<div class="kb-rail"><aside class="kb-card" id="kb-card" aria-label="Quick reference">${card}</aside></div>` : ""}
  </div>`;
}

async function guidePaintSheet() {
  if (!document.getElementById("kb-sheet")) return;
  const path = G.path;
  let doc;
  try {
    doc = await guideFetchDoc(path);
  } catch (e) {
    const missing = document.getElementById("kb-sheet");
    if (missing) missing.innerHTML = `<h1 class="kb-title">Sheet not found</h1><p class="kb-lede">${esc(path)} is no longer in the vault. Pick another sheet from the tabs.</p>`;
    return;
  }
  // Look the sheet up again: the view may have re-rendered while the document loaded.
  const el = document.getElementById("kb-sheet");
  if (path !== G.path || !el) return;
  const known = new Set(guideDocs().map((d) => d.path));
  let out;
  try {
    out = GuideRender.render(doc, { path: doc.path, known, idPrefix: "kb" });
  } catch (err) {
    out = { title: doc.title || doc.path, lede: "", words: 0, html: `<pre class="g-raw">${esc(doc.text || "")}</pre>` };
  }
  const siblings = guideDocsIn(G.group);
  const at = siblings.findIndex((d) => d.path === doc.path);
  const minutes = Math.max(1, Math.round(out.words / 220));
  const step = (d, dir, label) => d
    ? `<a class="kb-step" href="#guide/${encodeURIComponent(d.path)}" aria-label="${label}: ${esc(d.title)}" title="${esc(d.title)}">${icon(dir)}</a>`
    : `<span class="kb-step off" aria-hidden="true">${icon(dir)}</span>`;
  const index = siblings.length > 1 ? `
    <nav class="kb-index" aria-label="Sheets on this tab">
      <ol>${siblings.map((d, n) => `<li><a href="#guide/${encodeURIComponent(d.path)}" ${d.path === doc.path ? 'aria-current="page"' : ""} title="${esc(d.title)}"><span>${n + 1}</span>${esc(guideShort(d))}</a></li>`).join("")}</ol>
      <span class="kb-stepper">${step(siblings[at - 1], "left", "Previous sheet")}${step(siblings[at + 1], "right", "Next sheet")}</span>
    </nav>` : "";
  const meta = [
    doc.path.startsWith("guide:") ? "Built into the dashboard" : `<code>${esc(doc.path)}</code>`,
    doc.modified ? `updated ${esc(guideAgo(doc.modified))}` : "",
    out.words ? `${minutes} min read` : "",
  ].filter(Boolean).join('<span class="kb-dot" aria-hidden="true"></span>');
  el.innerHTML = `${index}
    <header class="kb-head">
      <h1 class="kb-title">${esc(out.title)}</h1>
      <p class="kb-meta">${meta}</p>
      ${out.lede ? `<p class="kb-lede">${out.lede}</p>` : ""}
    </header>
    <div class="kb-body" id="kb-body">${out.html}</div>
    <div class="kb-fold" id="kb-fold" hidden><button type="button" class="kb-fold-btn" data-kb-expand="${esc(doc.path)}">Show the whole sheet<small>${minutes} min read</small></button></div>`;
  const body = document.getElementById("kb-body");
  if (body && !G.expanded.has(doc.path) && body.scrollHeight > 1500) {
    body.classList.add("is-folded");
    document.getElementById("kb-fold").hidden = false;
  }
  // On narrow screens the tab strip scrolls: keep the active tab in view without moving the page.
  const strip = document.querySelector(".kb-tabs");
  const active = strip && strip.querySelector('.kb-tab[aria-selected="true"]');
  if (strip && active && strip.scrollWidth > strip.clientWidth) {
    strip.scrollLeft = active.offsetLeft - (strip.clientWidth - active.offsetWidth) / 2;
  }
  if (G.swap) {
    G.swap = false;
    el.classList.remove("kb-in");
    void el.offsetWidth;
    el.classList.add("kb-in");
  }
}

function guideShort(d) {
  if (d.path.startsWith("guide:")) return "How it works";
  const stem = d.path.split("/").pop().replace(/\.(md|json)$/, "");
  const named = { README: "Read me", AGENTS: "Agents", ACCESS: "Access", "writing-style.draft": "Profile draft", "write-notes": "Writer's notes",
    "voice-tests": "Blind tests", "voice-study": "Voice study", "voice-guidance": "Voice guidance", "pasted-review": "Pasted review",
    "corpus-summary": "Corpus summary", "open-questions": "Open questions" };
  return named[stem] || stem.replace(/[-_.]/g, " ").replace(/^./, (c) => c.toUpperCase());
}

function guideAgo(iso) {
  const ms = Date.now() - new Date(iso).getTime();
  const m = Math.round(ms / 60000);
  if (m < 2) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 36) return `${h} h ago`;
  return new Date(iso).toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
}

/* ---------- the quick-reference card */

const GUIDE_STATIONS = [["Collect", "corpus.jsonl"], ["Mark", null], ["Combine", "findings.md"], ["Write", "writing-style.draft.md"], ["Test", "blind-test.html"]];

function guideLive() {
  const v = G.data && G.data.voice;
  if (!v) {
    return `<section class="kb-sec"><h2>Voice study</h2><p class="kb-live-sub">Not started yet. Run <code>sbrain corpus</code>, then the study script.</p></section>`;
  }
  const r = v.run || {};
  const pct = v.total ? Math.floor((100 * v.marked) / v.total) : 0;
  const files = v.files || {};
  let state = "done", line = "", sub = "", action = "";
  if (r.step === "write") {
    state = "live"; line = "Writing the profile"; sub = "Sol is reading the findings. About 6 minutes.";
  } else if (r.step === "test") {
    state = "live"; line = "Building a blind test"; sub = "Marking new messages, then writing the other side of each pair. A few minutes.";
  } else if (r.running) {
    state = "live"; line = `Marking ${pct}%${r.eta ? `, about ${esc(r.eta)} left` : ""}`;
    sub = `${v.marked.toLocaleString("en-US")} of ${v.total.toLocaleString("en-US")} messages · batch ${r.finished || 0}/${r.batches || "?"}`;
  } else if (r.account_stop) {
    state = "stopped"; line = "Stopped: out of Codex usage"; sub = "Sign in with another account (codex logout, then codex login), then resume."; action = "resume";
  } else if (v.marked < v.total) {
    state = "paused"; line = `Paused at ${pct}%`; sub = `${(v.total - v.marked).toLocaleString("en-US")} messages still to mark.`; action = "resume";
  } else if (files["writing-style.draft.md"]) {
    line = "Profile drafted"; sub = "Waiting for your review.";
  } else {
    state = "paused"; line = "Marking done"; sub = "The profile is still to be written."; action = "resume";
  }
  const busy = state === "live";
  const bar = busy && r.running ? `<div class="kb-bar" role="progressbar" aria-label="Messages marked" aria-valuemin="0" aria-valuemax="${v.total}" aria-valuenow="${v.marked}"><span style="transform:scaleX(${(pct / 100).toFixed(3)})"></span></div>` : "";
  const study = `<section class="kb-sec kb-live is-${state}">
    <h2>Voice study</h2>
    <p class="kb-live-line">${busy ? '<i class="kb-pulse" aria-hidden="true"></i>' : ""}${line}</p>
    ${bar}
    <p class="kb-live-sub">${sub}</p>
    ${files["writing-style.draft.md"] && !busy ? `<a href="#guide/${encodeURIComponent(".brain/voice/writing-style.draft.md")}" class="kb-link">Read the profile draft</a>` : ""}
    ${action ? `<button type="button" class="btn primary small kb-resume" data-kb-resume>${icon("run")}Resume the study</button>` : ""}
  </section>`;

  const t = v.tests || {};
  const hist = (t.history || []).slice(-8);
  const last = hist[hist.length - 1];
  let testAction;
  if (r.step === "test") testAction = `<p class="kb-live-sub"><i class="kb-pulse" aria-hidden="true"></i>A new test is being built in its own window. Closing that window cancels it.</p>`;
  else if (t.open) testAction = `<a class="btn primary kb-test-cta" href="/voice/test" target="_blank" rel="noopener">${icon("run")}Take the blind test<small>${t.open_pairs} rounds · about ${Math.max(2, Math.round(t.open_pairs / 4))} min</small></a>`;
  else testAction = `<button type="button" class="kb-test-cta" data-kb-newtest ${busy ? "disabled" : ""}>Start a new blind test<small>Builds 10 rounds from messages you have not seen, in its own window</small></button>`;
  const chart = hist.length > 1 ? `<ol class="kb-test-hist" aria-label="Blind test scores">${hist.map((h) => {
      const p = Math.round((100 * h.fooled) / Math.max(h.pairs, 1));
      const top = Math.max(50, ...hist.map((x) => Math.round((100 * x.fooled) / Math.max(x.pairs, 1))));
      return `<li title="${esc(h.week)}: fooled you ${h.fooled} of ${h.pairs}"><span>${p}%</span><i style="height:${Math.max(4, Math.round((80 * p) / top))}%"></i></li>`;
    }).join("")}</ol>` : "";
  const stopped = r.interrupted && !busy ? `<p class="kb-note">The last ${r.interrupted === "test" ? "test build" : r.interrupted === "write" ? "profile write" : "marking run"} stopped before it finished, probably because its window was closed. Nothing finished was lost.</p>` : "";
  const tests = `<section class="kb-sec kb-tests">
    <h2>Blind tests</h2>
    <p class="kb-live-sub">${last ? `Last score: the model fooled you <b>${last.fooled} of ${last.pairs}</b> times (${esc(last.week)}).` : "Pick your real message out of pairs. The more the model fools you, the better the profile."}</p>
    ${chart}
    ${testAction}
    ${r.interrupted === "test" ? stopped : ""}
    ${hist.length ? `<a href="#guide/${encodeURIComponent("brain/voice-tests.md")}" class="kb-link">Every test and what gave it away</a>` : ""}
  </section>`;
  return study + tests;
}

function guideCard() {
  const groups = (G.data && G.data.commands) || [];
  const count = groups.reduce((a, g) => a + g.items.length, 0);
  const list = groups.map((g) => `
    <li class="kb-cgroup"><h3>${esc(g.title)}</h3>
      <ul>${g.items.map((c) => `
        <li><button type="button" class="kb-cmd ${G.copied.has(c.cmd) ? "done" : ""}" data-kb-copy="${esc(c.cmd)}" data-kb-what="${esc(c.what)}" aria-label="Copy ${esc(c.cmd)}: ${esc(c.what)}">
          <span class="kb-box" aria-hidden="true"><svg viewBox="0 0 12 12"><path d="M2.5 6.2 5 8.6l4.6-5.2"/></svg></span>
          <code title="${esc(c.cmd)}">${esc(c.cmd)}</code><span class="kb-lead" aria-hidden="true"></span><span class="kb-copy">${G.copied.has(c.cmd) ? "Copied" : "Copy"}</span>
        </button></li>`).join("")}</ul></li>`).join("");
  return `<span class="kb-tab-l" aria-hidden="true"></span><span class="kb-tab-r" aria-hidden="true"></span>
    <div id="kb-live-slot">${guideLive()}</div>
    <section class="kb-sec kb-commands ${G.commandsOpen ? "open" : ""}">
      <button type="button" class="kb-disclose" data-kb-commands aria-expanded="${G.commandsOpen}">Commands<span>${count}</span>${icon("down")}</button>
      <div class="kb-commands-body" ${G.commandsOpen ? "" : "hidden"}>
        <ul class="kb-checklist" aria-label="Commands">${list}</ul>
        <p class="kb-hint" id="kb-hint">${esc(G.hint || "Pick a command to copy it. Hover one to see what it does.")}</p>
      </div>
    </section>`;
}

/* ---------- live refresh: only the card's live slot and the tab counts change while you read */

function guideStartPolling() {
  if (G.timer) return;
  G.timer = setInterval(async () => {
    if (S.tab !== "guide") { clearInterval(G.timer); G.timer = null; return; }
    const before = JSON.stringify(((G.data && G.data.docs) || []).map((d) => d.path));
    await guideLoad();
    const slot = document.getElementById("kb-live-slot");
    if (slot) slot.innerHTML = guideLive();
    const after = JSON.stringify(((G.data && G.data.docs) || []).map((d) => d.path));
    if (before !== after) render();
  }, 15000);
}

/* ---------- interaction */

document.addEventListener("click", async (e) => {
  const tab = e.target.closest("[data-kb-tab]");
  if (tab) {
    const first = guideDocsIn(tab.dataset.kbTab)[0];
    if (first) go("guide", first.path);
    return;
  }
  const copy = e.target.closest("[data-kb-copy], .g-cmd[data-copy]");
  if (copy) {
    const text = copy.dataset.kbCopy || copy.dataset.copy;
    try {
      await navigator.clipboard.writeText(text);
    } catch (err) {
      const t = document.createElement("textarea");
      t.value = text; document.body.appendChild(t); t.select();
      try { document.execCommand("copy"); } catch (e2) {}
      t.remove();
    }
    if (copy.dataset.kbCopy) {
      G.copied.add(text);
      copy.classList.add("done", "flash");
      copy.querySelector(".kb-copy").textContent = "Copied";
      setTimeout(() => copy.classList.remove("flash"), 900);
    } else {
      copy.classList.add("copied");
      setTimeout(() => copy.classList.remove("copied"), 1200);
    }
    return;
  }
  const expand = e.target.closest("[data-kb-expand]");
  if (expand) {
    G.expanded.add(expand.dataset.kbExpand);
    document.getElementById("kb-body")?.classList.remove("is-folded");
    document.getElementById("kb-fold").hidden = true;
    return;
  }
  if (e.target.closest("[data-kb-commands]")) {
    G.commandsOpen = !G.commandsOpen;
    try { localStorage.setItem("brain-guide-commands", G.commandsOpen ? "open" : "closed"); } catch (err) {}
    const sec = e.target.closest(".kb-commands");
    sec.classList.toggle("open", G.commandsOpen);
    sec.querySelector(".kb-commands-body").hidden = !G.commandsOpen;
    e.target.closest("[data-kb-commands]").setAttribute("aria-expanded", String(G.commandsOpen));
    return;
  }
  if (e.target.closest("[data-kb-newtest]")) {
    const btn = e.target.closest("[data-kb-newtest]");
    btn.disabled = true;
    try {
      const r = await api("/api/voice/new-test", {});
      toast(r.started ? "Building a new blind test in its own window. It shows up here when it is ready." : "Something is already running. Try again when it finishes.");
      await guideLoad();
      const slot = document.getElementById("kb-live-slot");
      if (slot) slot.innerHTML = guideLive();
    } catch (err) {
      toast(err.message, null, true);
      btn.disabled = false;
    }
    return;
  }
  if (e.target.closest("[data-kb-resume]")) {
    const btn = e.target.closest("[data-kb-resume]");
    btn.disabled = true;
    try {
      const r = await api("/api/voice/start", {});
      toast(r.started ? "The study is running in its own window. Close that window to stop it." : "It is already running, or this machine cannot open the study window.");
      await guideLoad();
      const slot = document.getElementById("kb-live-slot");
      if (slot) slot.innerHTML = guideLive();
    } catch (err) {
      toast(err.message, null, true);
      btn.disabled = false;
    }
  }
});

function guideShowHint(e) {
  const cmd = e.target.closest && e.target.closest("[data-kb-what]");
  const hint = document.getElementById("kb-hint");
  if (!cmd || !hint) return;
  G.hint = cmd.dataset.kbWhat;
  hint.textContent = G.hint;
}
document.addEventListener("mouseover", guideShowHint);
document.addEventListener("focusin", guideShowHint);

/* The tab's count: the voice study's progress while it runs, otherwise the number of sheets. */
function guideTabCount() {
  if (!G.data) { if (!G.loading && !G.error) guideLoad().then(() => { if (S.data) renderTabs(); }); return "sheets"; }
  const v = G.data.voice;
  if (v && v.run && v.run.running && v.total) return `${Math.floor((100 * v.marked) / v.total)}% marked`;
  return `${guideDocs().length} sheets`;
}


/* ---------- the blind test in the weekly Log, and a one-time nudge when a new test is waiting */

function blindTestCard(week) {
  const bt = S.data && S.data.blindTests;
  if (!bt) return "";
  const result = (bt.history || []).filter((h) => h.week === week).slice(-1)[0];
  const latest = S.data.weeks && S.data.weeks[0] && S.data.weeks[0].week === week;
  const waiting = bt.open && (bt.open_week === week || latest);
  const takeNew = waiting ? `<a class="btn primary bt-go" href="/voice/test" target="_blank" rel="noopener">${icon("run")}Take the new test · ${bt.open_pairs} rounds</a>` : "";
  if (result) {
    const pct = Math.round((100 * result.fooled) / Math.max(result.pairs, 1));
    return `<section class="bt-card${waiting ? " is-open" : ""}" aria-label="This week's blind test">
      <h2>This week's blind test</h2>
      <p class="bt-score"><b>${result.fooled}</b><span>of ${result.pairs}</span></p>
      <p class="bt-line">The model passed for you in ${result.fooled} of ${result.pairs} rounds (${pct}%).${result.note ? ` <q>${esc(result.note.length > 140 ? result.note.slice(0, 137) + "…" : result.note)}</q>` : ""}</p>
      ${takeNew}
      <a class="bt-link" href="#guide/${encodeURIComponent("brain/voice-tests.md")}">Every test so far</a>
    </section>`;
  }
  if (waiting) {
    return `<section class="bt-card is-open" aria-label="This week's blind test">
      <h2>This week's blind test</h2>
      <p class="bt-line">${bt.open_pairs} rounds are ready. In each one, pick the message you really wrote. About ${Math.max(2, Math.round(bt.open_pairs / 4))} minutes.</p>
      <a class="btn primary bt-go" href="/voice/test" target="_blank" rel="noopener">${icon("run")}Take the test</a>
    </section>`;
  }
  return "";
}

function blindTestPopover() {
  const bt = S.data && S.data.blindTests;
  if (!bt || !bt.open) return;
  try { if (localStorage.getItem("brain-bt-seen") === bt.open) return; } catch (e) {}
  const el = document.createElement("div");
  el.className = "bt-pop";
  el.setAttribute("role", "dialog");
  el.setAttribute("aria-label", "Blind test ready");
  el.innerHTML = `<p class="bt-pop-title">Your blind test is ready</p>
    <p>${bt.open_pairs} rounds. Pick the message you really wrote; the model wrote the other one from your profile. About ${Math.max(2, Math.round(bt.open_pairs / 4))} minutes.</p>
    <div class="bt-pop-actions"><a class="btn primary" href="/voice/test" target="_blank" rel="noopener" data-bt-close>Take it now</a><button type="button" class="btn ghost" data-bt-close>Later</button></div>`;
  document.body.appendChild(el);
  const close = () => { try { localStorage.setItem("brain-bt-seen", bt.open); } catch (e) {} el.classList.add("out"); setTimeout(() => el.remove(), 250); };
  el.querySelectorAll("[data-bt-close]").forEach((b) => b.addEventListener("click", close));
  document.addEventListener("keydown", function esc(e) { if (e.key === "Escape") { close(); document.removeEventListener("keydown", esc); } });
}
