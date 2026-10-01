"use strict";

/* Second brain logbook. Reads /api/state, writes through small POSTs, re-renders. */

const SCALE = 38; // fixed attention scale: the digest formula tops out near 37.5, so weeks compare honestly
const KIND_LABEL = { stated: "Stated", preference: "Preference", dislike: "Dislike", "work-style": "Work style", communication: "Communication", interest: "Interest", skill: "Skill", goal: "Goal", advice: "Advice" };
const NOTE_ORDER = [["goal", "Goals"], ["decision", "Decisions"], ["milestone", "Milestones"], ["stack", "Stack"], ["preference", "Preferences here"], ["open-problem", "Open problems"]];
const STATE_LABEL = { ongoing: "Ongoing", exploring: "Exploring", "on-hold": "On hold", earlier: "Earlier" };
const CLI_LABEL = { agy: "Antigravity", codex: "Codex", manual: "By hand" };
const RULES = [["", "Automatic"], ["project", "One project"], ["project+subprojects", "With sub-projects"], ["collection", "Collection"], ["ignore", "Ignore"]];
const SKILL_LEVEL = { strong: "Strong", growing: "Growing", shown: "Shown once", learning: "Learning", struggled: "Struggled", directs: "Directing" };

const S = {
  models: null,
  choice: loadChoice(),
  data: null,
  tab: "log",
  week: null,
  open: new Set(),
  quotes: new Set(),
  kind: "all",
  folderFilter: "",
  polling: null,
  lastRunFinished: null,
  // Presentation-only state
  logSection: "skills",
  logSummaryOpen: false,
  logPages: { ledger: 1, skills: 1, learned: 1, highlights: 1 },
  lastLogWeek: null,
  aboutStatus: "active",
  aboutStanding: "all",
  aboutPages: { status: 1, skills: 1 },
  aboutLegendOpen: false,
  quotePages: {},
  projectGroupOpen: { ongoing: true, exploring: false, "on-hold": false, earlier: false },
  projectPages: { ongoing: 1, exploring: 1, "on-hold": 1, earlier: 1 },
  projectDetailPages: {},
  projectNotesOpen: {},
  foldersOpen: false,
  runsPage: 1,
};
const $ = (sel) => document.querySelector(sel);

/* ---------- helpers */

