"use strict";

/* Turns a vault Markdown document into a designed page rather than plain rendered Markdown.
   Parsing is deliberately small: the vault's notes use headings, paragraphs, lists, tables,
   fenced code, and quotes. Every block is then given a presentation that suits its shape:
   the first heading and paragraph become a masthead and lede, second-level sections get
   numerals, "**Label:** text" lists become fact cards, dated lists become a timeline, tables
   become ledgers, and commands become copyable chips. Everything is escaped before markup. */

const GuideRender = (() => {
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  /* ---------- parse */

  function parse(md) {
    const lines = md.replace(/\r\n?/g, "\n").replace(/<!--[\s\S]*?-->/g, "").split("\n");
    const blocks = [];
    let i = 0;
    const isTable = (l) => /^\s*\|.*\|\s*$/.test(l);
    const listRe = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) { i++; continue; }
      let m;
      if ((m = line.match(/^```\s*([\w-]*)/))) {
        const body = [];
        i++;
        while (i < lines.length && !/^```/.test(lines[i])) body.push(lines[i++]);
        i++;
        blocks.push({ type: "code", lang: m[1], text: body.join("\n") });
        continue;
      }
      if ((m = line.match(/^(#{1,6})\s+(.*?)\s*#*\s*$/))) {
        blocks.push({ type: "heading", level: m[1].length, text: m[2] });
        i++;
        continue;
      }
      if (/^\s*([-*_])\s*\1\s*\1[\s\1]*$/.test(line)) { blocks.push({ type: "hr" }); i++; continue; }
      if (isTable(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
        const split = (l) => l.trim().replace(/^\|/, "").replace(/\|$/, "").split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
        const head = split(line);
        const align = split(lines[i + 1]).map((c) => (/^:?-+:$/.test(c) ? "right" : /^:-+:$/.test(c) ? "center" : ""));
        const rows = [];
        i += 2;
        while (i < lines.length && isTable(lines[i])) rows.push(split(lines[i++]));
        blocks.push({ type: "table", head, rows, align });
        continue;
      }
      if (/^\s*>/.test(line)) {
        const body = [];
        while (i < lines.length && /^\s*>/.test(lines[i])) body.push(lines[i++].replace(/^\s*>\s?/, ""));
        blocks.push({ type: "quote", text: body.join(" ") });
        continue;
      }
      if (listRe.test(line)) {
        const ordered = /\d/.test(line.match(listRe)[2]);
        const items = [];
        while (i < lines.length && (listRe.test(lines[i]) || (/^\s{2,}\S/.test(lines[i]) && items.length))) {
          const lm = lines[i].match(listRe);
          if (lm && (lm[1].length < 2 || !items.length)) items.push({ text: lm[3], children: [] });
          else if (lm) items[items.length - 1].children.push(lm[3]);
          else items[items.length - 1].text += " " + lines[i].trim();
          i++;
        }
        blocks.push({ type: "list", ordered, items });
        continue;
      }
      const body = [line.trim()];
      i++;
      while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|```|\s*>|\s*\|)/.test(lines[i]) && !listRe.test(lines[i])) body.push(lines[i++].trim());
      blocks.push({ type: "para", text: body.join(" ") });
    }
    return blocks;
  }

  /* ---------- inline */

  function inline(text, ctx) {
    const codes = [];
    let s = String(text).replace(/`([^`]+)`/g, (_, c) => { codes.push(c); return `\u0000${codes.length - 1}\u0000`; });
    s = esc(s);
    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, label, href) => {
      const target = resolveLink(href, ctx);
      if (target) return `<a href="#guide/${encodeURIComponent(target)}" class="g-xref" data-doc="${esc(target)}">${label}</a>`;
      if (/^https?:\/\//.test(href)) return `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${label}</a>`;
      if (/^\/voice\/test(\?id=[\w-]+)?$/.test(href)) return `<a href="${esc(href)}" class="g-open" target="_blank" rel="noopener">${label}</a>`;
      return `<span class="g-ref">${label}</span>`;
    });
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>").replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>").replace(/(^|\W)_([^_\n]+)_(?!\w)/g, "$1<em>$2</em>");
    s = s.replace(/\u0000(\d+)\u0000/g, (_, n) => codeChip(codes[+n], ctx));
    return s;
  }

  function resolveLink(href, ctx) {
    if (!ctx || !ctx.known || /^[a-z]+:/i.test(href)) return null;
    const clean = href.split("#")[0].replace(/^\.\//, "");
    const base = (ctx.path || "").split("/").slice(0, -1);
    const parts = [...base];
    for (const p of clean.split("/")) {
      if (p === "..") parts.pop();
      else if (p && p !== ".") parts.push(p);
    }
    const joined = parts.join("/");
    return ctx.known.has(joined) ? joined : ctx.known.has(clean) ? clean : null;
  }

  function codeChip(code, ctx) {
    const isCommand = /^(sbrain|codex|powershell|agy|git|uv|python)\b/.test(code);
    const known = ctx && ctx.known && ctx.known.has(code) ? code : null;
    if (known) return `<a href="#guide/${encodeURIComponent(known)}" class="g-path g-xref" data-doc="${esc(known)}">${esc(code)}</a>`;
    if (isCommand) return `<button type="button" class="g-cmd" data-copy="${esc(code)}" title="Copy"><code>${esc(code)}</code><span class="g-copy" aria-hidden="true">copy</span></button>`;
    return `<code class="g-code">${esc(code)}</code>`;
  }

  /* ---------- shapes */

  const DATE_LEAD = /^\*\*(\d{4}-\d{2}-\d{2}|\d{4}-W\d{2})[^*]*\*\*\s*:?\s*/;
  const LABEL_LEAD = /^\*\*([^*]{1,60}?)[:.]?\*\*\s*[:.—-]?\s*(.+)$/;
  const EVIDENCE = /\s*\*\(([^)]*(?:said in|seen in)[^)]*)\)\*\s*$/;

  function listBlock(b, ctx) {
    const items = b.items;
    if (items.length >= 2 && items.every((it) => DATE_LEAD.test(it.text))) {
      return `<ol class="g-timeline">${items.map((it) => {
        const date = it.text.match(DATE_LEAD)[1];
        const rest = it.text.replace(DATE_LEAD, "");
        return `<li><time>${esc(date)}</time><div>${inline(rest, ctx)}${children(it, ctx)}</div></li>`;
      }).join("")}</ol>`;
    }
    if (items.some((it) => EVIDENCE.test(it.text))) {
      return `<ul class="g-evidence">${items.map((it) => {
        const m = it.text.match(EVIDENCE);
        const text = it.text.replace(EVIDENCE, "");
        return `<li><p>${inline(text, ctx)}</p>${m ? `<small>${esc(m[1])}</small>` : ""}</li>`;
      }).join("")}</ul>`;
    }
    if (b.ordered) {
      return `<ol class="g-steps">${items.map((it, n) => {
        return `<li><span class="g-step-n" aria-hidden="true">${n + 1}</span><div>${inline(it.text, ctx)}${children(it, ctx)}</div></li>`;
      }).join("")}</ol>`;
    }
    const labelled = items.filter((it) => LABEL_LEAD.test(it.text)).length;
    if (items.length >= 2 && labelled >= Math.ceil(items.length * 0.75)) {
      // Labelled rules read as a numbered set: people refer to "rule 2", so the numerals carry meaning.
      return `<ol class="g-rules">${items.map((it, n) => {
        const m = it.text.match(LABEL_LEAD);
        return `<li><span class="g-rule-n" aria-hidden="true">${String(n + 1).padStart(2, "0")}</span>`
          + (m ? `<b>${inline(m[1], ctx)}</b><p>${inline(m[2], ctx)}${children(it, ctx)}</p>` : `<b></b><p>${inline(it.text, ctx)}</p>`) + `</li>`;
      }).join("")}</ol>`;
    }
    return `<ul class="g-list">${items.map((it) => `<li>${inline(it.text, ctx)}${children(it, ctx)}</li>`).join("")}</ul>`;
  }

  function children(it, ctx) {
    return it.children.length ? `<ul class="g-sub">${it.children.map((c) => `<li>${inline(c, ctx)}</li>`).join("")}</ul>` : "";
  }

  const NUMERIC = /^[\s~≈<>+\-–]*[\d.,]+\s*(%|[kKmM]|ms|s|h|x)?\s*$/;

  function tableBlock(b, ctx) {
    const numericCol = b.head.map((_, c) => b.rows.length > 0 && b.rows.every((r) => !r[c] || NUMERIC.test(r[c])));
    const keyValue = b.head.length === 2 && b.rows.every((r) => (r[0] || "").length <= 60);
    const cls = keyValue ? "g-ledger g-kv" : "g-ledger";
    return `<div class="g-ledger-wrap"><table class="${cls}"><thead><tr>${b.head.map((h, c) => `<th class="${numericCol[c] ? "n" : ""}">${inline(h, ctx)}</th>`).join("")}</tr></thead>
      <tbody>${b.rows.map((r) => `<tr>${b.head.map((h, c) => `<td class="${numericCol[c] ? "n" : ""}${(r[c] || "").length <= 18 ? " nw" : ""}" data-label="${esc(h.replace(/[*`_]/g, ""))}">${inline(r[c] || "", ctx)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  }

  function codeBlock(b) {
    const lines = b.text.split("\n");
    const commands = lines.every((l) => !l.trim() || /^\s*(sbrain|codex|powershell|agy|git|uv|python|cd|npm|npx)\b/.test(l));
    if (commands) {
      return `<div class="g-commands">${lines.filter((l) => l.trim()).map((l) => `<button type="button" class="g-cmd g-cmd-line" data-copy="${esc(l.trim())}"><code>${esc(l.trim())}</code><span class="g-copy" aria-hidden="true">copy</span></button>`).join("")}</div>`;
    }
    return `<figure class="g-codeblock">${b.lang ? `<figcaption>${esc(b.lang)}</figcaption>` : ""}<pre><code>${esc(b.text)}</code></pre></figure>`;
  }

  function paraBlock(b, ctx) {
    const m = b.text.match(/^\*\*([^*]{1,60}?)[:.]?\*\*\s*[:.]?\s*(.+)$/);
    if (m) return `<aside class="g-callout"><b>${inline(m[1], ctx)}</b><p>${inline(m[2], ctx)}</p></aside>`;
    return `<p>${inline(b.text, ctx)}</p>`;
  }

  /* ---------- document */

  function render(doc, ctx = {}) {
    const isJson = doc.path.endsWith(".json");
    if (isJson) return renderJson(doc);
    const blocks = parse(doc.text);
    const titleIdx = blocks.findIndex((b) => b.type === "heading" && b.level === 1);
    const title = titleIdx >= 0 ? blocks[titleIdx].text : doc.title;
    const rest = blocks.filter((_, n) => n !== titleIdx);
    let lede = "";
    if (rest[0] && rest[0].type === "para" && !/^\*\*/.test(rest[0].text)) lede = inline(rest.shift().text, ctx);
    const sections = [];
    let current = { title: null, blocks: [] };
    for (const b of rest) {
      if (b.type === "heading" && b.level === 2) {
        if (current.title || current.blocks.length) sections.push(current);
        current = { title: b.text, blocks: [] };
      } else current.blocks.push(b);
    }
    if (current.title || current.blocks.length) sections.push(current);
    const words = doc.text.split(/\s+/).length;
    const toc = sections.filter((s) => s.title);
    let n = 0;
    const body = sections.map((s) => {
      const id = s.title ? `s${++n}` : "";
      const inner = s.blocks.map((b) => blockHtml(b, ctx)).join("");
      if (!s.title) return `<div class="g-section g-intro">${inner}</div>`;
      return `<section class="g-section" id="${ctx.idPrefix || "g"}-${id}"><h2>${inline(s.title, ctx)}</h2>${inner}</section>`;
    }).join("");
    return {
      title, lede, words, toc: toc.map((s, k) => ({ title: s.title, id: `${ctx.idPrefix || "g"}-s${k + 1}` })),
      html: body,
    };
  }

  function blockHtml(b, ctx) {
    switch (b.type) {
      case "heading": return `<h${Math.min(6, b.level + 1)} class="g-h${b.level}">${inline(b.text, ctx)}</h${Math.min(6, b.level + 1)}>`;
      case "para": return paraBlock(b, ctx);
      case "list": return listBlock(b, ctx);
      case "table": return tableBlock(b, ctx);
      case "code": return codeBlock(b);
      case "quote": return `<blockquote class="g-quote">${inline(b.text, ctx)}</blockquote>`;
      case "hr": return `<hr class="g-rule">`;
      default: return "";
    }
  }

  function renderJson(doc) {
    let data;
    try { data = JSON.parse(doc.text); } catch (e) { return { title: doc.title, lede: "", words: 0, toc: [], html: `<pre class="g-raw">${esc(doc.text)}</pre>` }; }
    const tiles = [];
    const nested = [];
    for (const [k, v] of Object.entries(data)) {
      const label = k.replace(/_/g, " ");
      if (v && typeof v === "object" && !Array.isArray(v)) nested.push([label, v]);
      else if (Array.isArray(v)) nested.push([label, v.length ? Object.fromEntries(v.map((x, n) => [n + 1, x])) : { none: "—" }]);
      else tiles.push([label, typeof v === "number" ? v.toLocaleString("en-US") : String(v ?? "—").replace("T", " ").slice(0, 19)]);
    }
    const html = `<div class="g-ledger-wrap"><table class="g-ledger g-kv"><tbody>${tiles.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join("")}</tbody></table></div>` +
      nested.map(([k, obj]) => `<section class="g-section"><h2>${esc(k)}</h2>
        <div class="g-ledger-wrap"><table class="g-ledger g-kv"><tbody>${Object.entries(obj).map(([a, b]) => `<tr><td>${esc(a)}</td><td class="${typeof b === "number" ? "n" : ""}">${esc(typeof b === "number" ? b.toLocaleString("en-US") : b)}</td></tr>`).join("")}</tbody></table></div></section>`).join("");
    return { title: doc.title, lede: "Machine-written summary, shown as figures.", words: 0, toc: [], html };
  }

  return { render, parse, esc };
})();
