// Shared components: chips, cards, verdicts, claim cards, source drawer, hash chain, event feed, states, modal, tabs.
import { h, s, icon, fmtUTC, fmtNum, fixArrow, clear, extLink, safeUrl, copyText } from "./util.js";
import { fetchJSON } from "./api.js";
import { state, emit } from "./state.js";

// ---------------------------------------------------------------- chips
const CHIPS = {
  live: ["LIVE", "live"], replay: ["REPLAY", "replay"], precomputed: ["PRECOMPUTED", "precomputed"],
  cached: ["CACHED", "cached"], synthetic: ["SYNTHETIC", "synthetic"], posthoc: ["POST-HOC", "posthoc"],
  prereg: ["PRE-REGISTERED", "prereg"], seeded: ["SEEDED", "seeded"], concept: ["CONCEPT \u00B7 NOT BUILT", "concept"],
  mock: ["MOCK DATA", "mock"], relative: ["RELATIVE dB", "relative"], neutral: ["", "relative"], readonly: ["READ-ONLY", "relative"],
};
export function chip(kind, text, opts = {}) {
  const [def, cls] = CHIPS[kind] || ["", "relative"];
  return h("span", { class: ["chip", `chip--${cls}`, opts.large && "chip--large"], title: opts.title },
    kind === "live" ? h("span", { class: "live-dot", "aria-hidden": "true" }) : null, text ?? def);
}
export const chipRow = (...chips) => h("div", { class: "chip-row" }, chips.flat().filter(Boolean));

/** Provenance chip from an API label ("REPLAY", "PRECOMPUTED", ...) plus its original timestamp when it is not now. */
export function provChip(label, ts, extra) {
  const k = String(label || "").toLowerCase().replace(/[^a-z]/g, "");
  const kind = { live: "live", replay: "replay", precomputed: "precomputed", cached: "cached", synthetic: "synthetic",
                 posthoc: "posthoc", preregistered: "prereg", seeded: "seeded" }[k] || "neutral";
  const base = CHIPS[kind][0] || String(label);
  const t = ts ? ` \u00B7 ${fmtUTC(ts)}` : "";
  return chip(kind, `${base}${t}${extra ? " \u00B7 " + extra : ""}`);
}

// ---------------------------------------------------------------- verdicts
const VERDICTS = {
  "GO": ["ok", "check"], "VERIFIED": ["ok", "check"], "ACCEPTED": ["ok", "check"], "INTACT": ["ok", "check"], "MATCH": ["ok", "check"],
  "NO-GO": ["bad", "cross"], "REJECTED": ["bad", "cross"], "FAILS": ["bad", "cross"], "BROKEN": ["bad", "cross"], "DIFFERENT VERDICT": ["bad", "cross"],
  "WEAK": ["warn", "warn"], "OPEN": ["warn", "warn"], "EXPLORATORY": ["warn", "warn"], "MATCHED, NOT BEATEN": ["warn", "warn"],
  "MEASURED": ["info", "dot"], "DROPPED": ["bad", "cross"],
};
export function verdict(kind, text) {
  const [tone, ic] = VERDICTS[String(kind).toUpperCase()] || ["info", "dot"];
  return h("span", { class: ["verdict", `verdict--${tone}`] }, icon(ic, 14), h("span", null, text ?? kind));
}

// ---------------------------------------------------------------- cards and tiles
export function card({ title, level = 2, chips, body, footer, cls, id, actions } = {}) {
  const head = title || chips || actions
    ? h("header", { class: "card__head" },
        title ? h("h" + level, { class: "card__title" }, title) : null,
        chips ? chipRow(chips) : null, actions ? h("div", { class: "card__actions" }, actions) : null)
    : null;
  return h("section", { class: ["card", cls], id }, head, h("div", { class: "card__body" }, body), footer ? h("footer", { class: "card__foot" }, footer) : null);
}
export function statTile({ value, unit, caption, source, tone, cls }) {
  return h("div", { class: ["stat", tone && `stat--${tone}`, cls] },
    h("div", { class: "stat__value" }, h("span", null, value), unit ? h("span", { class: "stat__unit" }, unit) : null),
    h("div", { class: "stat__caption" }, caption),
    h("div", { class: "stat__source" }, source));
}
export function screenHeader({ title, subtitle, chips, extra }) {
  return h("header", { class: "screen-head" },
    h("div", { class: "screen-head__top" }, h("h1", { id: "screen-title", tabindex: -1 }, title), chips ? chipRow(chips) : null),
    subtitle ? h("p", { class: "screen-head__sub" }, subtitle) : null, extra);
}
export const whereFrom = (...lines) => h("footer", { class: "where" }, h("strong", null, "Where this comes from: "), lines.flat().join(" \u00B7 "));
export const skeleton = (lines = 4, cls) => h("div", { class: ["skeleton", cls], "aria-busy": "true", "aria-label": "Loading" },
  Array.from({ length: lines }, (_, i) => h("div", { class: "skeleton__line", style: { width: `${95 - ((i * 17) % 40)}%` } })));