function loadChoice() {
  try { return JSON.parse(localStorage.getItem("brain-run-choice")) || {}; } catch (e) { return {}; }
}
function saveChoice() {
  try { localStorage.setItem("brain-run-choice", JSON.stringify(S.choice)); } catch (e) {}
}

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const icon = (name, cls = "") => `<svg class="i ${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const plural = (n, word, many) => `${n} ${n === 1 ? word : many || word + "s"}`;
const sum = (list, key) => list.reduce((a, x) => a + (x[key] || 0), 0);
const fmtNum = (n) => (n || 0).toLocaleString("en-US");

function pageSlice(list, page = 1, pageSize = 10) {
  const total = list.length;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const currentPage = Math.max(1, Math.min(page, totalPages));
  const start = (currentPage - 1) * pageSize;
  const items = list.slice(start, start + pageSize);
  return { items, page: currentPage, totalPages, total, start: total ? start + 1 : 0, end: Math.min(start + pageSize, total) };
}

function renderPager(slice, typeLabel, id, accessibleTarget) {
  if (slice.total <= 0 || slice.totalPages <= 1) return "";
  const target = accessibleTarget || typeLabel;
  return `
    <nav class="pager-nav" aria-label="${esc(target)} pagination">
      <span class="pager-range" aria-live="polite">Showing ${slice.start}–${slice.end} of ${slice.total} ${esc(typeLabel)}</span>
      <div class="pager-btns">
        <button class="btn small ghost icon-btn" type="button" data-page-nav="${esc(id)}" data-page="${slice.page - 1}" ${slice.page <= 1 ? "disabled" : ""} aria-label="Previous page of ${esc(target)}">${icon("left")}<span class="btn-txt">Previous</span></button>
        <span class="pager-count" aria-hidden="true">${slice.page} / ${slice.totalPages}</span>
        <button class="btn small ghost icon-btn" type="button" data-page-nav="${esc(id)}" data-page="${slice.page + 1}" ${slice.page >= slice.totalPages ? "disabled" : ""} aria-label="Next page of ${esc(target)}"><span class="btn-txt">Next</span>${icon("right")}</button>
      </div>
    </nav>`;
}

function entryBadge(status) {
  if (status === "active") {
    return `<span class="entry-badge ink" aria-label="Status: Inked"><span class="badge-dot" aria-hidden="true"></span>Inked</span>`;
  }
  if (status === "candidate") {
    return `<span class="entry-badge pencil" aria-label="Status: Pencilled"><span class="badge-dot" aria-hidden="true"></span>Pencilled</span>`;
  }
  return `<span class="entry-badge struck" aria-label="Status: Struck">${icon("strike", "badge-icon")}Struck</span>`;
}

function weekStart(label) {
  const [y, w] = label.split("-W").map(Number);
  const jan4 = new Date(Date.UTC(y, 0, 4));
  const monday = new Date(jan4);
  monday.setUTCDate(jan4.getUTCDate() - ((jan4.getUTCDay() + 6) % 7) + (w - 1) * 7);
  return monday;
}
function weekLabel(date) {
  const d = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()));
  d.setUTCDate(d.getUTCDate() + 4 - (d.getUTCDay() || 7)); // the Thursday of this ISO week
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  const week = Math.ceil(((d - yearStart) / 86400000 + 1) / 7);
  return `${d.getUTCFullYear()}-W${String(week).padStart(2, "0")}`;
}
function shiftWeek(label, n) {
  const d = weekStart(label);
  d.setUTCDate(d.getUTCDate() + 7 * n);
  return weekLabel(d);
}
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function weekSpan(label) {
  const a = weekStart(label);
  const b = new Date(a);
  b.setUTCDate(a.getUTCDate() + 6);
  const left = `${MONTHS[a.getUTCMonth()]} ${a.getUTCDate()}`;
  const right = a.getUTCMonth() === b.getUTCMonth() ? `${b.getUTCDate()}` : `${MONTHS[b.getUTCMonth()]} ${b.getUTCDate()}`;
  return `${left} – ${right}, ${b.getUTCFullYear()}`;
}
const weekNo = (label) => Number(label.split("-W")[1]);
function day(iso) {
  if (!iso) return "never";
  const d = new Date(iso.length <= 10 ? iso + "T12:00:00" : iso);
  return `${MONTHS[d.getMonth()]} ${d.getDate()}`;
}
function ago(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}
const name = (pid) => (S.data.names && S.data.names[pid]) || pid;

function toast(message, action, error = false) {
  const el = $("#toast");
  el.className = "toast show" + (error ? " error" : "");
  el.innerHTML = `<span>${esc(message)}</span>` + (action ? `<button class="undo" type="button">${icon("undo")}${esc(action.label)}</button>` : "");
  if (action) el.querySelector("button").onclick = () => { el.className = "toast"; action.run(); };
  clearTimeout(toast.t);
  toast.t = setTimeout(() => (el.className = "toast"), action ? 6500 : 3200);
}

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json", "X-Brain": "1" }, body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

async function write(path, body, done) {
  try {
    S.data = await api(path, body);
    render();
    if (done) done();
  } catch (e) {
    toast(e.message, null, true);
    render();
  }
}

/* ---------- routing */

function readHash() {
  const [tab, arg] = decodeURIComponent(location.hash.slice(1)).split("/");
  S.tab = ["log", "projects", "about", "runs"].includes(tab) ? tab : "log";
  if (S.tab === "log" && arg) {
    if (S.week !== arg) {
      S.week = arg;
      S.logSection = "skills";
      S.logSummaryOpen = false;
      S.logPages = { ledger: 1, skills: 1, learned: 1, highlights: 1 };
    }
  }
  if (S.tab === "projects" && arg) {
    S.open.add(arg);
    syncProjectDeepLink(arg);
  }
  if (S.tab === "about" && arg && (arg === "all" || KIND_LABEL[arg])) {
    if (S.kind !== arg) {
      S.kind = arg;
      S.aboutPages = { status: 1, skills: 1 };
    }
  }
}

function syncProjectDeepLink(pid) {
  if (!S.data || !S.data.projects || !pid) return;
  const p = S.data.projects.find((x) => x.id === pid);
  if (p && p.status && p.status.state) {
    S.projectGroupOpen[p.status.state] = true;
    const list = S.data.projects
      .filter((x) => x.status.state === p.status.state && x.status.last_week)
      .sort((a, b) => (b.status.last_day || "").localeCompare(a.status.last_day || ""));
    const idx = list.findIndex((x) => x.id === pid);
    if (idx >= 0) {
      S.projectPages[p.status.state] = Math.floor(idx / 12) + 1;
    }
  }
}
function go(tab, arg) {
  location.hash = arg ? `${tab}/${encodeURIComponent(arg)}` : tab;
}
function focusProjectDeepLink() {
  if (S.tab !== "projects") return false;
  const [, pid] = decodeURIComponent(location.hash.slice(1)).split("/");
  if (!pid) return false;
  const trigger = document.querySelector(`[data-open="${CSS.escape(pid)}"]`);
  if (!trigger) return false;
  trigger.scrollIntoView({ block: "center", behavior: "instant" });
  trigger.focus({ preventScroll: true });
  return true;
}
window.addEventListener("hashchange", () => {
  readHash(); render();
  if (!focusProjectDeepLink()) $("#view").focus({ preventScroll: true });
});

/* ---------- header + tabs */

function renderHeader() {
  const d = S.data;
  if (!$("#week-picker").hidden) showWeekPicker(false);
  $("#owner").textContent = `Second brain of ${d.owner}`;
  const weeks = d.weeks.map((w) => w.week);
  if (S.tab === "log" && S.week) {
    const i = weeks.indexOf(S.week);
    const older = weeks[i + 1], newer = weeks[i - 1];
    $("#pager").innerHTML = `
      <button class="btn ghost icon" type="button" ${older ? "" : "disabled"} data-week="${esc(older || "")}" aria-label="Previous week">${icon("left")}</button>
      <button id="week-jump" class="label" type="button" aria-label="Choose a week. Week ${weekNo(S.week)}" aria-haspopup="dialog" aria-expanded="false" aria-controls="week-picker"><span class="week-label-top">Week ${weekNo(S.week)}${icon("down")}</span><small>${esc(weekSpan(S.week))}</small></button>
      <button class="btn ghost icon" type="button" ${newer ? "" : "disabled"} data-week="${esc(newer || "")}" aria-label="Next week">${icon("right")}</button>`;
    $("#pager").querySelectorAll("[data-week]").forEach((b) => (b.onclick = () => b.dataset.week && go("log", b.dataset.week)));
  } else {
    $("#pager").innerHTML = `<div class="label">${plural(weeks.length, "week")} logged<small>${weeks.length ? `since ${esc(weekSpan(weeks[weeks.length - 1]).split(" – ")[0])}` : "nothing yet"}</small></div>`;
  }

  const last = d.runs[0];
  $("#lastrun").innerHTML = last
    ? (last.status === "ok"
      ? `Last entry <b>${esc(ago(last.started))}</b><br>${esc(last.model || "")}`
      : `<b style="color:var(--bad)">Last run failed</b><br>${esc(ago(last.started))}`)
    : "No runs yet";

  const running = d.run && d.run.running;
  const btn = $("#runnow");
  btn.disabled = running;
  btn.classList.toggle("running", running);
  btn.innerHTML = running ? `${icon("spin", "spin")}Logging…` : `${icon("run")}Run now`;
  btn.title = running ? "An update is running" : `Update the brain now, including the week in progress, with ${choiceLabel()}`;
  $("#runpick").disabled = running;

  const mode = document.documentElement.dataset.theme || "auto";
  const themeBtn = $("#theme");
  themeBtn.innerHTML = icon(mode === "light" ? "sun" : mode === "dark" ? "moon" : "auto");
  themeBtn.setAttribute("aria-label", `Theme: ${mode}. Switch theme`);
  themeBtn.title = `Theme: ${mode}`;
}

function renderTabs() {
  const d = S.data;
  const inked = d.items.filter((i) => i.status === "active").length;
  const pencil = d.items.filter((i) => i.status === "candidate").length;
  const ongoing = d.projects.filter((p) => p.status.state === "ongoing").length;
  const questions = Object.keys(d.review).length;
  const failed = d.runs[0] && d.runs[0].status !== "ok";
  const tabs = [
    ["log", "Log", `${d.weeks.length}`],
    ["projects", "Projects", `${ongoing} ongoing`, questions > 0],
    ["about", `About ${d.owner}`, `${inked} inked · ${pencil} pencilled`],
    ["runs", "Runs", d.run && d.run.running ? "running" : `${d.runs.length}`, failed],
  ];
  $("#tabs").innerHTML = tabs.map(([id, label, count, flag]) => `
    <button class="tab" role="tab" type="button" aria-selected="${S.tab === id}" data-tab="${id}">
      ${esc(label)}<span class="count">${esc(count)}</span>${flag ? '<span class="flag" aria-label="needs attention"></span>' : ""}
    </button>`).join("");
  $("#tabs").querySelectorAll(".tab").forEach((t) => (t.onclick = () => go(t.dataset.tab, t.dataset.tab === "log" ? S.week : null)));
}

/* ---------- log */

function stateTag(status, marked) {
  if (!status) return `<span class="state earlier">Outside projects</span>`;
  return `<span class="state ${status.state}">${esc(STATE_LABEL[status.state])}${marked ? ` ${icon("pin", "pin")}` : ""}</span>`;
}
function attBar(v) {
  const pct = Math.min(100, (v / SCALE) * 100);
  return `<div class="att-bar"><div class="track"><div class="fill" style="width:${pct.toFixed(1)}%"></div></div><span class="v">${v.toFixed(1)}</span></div>`;
}
const numCell = (v, cls = "") => `<td class="num ${cls} ${v ? "" : "zero"}">${v ? fmtNum(v) : "–"}</td>`;

function viewLog() {
  const d = S.data;
  if (!d.weeks.length) {
    return `<div class="sheet empty"><h2>No logbook pages yet</h2><p>Run the first update to log last week. After that the brain writes a page every Monday on its own.</p><button class="btn primary" type="button" data-run>${icon("run")}Run now</button></div>`;
  }
  const w = d.weeks.find((x) => x.week === S.week) || d.weeks[0];
  S.week = w.week;

  if (S.lastLogWeek !== w.week) {
    S.lastLogWeek = w.week;
    S.logSection = "skills";
    S.logSummaryOpen = false;
    S.logPages = { ledger: 1, skills: 1, learned: 1, highlights: 1 };
  }

  const byId = Object.fromEntries(d.projects.map((p) => [p.id, p]));
  const rows = Object.entries(w.projects).sort((a, b) => (b[1].attention || 0) - (a[1].attention || 0));
  const stats = rows.map(([, p]) => p);
  const days = new Set(stats.flatMap((p) => p.active_days || []));

  const upTo = d.weeks.filter((x) => x.week <= w.week);
  const fwd = upTo.flatMap((x) => Object.values(x.projects));
  const fwdDays = new Set(fwd.flatMap((p) => p.active_days || []));

  // Ledger pagination at 12 project pairs per page
  const ledgerPage = S.logPages.ledger || 1;
  const ledgerSlice = pageSlice(rows, ledgerPage, 12);
  if (ledgerSlice.page !== ledgerPage) S.logPages.ledger = ledgerSlice.page;

  const body = ledgerSlice.items.map(([pid, p]) => {
    const proj = byId[pid];
    const open = S.open.has("w:" + pid);
    const group = proj && proj.group ? proj.group : proj ? "" : "Chats outside the projects folder";
    const sub = (p.subprojects_touched || []).length ? `Sub-projects: ${p.subprojects_touched.join(", ")}` : group;
    return `
      <tr class="row" tabindex="0" data-row="${esc(pid)}" aria-expanded="${open}">
        <td><div class="pname"><b>${esc(name(pid))}</b>${sub ? `<small>${esc(sub)}</small>` : ""}<span class="m-state">${stateTag(proj && proj.status, proj && proj.status.marked)}</span></div></td>
        <td>${stateTag(proj && proj.status, proj && proj.status.marked)}</td>
        ${numCell((p.active_days || []).length)}
        ${numCell(p.conversations, "hide-sm")}
        ${numCell(p.exchanges, "hide-sm")}
        ${numCell(p.commits, "hide-sm")}
        ${numCell(p.files, "hide-sm")}
        <td>${attBar(p.attention || 0)}</td>
      </tr>
      <tr class="remark ${open ? "open" : ""}" data-row="${esc(pid)}">
        <td colspan="8">
          <div class="remark-content">
            <p>${esc(p.summary || "Files changed, but no AI conversations this week.")}</p>
            <div class="remark-actions">
              <button class="btn ghost small row-summary-toggle" type="button" data-toggle-project-row="${esc(pid)}" aria-expanded="${open}">${open ? "Hide full summary" : "Show full summary"}</button>
              ${open && proj ? `<div class="links"><a href="#projects/${encodeURIComponent(pid)}">Open ${esc(proj.name)} in Projects</a></div>` : ""}
            </div>
          </div>
        </td>
      </tr>`;
  }).join("");

  const learnedIn = (i) => i.evidence.some((e) => e.week === w.week);
  const notSkill = (i) => i.kind !== "skill";
  const inked = d.items.filter((i) => i.status === "active" && i.promoted === w.week && notSkill(i));
  const pencil = d.items.filter((i) => i.status === "candidate" && learnedIn(i) && notSkill(i));
  const struck = d.items.filter((i) => i.status === "removed" && learnedIn(i) && notSkill(i));
  const learnedItems = [...inked, ...pencil, ...struck];
  const skills = d.items.filter((i) => i.kind === "skill" && i.status !== "removed" && learnedIn(i));
  const highlights = w.highlights || [];

  // Week overview: full-width headline and summary
  const isSummaryLong = w.summary && (w.summary.length > 220 || w.summary.includes("\n"));
  const summaryOpen = Boolean(S.logSummaryOpen);
  const overviewHtml = `
    <header class="week-overview" aria-label="Week overview">
      ${w.headline ? `<h1 class="headline">${esc(w.headline)}</h1>` : ""}
      ${w.summary ? `
        <div class="summary-wrapper">
          <p class="summary ${isSummaryLong && !summaryOpen ? "clamped" : ""}">${esc(w.summary)}</p>
          ${isSummaryLong ? `<button class="btn ghost small toggle-summary-btn" type="button" data-toggle-summary aria-expanded="${summaryOpen}">${summaryOpen ? "Collapse summary" : "Read full summary"}</button>` : ""}
        </div>` : ""}
      <p class="meta">Written ${esc(w.ran_at ? day(w.ran_at) : "")}${w.model ? ` by ${esc(w.model)}` : ""}.</p>
    </header>`;

  // Context switcher & content
  const activeSection = S.logSection || "skills";
  let sectionContent = "";

  if (activeSection === "skills") {
    if (!skills.length) {
      sectionContent = `<p class="summary empty-context">No skills recorded this week.</p>`;
    } else {
      const skillsPage = S.logPages.skills || 1;
      const skillsSlice = pageSlice(skills, skillsPage, 6);
      if (skillsSlice.page !== skillsPage) S.logPages.skills = skillsSlice.page;
      const weekSkills = skillsSlice.items.map((i) => {
        const evidence = i.evidence.filter((e) => e.week === w.week);
        const level = evidence.some((e) => e.level === "struggled") ? "struggled"
          : evidence.some((e) => e.level === "learning") ? "learning" : "directs";
        return `<li class="skill-week"><a href="#about/skill">${esc(i.text)}</a> · ${esc(SKILL_LEVEL[level])}</li>`;
      }).join("");
      const pager = renderPager(skillsSlice, "skills", "log:skills", "skills this week");
      sectionContent = `
        <ul class="entries">${weekSkills}</ul>
        ${pager}
        <p class="meta"><a href="#about/skill">Review skill progress and evidence</a></p>`;
    }
  } else if (activeSection === "learned") {
    if (!learnedItems.length) {
      sectionContent = `<p class="summary empty-context">Nothing new about ${esc(d.owner)} this week. That is normal.</p>`;
    } else {
      const learnedPage = S.logPages.learned || 1;
      const learnedSlice = pageSlice(learnedItems, learnedPage, 6);
      if (learnedSlice.page !== learnedPage) S.logPages.learned = learnedSlice.page;
      const entriesList = learnedSlice.items.map((i) => entry(i, false)).join("");
      const pager = renderPager(learnedSlice, "learned entries", "log:learned", "learned entries");
      sectionContent = `<ul class="entries">${entriesList}</ul>${pager}`;
    }
  } else {
    // activeSection === "highlights"
    if (!highlights.length) {
      sectionContent = `<p class="summary empty-context">No highlights recorded this week.</p>`;
    } else {
      const hlPage = S.logPages.highlights || 1;
      const hlSlice = pageSlice(highlights, hlPage, 6);
      if (hlSlice.page !== hlPage) S.logPages.highlights = hlSlice.page;
      const hlList = hlSlice.items.map((h) => `<li>${esc(h)}</li>`).join("");
      const pager = renderPager(hlSlice, "highlights", "log:highlights", "highlights this week");
      sectionContent = `<ul class="hl">${hlList}</ul>${pager}`;
    }
  }

  const sectionTitle = activeSection === "skills" ? "Skills this week" : activeSection === "learned" ? `Learned about ${esc(d.owner)}` : "Highlights";

  const contextHtml = `
    <aside class="margin" aria-label="This week">
      <div class="context-switch" role="group" aria-label="This week sections">
        <button class="btn small ${activeSection === "skills" ? "active" : "ghost"}" type="button" aria-pressed="${activeSection === "skills"}" data-log-section="skills">Skills this week <span class="count">${skills.length}</span></button>
        <button class="btn small ${activeSection === "learned" ? "active" : "ghost"}" type="button" aria-pressed="${activeSection === "learned"}" data-log-section="learned">Learned <span class="count">${learnedItems.length}</span></button>
        <button class="btn small ${activeSection === "highlights" ? "active" : "ghost"}" type="button" aria-pressed="${activeSection === "highlights"}" data-log-section="highlights">Highlights <span class="count">${highlights.length}</span></button>
      </div>
      <section class="context-body" aria-label="${sectionTitle}">
        <h2>${sectionTitle}</h2>
        ${sectionContent}
      </section>
    </aside>`;

  const ledgerPager = renderPager(ledgerSlice, "projects", "ledger", "projects");

  return `
    <div class="log-container">
      ${overviewHtml}
      <div class="page">
        <section aria-label="Log for week ${weekNo(w.week)}">
          <div class="ledger-header">
            <h2>Projects this week <span class="count">${rows.length}</span></h2>
            ${ledgerPager}
          </div>
          ${w.partial ? `<div class="banner">${icon("alert")}Week in progress. This page is rewritten when the week ends.</div>` : ""}
          <table class="log">
            <thead><tr>
              <th scope="col">Project</th><th scope="col" style="text-align:left">Status</th><th scope="col">Days</th>
              <th scope="col" class="hide-sm">Conv.</th><th scope="col" class="hide-sm">Exch.</th><th scope="col" class="hide-sm">Commits</th><th scope="col" class="hide-sm">Files</th>
              <th scope="col" class="att">Attention</th>
            </tr></thead>
            <tbody>${body}</tbody>
            <tfoot>
              <tr>
                <td>Totals this page</td><td></td><td></td>
                ${numCell(sum(stats, "conversations"), "hide-sm")}
                ${numCell(sum(stats, "exchanges"), "hide-sm")}
                ${numCell(sum(stats, "commits"), "hide-sm")}
                ${numCell(sum(stats, "files"), "hide-sm")}
                <td style="text-align:left">
                  ${plural(rows.length, "project")} · ${plural(days.size, "active day")}
                  ${ledgerSlice.totalPages > 1 ? `<br><small class="totals-note">Totals include every project in this week</small>` : ""}
                </td>
              </tr>
              <tr class="fwd">
                <td>Brought forward</td><td></td><td></td>
                ${numCell(sum(fwd, "conversations"), "hide-sm")}
                ${numCell(sum(fwd, "exchanges"), "hide-sm")}
                ${numCell(sum(fwd, "commits"), "hide-sm")}
                ${numCell(sum(fwd, "files"), "hide-sm")}
                <td style="text-align:left">${plural(upTo.length, "week")} · ${plural(fwdDays.size, "active day")}</td>
              </tr>
            </tfoot>
          </table>
        </section>
        ${contextHtml}
      </div>
    </div>`;
}

/* ---------- learned entries */

function entry(i, withQuotes = true) {
  const cls = i.status === "active" ? "ink" : i.status === "candidate" ? "pencil" : "struck";
  const weeks = [...new Set(i.evidence.map((e) => e.week))].sort();
  const seen = `seen in ${i.projects.map(name).join(", ")} · ${weeks.length > 1 ? `${weeks[0]} to ${weeks[weeks.length - 1]}` : weeks[0] || ""}`;
  const quotesOpen = S.quotes.has(i.id);
  const act = i.status === "removed"
    ? `<button class="undo" type="button" data-restore="${esc(i.id)}">${icon("undo")}Undo</button>`
    : `<button class="strike" type="button" data-strike="${esc(i.id)}" aria-label="Strike this entry">${icon("strike")}Strike</button>`;

  let quotesHtml = "";
  if (withQuotes && quotesOpen && i.evidence.length) {
    const qPage = S.quotePages[i.id] || 1;
    const qSlice = pageSlice(i.evidence, qPage, 6);
    if (qSlice.page !== qPage) S.quotePages[i.id] = qSlice.page;
    const listHtml = qSlice.items.map((e) =>
      `<li><q>${esc(e.quote)}</q><small>${esc(name(e.project))} · ${esc(e.week)}${i.kind === "skill" ? ` · ${esc(SKILL_LEVEL[e.level || "directs"] || e.level)}` : ""}</small></li>`
    ).join("");
    const pagerHtml = renderPager(qSlice, "quotes", "quotes:" + i.id, "quotes for " + i.text.slice(0, 24));
    quotesHtml = `<div class="quotes-wrapper"><ul class="quotes">${listHtml}</ul>${pagerHtml}</div>`;
  }

  return `
    <li class="entry ${cls}" data-entry="${esc(i.id)}">
      <div class="entry-header">
        ${entryBadge(i.status)}
        <span class="text strikable">${esc(i.text)}</span>
      </div>
      <span class="acts">${act}</span>
      ${i.kind === "skill" && i.standing ? `<div class="skill-progress"><b>${esc(SKILL_LEVEL[i.standing.level] || i.standing.level)}</b>${esc(i.standing.trend)}</div>` : ""}
      ${i.kind === "goal" ? `<div class="skill-progress"><b>${i.open === false ? "Settled goal" : "Open goal"}</b>${i.created ? `Since ${esc(i.created)}` : ""}${i.resolved ? ` · settled ${esc(i.resolved)}` : ""}</div>` : ""}
      <span class="sub"><span class="kind">${esc(KIND_LABEL[i.kind] || i.kind)}</span>${esc(seen)}
        ${withQuotes && i.evidence.length ? ` · <button class="linkish" type="button" data-quotes="${esc(i.id)}" aria-expanded="${quotesOpen}">${quotesOpen ? "Hide" : "Show"} ${plural(i.evidence.length, "quote")}</button>` : ""}</span>
      ${quotesHtml}
    </li>`;
}

function viewAbout() {
  const d = S.data;
  const kinds = ["all", ...Object.keys(KIND_LABEL).filter((k) => d.items.some((i) => i.kind === k))];
  const pass = (i) => (S.kind === "all" ? i.kind !== "skill" : i.kind === S.kind);
  const order = (a, b) => b.evidence.length - a.evidence.length;
  const inked = d.items.filter((i) => i.status === "active" && pass(i)).sort(order);
  const pencil = d.items.filter((i) => i.status === "candidate" && pass(i)).sort(order);
  const struck = d.items.filter((i) => i.status === "removed" && pass(i));
  const progressLevels = ["strong", "growing", "shown", "learning", "struggled"];

  const legendHtml = `
    <details class="legend-disclosure" ${S.aboutLegendOpen ? "open" : ""}>
      <summary class="btn ghost small legend-summary">${icon("down", "toggle-icon")}How entries work</summary>
      <div class="legend-content">
        <p>The brain writes what your conversations show about you. Nothing waits for approval.</p>
        <div class="legend-sample">${entryBadge("candidate")}<span class="sample-label">Pencilled entry</span></div>
        <p>${S.kind === "skill" ? "Skills retain their evidence marks; the progress level is computed from their weekly history." : "Seen once. It stays in pencil until it shows up again in another project or another week."}</p>
        <div class="legend-sample">${entryBadge("active")}<span class="sample-label">Inked entry</span></div>
        <p>Confirmed. Stated facts, goals and advice are recorded directly; other observations recur in two projects or two weeks. ${S.kind === "skill" ? "Skill progress appears in <code>me/skills.md</code>." : "It appears in <code>me/learned.md</code> in your vault; goals are kept in <code>me/open-questions.md</code>."}</p>
        ${S.kind === "skill" ? `<p>Skill levels describe the evidence: <b>Strong</b> means directing across two weeks or projects; <b>Growing</b> means asking earlier and directing now; <b>Shown once</b> means one week and project; <b>Learning</b> means asking how it works; <b>Struggled</b> means it kept failing the latest time.</p>` : ""}
        <div class="legend-sample">${entryBadge("removed")}<span class="sample-label">Struck entry</span></div>
        <p>Removed by you. Kept legible so you can undo it, and shown to the model as something never to propose again.</p>
      </div>
    </details>`;

  let mainContent = "";

  if (S.kind === "skill") {
    const skills = [...inked, ...pencil];
    const unranked = skills.filter((i) => !progressLevels.includes(i.standing?.level));
    const removedSkills = d.items.filter((i) => i.kind === "skill" && i.status === "removed");

    const counts = {
      all: skills.length,
      strong: skills.filter((i) => i.standing?.level === "strong").length,
      growing: skills.filter((i) => i.standing?.level === "growing").length,
      shown: skills.filter((i) => i.standing?.level === "shown").length,
      learning: skills.filter((i) => i.standing?.level === "learning").length,
      struggled: skills.filter((i) => i.standing?.level === "struggled").length,
      unranked: unranked.length,
      removed: removedSkills.length,
    };

    const standing = S.aboutStanding || "all";

    let filteredList = [];
    if (standing === "all") {
      for (const lvl of progressLevels) {
        filteredList.push(...skills.filter((i) => i.standing?.level === lvl));
      }
      filteredList.push(...unranked);
    } else if (standing === "removed") {
      filteredList = removedSkills;
    } else if (standing === "unranked") {
      filteredList = unranked;
    } else {
      filteredList = skills.filter((i) => i.standing?.level === standing);
    }

    const standingPage = S.aboutPages.skills || 1;
    const skillSlice = pageSlice(filteredList, standingPage, 10);
    if (skillSlice.page !== standingPage) S.aboutPages.skills = skillSlice.page;

    const standingSelector = `
      <div class="chips standing-chips" role="group" aria-label="Filter skills by standing">
        <div class="segmented">
          <button type="button" aria-pressed="${standing === 'all'}" data-skill-standing="all">All levels <span class="count">${counts.all}</span></button>
          <button type="button" aria-pressed="${standing === 'strong'}" data-skill-standing="strong">Strong <span class="count">${counts.strong}</span></button>
          <button type="button" aria-pressed="${standing === 'growing'}" data-skill-standing="growing">Growing <span class="count">${counts.growing}</span></button>
          <button type="button" aria-pressed="${standing === 'shown'}" data-skill-standing="shown">Shown once <span class="count">${counts.shown}</span></button>
          <button type="button" aria-pressed="${standing === 'learning'}" data-skill-standing="learning">Learning <span class="count">${counts.learning}</span></button>
          <button type="button" aria-pressed="${standing === 'struggled'}" data-skill-standing="struggled">Struggled <span class="count">${counts.struggled}</span></button>
          <button type="button" aria-pressed="${standing === 'unranked'}" data-skill-standing="unranked">Recorded skills <span class="count">${counts.unranked}</span></button>
          <button type="button" aria-pressed="${standing === 'removed'}" data-skill-standing="removed">Struck <span class="count">${counts.removed}</span></button>
        </div>
      </div>`;

    let skillsMarkup = "";
    if (!skillSlice.items.length) {
      skillsMarkup = `<div class="sheet empty"><h2>No skills in this category</h2><p>No skills match the selected standing.</p></div>`;
    } else if (standing !== "all") {
      const title = standing === "removed" ? "Struck skills" : standing === "unranked" ? "Recorded skills" : SKILL_LEVEL[standing] || standing;
      const subtitle = standing === "removed" ? `${plural(counts.removed, "skill")} · removed by you` : `${plural(counts[standing] || skillSlice.total, "skill")}`;
      const pager = renderPager(skillSlice, "skills", "about-skills", title);
      skillsMarkup = `
        <div class="section-head">
          <div><h2>${title}</h2><p>${subtitle}</p></div>
          ${pager}
        </div>
        <div class="sheet"><ul class="entries">${skillSlice.items.map((i) => entry(i)).join("")}</ul></div>`;
    } else {
      const pager = renderPager(skillSlice, "skills", "about-skills", "all skills");
      let currentGroup = null;
      let groupedHtml = "";
      for (const item of skillSlice.items) {
        const itemLevel = item.standing?.level;
        const groupKey = progressLevels.includes(itemLevel) ? itemLevel : "unranked";
        if (groupKey !== currentGroup) {
          currentGroup = groupKey;
          const groupTitle = groupKey === "unranked" ? "Recorded skills" : SKILL_LEVEL[groupKey] || groupKey;
          const groupCount = counts[groupKey] || 0;
          groupedHtml += `<li class="standing-header" aria-label="${groupTitle}"><h3>${groupTitle} <span class="count">(${groupCount} total)</span></h3></li>`;
        }
        groupedHtml += entry(item);
      }
      skillsMarkup = `
        <div class="section-head">
          <div><h2>All skill levels</h2><p>${plural(counts.all, "skill")} across all progress levels</p></div>
          ${pager}
        </div>
        <div class="sheet"><ul class="entries">${groupedHtml}</ul></div>`;
    }

    mainContent = standingSelector + skillsMarkup;
  } else {
    const status = S.aboutStatus || "active";
    const statusCounts = {
      active: inked.length,
      candidate: pencil.length,
      removed: struck.length,
    };

    const statusSelector = `
      <div class="chips status-chips" role="group" aria-label="Filter by status">
        <div class="segmented">
          <button type="button" aria-pressed="${status === 'active'}" data-about-status="active">Inked <span class="count">${statusCounts.active}</span></button>
          <button type="button" aria-pressed="${status === 'candidate'}" data-about-status="candidate">Pencilled <span class="count">${statusCounts.candidate}</span></button>
          <button type="button" aria-pressed="${status === 'removed'}" data-about-status="removed">Struck <span class="count">${statusCounts.removed}</span></button>
        </div>
      </div>`;

    const statusList = status === "active" ? inked : status === "candidate" ? pencil : struck;
    const statusPage = S.aboutPages.status || 1;
    const statusSlice = pageSlice(statusList, statusPage, 10);
    if (statusSlice.page !== statusPage) S.aboutPages.status = statusSlice.page;

    const titles = {
      active: ["Inked", `${plural(statusCounts.active, "entry", "entries")} · stated directly or confirmed in two projects or two weeks`, "Nothing confirmed yet."],
      candidate: ["Pencilled", `${plural(statusCounts.candidate, "entry", "entries")} · seen once, waiting to recur`, "Nothing waiting."],
      removed: ["Struck", `${plural(statusCounts.removed, "entry", "entries")} · never proposed again`, "Nothing struck."],
    };
    const [title, subtitle, emptyMsg] = titles[status] || titles.active;
    const pager = renderPager(statusSlice, "entries", "about-status", title);

    mainContent = `
      ${statusSelector}
      <div class="section-head">
        <div><h2>${title}</h2><p>${subtitle}</p></div>
        ${pager}
      </div>
      <div class="sheet">
        ${statusSlice.items.length ? `<ul class="entries">${statusSlice.items.map((i) => entry(i)).join("")}</ul>` : `<p class="summary" style="margin:14px 18px">${emptyMsg}</p>`}
      </div>`;
  }

  return `
    <div class="about-container">
      <div class="about-controls">
        <div class="chips kind-chips" role="group" aria-label="Filter by kind">
          <div class="segmented">${kinds.map((k) => `<button type="button" aria-pressed="${S.kind === k}" data-kind="${k}">${k === "all" ? "All" : esc(KIND_LABEL[k])}</button>`).join("")}</div>
        </div>
        ${legendHtml}
      </div>
      ${S.kind === "all" ? `<p class="kind-explainer">Showing all observations about ${esc(d.owner)} (skills have their own filter).</p>` : ""}
      ${mainContent}
    </div>`;
}

/* ---------- projects */

function eightWeeks() {
  const out = [];
  let w = S.data.thisWeek;
  for (let i = 0; i < 8; i++) { out.unshift(w); w = shiftWeek(w, -1); }
  return out;
}

function markControl(p) {
  const value = p.status.marked ? "mark" : p.status.unmarked ? "unmark" : "auto";
  const opts = [["auto", "Auto"], ["mark", "Project"], ["unmark", "Not one"]];
  return `<div class="segmented" role="group" aria-label="Is ${esc(p.name)} a real project?">${opts.map(([v, l]) =>
    `<button type="button" aria-pressed="${value === v}" data-mark="${esc(p.id)}" data-value="${v}">${l}</button>`).join("")}</div>`;
}

function projectDetail(p) {
  const weeks = S.data.weeks.filter((w) => w.projects[p.id] && w.projects[p.id].summary);
  const timePage = (S.projectDetailPages[p.id]?.timeline) || 1;
  const timeSlice = pageSlice(weeks, timePage, 6);
  if (timeSlice.page !== timePage) {
    S.projectDetailPages[p.id] = { ...(S.projectDetailPages[p.id] || {}), timeline: timeSlice.page };
  }
  const timePager = renderPager(timeSlice, "weeks", "timeline:" + p.id, "timeline for " + p.name);

  const notes = NOTE_ORDER.map(([k, title]) => {
    const list = p.notes.filter((n) => n.kind === k);
    if (!list.length) return "";
    const nKey = p.id + ":" + k;
    const isOpen = S.projectNotesOpen[nKey] ?? true;
    return `
      <div class="note-section">
        <button class="notes-toggle" type="button" data-toggle-notes="${esc(nKey)}" aria-expanded="${isOpen}">
          <h3>${title} · ${list.length}</h3>
          <span class="disclosure-state">${isOpen ? "Hide" : "Show"}</span>
        </button>
        ${isOpen ? `<ul class="notes">${list.map((n) => note(p.id, n, false)).join("")}</ul>` : ""}
      </div>`;
  }).join("");

  const removedNotes = p.removed_notes || [];
  const removedKey = p.id + ":removed";
  const removedOpen = S.projectNotesOpen[removedKey] ?? false;
  const removed = removedNotes.length ? `
    <div class="note-section">
      <button class="notes-toggle" type="button" data-toggle-notes="${esc(removedKey)}" aria-expanded="${removedOpen}">
        <h3>Struck notes · ${removedNotes.length}</h3>
        <span class="disclosure-state">${removedOpen ? "Hide" : "Show"}</span>
      </button>
      ${removedOpen ? `<ul class="notes">${removedNotes.map((n) => note(p.id, n, true)).join("")}</ul>` : ""}
    </div>` : "";

  return `
    <div class="detail" id="detail-${esc(p.id)}">
      <div>
        ${p.subprojects.length ? `<h3>Sub-projects</h3><div class="subs">${p.subprojects.map((s) => `<span>${esc(s)}</span>`).join("")}</div>` : ""}
        ${notes || removed ? notes + removed : `<p class="summary">No project notes yet. Notes appear once the brain has seen decisions, milestones, or open problems here.</p>`}
        ${p.page ? `<p class="meta" style="color:var(--ink-3);font-size:12.5px">In the vault: <code>${esc(p.page)}</code></p>` : ""}
      </div>
      <div>
        <div class="section-head" style="margin-top:0">
          <h3>Timeline</h3>
          ${timePager}
        </div>
        ${weeks.length ? `<ul class="timeline">${timeSlice.items.map((w) => `<li><b><a href="#log/${w.week}">Week ${weekNo(w.week)} · ${esc(weekSpan(w.week))}</a></b><p>${esc(w.projects[p.id].summary)}</p></li>`).join("")}</ul>` : `<p class="summary">No summarized weeks yet.</p>`}
      </div>
    </div>`;
}

function note(pid, n, struck) {
  return `<li class="note ${struck ? "struck" : ""}"><span class="k">${esc((NOTE_ORDER.find(([k]) => k === n.kind) || [0, n.kind])[1])}</span>
    <span class="text strikable">${esc(n.text)}</span>
    ${struck ? `<button class="undo" type="button" data-note-restore="${esc(n.id)}" data-project="${esc(pid)}">${icon("undo")}Undo</button>`
      : `<button class="strike" type="button" data-note-strike="${esc(n.id)}" data-project="${esc(pid)}" aria-label="Strike this note">${icon("strike")}Strike</button>`}</li>`;
}

function viewProjects() {
  const d = S.data;
  const axis = eightWeeks();
  const questions = Object.entries(d.review);
  const qBlock = questions.length ? `
    <div class="section-head"><h2>Folders to confirm</h2><p>Files alone can't tell these apart. Answer once; it is remembered.</p></div>
    <div class="sheet">${questions.map(([fid, why]) => `
      <div class="question"><div><b>${esc(fid)}</b><p>${esc(why)}</p></div>
        <div class="choices">
          <button class="btn small" type="button" data-rule="project" data-folder="${esc(fid)}">One project</button>
          <button class="btn small" type="button" data-rule="project+subprojects" data-folder="${esc(fid)}">Project with sub-projects</button>
          <button class="btn small" type="button" data-rule="collection" data-folder="${esc(fid)}">Separate projects</button>
        </div></div>`).join("")}</div>` : "";

  const head = `<div class="reg-row head" aria-hidden="true"><div>Project</div><div>What it is</div><div><div class="weeks8 labels">${axis.map((w) => `<span>W${weekNo(w)}</span>`).join("")}</div></div><div>Last active</div><div>Real project?</div></div>`;
  const sections = ["ongoing", "exploring", "on-hold", "earlier"].map((state) => {
    const list = d.projects.filter((p) => p.status.state === state && p.status.last_week)
      .sort((a, b) => (b.status.last_day || "").localeCompare(a.status.last_day || ""));
    if (!list.length) return "";
    const isOpen = S.projectGroupOpen[state] ?? (state === "ongoing");
    const page = S.projectPages[state] || 1;
    const groupSlice = pageSlice(list, page, 12);
    if (groupSlice.page !== page) S.projectPages[state] = groupSlice.page;

    const rows = groupSlice.items.map((p) => {
      const open = S.open.has(p.id);
      const bars = axis.map((w) => {
        const v = p.history[w] || 0;
        return v ? `<i style="height:${Math.max(8, Math.min(100, (v / SCALE) * 100)).toFixed(0)}%" title="W${weekNo(w)}: ${v.toFixed(1)}"></i>` : `<i class="none" title="W${weekNo(w)}: no activity"></i>`;
      }).join("");
      const when = state === "on-hold" ? `On hold since ${day(p.status.last_day)}` : day(p.status.last_day);
      return `
        <div class="reg-row ${open ? "open" : ""}">
          <div><button class="reg-open" type="button" data-open="${esc(p.id)}" aria-expanded="${open}" aria-controls="detail-${esc(p.id)}">
            <b>${esc(p.name)} ${p.status.marked ? icon("pin") : ""}</b>${p.group || p.kind === "loose" ? `<small>${esc(p.group || "Loose folder")}</small>` : ""}</button></div>
          <div class="blurb"><span>${esc(p.blurb || "No description yet.")}</span></div>
          <div><div style="width:100%"><div class="weeks8" aria-label="Attention over the last 8 weeks">${bars}</div>
            <div class="weeks8 labels m-only" aria-hidden="true">${axis.map((w) => `<span>W${weekNo(w)}</span>`).join("")}</div></div></div>
          <div class="when">${esc(when)}</div>
          <div>${markControl(p)}</div>
        </div>
        ${open ? projectDetail(p) : ""}`;
    }).join("");
    const notes = { ongoing: "Real attention in 3 of the last 8 weeks, or marked by you", exploring: "Touched in the last 4 weeks", "on-hold": "Was ongoing, quiet for 3 weeks or more", earlier: "Nothing recent" };
    const pager = isOpen ? renderPager(groupSlice, "projects", "proj:" + state, `${STATE_LABEL[state]} projects`) : "";
    return `
      <div class="section-head">
        <button class="group-toggle" type="button" data-toggle-proj-group="${state}" aria-expanded="${isOpen}">
          <div>
            <h2>${STATE_LABEL[state]} · ${list.length}</h2>
            <p>${notes[state]}</p>
          </div>
          <span class="disclosure-state">${isOpen ? "Collapse group" : "Expand group"}</span>
        </button>
        ${pager}
      </div>
      ${isOpen ? `<div class="sheet">${head}${rows}</div>` : ""}`;
  }).join("");

  const f = S.folderFilter.toLowerCase();
  const groups = {};
  d.folders.filter((x) => !f || x.name.toLowerCase().includes(f) || (x.group || "").toLowerCase().includes(f))
    .forEach((x) => (groups[x.group || "Top level"] ||= []).push(x));
  const foldersOpen = S.foldersOpen;
  const folderBlock = `
    <div class="section-head">
      <button class="group-toggle" type="button" data-toggle-folders aria-expanded="${foldersOpen}">
        <div>
          <h2>All folders · ${d.folders.length}</h2>
          <p>Every folder the brain tracks, groups or ignores</p>
        </div>
        <span class="disclosure-state">${foldersOpen ? "Collapse folders" : "Expand folders"}</span>
      </button>
      ${foldersOpen ? `<input class="filter" type="search" placeholder="Filter folders" value="${esc(S.folderFilter)}" data-folder-filter aria-label="Filter folders">` : ""}
    </div>
    ${foldersOpen ? `<div class="sheet"><div class="folders">${Object.entries(groups).map(([g, list]) => `
      <div class="fgroup"><h4>${esc(g)}</h4>${list.map((x) => `
        <div class="folder ${x.tracked ? "tracked" : ""}"><span title="${esc(x.id)}">${esc(x.name)}${x.marked ? " " + icon("pin") : ""}</span>
          <select data-folder-rule="${esc(x.id)}" aria-label="How to treat ${esc(x.name)}">${RULES.map(([v, l]) => `<option value="${v}" ${(x.rule || "") === v ? "selected" : ""}>${l}</option>`).join("")}</select>
        </div>`).join("")}</div>`).join("") || `<p class="summary" style="padding:14px 18px;margin:0">No folder matches.</p>`}</div></div>` : ""}`;

  return qBlock + (sections || `<div class="sheet empty"><h2>No project activity logged yet</h2><p>Projects appear here after the first weekly update.</p></div>`) + folderBlock;
}

/* ---------- runs */

function viewRuns() {
  const d = S.data;
  const r = d.run || {};
  const live = r.running || (r.lines && r.lines.length) ? `
    <div class="live"><h2>${r.running ? `${icon("spin", "spin")}Updating now` : r.error ? "Last Run now failed" : "Last Run now finished"}</h2>
      <pre>${(r.lines || []).map((l) => esc(l)).join("\n") || "Starting…"}${r.error ? `\n<b>${esc(r.error)}</b>` : ""}</pre></div>` : "";
  const runsPage = S.runsPage || 1;
  const runsSlice = pageSlice(d.runs, runsPage, 20);
  if (runsSlice.page !== runsPage) S.runsPage = runsSlice.page;

  const rows = runsSlice.items.map((x) => `
    <tr>
      <td>${esc(x.week)}${x.partial ? " <small style='color:var(--ink-3)'>in progress</small>" : ""}</td>
      <td>${esc(day(x.started))} ${esc(new Date(x.started).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }))}</td>
      <td><span class="${x.status === "ok" ? "ok" : "fail"}">${x.status === "ok" ? "Logged" : "Failed"}</span>${x.error ? `<div class="err">${esc(x.error)}</div>` : ""}</td>
      <td class="n">${x.seconds != null ? `${Math.floor(x.seconds / 60)}m ${String(x.seconds % 60).padStart(2, "0")}s` : "–"}</td>
      <td>${esc(x.model || "")}<span class="sub">${esc([CLI_LABEL[runCli(x)] || "", x.effort ? `${x.effort} effort` : ""].filter(Boolean).join(" · "))}</span></td>
      <td class="n">${fmtNum(measured(x) ? (x.usage || {}).calls : x.model_calls)}</td>
      ${measured(x) ? `<td class="n">${kTokens((x.usage || {}).input_tokens)}</td><td class="n">${kTokens((x.usage || {}).output_tokens)}</td>`
        : `<td class="n unmeasured" colspan="2" title="Answered by hand in another tool, so its tokens were counted there">not measured</td>`}
    </tr>`).join("");
  const pager = renderPager(runsSlice, "runs", "runs", "run history");
  return live + weeklyPanel() + `
    <div class="section-head">
      <div>
        <h2>Run history</h2>
        <p>${d.schedule && d.schedule.next && !d.schedule.next.startsWith("0001")
          ? `Next scheduled run ${esc(day(d.schedule.next))} at ${esc(new Date(d.schedule.next).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }))}. Missed weeks are caught up.`
          : "No weekly schedule installed. Run <code>sbrain install</code> to add one."} Every run is committed to the vault.</p>
      </div>
      ${pager}
    </div>
    <div class="sheet">${d.runs.length ? `<table class="runs"><thead><tr><th>Week</th><th>Started</th><th>Result</th><th class="n">Duration</th><th>Model</th><th class="n">Calls</th><th class="n">Input tokens</th><th class="n">Output tokens</th></tr></thead><tbody>${rows}</tbody></table>`
      : `<div class="empty"><h2>No runs yet</h2><p>The first run logs last week.</p></div>`}</div>`;
}

/* ---------- weekly run settings: the defaults every scheduled run uses */

const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

function weeklySummary(s) {
  const model = s.model || (s.cli === "agy" ? "newest Gemini Flash" : "Codex default model");
  return `Every ${s.day} at ${s.time} with ${CLI_LABEL[s.cli] || s.cli} · ${model}${s.effort ? ` · ${s.effort} effort` : ""}`;
}

function weeklyPanel() {
  const s = S.data.settings;
  if (!s) return "";
  const w = S.weekly;
  if (!w) {
    return `
    <section class="sheet weekly" aria-label="Weekly run">
      <div class="weekly-head">
        <div><div class="section-head" style="margin:0 0 4px"><h2>Weekly run</h2></div><p>${esc(weeklySummary(s))}, your PC's time.</p></div>
        <button class="btn ghost small" type="button" data-weekly-edit>Change</button>
      </div>
    </section>`;
  }
  const m = S.models;
  if (!m) {
    return `<section class="sheet weekly"><p class="hint">${icon("spin", "spin")} Asking the CLIs for their models…</p></section>`;
  }
  const info = m.clis[w.cli] || { models: [] };
  const models = info.models || [];
  const picked = models.find((x) => x.id === (w.model || info.default));
  const efforts = picked ? picked.efforts || [] : [];
  const defaultName = w.cli === "agy" ? "newest Gemini Flash" : info.default || "your Codex default";
  return `
    <section class="sheet weekly" aria-label="Weekly run settings">
      <div class="section-head" style="margin:0 0 4px"><h2>Weekly run</h2></div>
      <p class="hint" style="margin-top:0">Every scheduled run uses these. Run now can still pick something else for one run.</p>
      <div class="weekly-grid">
        <div class="field">CLI
          <div class="seg" role="group" aria-label="CLI">${["agy", "codex"].map((c) =>
            `<button type="button" data-weekly-cli="${c}" aria-pressed="${c === w.cli}" ${m.clis[c] ? "" : "disabled title=\"Not installed\""}>${CLI_LABEL[c]}</button>`).join("")}</div>
        </div>
        <label class="field">Model
          <select data-weekly-field="model">
            <option value="">Default (${esc(defaultName)})</option>
            ${models.map((x) => `<option value="${esc(x.id)}" ${x.id === w.model ? "selected" : ""}>${esc(x.label)}${x.label === x.id ? "" : ` · ${esc(x.id)}`}</option>`).join("")}
          </select></label>
        <label class="field">Reasoning effort
          <select data-weekly-field="effort" ${efforts.length ? "" : "disabled"}>
            <option value="">${efforts.length ? `Default${picked && picked.default_effort ? ` (${esc(picked.default_effort)})` : ""}` : "Set by the model's name"}</option>
            ${efforts.map((e) => `<option value="${esc(e)}" ${e === w.effort ? "selected" : ""}>${esc(e)}</option>`).join("")}
          </select></label>
        <label class="field">Day
          <select data-weekly-field="day">${DAYS.map((d) => `<option ${d === w.day ? "selected" : ""}>${d}</option>`).join("")}</select></label>
        <label class="field">Time (your PC's time)
          <input type="time" data-weekly-field="time" value="${esc(w.time)}" required></label>
      </div>
      <p class="hint">A run covers the weeks that ended before it starts, so Monday catches the week that just ended. If the PC is off at that time, the run starts when it is next on.</p>
      <div class="actions">
        <button class="btn ghost small" type="button" data-weekly-cancel>Cancel</button>
        <button class="btn primary" type="button" data-weekly-save>Save</button>
      </div>
    </section>`;
}

async function editWeekly() {
  S.weekly = { ...S.data.settings };
  render();
  if (!S.models) {
    try { S.models = await api("/api/models"); } catch (e) { S.weekly = null; toast(e.message, null, true); }
    render();
  }
}

async function saveWeekly() {
  try {
    const r = await api("/api/settings", S.weekly);
    S.models = r.models ? { ...S.models, config: r.models.config } : S.models;
    delete r.models;
    const message = r.message;
    delete r.message;
    S.data = r;
    S.weekly = null;
    render();
    toast(message || "Saved.");
  } catch (e) { toast(e.message, null, true); }
}

/* ---------- render + events */

let lastFocusTarget = null;
function rememberFocus(target) {
  if (!target) return;
  if (target.dataset.pageNav) lastFocusTarget = `[data-page-nav="${CSS.escape(target.dataset.pageNav)}"][aria-label="${CSS.escape(target.getAttribute("aria-label"))}"]`;
  else if (target.dataset.logSection) lastFocusTarget = `[data-log-section="${target.dataset.logSection}"]`;
  else if (target.dataset.aboutStatus) lastFocusTarget = `[data-about-status="${target.dataset.aboutStatus}"]`;
  else if (target.dataset.skillStanding) lastFocusTarget = `[data-skill-standing="${target.dataset.skillStanding}"]`;
  else if (target.dataset.toggleSummary !== undefined) lastFocusTarget = `[data-toggle-summary]`;
  else if (target.dataset.toggleProjectRow) lastFocusTarget = `[data-toggle-project-row="${target.dataset.toggleProjectRow}"]`;
  else if (target.dataset.toggleProjGroup) lastFocusTarget = `[data-toggle-proj-group="${target.dataset.toggleProjGroup}"]`;
  else if (target.dataset.toggleNotes) lastFocusTarget = `[data-toggle-notes="${target.dataset.toggleNotes}"]`;
  else if (target.dataset.toggleFolders !== undefined) lastFocusTarget = `[data-toggle-folders]`;
  else if (target.dataset.quotes) lastFocusTarget = `[data-quotes="${target.dataset.quotes}"]`;
  else if (target.dataset.open) lastFocusTarget = `[data-open="${target.dataset.open}"]`;
  else if (target.dataset.row) lastFocusTarget = `[data-row="${target.dataset.row}"]`;
  else if (target.matches(".legend-summary")) lastFocusTarget = ".legend-summary";
  else lastFocusTarget = null;
}

function restoreFocus() {
  if (!lastFocusTarget) return;
  let el = document.querySelector(lastFocusTarget);
  if (el?.disabled) el = el.closest(".pager-nav")?.querySelector("button:not(:disabled)");
  if (el) {
    try { el.focus({ preventScroll: true }); } catch (e) {}
  }
  lastFocusTarget = null;
}

function render() {
  if (!S.data) return;
  if (!lastFocusTarget && document.activeElement?.closest("#view")) rememberFocus(document.activeElement);
  if (!S.week && S.data.weeks.length) S.week = S.data.weeks[0].week;
  renderHeader();
  renderTabs();
  const view = { log: viewLog, projects: viewProjects, about: viewAbout, runs: viewRuns }[S.tab]();
  $("#view").innerHTML = view;
  if (S.tab === "log") {
    // Timeouts, not animation frames: frames pause in background tabs, and text reflows once Barlow arrives.
    setTimeout(fillRuledPage, 0);
    document.fonts.ready.then(() => setTimeout(fillRuledPage, 0));
  }
  document.title = `${{ log: `Week ${S.week ? weekNo(S.week) : ""}`, projects: "Projects", about: `About ${S.data.owner}`, runs: "Runs" }[S.tab]} · Logbook`;
  restoreFocus();
}

/* A logbook page keeps its ruled lines down to the foot: pad the log with blank rows
   until it is as tall as the remarks margin beside it. */
function fillRuledPage() {
  const section = document.querySelector(".page > section");
  const margin = document.querySelector(".page > .margin");
  const body = document.querySelector("table.log tbody");
  if (!section || !margin || !body) return;
  body.querySelectorAll("tr.blank").forEach((r) => r.remove());
  if (document.querySelector("link[href=\"reading-room.css\"]")) return;
  if (getComputedStyle(document.querySelector(".page")).gridTemplateColumns.split(" ").length < 2) return;
  // Grid cells stretch to equal height, so measure the content, not the cell.
  const used = [...section.children].reduce((h, el) => h + el.offsetHeight, 0);
  const gap = margin.offsetHeight - used;
  const cols = document.querySelectorAll("table.log thead th").length;
  const rows = Math.floor(gap / 45);
  for (let i = 0; i < rows; i++) {
    const tr = document.createElement("tr");
    tr.className = "blank";
    tr.setAttribute("aria-hidden", "true");
    tr.innerHTML = "<td></td>".repeat(cols);
    body.appendChild(tr);
  }
  // The last ruled line absorbs the remainder so the totals end flush with the page frame.
  const last = body.querySelector("tr.blank:last-child");
  if (last) {
    const cell = last.firstElementChild;
    cell.style.height = `${44 + (gap - rows * 45)}px`;
    const left = margin.offsetHeight - [...section.children].reduce((h, el) => h + el.offsetHeight, 0);
    if (left > 0) cell.style.height = `${cell.offsetHeight + left}px`; // borders are not in the pitch; measure, then settle
  }
}
let resizeTimer;
window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => S.tab === "log" && fillRuledPage(), 120); });

function strikeAnimate(el) {
  if (el) el.classList.add("struck");
  return new Promise((r) => setTimeout(r, matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 250));
}

$("#view").addEventListener("click", async (ev) => {
  if (ev.target.closest("a")) return;
  const t = ev.target.closest("button, tr.row, tr.remark, [data-run], summary");
  if (!t) return;
  const ds = t.dataset;

  // Presentation-only controls (NEVER call write() or API)
  if (ds.pageNav) {
    rememberFocus(t);
    const page = Number(ds.page);
    const id = ds.pageNav;
    if (id === "ledger") S.logPages.ledger = page;
    else if (id === "log:skills") S.logPages.skills = page;
    else if (id === "log:learned") S.logPages.learned = page;
    else if (id === "log:highlights") S.logPages.highlights = page;
    else if (id === "about-status") S.aboutPages.status = page;
    else if (id === "about-skills") S.aboutPages.skills = page;
    else if (id.startsWith("quotes:")) S.quotePages[id.slice(7)] = page;
    else if (id.startsWith("proj:")) S.projectPages[id.slice(5)] = page;
    else if (id.startsWith("timeline:")) {
      const pid = id.slice(9);
      S.projectDetailPages[pid] = { ...(S.projectDetailPages[pid] || {}), timeline: page };
    } else if (id === "runs") S.runsPage = page;
    return render();
  }

  if (ds.logSection) {
    rememberFocus(t);
    S.logSection = ds.logSection;
    return render();
  }

  if ("toggleSummary" in ds) {
    rememberFocus(t);
    S.logSummaryOpen = !S.logSummaryOpen;
    return render();
  }

  if (ds.toggleProjectRow) {
    rememberFocus(t);
    const key = "w:" + ds.toggleProjectRow;
    S.open.has(key) ? S.open.delete(key) : S.open.add(key);
    return render();
  }

  if (ds.aboutStatus) {
    rememberFocus(t);
    S.aboutStatus = ds.aboutStatus;
    S.aboutPages.status = 1;
    return render();
  }

  if (ds.skillStanding) {
    rememberFocus(t);
    S.aboutStanding = ds.skillStanding;
    S.aboutPages.skills = 1;
    return render();
  }

  if (ds.toggleProjGroup) {
    rememberFocus(t);
    const curr = S.projectGroupOpen[ds.toggleProjGroup] ?? (ds.toggleProjGroup === "ongoing");
    S.projectGroupOpen[ds.toggleProjGroup] = !curr;
    return render();
  }

  if (ds.toggleNotes) {
    rememberFocus(t);
    const nKey = ds.toggleNotes;
    const curr = S.projectNotesOpen[nKey] ?? !nKey.endsWith(":removed");
    S.projectNotesOpen[nKey] = !curr;
    return render();
  }

  if ("toggleFolders" in ds) {
    rememberFocus(t);
    S.foldersOpen = !S.foldersOpen;
    return render();
  }

  if (t.matches(".legend-summary")) {
    const details = t.closest("details");
    if (details) S.aboutLegendOpen = !details.open;
    return;
  }

  if (t.matches("tr.row, tr.remark")) {
    rememberFocus(t);
    const key = "w:" + ds.row;
    S.open.has(key) ? S.open.delete(key) : S.open.add(key);
    return render();
  }

  if ("weeklyEdit" in ds) return editWeekly();
  if ("weeklyCancel" in ds) { S.weekly = null; return render(); }
  if ("weeklySave" in ds) return saveWeekly();
  if (ds.weeklyCli) { S.weekly = { ...S.weekly, cli: ds.weeklyCli, model: "", effort: "" }; return render(); }
  if ("run" in ds) return startRun();
  if (ds.strike) {
    const text = S.data.items.find((i) => i.id === ds.strike)?.text || "";
    await strikeAnimate(t.closest(".entry"));
    return write("/api/item", { id: ds.strike, action: "remove" }, () =>
      toast(`Struck “${text.slice(0, 48)}${text.length > 48 ? "…" : ""}”. It won't be proposed again.`,
        { label: "Undo", run: () => write("/api/item", { id: ds.strike, action: "restore" }) }));
  }
  if (ds.restore) return write("/api/item", { id: ds.restore, action: "restore" }, () => toast("Restored."));
  if (ds.noteStrike) {
    await strikeAnimate(t.closest(".note"));
    return write("/api/note", { project: ds.project, id: ds.noteStrike, action: "remove" }, () =>
      toast("Note struck.", { label: "Undo", run: () => write("/api/note", { project: ds.project, id: ds.noteStrike, action: "restore" }) }));
  }
  if (ds.noteRestore) return write("/api/note", { project: ds.project, id: ds.noteRestore, action: "restore" }, () => toast("Note restored."));
  if (ds.quotes) {
    rememberFocus(t);
    S.quotes.has(ds.quotes) ? S.quotes.delete(ds.quotes) : S.quotes.add(ds.quotes);
    return render();
  }
  if (ds.kind) {
    rememberFocus(t);
    S.kind = ds.kind;
    S.aboutPages = { status: 1, skills: 1 };
    go("about", ds.kind);
    return render();
  }
  if (ds.open) {
    rememberFocus(t);
    S.open.has(ds.open) ? S.open.delete(ds.open) : S.open.add(ds.open);
    return render();
  }
  if (ds.mark) {
    const labels = { mark: "Marked as a project. The next run gives it full attention.", unmark: "Marked as not a real project.", auto: "Back to automatic." };
    return write("/api/mark", { id: ds.mark, value: ds.value }, () => toast(labels[ds.value]));
  }
  if (ds.rule) return write("/api/folder", { id: ds.folder, rule: ds.rule }, () => toast(`Saved. ${ds.folder} is remembered.`));
});

