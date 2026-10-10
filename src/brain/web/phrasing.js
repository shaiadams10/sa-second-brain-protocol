"use strict";

/* Phrase it better: the week's cards on saying a request shorter or with the right term, set as a
   proof sheet. Your words sit above, the better phrasing below in the serif of an editor's note.
   Practice mode hides each answer until you have tried your own version. Loaded before app.js,
   and uses its globals (S, $, esc, icon, api, toast, render, go) only when called. */

const PH_KIND = { term: "Right term", shorter: "Shorter", misread: "Misread" };
const PH_SOURCE = { codex: "Codex", "claude-code": "Claude Code", antigravity: "Antigravity" };
const PH_AREA = { ui: "UI and layout", code: "Code", git: "Git", data: "Data", agents: "AI agents", tools: "Tools and setup", english: "Everyday English" };
const PH_NUM = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve"];

const P = {
  data: null, loading: null, error: "", week: null, filter: "all",
  practice: (() => { try { return localStorage.getItem("brain-phrasing-practice") === "on"; } catch (e) { return false; } })(),
  revealed: new Set(), notes: new Set(), full: new Set(), more: new Set(), termsAll: false, sheetFilter: "open",
};
const PH_VERDICT = { got: "Got it", knew: "Knew this", not_useful: "Not useful" };
const PH_SHEET = "guide:phrasing";

/* ---------- data */

function phrasingLoad() {
  if (P.loading) return P.loading;
  P.loading = (async () => {
    try {
      const res = await fetch("/api/phrasing");
      const data = await res.json();
      if (!data || !Array.isArray(data.weeks)) throw new Error("bad shape");
      P.data = data;
      P.error = "";
    } catch (e) {
      if (!P.data) P.error = "The dashboard server is older than this page. Close the dashboard window and open it again.";
    } finally {
      P.loading = null;
    }
  })();
  return P.loading;
}

function phrasingTabCount() {
  const s = S.data && S.data.phrasing;
  if (!s || !s.week) return "new";
  return s.unread ? `${s.unread} to read` : `week ${weekNo(s.week)}`;
}

async function phrasingSend(body, message, undo) {
  try {
    const data = await api("/api/phrasing/feedback", body);
    P.data = data;
    if (S.data && data.summary) S.data.phrasing = data.summary;
    if (message) toast(message, undo);
  } catch (e) {
    toast(e.message, null, true);
  }
  phrasingRefresh(body.verdict !== undefined ? `[data-ph-verdict="${body.verdict || "got"}"][data-card="${CSS.escape(body.card)}"]` : null);
}

/* On the Guide sheet, repaint only the sheet and the card's count, so the page keeps its scroll
   position and focus; on the Phrase it better tab, render as usual. */
function phrasingRefresh(focus) {
  const sheet = S.tab === "guide" && document.getElementById("phs");
  if (!sheet) { render(); return; }
  sheet.innerHTML = phrasingSummarySheet();
  const slot = document.getElementById("ph-guide-slot");
  if (slot) slot.innerHTML = phrasingGuideCard();
  renderTabs();
  if (focus) { const el = sheet.querySelector(focus); if (el) el.focus({ preventScroll: true }); }
}

/* ---------- view */