export function errorCard({ message, retry, fallbackLabel, fallback, title = "Something went wrong" }) {
  return h("div", { class: "error-card", role: "alert" },
    icon("warn", 20), h("div", { class: "error-card__text" }, h("strong", null, title), h("p", null, message)),
    h("div", { class: "error-card__actions" },
      retry ? h("button", { class: "btn", type: "button", onclick: retry }, "Retry") : null,
      fallback ? h("button", { class: "btn btn--secondary", type: "button", onclick: fallback }, fallbackLabel) : null));
}
export function emptyState(text, action) {
  return h("div", { class: "empty" }, h("p", null, text), action || null);
}
export const banner = (kind, ...kids) => h("div", { class: ["banner", `banner--${kind}`], role: kind === "red" ? "alert" : "status" }, kids);

export function toast(text, ms = 3200) {
  const root = document.getElementById("toast-root");
  if (!root) return;
  const t = h("div", { class: "toast" }, text);
  root.append(t);
  setTimeout(() => t.classList.add("toast--out"), ms);
  setTimeout(() => t.remove(), ms + 400);
}

// ---------------------------------------------------------------- controls
export function segmented({ options, value, onChange, label, cls }) {
  const wrap = h("div", { class: ["segmented", cls], role: "radiogroup", "aria-label": label });
  let cur = value;
  const btns = options.map((o) => {
    const b = h("button", { type: "button", role: "radio", class: "segmented__btn", dataset: { v: String(o.value) },
      "aria-checked": String(o.value === cur), tabindex: o.value === cur ? 0 : -1, disabled: o.disabled, title: o.title,
      onclick: () => set(o.value, true) }, o.label);
    return b;
  });
  const set = (v, fire) => {
    cur = v;
    btns.forEach((b, i) => { const on = options[i].value === v; b.setAttribute("aria-checked", String(on)); b.tabIndex = on ? 0 : -1; });
    if (fire) onChange?.(v);
  };
  wrap.addEventListener("keydown", (e) => {
    if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) return;
    e.preventDefault();
    const en = options.map((o, i) => (o.disabled ? -1 : i)).filter((i) => i >= 0);
    const at = en.indexOf(options.findIndex((o) => o.value === cur));
    const next = en[(at + (e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 1) + en.length) % en.length];
    set(options[next].value, true);
    btns[next].focus();
  });
  btns.forEach((b) => wrap.append(b));
  wrap.set = (v) => set(v, false);
  wrap.setDisabled = (d) => btns.forEach((b, i) => { b.disabled = d || !!options[i].disabled; });
  return wrap;
}

export function tabs({ tabs: list, active, onChange, label }) {
  const bar = h("div", { class: "tabs", role: "tablist", "aria-label": label });
  const buttons = list.map((t) => h("button", { type: "button", role: "tab", class: "tab", id: `tab-${t.id}`, "aria-controls": `panel-${t.id}`,
    "aria-selected": String(t.id === active), tabindex: t.id === active ? 0 : -1, onclick: () => set(t.id) }, t.label));
  const set = (id) => {
    buttons.forEach((b, i) => { const on = list[i].id === id; b.setAttribute("aria-selected", String(on)); b.tabIndex = on ? 0 : -1; });
    onChange(id);
  };
  bar.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    const i = buttons.indexOf(document.activeElement);
    if (i < 0) return;
    const n = (i + (e.key === "ArrowLeft" ? -1 : 1) + buttons.length) % buttons.length;
    set(list[n].id); buttons[n].focus();
  });
  buttons.forEach((b) => bar.append(b));
  bar.set = set;
  return bar;
}