$("#view").addEventListener("keydown", (ev) => {
  const row = ev.target.closest && ev.target.closest("tr.row");
  if (row && (ev.key === "Enter" || ev.key === " ") && !ev.target.closest("button, a")) {
    ev.preventDefault();
    row.click();
  }
});

$("#view").addEventListener("change", (ev) => {
  const t = ev.target;
  if (t.dataset.weeklyField && S.weekly) {
    const field = t.dataset.weeklyField;
    S.weekly = { ...S.weekly, [field]: t.value, ...(field === "model" ? { effort: "" } : {}) };
    if (field !== "time") render();
    return;
  }
  if (t.dataset.folderRule !== undefined) {
    write("/api/folder", { id: t.dataset.folderRule, rule: t.value || null }, () => toast(`${t.dataset.folderRule}: ${t.options[t.selectedIndex].text.toLowerCase()}.`));
  }
});

$("#view").addEventListener("input", (ev) => {
  if ("folderFilter" in ev.target.dataset) {
    S.folderFilter = ev.target.value;
    const pos = ev.target.selectionStart;
    render();
    const input = document.querySelector("[data-folder-filter]");
    input.focus();
    input.setSelectionRange(pos, pos);
  }
});

document.addEventListener("keydown", (ev) => {
  if (!$("#week-picker").hidden || S.tab !== "log" || ev.target.closest("input, select, textarea") || ev.altKey || ev.ctrlKey || ev.metaKey) return;
  const weeks = S.data ? S.data.weeks.map((w) => w.week) : [];
  const i = weeks.indexOf(S.week);
  if (ev.key === "ArrowLeft" && weeks[i + 1]) go("log", weeks[i + 1]);
  if (ev.key === "ArrowRight" && weeks[i - 1]) go("log", weeks[i - 1]);
});

