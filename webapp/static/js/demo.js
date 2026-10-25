// Demo mode: six steps in about three minutes, driven by PageDown / a presenter clicker. Pre-warms the ride (held until its step),
// the analyst session, the Atlas search, the pack and canary, and optionally a live ask. The demo never touches the approval form.
import { h, $, mount as mountEl, clear, fmtClock, icon, store } from "./util.js";
import { MOCK, fetchJSON } from "./api.js";
import { state, getRides, getAnalyst, getAtlas, getPack, getCanary, saveDemoSettings, emit, on, DEFAULTS } from "./state.js";
import { chip, segmented, modal, isModalOpen, toast } from "./ui.js";
import { rideSession } from "./ridectl.js";
import { startAsk, askStore, adoptRunningAsk } from "./askctl.js";

export const STEPS = [
  { n: 1, label: "Problem", window: [0, 20], view: "story", title: "Demo: the problem", chips: () => [chip("precomputed", "PRECOMPUTED · public records")] },
  { n: 2, label: "Named user", window: [20, 35], view: "story", title: "Demo: the named user", chips: () => [chip("precomputed", "PRECOMPUTED · public record")] },
  { n: 3, label: "Live tracking", window: [35, 75], view: "ride", title: "Demo: live tracking",
    chips: () => [chip("replay", "REPLAY of a recorded ride"), chip("live", "LIVE compute"), chip("posthoc", "POST-HOC tracker v2"), state.demo.settings.ride === "r5stop" ? chip("synthetic", "SYNTHETIC stop") : null] },
  { n: 4, label: "Agent loop", window: [75, 110], view: "agent", title: "Demo: the agent loop", chips: () => [chip("replay", "REPLAY · recorded 24 Oct"), chip("live", "LIVE checks")] },
  { n: 5, label: "Verified answer", window: [110, 150], view: "verified", title: "Demo: the verified answer", chips: () => [] },   // filled by the screen (views/verified.js) from what it shows
  { n: 6, label: "Approve", window: [150, 180], view: "pack", title: "Demo: approve", chips: () => [chip("precomputed", "PRECOMPUTED pack"), chip("live", "LIVE verifier + approval")] },
];
const fmtWin = (w) => `${fmtClock(w[0])}–${fmtClock(w[1])}`;
export function route(step) { const s = STEPS[step - 1]; return { view: s.view, title: s.title, query: s.view === "ride" ? "" : "" }; }

const ready = { ride: ["pending", "not started"], analyst: ["pending", "not started"], atlas: ["pending", "not started"], pack: ["pending", "not started"], ask: ["off", "off"] };
const LABEL = { ride: "Ride tracker (held)", analyst: "Analyst session", atlas: "Atlas search", pack: "Pack and canary", ask: "Live ask (background)" };
const RECENT_ASK_MIN = 5;
const askAgeMin = (ts) => (Date.now() - Date.parse(String(ts).replace(/([+-]\d\d)(\d\d)$/, "$1:$2"))) / 60000;
let bar = null, keyHandler = null, timer = 0, prewarmed = false, offs = [], popover = null;

function setReady(k, st, text) { ready[k] = [st, text]; emit("demo-ready", k); }