function viewPhrasing() {
  if (!P.data && !P.error) {
    phrasingLoad().then(() => { if (S.tab === "phrasing") render(); });
    return `<div class="ph"><p class="ph-quiet">Sharpening the red pencil…</p></div>`;
  }
  if (P.error) return `<div class="ph"><div class="sheet empty"><h2>This tab needs a restart</h2><p>${esc(P.error)}</p></div></div>`;
  const weeks = P.data.weeks;
  if (!weeks.length) return phrasingEmpty();
  const arg = phrasingHashArg();
  if (arg && weeks.some((w) => w.week === arg)) P.week = arg;
  if (!weeks.some((w) => w.week === P.week)) P.week = weeks[0].week;
  const w = weeks.find((x) => x.week === P.week);
  const i = weeks.indexOf(w);
  const all = [...w.cards, ...w.returning];
  const count = (k) => all.filter((c) => (k === "earlier" ? c.returning : !c.returning && c.kind === k)).length;
  const shown = all.filter((c) => P.filter === "all" || (P.filter === "earlier" ? c.returning : !c.returning && c.kind === P.filter));
  const filters = [["all", "All", all.length], ["term", "Right term", count("term")], ["shorter", "Shorter", count("shorter")],
    ["misread", "Misread", count("misread")], ["earlier", "From earlier weeks", count("earlier")]].filter(([k, , n]) => k === "all" || n);
  const s = w.stats || {};
  const n = w.cards.length;

  return `<div class="ph">
    <header class="ph-head">
      <div>
        <p class="ph-kicker">Week ${weekNo(w.week)} · ${esc(weekSpan(w.week))}${w.partial ? " · in progress" : ""}</p>
        <h1 class="ph-title">${n ? `${PH_NUM[n] || n} ${n === 1 ? "way" : "ways"} to say it better` : "Nothing to rephrase this week"}</h1>
        <p class="ph-lede">From ${fmtNum(s.prompts)} prompts you typed to coding agents this week. Each card puts what you wrote next to a shorter or more exact way to say it, often in the words the agent used back.</p>
        ${phrasingOpenLink()}
      </div>
      <nav class="ph-weeks" aria-label="Weeks">
        <button class="btn ghost icon" type="button" data-ph-week="${esc((weeks[i + 1] || {}).week || "")}" aria-label="Earlier week" ${weeks[i + 1] ? "" : "disabled"}>${icon("left")}</button>
        <span>Week ${weekNo(w.week)}</span>
        <button class="btn ghost icon" type="button" data-ph-week="${esc((weeks[i - 1] || {}).week || "")}" aria-label="Later week" ${weeks[i - 1] ? "" : "disabled"}>${icon("right")}</button>
      </nav>
    </header>
    ${w.partial ? `<div class="banner">${icon("alert")}Week in progress. Monday's run builds this week again with every prompt, so some cards may change. Answers stay with any card that comes back.</div>` : ""}
    <div class="page ph-page">
      <section class="ph-deck" aria-label="Cards for week ${weekNo(w.week)}">
        <div class="ph-tools">
          <div class="ph-filters" role="group" aria-label="Show">
            ${filters.map(([k, label, c]) => `<button class="ph-chip" type="button" data-ph-filter="${k}" aria-pressed="${P.filter === k}">${esc(label)} <span>${c}</span></button>`).join("")}
          </div>
          <label class="ph-switch"><input type="checkbox" data-ph-practice ${P.practice ? "checked" : ""}><span class="ph-track" aria-hidden="true"></span>Practice: try it yourself first</label>
        </div>
        ${shown.length ? shown.map(phrasingCard).join("") : `<p class="ph-quiet">No cards of this kind this week.</p>`}
      </section>
      <aside class="margin ph-margin" aria-label="Your progress">
        ${phrasingStats(w)}
        ${phrasingTrend(w)}
        ${phrasingPatterns(w)}
        ${phrasingTerms(w)}
      </aside>
    </div>
  </div>`;
}

function phrasingHashArg() {
  const [tab, arg] = decodeURIComponent(location.hash.slice(1)).split("/");
  return tab === "phrasing" ? arg : null;
}

function phrasingEmpty() {
  return `<div class="ph"><div class="sheet empty ph-empty">
    <h2>Your first cards arrive with Monday's run</h2>
    <p>Each week the brain reads the prompts you typed to Codex, Claude Code, and Antigravity, compares them with how the agent said them back, and picks up to ten places where a shorter sentence or the right term would have done the job.</p>
    <p>To see cards now, run <code>sbrain phrasing week --week last</code> in the vault folder.</p>
  </div></div>`;
}

function phrasingWhen(iso) {
  const d = new Date(iso);
  return `${["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][d.getDay()]} ${MONTHS[d.getMonth()]} ${d.getDate()}`;
}

function phrasingCard(c) {
  const fb = c.feedback || {};
  const done = !!fb.verdict;
  const hidden = P.practice && !done && !fb.tried && !P.revealed.has(c.id);
  const full = P.full.has(c.id) && c.full;
  const said = full ? `<p class="ph-said-full">${esc(c.full)}</p>` : `<p class="ph-said-text">${esc(c.said)}</p>`;
  const meta = [PH_SOURCE[c.source] || c.source, c.project, phrasingWhen(c.at), plural(c.words, "word")].filter(Boolean).map(esc).join(" · ");
  const verdicts = [["got", "Got it"], ["knew", "I knew this"], ["not_useful", "Not useful"]];
  return `<article class="ph-card kind-${c.kind}${done ? " is-done" : ""}" aria-label="${esc(PH_KIND[c.kind])} card">
    <header class="ph-meta">
      <span class="ph-kind">${esc(PH_KIND[c.kind])}</span>
      ${c.returning ? `<span class="ph-back">Back from week ${weekNo(c.week)}</span>` : ""}
      <span class="ph-where">${meta}</span>
    </header>
    <div class="ph-said">
      <p class="ph-label">You said</p>
      ${said}
      ${c.full ? `<button class="ph-link" type="button" data-ph-full="${esc(c.id)}">${full ? "Show only the part this card is about" : `Show the whole message (${plural(c.words, "word")})`}</button>` : ""}
    </div>
    ${hidden ? `<div class="ph-try">
        <label class="ph-label" for="try-${esc(c.id)}">Your version</label>
        <textarea id="try-${esc(c.id)}" rows="2" placeholder="${c.kind === "term" ? "Say it with the right word" : "Say it in one or two sentences"}"></textarea>
        <button class="btn primary" type="button" data-ph-reveal="${esc(c.id)}">Show the answer</button>
      </div>` : phrasingAnswer(c, fb)}
    ${c.grammar && c.grammar.length ? `<p class="ph-grammar"><span>Grammar</span>${c.grammar.map((g) => `${esc(g.wrong)} <b aria-label="should be">→</b> ${esc(g.right)}`).join("<i>·</i>")}</p>` : ""}
    <footer class="ph-actions">
      <div class="ph-verdicts" role="group" aria-label="Your answer">
        ${verdicts.map(([v, label]) => `<button class="btn small ${fb.verdict === v ? "active" : "ghost"}" type="button" aria-pressed="${fb.verdict === v}" data-ph-verdict="${v}" data-card="${esc(c.id)}">${fb.verdict === v ? icon("check", "ph-tick") : ""}${label}</button>`).join("")}
      </div>
      <button class="ph-link" type="button" data-ph-note="${esc(c.id)}">${fb.note ? "Edit note" : "Add a note"}</button>
    </footer>
    ${P.notes.has(c.id) ? `<div class="ph-note">
        <label class="sr" for="note-${esc(c.id)}">Note on this card</label>
        <textarea id="note-${esc(c.id)}" rows="2" placeholder="What was off, or what you want more of. Next week's run reads it.">${esc(fb.note || "")}</textarea>
        <button class="btn small" type="button" data-ph-save-note="${esc(c.id)}">Save note</button>
      </div>` : fb.note ? `<p class="ph-note-text">${esc(fb.note)}</p>` : ""}
  </article>`;
}

function phrasingAnswer(c, fb) {
  return `${fb.tried ? `<div class="ph-tried"><p class="ph-label">Your try</p><p>${esc(fb.tried)}</p></div>` : ""}
    <div class="ph-better">
      <p class="ph-label">Phrase it better</p>
      <p class="ph-better-text">${esc(c.better)}</p>
      ${c.why ? `<p class="ph-why">${esc(c.why)}</p>` : ""}
    </div>
    ${c.terms && c.terms.length ? `<ul class="ph-terms">${c.terms.map((t) => `<li><b>${esc(t.term)}</b><span>${esc(t.meaning)}</span></li>`).join("")}</ul>` : ""}
    ${c.agent_put_it ? `<p class="ph-agent"><span>How the agent put it</span><q>${esc(c.agent_put_it)}</q></p>` : ""}`;
}

/* ---------- margin */

function phrasingStats(w) {
  const trend = P.data.trend;
  const at = trend.findIndex((t) => t.week === w.week);
  const prev = at > 0 ? trend[at - 1] : null;
  const s = w.stats || {};
  const terms = P.data.terms;
  const inUse = terms.filter((t) => t.used && t.used.week <= w.week).length;
  const words = s.avg_long_words || 0;
  const was = prev && prev.avg_long_words ? `was ${prev.avg_long_words}` : "first week";
  return `<section class="ph-stats" aria-label="This week in numbers">
    <div><b>${w.cards.length}</b><span>cards this week</span></div>
    <div><b>${inUse}</b><span>terms you now use${w.terms_used.length ? ` <em>+${w.terms_used.length} this week</em>` : ""}</span></div>
    <div><b>${words || "–"}</b><span>words per long prompt${words ? ` <small>${was}</small>` : ""}</span></div>
    <div><b>${s.rounds_lost || 0}</b><span>rounds lost to wording</span></div>
  </section>`;
}

function phrasingTrend(w) {
  const rows = P.data.trend.filter((t) => t.avg_long_words).slice(-8);
  if (rows.length < 2) return "";
  const top = Math.max(...rows.map((t) => t.avg_long_words));
  const label = rows.map((t) => `week ${weekNo(t.week)}: ${t.avg_long_words} words`).join(", ");
  return `<section class="ph-trend">
    <h2>Words per long prompt</h2>
    <p class="ph-sub">Prompts of 60 words or more, average per week. Fewer words for the same request is the goal.</p>
    <div class="ph-bars" role="img" aria-label="${esc(label)}">
      ${rows.map((t) => `<div class="ph-bar${t.week === w.week ? " is-now" : ""}" title="Week ${weekNo(t.week)}: ${t.avg_long_words} words over ${t.long} long prompts">
        <span class="ph-bar-v">${t.week === w.week ? t.avg_long_words : ""}</span>
        <span class="ph-bar-fill" style="height:${Math.max(4, Math.round((t.avg_long_words / top) * 100))}%"></span>
        <span class="ph-bar-x">W${weekNo(t.week)}</span>
      </div>`).join("")}
    </div>
  </section>`;
}

function phrasingPatterns(w) {
  const list = (w.patterns && w.patterns.length ? w.patterns : P.data.patterns) || [];
  if (!list.length) return "";
  return `<section class="ph-patterns">
    <h2>Your patterns</h2>
    <p class="ph-sub">Once a month, habits that show up across many cards.</p>
    ${list.map((p) => `<blockquote><p>${esc(p.pattern)}</p>${p.example && p.example !== p.pattern ? `<footer>${esc(p.example)}</footer>` : ""}</blockquote>`).join("")}
  </section>`;
}

function phrasingTerms(w) {
  const terms = P.data.terms.filter((t) => t.week <= w.week);
  if (!terms.length) return "";
  const groups = {};
  terms.slice().reverse().forEach((t) => (groups[t.area] = groups[t.area] || []).push(t));
  const order = Object.keys(PH_AREA).filter((a) => groups[a]);
  let left = P.termsAll ? Infinity : 14;
  const body = order.map((a) => {
    const items = groups[a].slice(0, Math.max(0, left));
    left -= items.length;
    if (!items.length) return "";
    return `<h3>${esc(PH_AREA[a])}</h3><ul>${items.map((t) => `<li${t.used ? ' class="is-used"' : ""}>
        <b>${esc(t.term)}</b><span>${esc(t.meaning)}</span>
        <em>${t.used ? `you used it · W${weekNo(t.used.week)}` : t.week === w.week ? "new" : `from W${weekNo(t.week)}`}</em>
      </li>`).join("")}</ul>`;
  }).join("");
  return `<section class="ph-termlist">
    <h2>Your terms <span class="count">${terms.length}</span></h2>
    <p class="ph-sub">A term is marked as used once you use it correctly in a later prompt.</p>
    ${body}
    ${terms.length > 14 ? `<button class="ph-link" type="button" data-ph-terms>${P.termsAll ? "Show fewer" : `Show all ${terms.length}`}</button>` : ""}
  </section>`;
}

/* The Log's margin: this week's line, under the blind test. */
function phrasingLogCard(week) {
  const s = S.data && S.data.phrasing;
  const w = s && s.weeks && s.weeks[week];
  if (!w || !w.cards) return "";
  const used = w.used ? `, and you started using ${plural(w.used, "term")} from earlier cards` : "";
  return `<section class="bt-card ph-logcard${s.week === week && s.unread ? " is-open" : ""}" aria-label="Phrase it better this week">
    <h2>Phrase it better</h2>
    <p class="bt-line">${plural(w.cards, "card")} on saying it shorter or with the right term${used}.</p>
    <a class="bt-link" href="#phrasing/${encodeURIComponent(week)}">Read this week's cards</a>
  </section>`;
}

/* ---------- events (delegated once, from app.js's #view) */

document.addEventListener("click", (e) => {
  if (S.tab !== "phrasing" && S.tab !== "guide") return;
  const t = e.target.closest("[data-ph-week],[data-ph-filter],[data-ph-sheet-filter],[data-ph-more],[data-ph-reveal],[data-ph-verdict],[data-ph-note],[data-ph-save-note],[data-ph-full],[data-ph-terms]");
  if (!t) return;
  const ds = t.dataset;
  if (ds.phWeek !== undefined) {
    if (ds.phWeek) { P.week = ds.phWeek; P.filter = "all"; go("phrasing", ds.phWeek); }
    return;
  }
  if (ds.phFilter) { P.filter = ds.phFilter; render(); return; }
  if (ds.phSheetFilter) { P.sheetFilter = ds.phSheetFilter; phrasingRefresh(`[data-ph-sheet-filter="${ds.phSheetFilter}"]`); return; }
  if (ds.phMore) { P.more.has(ds.phMore) ? P.more.delete(ds.phMore) : P.more.add(ds.phMore); phrasingRefresh(`[data-ph-more="${CSS.escape(ds.phMore)}"]`); return; }
  if (ds.phFull) { P.full.has(ds.phFull) ? P.full.delete(ds.phFull) : P.full.add(ds.phFull); render(); return; }
  if (ds.phTerms !== undefined) { P.termsAll = !P.termsAll; render(); return; }
  if (ds.phReveal) {
    const box = document.getElementById(`try-${ds.phReveal}`);
    const tried = box ? box.value.trim() : "";
    P.revealed.add(ds.phReveal);
    if (tried) phrasingSend({ card: ds.phReveal, tried });
    else phrasingRefresh();
    return;
  }
  if (ds.phVerdict) {
    const card = ds.card;
    const current = (phrasingFind(card).feedback || {}).verdict;
    const verdict = current === ds.phVerdict ? "" : ds.phVerdict;
    rememberFocus(t);
    // On the Guide's open list an answered card leaves the list at once, so say so and offer Undo.
    const leaves = S.tab === "guide" && P.sheetFilter === "open" && verdict;
    phrasingSend({ card, verdict }, leaves ? `Marked ${PH_VERDICT[verdict]}.` : verdict ? null : "Answer cleared.",
      leaves ? { label: "Undo", run: () => phrasingSend({ card, verdict: current || "" }, "Answer undone.") } : null);
    return;
  }
  if (ds.phNote) { P.notes.has(ds.phNote) ? P.notes.delete(ds.phNote) : P.notes.add(ds.phNote); phrasingRefresh(); setTimeout(() => { const n = document.getElementById(`note-${ds.phNote}`); if (n) n.focus(); }, 0); return; }
  if (ds.phSaveNote) {
    const box = document.getElementById(`note-${ds.phSaveNote}`);
    P.notes.delete(ds.phSaveNote);
    phrasingSend({ card: ds.phSaveNote, note: box ? box.value.trim() : "" }, "Note saved. Next week's run reads it.");
  }
});

document.addEventListener("change", (e) => {
  if (S.tab !== "phrasing" || !e.target.matches("[data-ph-practice]")) return;
  P.practice = e.target.checked;
  try { localStorage.setItem("brain-phrasing-practice", P.practice ? "on" : "off"); } catch (err) {}
  render();
});

function phrasingFind(id) {
  for (const w of (P.data && P.data.weeks) || []) {
    const c = [...w.cards, ...w.returning].find((x) => x.id === id);
    if (c) return c;
  }
  return {};
}

/* ---------- the Guide's sheet: every week and every card, with what is still unanswered */

function phrasingOpenLink() {
  const s = S.data && S.data.phrasing;
  if (!s || !s.cards) return "";
  const what = s.open ? `${plural(s.open, "card")} still open across ${plural(s.open_weeks, "week")}` : "every card answered";
  return `<a class="ph-all" href="#guide/${encodeURIComponent(PH_SHEET)}">All weeks in one list · ${what}</a>`;
}

function phrasingGuideCard() {
  const s = S.data && S.data.phrasing;
  if (!s || !s.cards) return "";
  return `<section class="kb-sec kb-phrasing">
    <h2>Phrase it better</h2>
    <p class="kb-live-sub">${s.open ? `<b>${plural(s.open, "card")}</b> still open across ${plural(s.open_weeks, "week")}, of ${s.cards} so far.` : `All ${s.cards} cards answered. New ones arrive with Monday's run.`}</p>
    <a href="#guide/${encodeURIComponent(PH_SHEET)}" class="kb-link">${s.open ? "Answer them in one list" : "Every card and week"}</a>
  </section>`;
}

function phrasingSummarySheet() {
  if (!P.data) return `<p class="ph-quiet">${esc(P.error || "Loading the cards…")}</p>`;
  const weeks = P.data.weeks;
  if (!weeks.length) return `<p class="ph-quiet">No cards yet. They arrive with Monday's run, or run <code>sbrain phrasing week --week last</code>.</p>`;
  const cards = weeks.flatMap((w) => w.cards);
  const isOpen = (c) => !(c.feedback || {}).verdict;
  const open = cards.filter(isOpen).length;
  const inUse = P.data.terms.filter((t) => t.used).length;
  const f = P.sheetFilter;
  const keep = (c) => f === "all" || (f === "open" ? isOpen(c) : !isOpen(c));
  const chips = [["open", "Still open", open], ["done", "Answered", cards.length - open], ["all", "All", cards.length]];

  const rows = weeks.map((w) => {
    const o = w.cards.filter(isOpen).length;
    const s = w.stats || {};
    return `<tr class="${o ? "has-open" : "is-done"}">
      <th scope="row"><a href="#phrasing/${encodeURIComponent(w.week)}">Week ${weekNo(w.week)}</a><span>${esc(weekSpan(w.week))}${w.partial ? " · in progress" : ""}</span></th>
      <td class="phs-opencell">${o ? `<b>${o} open</b>` : `${icon("check")}Done`}</td>
      <td class="num">${w.cards.length}</td>
      <td class="num">${w.cards.length - o}</td>
      <td class="num">${s.rounds_lost || 0}</td>
      <td class="num">${s.avg_long_words || "–"}</td>
      <td class="num">${w.terms_used.length}</td>
    </tr>`;
  }).join("");

  const groups = weeks.map((w) => {
    const list = w.cards.filter(keep);
    if (!list.length) return "";
    const o = w.cards.filter(isOpen).length;
    return `<section class="phs-week" aria-label="Week ${weekNo(w.week)}">
      <h3>Week ${weekNo(w.week)} <span>${esc(weekSpan(w.week))} · ${o ? `${o} of ${w.cards.length} open` : "all answered"}</span></h3>
      ${list.map(phrasingSheetRow).join("")}
    </section>`;
  }).join("");
  const none = { open: "Every card is answered. New cards arrive with Monday's run.", done: "No answers yet. Start with the open cards.", all: "" }[f];

  return `<div class="phs-totals">
      <div><b>${cards.length}</b><span>cards in ${plural(weeks.length, "week")}</span></div>
      <div class="${open ? "is-open" : ""}"><b>${open}</b><span>still open</span></div>
      <div><b>${cards.length - open}</b><span>answered</span></div>
      <div><b>${inUse}<small> of ${P.data.terms.length}</small></b><span>terms you now use</span></div>
    </div>
    <h2 class="phs-h">Weeks</h2>
    <div class="phs-scroll"><table class="phs-weeks">
      <thead><tr><th scope="col">Week</th><th scope="col">Open</th><th scope="col">Cards</th><th scope="col">Answered</th><th scope="col">Rounds lost</th><th scope="col">Words per long prompt</th><th scope="col">Terms used</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>
    <div class="phs-listhead">
      <h2 class="phs-h">Cards</h2>
      <div class="ph-filters" role="group" aria-label="Show">
        ${chips.map(([k, label, n]) => `<button class="ph-chip" type="button" data-ph-sheet-filter="${k}" aria-pressed="${f === k}">${label} <span>${n}</span></button>`).join("")}
      </div>
    </div>
    ${groups || `<p class="ph-quiet">${none}</p>`}`;
}

function phrasingSheetRow(c) {
  const fb = c.feedback || {};
  const done = !!fb.verdict;
  const showBetter = !P.practice || done || fb.tried || P.revealed.has(c.id);
  const more = P.more.has(c.id);
  const said = c.said.length > 220 ? c.said.slice(0, 217).trimEnd() + "…" : c.said;
  const meta = [PH_KIND[c.kind], c.project, phrasingWhen(c.at)].filter(Boolean).map(esc).join(" · ");
  const verdicts = [["got", "Got it"], ["knew", "I knew this"], ["not_useful", "Not useful"]];
  return `<article class="phs-row ${done ? "is-done" : "is-open"} kind-${c.kind}">
    <div class="phs-status">${done
      ? `<span class="phs-badge done">${icon("check")}${esc(PH_VERDICT[fb.verdict])}</span>`
      : `<span class="phs-badge open"><i aria-hidden="true"></i>Not answered</span>`}</div>
    <div class="phs-main">
      <p class="phs-meta">${meta}</p>
      <p class="phs-said">${esc(said)}</p>
      ${showBetter ? `<p class="phs-better">${esc(c.better)}</p>` : `<button class="ph-link" type="button" data-ph-reveal="${esc(c.id)}">Show the answer</button>`}
      ${more && showBetter ? `<div class="phs-more">
          ${c.why ? `<p>${esc(c.why)}</p>` : ""}
          ${c.terms && c.terms.length ? `<ul class="ph-terms">${c.terms.map((t) => `<li><b>${esc(t.term)}</b><span>${esc(t.meaning)}</span></li>`).join("")}</ul>` : ""}
          ${c.agent_put_it ? `<p class="ph-agent"><span>How the agent put it</span><q>${esc(c.agent_put_it)}</q></p>` : ""}
          ${c.grammar && c.grammar.length ? `<p class="ph-grammar"><span>Grammar</span>${c.grammar.map((g) => `${esc(g.wrong)} <b aria-label="should be">→</b> ${esc(g.right)}`).join("<i>·</i>")}</p>` : ""}
        </div>` : ""}
      ${showBetter ? `<button class="ph-link phs-toggle" type="button" data-ph-more="${esc(c.id)}" aria-expanded="${more}">${more ? "Less" : "Why, terms, and grammar"}</button>` : ""}
      ${P.notes.has(c.id) ? `<div class="ph-note">
          <label class="sr" for="note-${esc(c.id)}">Note on this card</label>
          <textarea id="note-${esc(c.id)}" rows="2" placeholder="What was off, or what you want more of. Next week's run reads it.">${esc(fb.note || "")}</textarea>
          <button class="btn small" type="button" data-ph-save-note="${esc(c.id)}">Save note</button>
        </div>` : fb.note ? `<p class="ph-note-text">${esc(fb.note)}</p>` : ""}
    </div>
    <div class="phs-actions">
      <div class="ph-verdicts" role="group" aria-label="Your answer">
        ${verdicts.map(([v, label]) => `<button class="btn small ${fb.verdict === v ? "active" : "ghost"}" type="button" aria-pressed="${fb.verdict === v}" data-ph-verdict="${v}" data-card="${esc(c.id)}">${label}</button>`).join("")}
      </div>
      <button class="ph-link" type="button" data-ph-note="${esc(c.id)}">${fb.note ? "Edit note" : "Add a note"}</button>
    </div>
  </article>`;
}