$("#theme").onclick = () => {
  const order = ["auto", "dark", "light"];
  const next = order[(order.indexOf(document.documentElement.dataset.theme || "auto") + 1) % 3];
  if (next === "auto") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = next;
  try { next === "auto" ? localStorage.removeItem("brain-theme") : localStorage.setItem("brain-theme", next); } catch (e) {}
  renderHeader();
};

$("#runnow").onclick = startRun;
$("#runpick").onclick = () => toggleRunMenu();
document.addEventListener("click", (e) => {
  if (!$("#runmenu").hidden && e.target.isConnected && !e.target.closest("#runmenu, #runpick")) toggleRunMenu(false);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("#runmenu").hidden) { toggleRunMenu(false); $("#runpick").focus(); }
});

/* ---------- Run now: which CLI, model and effort */

const runCli = (x) => x.cli || ((x.model || "").startsWith("manual") ? "manual" : "agy");
const measured = (x) => (x.usage || {}).measured !== false && runCli(x) !== "manual";
function kTokens(n) {
  if (!n) return "0";
  return n < 1000 ? fmtNum(n) : n < 1e6 ? `${fmtNum(Math.round(n / 1000))}k` : `${(n / 1e6).toFixed(2)}M`;
}

// What a choice resolves to when nothing is picked: the vault's [model] for its own CLI,
// otherwise the CLI's own default (the Codex config model, or the newest Gemini Flash for agy).
function resolved() {
  const m = S.models || { clis: {}, config: {} };
  const cfg = m.config || {};
  const clis = Object.keys(m.clis);
  let cli = S.choice.cli || cfg.cli || "agy";
  if (S.models && clis.length && !clis.includes(cli)) cli = clis[0];
  const own = cli === (cfg.cli || "agy");
  const fallback = (own && cfg.model) || (m.clis[cli] || {}).default || null;
  const model = S.choice.model || fallback;
  const effort = S.choice.effort || (own && !S.choice.model ? cfg.effort : null) || null;
  return { cli, model, fallback, effort };
}
function choiceLabel() {
  const r = resolved();
  return [CLI_LABEL[r.cli] || r.cli, r.model || (r.cli === "agy" ? "newest Gemini Flash" : "its default model"),
    r.effort ? `${r.effort} effort` : ""].filter(Boolean).join(" · ");
}