export function attach() {
  if (state.demo.active) return;
  state.demo.active = true; state.demo.started = Date.now(); state.demo.hooks = null;
  state.demo.entryStep = Math.min(6, Math.max(1, parseInt((/^#\/demo\/(\d)/.exec(location.hash) || [])[1] || "1", 10) || 1));
  document.documentElement.dataset.demo = "1";
  build();
  keyHandler = onKey; window.addEventListener("keydown", keyHandler);
  timer = setInterval(updateClock, 500);
  offs.push(on("demo-ready", renderBackstage), on("ride", syncRideReady), on("health", () => {}));
  prewarm();
}
export function detach() {
  if (!state.demo.active) return;
  state.demo.active = false; state.demo.hooks = null;
  window.removeEventListener("keydown", keyHandler); clearInterval(timer);
  offs.forEach((o) => o()); offs = [];
  bar?.remove(); bar = null; popover = null; prewarmed = false;
  document.documentElement.dataset.demo = "0";
  if (["holding", "loading", "starting"].includes(rideSession.phase)) rideSession.stop();   // a held tracker would sit on RAM
}

/** A screen sets the demo bar's chips for its own step when they depend on what it shows. */
export function setChips(n, chips) { if (bar && state.demo.step === n) mountEl($(".demobar__chips", bar), chips); }

export function setStep(n) {
  state.demo.step = n;
  if (!bar) return;
  $$bar(".demobar__step").forEach((b, i) => { b.setAttribute("aria-current", i + 1 === n ? "step" : "false"); b.classList.toggle("is-done", i + 1 < n); });
  mountEl($(".demobar__chips", bar), STEPS[n - 1].chips());
  $(".demobar__window", bar).textContent = `target ${fmtWin(STEPS[n - 1].window)}`;
  $(".demobar__prev", bar).disabled = n <= 1; $(".demobar__next", bar).disabled = n >= 6;
  updateClock();
}
const $$bar = (sel) => [...bar.querySelectorAll(sel)];

function go(n) { n = Math.min(6, Math.max(1, n)); if (n !== state.demo.step || !location.hash.startsWith("#/demo")) location.hash = `#/demo/${n}`; }
function exit() { detach(); location.hash = "#/"; }

function onKey(e) {
  if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return;
  const t = e.target, tag = t?.tagName;
  const typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || t?.isContentEditable;
  if (e.key === "Escape") { if (isModalOpen() || popover) { closePopover(); return; } exit(); return; }
  if (typing || isModalOpen()) return;
  if (e.key === "ArrowRight" || e.key === "PageDown") { e.preventDefault(); go(state.demo.step + 1); }
  else if (e.key === "ArrowLeft" || e.key === "PageUp") { e.preventDefault(); go(state.demo.step - 1); }
  else if (e.key === " " && !["BUTTON", "A", "SUMMARY"].includes(tag) && state.demo.hooks?.playPause) { e.preventDefault(); state.demo.hooks.playPause(); }
  else if (e.key === "?") { e.preventDefault(); help(); }
}

function help() {
  let m;
  m = modal({ title: "Demo keys", labelledby: "demo-keys", body: h("div", null,
    h("dl", { class: "kv" }, h("dt", null, h("kbd", null, "→"), " ", h("kbd", null, "PageDown")), h("dd", null, "next step (a presenter clicker sends PageDown)"),
      h("dt", null, h("kbd", null, "←"), " ", h("kbd", null, "PageUp")), h("dd", null, "previous step"), h("dt", null, h("kbd", null, "Space")), h("dd", null, "play / pause the current replay"),
      h("dt", null, h("kbd", null, "Esc")), h("dd", null, "leave demo mode"), h("dt", null, h("kbd", null, "?")), h("dd", null, "this help")),
    h("p", { class: "help" }, "The demo never ticks a box, fills the name or submits the approval: the human does that."),
    h("div", { class: "dialog__actions" }, h("button", { class: "btn", type: "button", autofocus: true, onclick: () => m.close() }, "Close"))) });
}

// ---------------------------------------------------------------- bar
function build() {
  const steps = h("ol", { class: "demobar__steps" }, STEPS.map((s) => h("li", null, h("button", { type: "button", class: "demobar__step", "aria-label": `Step ${s.n}: ${s.label}`, onclick: () => go(s.n) },
    h("span", { class: "demobar__n" }, String(s.n)), h("span", { class: "demobar__l" }, s.label)))));
  bar = h("div", { class: "demobar", role: "region", "aria-label": "Demo controls" },
    steps, h("div", { class: "demobar__chips chip-row" }),
    h("div", { class: "demobar__clock" }, h("span", { class: "demobar__elapsed mono" }, "00:00"), h("span", { class: "demobar__window muted mono" }, "")),
    h("div", { class: "demobar__btns" },
      h("button", { class: "btn btn--small btn--secondary demobar__prev", type: "button", "aria-label": "Previous step", onclick: () => go(state.demo.step - 1) }, icon("prev", 16)),
      h("button", { class: "btn btn--small demobar__next", type: "button", "aria-label": "Next step", onclick: () => go(state.demo.step + 1) }, "Next", icon("next", 16)),
      h("button", { class: "icon-btn demobar__backstage", type: "button", "aria-label": "Backstage readiness", "aria-haspopup": "true", onclick: () => togglePopover("backstage"), title: "Backstage: what is pre-warmed" }, icon("check", 18)),
      h("button", { class: "icon-btn", type: "button", "aria-label": "Demo settings", "aria-haspopup": "true", onclick: () => togglePopover("settings"), title: "Demo settings" }, icon("gear", 18)),
      h("button", { class: "icon-btn", type: "button", "aria-label": "Keyboard help", onclick: help, title: "Keys (?)" }, "?"),
      h("button", { class: "icon-btn", type: "button", "aria-label": "Leave demo mode", onclick: exit, title: "Leave demo mode (Esc)" }, icon("close", 18))));
  bar.addEventListener("click", (e) => {      // after a mouse click the bar's step buttons must not keep focus: Space is play/pause, not another click on Next
    const b = e.target.closest(".demobar__step, .demobar__prev, .demobar__next");
    if (b && e.detail > 0) b.blur();
  });
  mountEl($("#demo-root"), bar);
}
function updateClock() {
  if (!bar) return;
  const el = (Date.now() - state.demo.started) / 1000, w = STEPS[state.demo.step - 1]?.window || [0, 180];
  const node = $(".demobar__elapsed", bar);
  node.textContent = fmtClock(el);
  node.classList.toggle("over", el > w[1] + 5);
}

// ---------------------------------------------------------------- popovers
function closePopover() { popover?.remove(); popover = null; }
function togglePopover(kind) {
  const had = popover?.dataset.kind === kind;
  closePopover();
  if (had) return;
  popover = h("div", { class: "demopop", role: "dialog", "aria-label": kind === "settings" ? "Demo settings" : "Backstage", dataset: { kind } });
  bar.append(popover);
  if (kind === "backstage") renderBackstage(); else renderSettings();
}
function renderBackstage() {
  if (!popover || popover.dataset.kind !== "backstage") return;
  syncRideReady();
  mountEl(popover, h("h3", null, "Backstage"), h("ul", { class: "backstage" }, Object.entries(ready).map(([k, [st, text]]) =>
    h("li", null, h("span", { class: ["dot", st === "ok" ? "dot--ok" : st === "fail" ? "dot--bad" : st === "pending" ? "dot--warn" : ""], "aria-hidden": "true" }), h("strong", null, LABEL[k]), h("span", { class: st === "fail" ? "warn" : "muted" }, ` ${st === "fail" ? "FAILED: " : ""}${text}`)))),
    h("p", { class: "help" }, "Everything here is optional: each screen falls back honestly (a PRECOMPUTED ride, a CACHED answer) and says so."));
}
function renderSettings() {
  const s = state.demo.settings, save = () => { saveDemoSettings(); };
  const q = h("input", { type: "text", maxlength: 300, value: s.question, "aria-label": "Demo question", onchange: () => { s.question = q.value.trim() || DEFAULTS.question; save(); } });
  mountEl(popover, h("h3", null, "Demo settings"),
    h("div", { class: "field" }, h("span", null, "Ride beat"), segmented({ label: "Ride beat", value: s.ride, options: [{ value: "r9", label: "R9 out-of-sample" }, { value: "r5stop", label: "R5 + SYNTHETIC 30 s stop" }], onChange: (v) => { s.ride = v; save(); restartRide(); } })),
    h("div", { class: "field" }, h("span", null, "Ride speed"), segmented({ label: "Speed", value: s.speed, options: [4, 8].map((n) => ({ value: n, label: `${n}×` })), onChange: (v) => { s.speed = v; save(); restartRide(); } })),
    h("div", { class: "field" }, h("span", null, "Demo question"), q),
    h("div", { class: "field" }, h("span", null, "Ask Argon live in the background when the demo starts at step 1 (default on when available; a reload mid-demo never starts one)"), segmented({ label: "Live ask in background", value: s.liveAsk ? "on" : "off", options: [{ value: "on", label: "on" }, { value: "off", label: "off" }], onChange: (v) => { s.liveAsk = v === "on"; save(); } })),
    h("div", { class: "field" }, h("span", null, "Live ask right now (one paid Gemini call, ignores the 5-minute repeat guard)"), h("button", { class: "btn btn--small btn--secondary", type: "button", onclick: () => { closePopover(); setReady("ask", "pending", "starting"); liveAsk(true); } }, "Ask now")),
    h("div", { class: "field" }, h("span", null, "Analyst replay length"), segmented({ label: "Replay length", value: s.replay_s, options: [20, 30, 45].map((n) => ({ value: n, label: `${n} s` })), onChange: (v) => { s.replay_s = v; save(); } })),
    h("p", { class: "help" }, "Saved in this browser only. The ride and question apply the next time they are pre-warmed."));
}

// ---------------------------------------------------------------- pre-warm
function syncRideReady() {
  const p = rideSession.phase;
  if (p === "holding") ready.ride = ["ok", "held, goes on step 3"];
  else if (p === "loading" || p === "starting") ready.ride = ["pending", `${rideSession.statusText || "loading"}…`];
  else if (p === "running" || p === "paused") ready.ride = ["ok", "playing"];
  else if (p === "ended") ready.ride = ["ok", "played"];
  else if (p === "error") ready.ride = ["fail", rideSession.error || "error"];
  if (popover?.dataset.kind === "backstage" && !syncRideReady.busy) { syncRideReady.busy = true; renderBackstage(); syncRideReady.busy = false; }
}
async function rideParams() {
  const s = state.demo.settings;
  let rides = null;
  try { rides = await getRides(); } catch { /* defaults below */ }
  const d = (s.ride === "r5stop" ? rides?.defaults?.stop_demo : rides?.defaults?.demo) || (s.ride === "r5stop"
    ? { run: "R5", track: "EB LST->BTH", tracker: "v2", synthetic_stop: 0.5, stop_variant: "quiet", truth: true } : { run: "R9", track: "EB LST->BTH", tracker: "v2", truth: true });
  return { run: d.run, track: d.track, tracker: d.tracker || "v2", speed: s.speed, synthetic_stop: d.synthetic_stop ?? null, stop_variant: d.stop_variant || "quiet", truth: true, hold: true };
}
async function startRide() {
  setReady("ride", "pending", "starting the tracker process");
  try {
    const params = await rideParams();
    const p = rideSession.params;
    if (rideSession.busy && p && p.run === params.run && p.track === params.track && (p.synthetic_stop ?? null) === (params.synthetic_stop ?? null)) { syncRideReady(); return; }
    await rideSession.startOrAdopt(params);
    syncRideReady();
  } catch (e) {
    if (e.status === 409) setReady("ride", "fail", "another heavy job is running: stop it, then restart the demo");
    else setReady("ride", "fail", e.message);
  }
}
function restartRide() { if (["holding", "loading", "starting"].includes(rideSession.phase)) rideSession.stop().then(startRide); }

async function prewarm() {
  if (prewarmed) return;
  prewarmed = true;
  const s = state.demo.settings;
  startRide();
  getAnalyst(1).then(() => setReady("analyst", "ok", "recorded session loaded"), (e) => setReady("analyst", "fail", e.message));
  getAtlas(s.question, "live").then((a) => setReady("atlas", "ok", a.mode === "live" ? "LIVE results" : "CACHED fallback"), (e) => setReady("atlas", "fail", e.message));
  Promise.all([getPack(), getCanary()]).then(() => setReady("pack", "ok", "pack and canary loaded"), (e) => setReady("pack", "fail", e.message));
  if (!s.liveAsk) { setReady("ask", "off", "off in settings"); return; }
  // A paid Gemini call: started automatically only when the demo starts at step 1 or 2 (not when a reload lands on a later
  // step), never twice for the same question (a running server job is followed instead, a recent answer is reused).
  if (state.demo.entryStep > 2) {
    adoptRunningAsk(s.question).then((r) => r ? trackAsk(r, "followed a live ask already running on the server")
      : setReady("ask", "off", `not started: the demo was opened at step ${state.demo.entryStep}, and a background ask starts only from step 1. Demo settings has "Ask now".`))
      .catch(() => setReady("ask", "off", "not started (opened mid-demo)"));
    return;
  }
  liveAsk(false);
}

function trackAsk(run, text) {
  setReady("ask", run.status === "done" ? "ok" : "pending", text);
  run.subscribe(() => { if (run.status === "done") setReady("ask", "ok", "answered live"); else if (run.status === "error") setReady("ask", "fail", run.error || "error"); });
}

/** The live ask is a paid Gemini call: a reload or a second device must not repeat one that was answered a moment ago. */
async function liveAsk(force) {
  const s = state.demo.settings;
  try {
    const st = await fetchJSON("/api/ask/status");
    if (!(st.available && (st.gemini_api_key || MOCK)) || !state.online) { setReady("ask", "off", !st.available ? st.reason || "agent.ask unavailable" : !state.online ? "offline" : "no Gemini key"); return; }
    if (askStore.current?.busy && !askStore.current.cached) { setReady("ask", "pending", "an ask is already running"); return; }
    const adopted = MOCK ? null : await adoptRunningAsk(force ? null : s.question).catch(() => null);
    if (adopted) { trackAsk(adopted, adopted.question === s.question ? "followed a live ask already running on the server" : "another live ask is running on the server; wait for it to finish"); return; }
    if (!force) {
      const recent = ((await fetchJSON("/api/ask/cached")).items || []).find((x) => x.question === s.question && askAgeMin(x.ts) < RECENT_ASK_MIN);
      if (recent) { setReady("ask", "off", `not repeated: this question was answered live ${Math.max(1, Math.round(askAgeMin(recent.ts)))} min ago (that CACHED answer is shown, with its timestamp). Demo settings has "Ask now".`); return; }
    }
    setReady("ask", "pending", "running");
    trackAsk(await startAsk(s.question), "running");
  } catch (e) { setReady("ask", "off", e.message); }
}
