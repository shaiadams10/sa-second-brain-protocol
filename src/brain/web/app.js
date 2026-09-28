"use strict";

/* Second brain logbook. Reads /api/state, writes through small POSTs, re-renders. */

const SCALE = 38; // fixed attention scale: the digest formula tops out near 37.5, so weeks compare honestly
const KIND_LABEL = { preference: "Preference", dislike: "Dislike", "work-style": "Work style", skill: "Skill", interest: "Interest" };
const NOTE_ORDER = [["goal", "Goals"], ["decision", "Decisions"], ["milestone", "Milestones"], ["stack", "Stack"], ["preference", "Preferences here"], ["open-problem", "Open problems"]];
const STATE_LABEL = { ongoing: "Ongoing", exploring: "Exploring", "on-hold": "On hold", earlier: "Earlier" };
const RULES = [["", "Automatic"], ["project", "One project"], ["project+subprojects", "With sub-projects"], ["collection", "Collection"], ["ignore", "Ignore"]];

const S = { data: null, tab: "log", week: null, open: new Set(), quotes: new Set(), kind: "all", folderFilter: "", polling: null, lastRunFinished: null };
const $ = (sel) => document.querySelector(sel);

/* ---------- helpers */

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const icon = (name, cls = "") => `<svg class="i ${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const plural = (n, word, many) => `${n} ${n === 1 ? word : many || word + "s"}`;
const sum = (list, key) => list.reduce((a, x) => a + (x[key] || 0), 0);
const fmtNum = (n) => (n || 0).toLocaleString("en-US");

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
  if (S.tab === "log" && arg) S.week = arg;
  if (S.tab === "projects" && arg) S.open.add(arg);
}
function go(tab, arg) {
  location.hash = arg ? `${tab}/${encodeURIComponent(arg)}` : tab;
}
window.addEventListener("hashchange", () => { readHash(); render(); $("#view").focus({ preventScroll: true }); });

/* ---------- header + tabs */

function renderHeader() {
  const d = S.data;
  $("#owner").textContent = `Second brain of ${d.owner}`;
  const weeks = d.weeks.map((w) => w.week);
  if (S.tab === "log" && S.week) {
    const i = weeks.indexOf(S.week);
    const older = weeks[i + 1], newer = weeks[i - 1];
    $("#pager").innerHTML = `
      <button class="btn ghost icon" type="button" ${older ? "" : "disabled"} data-week="${esc(older || "")}" aria-label="Previous week">${icon("left")}</button>
      <div class="label">Week ${weekNo(S.week)}<small>${esc(weekSpan(S.week))}</small></div>
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
  btn.title = running ? "An update is running" : "Update the brain now, including the week in progress";

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
  const byId = Object.fromEntries(d.projects.map((p) => [p.id, p]));
  const rows = Object.entries(w.projects).sort((a, b) => (b[1].attention || 0) - (a[1].attention || 0));
  const stats = rows.map(([, p]) => p);
  const days = new Set(stats.flatMap((p) => p.active_days || []));

  const upTo = d.weeks.filter((x) => x.week <= w.week);
  const fwd = upTo.flatMap((x) => Object.values(x.projects));
  const fwdDays = new Set(fwd.flatMap((p) => p.active_days || []));

  const body = rows.map(([pid, p]) => {
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
      <tr class="remark ${open ? "open" : ""}" data-row="${esc(pid)}"><td colspan="8"><p>${esc(p.summary || "Files changed, but no AI conversations this week.")}</p>
        ${open && proj ? `<div class="links"><a href="#projects/${encodeURIComponent(pid)}">Open ${esc(proj.name)} in Projects</a></div>` : ""}</td></tr>`;
  }).join("");

  const learnedIn = (i) => i.evidence.some((e) => e.week === w.week);
  const inked = d.items.filter((i) => i.status === "active" && i.promoted === w.week);
  const pencil = d.items.filter((i) => i.status === "candidate" && learnedIn(i));
  const struck = d.items.filter((i) => i.status === "removed" && learnedIn(i));

  return `
    <div class="page">
      <section aria-label="Log for week ${weekNo(w.week)}">
        ${w.partial ? `<div class="banner">${icon("alert")}Week in progress. This page is rewritten when the week ends.</div>` : ""}
        <table class="log">
          <thead><tr>
            <th scope="col">Project</th><th scope="col" style="text-align:left">Status</th><th scope="col">Days</th>
            <th scope="col" class="hide-sm">Conv.</th><th scope="col" class="hide-sm">Exch.</th><th scope="col" class="hide-sm">Commits</th><th scope="col" class="hide-sm">Files</th>
            <th scope="col" class="att">Attention</th>
          </tr></thead>
          <tbody>${body}</tbody>
          <tfoot>
            <tr><td>Totals this page</td><td></td><td></td>${numCell(sum(stats, "conversations"), "hide-sm")}${numCell(sum(stats, "exchanges"), "hide-sm")}${numCell(sum(stats, "commits"), "hide-sm")}${numCell(sum(stats, "files"), "hide-sm")}<td style="text-align:left">${plural(rows.length, "project")} · ${plural(days.size, "active day")}</td></tr>
            <tr class="fwd"><td>Brought forward</td><td></td><td></td>${numCell(sum(fwd, "conversations"), "hide-sm")}${numCell(sum(fwd, "exchanges"), "hide-sm")}${numCell(sum(fwd, "commits"), "hide-sm")}${numCell(sum(fwd, "files"), "hide-sm")}<td style="text-align:left">${plural(upTo.length, "week")} · ${plural(fwdDays.size, "active day")}</td></tr>
          </tfoot>
        </table>
      </section>
      <aside class="margin" aria-label="Remarks">
        ${w.headline ? `<p class="headline">${esc(w.headline)}</p>` : ""}
        ${w.summary ? `<p class="summary">${esc(w.summary)}</p>` : ""}
        ${(w.highlights || []).length ? `<h2>Highlights</h2><ul class="hl">${w.highlights.map((h) => `<li>${esc(h)}</li>`).join("")}</ul>` : ""}
        <h2>Learned about ${esc(d.owner)}</h2>
        ${inked.length + pencil.length + struck.length
          ? `<ul class="entries">${[...inked, ...pencil, ...struck].map((i) => entry(i, false)).join("")}</ul>`
          : `<p class="summary" style="margin:0">Nothing new about ${esc(d.owner)} this week. That is normal.</p>`}
        <p class="meta">Written ${esc(w.ran_at ? day(w.ran_at) : "")}${w.model ? ` by ${esc(w.model)}` : ""}.</p>
      </aside>
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
  return `
    <li class="entry ${cls}" data-entry="${esc(i.id)}">
      <span class="text strikable">${esc(i.text)}</span>
      <span class="acts">${act}</span>
      <span class="sub"><span class="kind">${esc(KIND_LABEL[i.kind] || i.kind)}</span>${esc(seen)}
        ${withQuotes && i.evidence.length ? ` · <button class="linkish" type="button" data-quotes="${esc(i.id)}" aria-expanded="${quotesOpen}">${quotesOpen ? "Hide" : "Show"} ${plural(i.evidence.length, "quote")}</button>` : ""}</span>
      ${withQuotes && quotesOpen ? `<ul class="quotes">${i.evidence.map((e) => `<li><q>${esc(e.quote)}</q><small>${esc(name(e.project))} · ${esc(e.week)}</small></li>`).join("")}</ul>` : ""}
    </li>`;
}

function viewAbout() {
  const d = S.data;
  const kinds = ["all", ...Object.keys(KIND_LABEL).filter((k) => d.items.some((i) => i.kind === k))];
  const pass = (i) => S.kind === "all" || i.kind === S.kind;
  const order = (a, b) => b.evidence.length - a.evidence.length;
  const inked = d.items.filter((i) => i.status === "active" && pass(i)).sort(order);
  const pencil = d.items.filter((i) => i.status === "candidate" && pass(i)).sort(order);
  const struck = d.items.filter((i) => i.status === "removed" && pass(i));
  const block = (title, note, list, emptyText) => `
    <div class="section-head"><h2>${title}</h2><p>${note}</p></div>
    <div class="sheet">${list.length ? `<ul class="entries">${list.map((i) => entry(i)).join("")}</ul>` : `<p class="summary" style="margin:14px 0">${emptyText}</p>`}</div>`;
  return `
    <div class="about">
      <div>
        <div class="chips" role="group" aria-label="Filter by kind">
          <div class="segmented">${kinds.map((k) => `<button type="button" aria-pressed="${S.kind === k}" data-kind="${k}">${k === "all" ? "All" : esc(KIND_LABEL[k])}</button>`).join("")}</div>
        </div>
        ${block("Inked", `${plural(inked.length, "entry", "entries")} · confirmed in two projects or two weeks`, inked, "Nothing confirmed yet.")}
        ${block("Pencilled", `${plural(pencil.length, "entry", "entries")} · seen once, waiting to recur`, pencil, "Nothing waiting.")}
        ${struck.length ? block("Struck", `${plural(struck.length, "entry", "entries")} · never proposed again`, struck, "") : ""}
      </div>
      <aside class="legend" aria-label="How entries work">
        <p>The brain writes what your conversations show about you. Nothing waits for approval.</p>
        <span class="sample pencil">Pencilled entry</span>
        <p>Seen once. It stays in pencil until it shows up again in another project or another week.</p>
        <span class="sample ink">Inked entry</span>
        <p>Confirmed. It appears in <code>me/learned.md</code> in your vault.</p>
        <span class="sample struck text strikable">Struck entry</span>
        <p>Removed by you. Kept legible so you can undo it, and shown to the model as something never to propose again.</p>
      </aside>
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
  const notes = NOTE_ORDER.map(([k, title]) => {
    const list = p.notes.filter((n) => n.kind === k);
    return list.length ? `<h3>${title}</h3><ul class="notes">${list.map((n) => note(p.id, n, false)).join("")}</ul>` : "";
  }).join("");
  const removed = p.removed_notes.length ? `<h3>Struck notes</h3><ul class="notes">${p.removed_notes.map((n) => note(p.id, n, true)).join("")}</ul>` : "";
  return `
    <div class="detail" id="detail-${esc(p.id)}">
      <div>
        ${p.subprojects.length ? `<h3>Sub-projects</h3><div class="subs">${p.subprojects.map((s) => `<span>${esc(s)}</span>`).join("")}</div>` : ""}
        ${notes || removed ? notes + removed : `<p class="summary">No project notes yet. Notes appear once the brain has seen decisions, milestones, or open problems here.</p>`}
        ${p.page ? `<p class="meta" style="color:var(--ink-3);font-size:12.5px">In the vault: <code>${esc(p.page)}</code></p>` : ""}
      </div>
      <div>
        <h3>Timeline</h3>
        ${weeks.length ? `<ul class="timeline">${weeks.map((w) => `<li><b><a href="#log/${w.week}">Week ${weekNo(w.week)} · ${esc(weekSpan(w.week))}</a></b><p>${esc(w.projects[p.id].summary)}</p></li>`).join("")}</ul>` : `<p class="summary">No summarized weeks yet.</p>`}
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
    const rows = list.map((p) => {
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
    return `<div class="section-head"><h2>${STATE_LABEL[state]} · ${list.length}</h2><p>${notes[state]}</p></div><div class="sheet">${head}${rows}</div>`;
  }).join("");

  const f = S.folderFilter.toLowerCase();
  const groups = {};
  d.folders.filter((x) => !f || x.name.toLowerCase().includes(f) || (x.group || "").toLowerCase().includes(f))
    .forEach((x) => (groups[x.group || "Top level"] ||= []).push(x));
  const folderBlock = `
    <div class="section-head"><h2>All folders · ${d.folders.length}</h2>
      <input class="filter" type="search" placeholder="Filter folders" value="${esc(S.folderFilter)}" data-folder-filter aria-label="Filter folders"></div>
    <div class="sheet"><div class="folders">${Object.entries(groups).map(([g, list]) => `
      <div class="fgroup"><h4>${esc(g)}</h4>${list.map((x) => `
        <div class="folder ${x.tracked ? "tracked" : ""}"><span title="${esc(x.id)}">${esc(x.name)}${x.marked ? " " + icon("pin") : ""}</span>
          <select data-folder-rule="${esc(x.id)}" aria-label="How to treat ${esc(x.name)}">${RULES.map(([v, l]) => `<option value="${v}" ${(x.rule || "") === v ? "selected" : ""}>${l}</option>`).join("")}</select>
        </div>`).join("")}</div>`).join("") || `<p class="summary" style="padding:14px 18px;margin:0">No folder matches.</p>`}</div></div>`;

  return qBlock + (sections || `<div class="sheet empty"><h2>No project activity logged yet</h2><p>Projects appear here after the first weekly update.</p></div>`) + folderBlock;
}

/* ---------- runs */

function viewRuns() {
  const d = S.data;
  const r = d.run || {};
  const live = r.running || (r.lines && r.lines.length) ? `
    <div class="live"><h2>${r.running ? `${icon("spin", "spin")}Updating now` : r.error ? "Last Run now failed" : "Last Run now finished"}</h2>
      <pre>${(r.lines || []).map((l) => esc(l)).join("\n") || "Starting…"}${r.error ? `\n<b>${esc(r.error)}</b>` : ""}</pre></div>` : "";
  const rows = d.runs.map((x) => `
    <tr>
      <td>${esc(x.week)}${x.partial ? " <small style='color:var(--ink-3)'>in progress</small>" : ""}</td>
      <td>${esc(day(x.started))} ${esc(new Date(x.started).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }))}</td>
      <td><span class="${x.status === "ok" ? "ok" : "fail"}">${x.status === "ok" ? "Logged" : "Failed"}</span>${x.error ? `<div class="err">${esc(x.error)}</div>` : ""}</td>
      <td class="n">${x.seconds != null ? `${Math.floor(x.seconds / 60)}m ${String(x.seconds % 60).padStart(2, "0")}s` : "–"}</td>
      <td>${esc(x.model || "")}</td>
      <td class="n">${fmtNum((x.usage || {}).calls)}</td>
      <td class="n">${fmtNum(Math.round(((x.usage || {}).input_tokens || 0) / 1000))}k</td>
    </tr>`).join("");
  return live + `
    <div class="section-head"><h2>Run history</h2><p>${d.schedule && d.schedule.next && !d.schedule.next.startsWith("0001")
      ? `Next scheduled run ${esc(day(d.schedule.next))} at ${esc(new Date(d.schedule.next).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }))}. Missed weeks are caught up.`
      : "No weekly schedule installed. Run <code>sbrain install</code> to add one."} Every run is committed to the vault.</p></div>
    <div class="sheet">${d.runs.length ? `<table class="runs"><thead><tr><th>Week</th><th>Started</th><th>Result</th><th class="n">Duration</th><th>Model</th><th class="n">Calls</th><th class="n">Input</th></tr></thead><tbody>${rows}</tbody></table>`
      : `<div class="empty"><h2>No runs yet</h2><p>The first run logs last week.</p></div>`}</div>`;
}