async function toggleRunMenu(open) {
  const menu = $("#runmenu");
  open = open ?? menu.hidden;
  menu.hidden = !open;
  $("#runpick").setAttribute("aria-expanded", String(open));
  if (!open) return;
  if (!S.models) {
    menu.innerHTML = `<h3>Run now with</h3><p class="hint loading">${icon("spin", "spin")}Asking the CLIs for their models…</p>`;
    try { S.models = await api("/api/models"); } catch (e) { menu.innerHTML = `<p class="hint">${esc(e.message)}</p>`; return; }
    renderHeader();
  }
  if (!menu.hidden) renderRunMenu();
}

function renderRunMenu() {
  const menu = $("#runmenu");
  const m = S.models;
  const clis = Object.keys(m.clis);
  if (!clis.length) {
    menu.innerHTML = `<h3>Run now with</h3><p class="hint">Neither the Antigravity CLI (agy) nor the Codex CLI was found on this PC.</p>`;
    return;
  }
  const r = resolved();
  const models = m.clis[r.cli].models || [];
  const current = models.find((x) => x.id === r.model);
  const efforts = current ? current.efforts || [] : (r.cli === "agy" && !r.model ? [] : ["low", "medium", "high"]);
  const defaultName = r.fallback || (r.cli === "agy" ? "newest Gemini Flash" : "Codex default");
  const cfg = m.config || {};
  const cfgText = [CLI_LABEL[cfg.cli] || cfg.cli, cfg.model, cfg.effort].filter(Boolean).join(" · ");
  menu.innerHTML = `
    <h3>Run now with</h3>
    <div class="seg" role="group" aria-label="CLI">${["agy", "codex"].map((c) =>
      `<button type="button" data-cli="${c}" aria-pressed="${c === r.cli}" ${m.clis[c] ? "" : "disabled title=\"Not installed\""}>${CLI_LABEL[c]}</button>`).join("")}</div>
    <label class="field">Model
      <select data-model>
        <option value="">Default (${esc(defaultName)})</option>
        ${models.map((x) => `<option value="${esc(x.id)}" ${x.id === S.choice.model ? "selected" : ""}>${esc(x.label)}${x.label === x.id ? "" : ` · ${esc(x.id)}`}</option>`).join("")}
      </select></label>
    <label class="field">Reasoning effort
      <select data-effort ${efforts.length ? "" : "disabled"}>
        <option value="">${efforts.length ? `Default${current && current.default_effort ? ` (${esc(current.default_effort)})` : ""}` : "Set by the model's name"}</option>
        ${efforts.map((e) => `<option value="${esc(e)}" ${e === r.effort ? "selected" : ""}>${esc(e)}</option>`).join("")}
      </select></label>
    <p class="hint">Scheduled runs use the Weekly run settings on the Runs page (now ${esc(cfgText || "Antigravity")}). Tokens are counted either way.</p>
    <div class="actions">
      <button class="btn ghost small" type="button" data-reset>Use defaults</button>
      <button class="btn primary" type="button" data-go ${S.data.run && S.data.run.running ? "disabled" : ""}>${icon("run")}Run now</button>
    </div>`;
  menu.querySelectorAll("[data-cli]").forEach((b) => (b.onclick = () => {
    S.choice = { cli: b.dataset.cli };
    saveChoice(); renderRunMenu(); renderHeader();
  }));
  menu.querySelector("[data-model]").onchange = (e) => {
    S.choice = { cli: r.cli, model: e.target.value || undefined };
    saveChoice(); renderRunMenu(); renderHeader();
  };
  menu.querySelector("[data-effort]").onchange = (e) => {
    S.choice = { ...S.choice, cli: r.cli, effort: e.target.value || undefined };
    saveChoice(); renderHeader();
  };
  menu.querySelector("[data-reset]").onclick = () => { S.choice = {}; saveChoice(); renderRunMenu(); renderHeader(); };
  menu.querySelector("[data-go]").onclick = () => { toggleRunMenu(false); startRun(); };
}