// ---------------------------------------------------------------- modal and drawer (focus trapped, Esc closes)
let modalDepth = 0;
export const isModalOpen = () => modalDepth > 0;
function trap(root, onClose) {
  const prev = document.activeElement;
  const focusables = () => [...root.querySelectorAll('a[href],button:not([disabled]),input:not([disabled]),textarea,select,[tabindex]:not([tabindex="-1"])')];
  const key = (e) => {
    if (e.key === "Escape") { e.stopPropagation(); onClose(); }
    if (e.key === "Tab") {
      const f = focusables();
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  };
  root.addEventListener("keydown", key);
  modalDepth++;
  return () => { modalDepth--; root.removeEventListener("keydown", key); try { prev?.focus?.(); } catch { /* element gone */ } };
}

export function modal({ title, body, labelledby = "modal-title", cls, onClose }) {
  const root = document.getElementById("overlay-root");
  const wrap = h("div", { class: ["overlay", cls] });
  const dlg = h("div", { class: "dialog", role: "dialog", "aria-modal": "true", "aria-labelledby": labelledby },
    h("h2", { id: labelledby, class: "dialog__title" }, title), body);
  wrap.append(dlg);
  let release = null;
  const close = (result) => { release?.(); wrap.remove(); onClose?.(result); };
  wrap.addEventListener("mousedown", (e) => { if (e.target === wrap) close(); });
  root.append(wrap);
  release = trap(dlg, close);
  setTimeout(() => (dlg.querySelector("[autofocus]") || dlg.querySelector("button, input, textarea"))?.focus(), 0);
  return { el: dlg, close };
}

/** Resolve true if the user wants to stop the running heavy job and start this one. */
export function confirmReplace(runningLabel) {
  return new Promise((resolve) => {
    let m;
    const body = h("div", null,
      h("p", null, `A ${runningLabel || "heavy"} job is running (only one ride, live analyst or stream replay can run at a time). Stop it and start this one?`),
      h("div", { class: "dialog__actions" },
        h("button", { class: "btn btn--secondary", type: "button", onclick: () => m.close(false) }, "Cancel"),
        h("button", { class: "btn", type: "button", autofocus: true, onclick: () => m.close(true) }, "Stop it and start this")));
    m = modal({ title: "Another job is running", body, labelledby: "busy-title", onClose: (r) => resolve(!!r) });
  });
}
/** Run start(replace) and, on a 409 busy, ask the user and run it again with replace=true. Returns the job or null. */
export async function startWithReplace(start) {
  try { return await start(false); }
  catch (e) {
    if (e.status === 409 && e.error === "busy") {
      const ok = await confirmReplace(e.body?.running?.label);
      return ok ? start(true) : null;
    }
    throw e;
  }
}

// ---------------------------------------------------------------- source drawer
let drawerEl = null;
function quoteRegex(q) {
  const esc = (t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/['\u2018\u2019]/g, "['\u2018\u2019]").replace(/["\u201C\u201D]/g, "[\"\u201C\u201D]").replace(/[-\u2013\u2014]/g, "[-\u2013\u2014]");
  return new RegExp(q.trim().split(/\s+/).map(esc).join("\\s+"), "i");
}
export function highlight(text, quote) {
  if (!quote) return [text];
  let m = null;
  try { m = quoteRegex(quote).exec(text); } catch { m = null; }
  if (!m) return [text];
  return [text.slice(0, m.index), h("mark", { class: "quote-hit", id: "quote-hit" }, m[0]), text.slice(m.index + m[0].length)];
}
export function openSource(chunkId, quote) {
  drawerEl?.close();
  state.sourcesOpened.add(chunkId);
  emit("source-opened", chunkId);
  const root = document.getElementById("overlay-root");
  const body = h("div", { class: "drawer__body" }, skeleton(6));
  const wrap = h("div", { class: "overlay overlay--drawer" });
  const panel = h("aside", { class: "drawer", role: "dialog", "aria-modal": "true", "aria-labelledby": "drawer-title" },
    h("header", { class: "drawer__head" }, h("h2", { id: "drawer-title" }, "Source"),
      h("button", { class: "icon-btn", type: "button", "aria-label": "Close source", onclick: () => close() }, icon("close", 20))),
    body);
  wrap.append(panel);
  let release;
  const close = () => { release?.(); wrap.remove(); drawerEl = null; };
  wrap.addEventListener("mousedown", (e) => { if (e.target === wrap) close(); });
  root.append(wrap);
  release = trap(panel, close);
  drawerEl = { close };
  panel.querySelector("button").focus();
  fetchJSON(`/api/source/${encodeURIComponent(chunkId)}${quote ? `?quote=${encodeURIComponent(quote)}` : ""}`).then((src) => {
    clear(body);
    document.getElementById("drawer-title").textContent = src.title || chunkId;
    body.append(
      h("dl", { class: "kv" },
        h("dt", null, "Publisher"), h("dd", null, src.publisher || "n/a"),
        h("dt", null, "Date"), h("dd", null, src.doc_date || "n/a"),
        h("dt", null, "Page"), h("dd", null, src.page_end && src.page_end !== src.page ? `${src.page}\u2013${src.page_end}` : String(src.page ?? "n/a")),
        h("dt", null, "Chunk"), h("dd", { class: "mono" }, src.chunk_id),
        src.reliability ? [h("dt", null, "Reliability"), h("dd", null, String(src.reliability))] : null,
        src.url ? [h("dt", null, "URL"), h("dd", null, extLink(src.url, src.url, "break"))] : null),
      quote ? h("p", { class: ["drawer__quote", src.quote_found === false && "warn"] },
        src.quote_found === false ? "The cited quote was not found in this chunk (the verifier will flag the claim)." : "The cited quote is highlighted below.") : null,
      h("pre", { class: "drawer__text" }, highlight(src.text || "", quote)));
    document.getElementById("quote-hit")?.scrollIntoView({ block: "center" });
  }).catch((e) => {
    clear(body);
    body.append(errorCard({ message: e.message, title: "Source not available", retry: () => { close(); openSource(chunkId, quote); } }));
  });
}

