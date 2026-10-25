// Shell: hash router (lazy view modules), status dots, global banners, health polling, service worker, demo hand-off.
import { h, $, $$, clear, icon, store } from "./util.js";
import { MOCK } from "./api.js";
import { state, on, getHealth, addClientEgress, emit } from "./state.js";
import { banner, skeleton, errorCard } from "./ui.js";

const ROUTES = {
  "": ["story", "Story"], ride: ["ride", "Live tracking"], agent: ["agent", "Agent"], ask: ["ask", "Ask Argon"], work: ["work", "Argon at work"], live: ["live", "Live sensors"], pack: ["pack", "Evidence pack"],
  atlas: ["atlas", "MongoDB Atlas"], "why-argon": ["compare", "Why Argon"], method: ["method", "Privacy & method"], box: ["box", "Research Box v2"],
};
const NAV_ROUTE = { story: "story", ride: "ride", agent: "agent", ask: "ask", pack: "pack", atlas: "atlas", compare: "why-argon", method: "method", box: "box", verified: "ask", work: "work", live: "live" };

export function parseHash() {
  const raw = location.hash.replace(/^#\/?/, "");
  const [pathPart, queryPart = ""] = raw.split("?");
  return { parts: pathPart.split("/").filter(Boolean), path: pathPart.split("/")[0] || "", query: new URLSearchParams(queryPart) };
}

let current = { cleanup: null, token: 0, view: null };
const viewEl = () => $("#view");

async function render() {
  const { parts, path, query } = parseHash();
  const token = ++current.token;
  try { current.cleanup?.(); } catch (e) { console.error(e); }
  current.cleanup = null;
  const isDemo = path === "demo";
  const demo = isDemo ? await import("./demo.js") : null;
  if (token !== current.token) return;
  if (isDemo) demo.attach(); else if (state.demo.active) (await import("./demo.js")).detach();
  document.documentElement.dataset.demo = isDemo ? "1" : "0";
  let name, title, ctx = { query, demo: null };
  if (isDemo) {
    const step = Math.min(6, Math.max(1, parseInt(parts[1] || "1", 10) || 1));
    const r = demo.route(step);
    name = r.view; title = r.title; ctx = { query: new URLSearchParams(r.query || ""), demo: { step } };
    demo.setStep(step);
  } else if (ROUTES[path]) [name, title] = ROUTES[path];
  else { location.replace("#/"); return; }
  setNav(name);
  document.title = `${title} · Tubesense`;
  const root = clear(viewEl());
  root.append(h("div", { class: "screen" }, skeleton(5)));
  let mod;
  try { mod = await import(`./views/${name}.js`); }
  catch (e) {
    console.error(e);
    clear(root).append(h("div", { class: "screen" }, errorCard({ title: "This screen failed to load", message: String(e.message || e), retry: render })));
    return;
  }
  if (token !== current.token) return;
  clear(root);
  let cleanup;
  try { cleanup = await mod.mount(root, ctx); }
  catch (e) { console.error(e); root.append(h("div", { class: "screen" }, errorCard({ title: "This screen hit an error", message: String(e.message || e), retry: render }))); }
  if (token !== current.token) { try { cleanup?.(); } catch (e) { console.error(e); } return; }
  current.cleanup = cleanup || null;
  window.scrollTo({ top: 0, left: 0, behavior: "instant" });   // every screen opens at its top; story step 2 scrolls itself 250 ms later
  const t = $("#screen-title");
  if (t && !isDemo) t.focus({ preventScroll: true });
}

function setNav(view) {
  const r = NAV_ROUTE[view];
  $$("[data-route]").forEach((a) => { if (a.dataset.route === r) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); });
  const more = $("#more-btn");
  if (more) { if (["ask", "work", "live", "atlas", "why-argon", "method", "box"].includes(r)) more.setAttribute("aria-current", "page"); else more.removeAttribute("aria-current"); }
}

// ---------------------------------------------------------------- banners and status dots
function renderBanners() {
  const b = $("#banners");
  clear(b);
  if (MOCK) b.append(banner("mock", "MOCK DATA: frontend fixtures from static/mock, not the backend. Nothing on any page is live."));
  if (!MOCK && !state.serverOk) {
    b.append(banner("red", `Server not reachable at ${location.origin}. Is \`python -m webapp.server\` running? Start buttons are disabled.`));
  }
  if (!state.online) b.append(banner("amber", "Offline: Atlas, Gemini and map tiles are unavailable. Local replays and checks still work."));
  if (state.health && state.health.posthoc_out_is_default === false) {
    b.append(banner("red", `TEST MODE: the server writes approvals to ${state.health.posthoc_out}, not to outputs_posthoc.`));
  }
}