async function startRun() {
  try {
    const r = await api("/api/run", { cli: S.choice.cli || null, model: S.choice.model || null, effort: S.choice.effort || null });
    S.data.run = r;
    if (!r.started && !r.running) toast("An update is already running.");
    render();
    poll();
  } catch (e) { toast(e.message, null, true); }
}

function poll() {
  clearInterval(S.polling);
  S.polling = setInterval(async () => {
    try {
      const r = await api("/api/run");
      const was = S.data.run && S.data.run.running;
      S.data.run = r;
      if (!r.running) {
        clearInterval(S.polling);
        S.data = await api("/api/state");
        if (was) toast(r.error ? "The update failed. Details are in Runs." : "Logbook updated.", r.error ? { label: "Open Runs", run: () => go("runs") } : null, !!r.error);
      }
      render();
    } catch (e) { /* server restarting; keep polling */ }
  }, 1500);
}

async function boot() {
  readHash();
  try {
    S.data = await api("/api/state");
  } catch (e) {
    $("#view").innerHTML = `<div class="sheet empty"><h2>The dashboard server isn't answering</h2><p>Start it again with <code>sbrain dashboard</code>.</p></div>`;
    return;
  }
  if (S.tab === "projects") {
    const [tab, arg] = decodeURIComponent(location.hash.slice(1)).split("/");
    if (arg) syncProjectDeepLink(arg);
  }
  render();
  focusProjectDeepLink();
  if (S.data.run && S.data.run.running) poll();
}