// ---------------------------------------------------------------- claim cards
const TYPE_LABEL = { sourced: "source", measured: "our measurement", derived: "calculated", inference: "inference" };
export const typeLabel = (c) => c.type_label || TYPE_LABEL[c.type] || c.type;

export function citationButton(c) {
  return h("button", { type: "button", class: "cite", onclick: () => openSource(c.chunk_id, c.quote), title: c.quote ? `\u201C${c.quote}\u201D` : undefined },
    icon("link", 14), h("span", { class: "mono" }, c.chunk_id), c.quote ? h("span", { class: "cite__q" }, `“${c.quote.length > 46 ? c.quote.slice(0, 44) + "…" : c.quote}”`) : null);
}
export function statPill(st) {
  const v = typeof st.value === "number" ? fmtNum(st.value, Math.abs(st.value) >= 100 ? 0 : 2) : String(st.value);
  return h("span", { class: "stat-pill", title: st.key }, h("span", { class: "mono" }, st.key), " = ", h("strong", null, v), st.unit ? ` ${st.unit}` : "", st.n ? ` (n=${st.n})` : "");
}

export function claimCard(claim, check, opts = {}) {
  const reasons = opts.reasons || check?.reasons || [];
  const cites = (claim.citations || []).map((c) => (typeof c === "string" ? { chunk_id: c } : c));
  const stats = claim.stats || (claim.stat_keys || []).map((k) => ({ key: k }));
  const f = claim.formula;
  const bad = opts.struck || (check && !check.ok);
  return h("article", { class: ["claim", opts.struck && "claim--struck", check && (check.ok ? "claim--ok" : "claim--bad"), opts.cls], id: opts.id },
    h("header", { class: "claim__head" },
      h("span", { class: "claim__id mono" }, claim.id),
      h("span", { class: ["chip", "chip--type", `chip--t-${claim.type}`] }, typeLabel(claim)),
      claim.section_title ? h("span", { class: "claim__section" }, claim.section_title) : null,
      opts.struck ? verdict("DROPPED", "dropped") : check ? (check.ok ? verdict("verified", opts.verifiedText || "verified now") : verdict("FAILS", "FAILS")) : null),
    h("p", { class: "claim__text" }, opts.struck ? h("span", { class: "sr-only" }, "Dropped claim: ") : null, h("span", { class: opts.struck ? "struck" : "" }, claim.text)),
    cites.length ? h("div", { class: "claim__cites" }, cites.map(citationButton)) : null,
    stats.length ? h("div", { class: "claim__stats" }, stats.map((st) => (st.value === undefined ? h("span", { class: "stat-pill mono" }, st.key) : statPill(st)))) : null,
    f ? h("div", { class: "claim__formula mono" }, `${f.expression} = ${f.result}`,
        f.inputs?.length ? h("span", { class: "claim__inputs" }, ` where ${f.inputs.map((i) => `${i.name}=${i.stat ?? i.value}`).join(", ")}`) : null) : null,
    bad && reasons.length ? h("ul", { class: "claim__reasons" }, reasons.map((r) => h("li", null, r))) : null,
    opts.stage ? h("div", { class: "claim__stage" }, opts.stage) : null);
}

