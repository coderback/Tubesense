// Small Markdown renderer. Builds DOM nodes directly (text nodes only), so model and document text can never
// become markup. Supports headings, paragraphs, lists (nested), blockquotes, fenced code, pipe tables, hr,
// bold, italic, strike, inline code, links, bare URLs and footnotes ([^n] and [^n]: ...).
import { h, safeUrl } from "./util.js";

const INLINE = [
  { name: "code", re: /`([^`]+)`/ },
  { name: "link", re: /\[([^\]]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/ },
  { name: "fn", re: /\[\^([^\]\s]+)\]/ },
  { name: "bold", re: /\*\*([^*]+?)\*\*|__([^_]+?)__/ },
  { name: "strike", re: /~~([^~]+?)~~/ },
  { name: "ital", re: /(?<![\w*])\*([^*\s][^*]*?)\*(?![\w*])|(?<![\w_])_([^_\s][^_]*?)_(?![\w_])/ },
  { name: "url", re: /https?:\/\/[^\s<>)\]]+[^\s<>)\].,;:!?'"]/ },
];

function inline(text, ctx) {
  const out = [];
  let rest = text;
  while (rest) {
    let best = null;
    for (const p of INLINE) {
      const m = p.re.exec(rest);
      if (m && (!best || m.index < best.m.index)) best = { p, m };
    }
    if (!best) { out.push(rest); break; }
    const { p, m } = best;
    if (m.index > 0) out.push(rest.slice(0, m.index));
    const g = m[1] ?? m[2];
    if (p.name === "code") out.push(h("code", null, g));
    else if (p.name === "bold") out.push(h("strong", null, inline(g, ctx)));
    else if (p.name === "strike") out.push(h("s", null, inline(g, ctx)));
    else if (p.name === "ital") out.push(h("em", null, inline(g, ctx)));
    else if (p.name === "link") {
      const u = safeUrl(m[2]);
      out.push(u ? h("a", { href: u, target: "_blank", rel: "noopener noreferrer" }, inline(m[1], ctx)) : m[1]);
    } else if (p.name === "url") {
      const u = safeUrl(m[0]);
      out.push(u ? h("a", { href: u, target: "_blank", rel: "noopener noreferrer" }, m[0]) : m[0]);
    } else if (p.name === "fn") {
      const id = m[1];
      out.push(h("sup", { class: "fnref" }, h("a", {
        href: "#", "aria-label": `Footnote ${id}`,
        onclick: (e) => {
          e.preventDefault();
          const t = ctx.root.querySelector(`[data-fn="${CSS.escape(id)}"]`);
          if (t) { t.scrollIntoView({ block: "center" }); t.classList.add("flash"); setTimeout(() => t.classList.remove("flash"), 1600); }
        },
      }, id)));
    }
    rest = rest.slice(m.index + m[0].length);
  }
  return out;
}

const isTableSep = (l) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(l) && l.includes("-");
const splitRow = (l) => l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
const listMatch = (l) => /^(\s*)([-*+]|\d+[.)])\s+(.*)$/.exec(l);

function blocks(lines, ctx) {
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    let m;
    if ((m = /^```\s*(\w*)/.exec(line))) {
      const buf = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]);
      i++;
      out.push(h("pre", { class: "md-code" }, h("code", null, buf.join("\n"))));
    } else if ((m = /^(#{1,6})\s+(.*?)\s*#*\s*$/.exec(line))) {
      const lvl = Math.min(6, m[1].length + 1); // page H1 is the screen title, so shift down one level
      out.push(h("h" + lvl, null, inline(m[2], ctx)));
      i++;
    } else if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { out.push(h("hr")); i++; }
    else if (line.includes("|") && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      const head = splitRow(line);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].trim() && lines[i].includes("|")) rows.push(splitRow(lines[i++]));
      out.push(h("div", { class: "table-wrap" }, h("table", { class: "md-table" },
        h("thead", null, h("tr", null, head.map((c) => h("th", null, inline(c, ctx))))),
        h("tbody", null, rows.map((r) => h("tr", null, head.map((_, k) => h("td", null, inline(r[k] ?? "", ctx)))))))));
    } else if (/^\s*>/.test(line)) {
      const buf = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) buf.push(lines[i++].replace(/^\s*>\s?/, ""));
      out.push(h("blockquote", null, blocks(buf, ctx)));
    } else if ((m = listMatch(line))) {
      const base = m[1].length, ordered = /\d/.test(m[2]);
      const items = [];
      while (i < lines.length) {
        const lm = listMatch(lines[i]);
        if (lm && lm[1].length === base) { items.push({ text: [lm[3]], sub: [] }); i++; }
        else if (lm && lm[1].length > base && items.length) {
          const sub = [];
          while (i < lines.length && (listMatch(lines[i])?.[1].length ?? 0) > base) sub.push(lines[i++]);
          items[items.length - 1].sub.push(...sub);
        } else if (lines[i].trim() && /^\s{2,}\S/.test(lines[i]) && items.length && !lm) items[items.length - 1].text.push(lines[i++].trim());
        else break;
      }
      out.push(h(ordered ? "ol" : "ul", null, items.map((it) => h("li", null, inline(it.text.join(" "), ctx), it.sub.length ? blocks(it.sub, ctx) : null))));
    } else {
      const buf = [];
      while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|```|\s*>|\s*([-*+]|\d+[.)])\s)/.test(lines[i]) &&
             !(lines[i].includes("|") && i + 1 < lines.length && isTableSep(lines[i + 1]))) buf.push(lines[i++].trim());
      if (!buf.length) { out.push(h("p", null, inline(line, ctx))); i++; } else out.push(h("p", null, inline(buf.join(" "), ctx)));
    }
  }
  return out;
}

export function markdown(md, opts = {}) {
  const lines = String(md ?? "").replace(/\r\n?/g, "\n").split("\n");
  const notes = [], body = [];
  for (let i = 0; i < lines.length; i++) {
    const m = /^\[\^([^\]\s]+)\]:\s*(.*)$/.exec(lines[i]);
    if (m) {
      let text = m[2];
      while (i + 1 < lines.length && /^\s{2,}\S/.test(lines[i + 1])) text += " " + lines[++i].trim();
      notes.push([m[1], text]);
    } else body.push(lines[i]);
  }
  const root = h("div", { class: ["md", opts.class] });
  const ctx = { root };
  blocks(body, ctx).forEach((n) => root.append(n));
  if (notes.length) {
    root.append(h("hr"), h("ul", { class: "footnotes" }, notes.map(([id, t]) =>
      h("li", { dataset: { fn: id } }, h("span", { class: "fn-id" }, `[${id}] `), inline(t, ctx)))));
  }
  return root;
}
