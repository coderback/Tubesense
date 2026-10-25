// Shared app state, a tiny event bus and memoised fetches (so demo pre-warming and the screens share results).
import { fetchJSON } from "./api.js";
import { store } from "./util.js";

export const bus = new EventTarget();
export const emit = (name, detail) => bus.dispatchEvent(new CustomEvent(name, { detail }));
export function on(name, fn) {
  const h = (e) => fn(e.detail);
  bus.addEventListener(name, h);
  return () => bus.removeEventListener(name, h);
}

export const DEMO_QUESTION = "Was the Central line between Liverpool Street and Bethnal Green ground to reduce noise, and when?";
export const DEFAULTS = { ride: "r9", speed: 8, question: DEMO_QUESTION, liveAsk: true, replay_s: 30 };

export const state = {
  health: null,          // last /api/health
  serverOk: true,
  online: navigator.onLine,
  atlasNetworkFallback: false,
  sourcesOpened: new Set(),
  demo: { active: false, step: 1, started: 0, settings: { ...DEFAULTS, ...(store.get("demo", {}) || {}) } },
};

export function saveDemoSettings() { store.set("demo", state.demo.settings); }

const memo = new Map();
/** Memoise an async producer under `key`. Pass force=true to refresh. Failed promises are not kept. */
export function cached(key, producer, { ttl = 60000, force = false } = {}) {
  const hit = memo.get(key);
  if (hit && !force && Date.now() - hit.t < ttl) return hit.p;
  const p = producer();
  memo.set(key, { t: Date.now(), p });
  p.catch(() => { if (memo.get(key)?.p === p) memo.delete(key); });
  return p;
}
export const peek = (key) => memo.get(key)?.p ?? null;

export const getHealth = (force) => cached("health", () => fetchJSON("/api/health"), { ttl: 4000, force });
export const getRides = (force) => cached("rides", () => fetchJSON("/api/rides"), { ttl: 300000, force });
export const getPack = (force) => cached("pack", () => fetchJSON("/api/pack"), { ttl: 20000, force });
export const getCanary = (force) => cached("canary", () => fetchJSON("/api/canary"), { ttl: 20000, force });
// every successful Atlas call updates the sidebar's Atlas dot (pre-warm, Atlas screen, ask retrieval, stream, analyst mirror)
export const getAnalyst = (atlas = 1, force) => cached(`analyst${atlas}`, () => fetchJSON(`/api/analyst/session?atlas=${atlas}`).then((S) => {
  if (atlas && /found in Atlas/i.test(String(S?.atlas_mirror || ""))) setAtlasStatus(true);
  return S;
}), { ttl: 60000, force });
export const getAtlas = (q, mode = "live", force) =>
  cached(`atlas:${mode}:${q}`, () => fetchJSON(`/api/atlas/search?q=${encodeURIComponent(q)}&k=3&mode=${mode}`).then((a) => {
    if (mode === "live") setAtlasStatus(a?.mode === "live", a?.mode !== "live");
    return a;
  }), { ttl: 120000, force });

// ---- what this browser itself requested from third parties (shown on the Privacy screen)
export const clientEgress = [];
export function addClientEgress(service, sent, endpoint) {
  const hit = clientEgress.find((e) => e.service === service && e.endpoint === endpoint);
  if (hit) { hit.count = (hit.count || 1) + 1; hit.ts = new Date().toISOString(); return; }
  clientEgress.unshift({ ts: new Date().toISOString(), service, sent, endpoint, count: 1, client: true });
  emit("egress", null);
}

state.atlas = null;     // true / false / null from the last Atlas-touching result
export function setAtlasStatus(ok, fallback = false) {
  if (state.atlas === ok && state.atlasNetworkFallback === fallback) return;     // polled callers: emit only on a change
  state.atlas = ok; state.atlasNetworkFallback = fallback; emit("health", null);
}