/* ---------- render + events */

function render() {
  if (!S.data) return;
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
}

/* A logbook page keeps its ruled lines down to the foot: pad the log with blank rows
   until it is as tall as the remarks margin beside it. */
function fillRuledPage() {
  const section = document.querySelector(".page > section");
  const margin = document.querySelector(".page > .margin");
  const body = document.querySelector("table.log tbody");
  if (!section || !margin || !body) return;
  body.querySelectorAll("tr.blank").forEach((r) => r.remove());
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
  return new Promise((r) => setTimeout(r, matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 340));
}

$("#view").addEventListener("click", async (ev) => {
  if (ev.target.closest("a")) return;
  const t = ev.target.closest("button, tr.row, tr.remark, [data-run]");
  if (!t) return;
  const ds = t.dataset;
  if (t.matches("tr.row, tr.remark")) {
    const key = "w:" + ds.row;
    S.open.has(key) ? S.open.delete(key) : S.open.add(key);
    return render();
  }
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
  if (ds.quotes) { S.quotes.has(ds.quotes) ? S.quotes.delete(ds.quotes) : S.quotes.add(ds.quotes); return render(); }
  if (ds.kind) { S.kind = ds.kind; return render(); }
  if (ds.open) { S.open.has(ds.open) ? S.open.delete(ds.open) : S.open.add(ds.open); return render(); }
  if (ds.mark) {
    const labels = { mark: "Marked as a project. The next run gives it full attention.", unmark: "Marked as not a real project.", auto: "Back to automatic." };
    return write("/api/mark", { id: ds.mark, value: ds.value }, () => toast(labels[ds.value]));
  }
  if (ds.rule) return write("/api/folder", { id: ds.folder, rule: ds.rule }, () => toast(`Saved. ${ds.folder} is remembered.`));
});

$("#view").addEventListener("keydown", (ev) => {
  const row = ev.target.closest && ev.target.closest("tr.row");
  if (row && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); row.click(); }
});

$("#view").addEventListener("change", (ev) => {
  const t = ev.target;
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
  if (S.tab !== "log" || ev.target.closest("input, select, textarea") || ev.altKey || ev.ctrlKey || ev.metaKey) return;
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

async function startRun() {
  try {
    const r = await api("/api/run", {});
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
  render();
  if (S.data.run && S.data.run.running) poll();
}

boot();
