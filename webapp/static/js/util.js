// DOM builders, formatting, safe storage, icons. Text always goes in as text nodes; no raw HTML is ever inserted.
const SVGNS = "http://www.w3.org/2000/svg";

function applyAttrs(node, attrs, svg) {
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.setAttribute("class", Array.isArray(v) ? v.filter(Boolean).join(" ") : v);
    else if (k === "style" && typeof v === "object") Object.assign(node.style, v);
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "for") node.setAttribute("for", v);
    else if (!svg && k in node && typeof v !== "object" && !k.includes("-") && k !== "list" && k !== "form") {
      try { node[k] = v; } catch { node.setAttribute(k, v); }
    } else node.setAttribute(k, v === true ? "" : v);
  }
}

function addKids(node, kids) {
  for (const c of kids.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

export function h(tag, attrs, ...kids) {
  const n = document.createElement(tag);
  applyAttrs(n, attrs, false);
  return addKids(n, kids);
}

export function s(tag, attrs, ...kids) {
  const n = document.createElementNS(SVGNS, tag);
  applyAttrs(n, attrs, true);
  return addKids(n, kids);
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
export function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }
export function mount(el, ...kids) { clear(el); return addKids(el, kids); }

// ---- storage (per-viewer conveniences only; every access guarded)
export const store = {
  get(k, def = null) {
    try { const v = localStorage.getItem("tubesense." + k); return v === null ? def : JSON.parse(v); } catch { return def; }
  },
  set(k, v) { try { localStorage.setItem("tubesense." + k, JSON.stringify(v)); } catch { /* private window */ } },
};

// ---- formatting
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const pad = (n) => String(n).padStart(2, "0");
export function fmtUTC(iso, withDate = true) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return String(iso);
  const t = `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
  return (withDate ? `${d.getUTCDate()} ${MON[d.getUTCMonth()]} ` : "") + `${t} UTC`;
}
export function fmtTimeLocal(iso) {
  const d = iso ? new Date(iso) : new Date();
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
export function fmtClock(sec) {
  sec = Math.max(0, Math.round(sec));
  return `${pad(Math.floor(sec / 60))}:${pad(sec % 60)}`;
}
export function fmtDur(sec) {
  if (sec === null || sec === undefined || isNaN(sec)) return "";
  if (sec < 60) return `${sec.toFixed(sec < 10 ? 1 : 0)} s`;
  return `${Math.floor(sec / 60)} min ${Math.round(sec % 60)} s`;
}
export function fmtNum(x, d = 1, unit = "") {
  if (x === null || x === undefined || (typeof x === "number" && !isFinite(x))) return "n/a";
  return `${Number(x).toFixed(d).replace("-", "−")}${unit}`;
}
export function fmtSigned(x, d = 2, unit = "") {
  if (x === null || x === undefined || !isFinite(x)) return "n/a";
  return `${x < 0 ? "−" : "+"}${Math.abs(x).toFixed(d)}${unit}`;
}
export const arrow = "→";
export const fixArrow = (t) => String(t ?? "").replace(/->/g, arrow);
export const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
export const uid = () => Math.random().toString(36).slice(2, 9);

export function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}
export function rafThrottle(fn) {
  let pending = false, last;
  return (...a) => {
    last = a;
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => { pending = false; fn(...last); });
  };
}

export const reduceMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

export async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch { /* fall through */ }
  const ta = h("textarea", { value: text, style: { position: "fixed", opacity: 0 } });
  document.body.append(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch { ok = false; }
  ta.remove();
  return ok;
}

// ---- polite live region: at most one update per second
let liveEl = null, lastAnnounce = 0, queued = null, qTimer = null;
export function announce(text) {
  liveEl = liveEl || document.getElementById("live-region");
  if (!liveEl) return;
  const now = Date.now();
  if (now - lastAnnounce >= 1000) { liveEl.textContent = text; lastAnnounce = now; return; }
  queued = text;
  if (!qTimer) qTimer = setTimeout(() => { qTimer = null; lastAnnounce = Date.now(); liveEl.textContent = queued; }, 1000 - (now - lastAnnounce));
}

// ---- icons (24x24 stroke paths drawn for this app)
const ICONS = {
  story: "M5 4h10a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3V4zM8 8h7M8 12h7M8 16h4",
  ride: "M4 18c4 0 4-12 8-12s4 12 8 12M4 18h0M20 18h0",
  agent: "M12 3v3M12 18v3M3 12h3M18 12h3M7 7l2 2M15 15l2 2M17 7l-2 2M9 15l-2 2M9 12a3 3 0 1 0 6 0 3 3 0 0 0-6 0",
  ask: "M4 5h16v11H9l-5 4V5zM8 9h8M8 12h5",
  pack: "M12 3l7 3v5c0 5-3 8-7 10-4-2-7-5-7-10V6l7-3zM9 12l2 2 4-4",
  atlas: "M5 6c0-1.7 3.1-3 7-3s7 1.3 7 3-3.1 3-7 3-7-1.3-7-3zM5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6",
  why: "M12 4v16M6 20h12M5 7h14M5 7l-3 6a3 3 0 0 0 6 0L5 7zM19 7l-3 6a3 3 0 0 0 6 0l-3-6z",
  method: "M7 11V8a5 5 0 0 1 10 0v3M5 11h14v9H5v-9zM12 15v2",
  box: "M12 3l8 4v10l-8 4-8-4V7l8-4zM4 7l8 4 8-4M12 11v10",
  more: "M5 12h0M12 12h0M19 12h0",
  work: "M4 4h11l5 5v11H4V4zM15 4v5h5M8 13l2 2 4-4M8 18h8",
  live: "M12 12h0M8.5 8.5a5 5 0 0 0 0 7M15.5 8.5a5 5 0 0 1 0 7M5.6 5.6a9 9 0 0 0 0 12.8M18.4 5.6a9 9 0 0 1 0 12.8",
  demo: "M8 5l11 7-11 7V5z",
  check: "M5 12l5 5 9-10",
  cross: "M6 6l12 12M18 6L6 18",
  dot: "M12 12h0",
  warn: "M12 4l9 16H3L12 4zM12 10v4M12 17h0",
  copy: "M9 9h10v11H9V9zM5 15V4h10",
  link: "M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1",
  close: "M6 6l12 12M18 6L6 18",
  gear: "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6L7 7M17 17l1.4 1.4M18.4 5.6L17 7M7 17l-1.4 1.4",
  pause: "M8 5v14M16 5v14",
  play: "M8 5l11 7-11 7V5z",
  stop: "M7 7h10v10H7z",
  next: "M9 5l7 7-7 7",
  prev: "M15 5l-7 7 7 7",
  zoom: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM21 21l-5-5M11 8v6M8 11h6",
};
export function icon(name, size = 18) {
  return s("svg", { viewBox: "0 0 24 24", width: size, height: size, fill: "none", stroke: "currentColor",
                    "stroke-width": 1.8, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true",
                    class: "icon" }, s("path", { d: ICONS[name] || ICONS.dot }));
}

// ---- small helpers used by several views
export function el(tag, cls, ...kids) { return h(tag, { class: cls }, ...kids); }
export function kv(rows, cls = "kv") {
  // rows: [[label, value], ...] -> definition list
  return h("dl", { class: cls }, rows.flatMap(([k, v]) => v === null || v === undefined ? [] : [h("dt", null, k), h("dd", null, v)]));
}
export function sameHost(url) { try { return new URL(url, location.href).origin === location.origin; } catch { return false; } }
export function safeUrl(u) {
  try { const x = new URL(u, location.href); return /^(https?:|mailto:)$/.test(x.protocol) ? x.href : null; } catch { return null; }
}
export function extLink(href, text, cls) {
  const u = safeUrl(href);
  if (!u) return h("span", { class: cls }, text ?? href);
  return h("a", { href: u, target: "_blank", rel: "noopener noreferrer", class: cls }, text ?? href);
}