boot();

/* Annual week selector, using the same Monday-based week numbering as the log. */
let pickerYear = null;
function showWeekPicker(open = true) {
  const picker = document.querySelector('#week-picker');
  const trigger = document.querySelector('#week-jump');
  picker.hidden = !open;
  trigger?.setAttribute('aria-expanded', String(open));
  if (!open) return;
  const top = Math.max(12, (trigger?.getBoundingClientRect().bottom || 100) + 8);
  picker.style.top = `${top}px`;
  picker.style.maxHeight = `${Math.max(200, innerHeight - top - 20)}px`;
  pickerYear = Number((S.week || S.data.thisWeek).split('-W')[0]);
  paintWeekPicker();
  (picker.querySelector('[aria-current="true"]') || picker.querySelector('.week-cell:not(:disabled)') || picker.querySelector('[data-year]'))?.focus();
}
function paintWeekPicker() {
  const total = weekNo(weekLabel(new Date(Date.UTC(pickerYear, 11, 28))));
  const logged = new Set(S.data.weeks.map(w => w.week));
  const cells = Array.from({length:total}, (_,n) => {
    const week = `${pickerYear}-W${String(n+1).padStart(2,'0')}`;
    const first = weekStart(week);
    const date = `${MONTHS[first.getUTCMonth()]} ${first.getUTCDate()}`;
    return `<button class="week-cell" type="button" data-select-week="${week}" ${logged.has(week)?'':'disabled'} ${week===S.week?'aria-current="true"':''} aria-label="Week ${n+1}, ${esc(weekSpan(week))}${logged.has(week)?'':', no saved entry'}"><span>W${String(n+1).padStart(2,'0')}</span><small>${esc(date)}</small></button>`;
  }).join('');
  document.querySelector('#week-picker').innerHTML = `
    <div class="week-picker-head"><h2>Choose a week</h2><div class="year-pager"><button class="btn ghost icon" type="button" data-year="-1" aria-label="Previous year">${icon('left')}</button><b>${pickerYear}</b><button class="btn ghost icon" type="button" data-year="1" aria-label="Next year">${icon('right')}</button></div><button class="btn ghost" type="button" data-close-weeks>Close</button></div>
    <div class="week-grid" aria-label="Weeks in ${pickerYear}">${cells}</div>
    <div class="week-picker-foot"><p>Select a saved week to open it. Dashed weeks have no saved entry. The outlined week is the one you’re viewing.</p><button class="btn" type="button" data-latest-week>Latest entry</button></div>`;
}
document.addEventListener('click', e => {
  if (e.target.closest('#week-jump')) {
    showWeekPicker(document.querySelector('#week-picker').hidden); return;
  }
  const picker = document.querySelector('#week-picker');
  if (picker.hidden) return;
  const year = e.target.closest('[data-year]');
  if (year) {
    const direction = year.dataset.year;
    pickerYear += Number(direction); paintWeekPicker();
    picker.querySelector(`[data-year="${direction}"]`).focus(); return;
  }
  const chosen = e.target.closest('[data-select-week]');
  if (chosen && !chosen.disabled) {
    showWeekPicker(false); go('log',chosen.dataset.selectWeek);
    setTimeout(()=>document.querySelector('#week-jump')?.focus(),0); return;
  }
  if (e.target.closest('[data-latest-week]')) {
    showWeekPicker(false); go('log',S.data.weeks[0].week); return;
  }
  if (e.target.closest('[data-close-weeks]') || !e.target.closest('#week-picker')) {
    showWeekPicker(false); document.querySelector('#week-jump')?.focus();
  }
});
document.addEventListener('keydown',e => {
  const picker = document.querySelector('#week-picker');
  if (picker.hidden) return;
  if (e.key === 'Escape') { e.preventDefault();showWeekPicker(false);document.querySelector('#week-jump')?.focus(); }
  if (e.target.matches('.week-cell') && ['ArrowLeft','ArrowRight'].includes(e.key)) {
    e.preventDefault();e.stopImmediatePropagation();
    const buttons=[...picker.querySelectorAll('.week-cell:not(:disabled)')];
    const next=buttons.indexOf(e.target)+(e.key==='ArrowRight'?1:-1);
    buttons[Math.max(0,Math.min(buttons.length-1,next))]?.focus();
  }
});