// ---------------------------------------------------------------- hash chain
export function hashChain(blocks = [], ok = null) {
  const root = h("div", { class: "chain", role: "list", "aria-label": "Hash chain of logged attempts" });
  const els = [];
  const add = (b) => {
    const node = h("div", { class: ["chain__block", b.kind && `chain__block--${b.kind}`], role: "listitem" },
      h("span", { class: "chain__n" }, `#${b.attempt}`), h("span", { class: "chain__kind" }, b.kind || ""), h("span", { class: "chain__hash mono", title: b.hash }, (b.hash || "").slice(0, 8)));
    if (els.length) root.append(h("span", { class: ["chain__link", ok === true && "chain__link--ok", ok === false && "chain__link--bad"], "aria-hidden": "true" }));
    root.append(node); els.push(node);
    return node;
  };
  blocks.forEach(add);
  root.add = add;
  root.setOk = (v) => { ok = v; root.querySelectorAll(".chain__link").forEach((l) => { l.classList.toggle("chain__link--ok", v === true); l.classList.toggle("chain__link--bad", v === false); }); };
  root.count = () => els.length;
  root.reset = () => { while (root.firstChild) root.removeChild(root.firstChild); els.length = 0; ok = null; };
  return root;
}

// ---------------------------------------------------------------- event feed with sticky auto-scroll
export function eventFeed({ height } = {}) {
  const box = h("div", { class: "feed", tabindex: 0, role: "log", "aria-label": "Event feed", style: height ? { maxHeight: height } : null });
  const pill = h("button", { class: "feed__jump", type: "button", hidden: true, onclick: () => { box.scrollTop = box.scrollHeight; pill.hidden = true; } }, "Jump to latest");
  const wrap = h("div", { class: "feed-wrap" }, box, pill);
  let stick = true;
  box.addEventListener("scroll", () => { stick = box.scrollHeight - box.scrollTop - box.clientHeight < 40; if (stick) pill.hidden = true; });
  wrap.push = (node) => {
    box.append(node);
    if (stick) box.scrollTop = box.scrollHeight; else pill.hidden = false;
    return node;
  };
  wrap.clear = () => { clear(box); stick = true; pill.hidden = true; };
  wrap.box = box;
  return wrap;
}

export function quoteFrame(title, content, cls) {
  return h("figure", { class: ["quote-frame", cls] }, h("figcaption", null, title), h("div", { class: "quote-frame__body" }, content));
}

export function copyButton(getText, label = "Copy") {
  return h("button", { class: "btn btn--small btn--secondary", type: "button", onclick: async (e) => {
    const ok = await copyText(typeof getText === "function" ? getText() : getText);
    const b = e.currentTarget; const t = b.textContent;
    b.textContent = ok ? "Copied" : "Copy failed"; setTimeout(() => { b.textContent = t; }, 1400);
  } }, icon("copy", 14), label);
}

export function jsonView(obj) {
  return h("pre", { class: "json mono" }, JSON.stringify(obj, null, 2));
}

export { fixArrow, safeUrl, s };