function renderStatus() {
  const mk = (cls, label, text) => h("li", null, h("span", { class: ["dot", cls], "aria-hidden": "true" }), h("span", { class: "lbl" }, `${label} `), h("span", { class: "sr-only" }, text));
  const server = MOCK ? ["warn", "mock"] : state.serverOk ? ["ok", "reachable"] : ["bad", "not reachable"];
  const net = state.online ? ["ok", "online"] : ["bad", "offline"];
  const atl = state.atlas === null ? ["", "not checked yet"] : state.atlas ? ["ok", "live"] : ["warn", state.atlasNetworkFallback ? "cached fallback" : "unavailable"];
  const list = $("#status-list");
  clear(list).append(mk(server[0], `Server • ${server[1]}`, ""), mk(net[0], `Internet • ${net[1]}`, ""), mk(atl[0], `Atlas • ${atl[1]}`, ""));
  $$("li", list).forEach((li, i) => li.setAttribute("title", ["Server", "Internet", "Atlas"][i]));
}

async function poll() {
  if (MOCK) { try { state.health = await getHealth(true); } catch { /* fixture missing */ } state.serverOk = true; emit("health", null); return; }
  try { state.health = await getHealth(true); state.serverOk = true; }
  catch { state.serverOk = false; }
  emit("health", null);
}

function buildPhoneNav() {
  const tab = (href, name, label) => h("a", { href, "data-route": name, "aria-label": label }, icon(name === "story" ? "story" : name, 22), h("span", null, label));
  const bar = $("#tabbar");
  bar.append(tab("#/", "story", "Story"), tab("#/ride", "ride", "Ride"), tab("#/agent", "agent", "Agent"), tab("#/pack", "pack", "Pack"),
    h("button", { type: "button", "aria-haspopup": "dialog", id: "more-btn", onclick: () => toggleSheet(true) }, icon("more", 22), h("span", null, "More")));
  const sheet = $("#sheet");
  const item = (href, ic, label) => h("a", { href, onclick: () => toggleSheet(false) }, icon(ic, 20), label);
  sheet.append(h("div", { class: "sheet__panel", role: "dialog", "aria-modal": "true", "aria-label": "More screens" },
    item("#/ask", "ask", "Ask Argon"), item("#/work", "work", "Argon at work"), item("#/live", "live", "Live sensors"), item("#/atlas", "atlas", "Atlas"), item("#/why-argon", "why", "Why Argon"),
    item("#/method", "method", "Privacy & method"), item("#/box", "box", "Research Box"), item("#/demo", "demo", "Start the demo"),
    h("button", { class: "sheet__item", type: "button", onclick: () => toggleSheet(false) }, icon("close", 20), "Close")));
  sheet.addEventListener("click", (e) => { if (e.target === sheet) toggleSheet(false); });
}
function toggleSheet(open) { const s = $("#sheet"); s.hidden = !open; if (open) $("a, button", s)?.focus(); else $("#more-btn")?.focus(); }

function registerSW() {
  if (MOCK || !("serviceWorker" in navigator) || !/^https?:$/.test(location.protocol)) return;
  try { navigator.serviceWorker.register("sw.js").catch(() => {}); } catch { /* unsupported */ }
}

function boot() {
  $$("[data-icon]").forEach((n) => n.append(icon(n.dataset.icon, 20)));
  buildPhoneNav();
  addClientEgress("Google Fonts", "Font files for Oswald, IBM Plex Sans, JetBrains Mono (requested by your browser at page load; no ride data)", "fonts.googleapis.com");
  window.addEventListener("hashchange", () => { toggleSheet(false); render(); });
  window.addEventListener("online", () => { state.online = true; renderBanners(); renderStatus(); emit("online", true); });
  window.addEventListener("offline", () => { state.online = false; renderBanners(); renderStatus(); emit("online", false); });
  on("health", () => { renderBanners(); renderStatus(); });
  renderBanners(); renderStatus();
  poll();
  setInterval(poll, 10000);
  registerSW();
  if (!location.hash) location.replace("#/");
  render();
  window.__tubesense = { version: 1 };
}

boot();
