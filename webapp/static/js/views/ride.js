// Live tracking: a recorded ride replayed through the tracker (real compute, paced by the wall clock), drawn as a strip map,
// a schematic geographic view, a readout, a yaw chart and the messy-input QC list.
import { h, mount as mountEl, clear, fmtNum, fmtDur, fmtUTC, fixArrow, announce, rafThrottle, store, icon, ensureCss } from "../util.js";
import { MOCK } from "../api.js";
import { state, getRides, on } from "../state.js";
import { card, chip, chipRow, screenHeader, whereFrom, segmented, errorCard, emptyState, skeleton, banner, startWithReplace, verdict, toast } from "../ui.js";
import { rideSession as session, listPrecomputed } from "../ridectl.js";
import { StripMap, paintRuleText } from "../stripmap.js";
import { GeoMap } from "../geomap.js";
import { YawChart } from "../yawchart.js";
import { createMap3D } from "../map3d.js";

const VARIANT_HELP = {
  quiet: "quiet: constant at the run's median dwell level",
  ideal: "ideal: far quieter than any real stop",
  dwell: "dwell: the run's own longest platform dwell replayed, the realistic one",
};
const HONESTY = "Errors are agreement with an offline curve-edge reference, not GPS truth. Between curves the tracker is no better than a timetable (11 vs 16 m, FINDINGS (f)). The v2 stop gate never engaged on a real ride (0 of 10 sealed-test sections; 0 of 3 real platform stops on an Elizabeth line test ride).";
const REF_TIP = "offline curve-edge reference, interpolated between curve edges matched to the map's base run; it shares the gyro with the tracker; not ground truth";

const keyOf = (r) => `${r.run_id}|${r.track}`;
const dirOf = (track) => String(track || "").split(" ")[0];

function severityFor(line, flags = []) {
  const l = line.toLowerCase();
  const hit = flags.find((f) => l.includes(String(f.type).replace(/_/g, " ")) || l.includes(String(f.type)));
  if (hit?.severity) return hit.severity;
  if (/floor|excluded/.test(l)) return "high";
  if (/pause|gap|handled|not scored|truncat|split|missed/.test(l)) return "medium";
  if (/raw|calibrated/.test(l)) return "info";
  return "low";
}

export async function mount(root, ctx) {
  const demo = ctx.demo;
  ensureCss("css/ride.css");
  root.append(h("div", { class: "screen ride", id: "ride-screen" }));
  const screen = root.firstChild;
  if (demo) screen.classList.add("is-demo");
  const disposers = [];
  let rides = null, strip, geo, yaw;

  const head = h("div");
  const controlsHost = h("div", { class: "ride-controls" });
  const errHost = h("div");
  const synthHost = h("div");
  const warnHost = h("div");
  const stripCard = h("div");
  const map3dHost = h("section", { class: "ride-3d", "aria-label": "3D map of the section (schematic)" });
  const lower = h("div", { class: "ride-grid" });
  const yawHost = h("div");
  const qcHost = h("div");
  screen.append(head, map3dHost, controlsHost, errHost, warnHost, synthHost, stripCard, lower, yawHost, qcHost,
    h("p", { class: "where ride__honesty" }, HONESTY),
    whereFrom("GET /api/rides", "POST /api/ride/start (job events over SSE)", "outputs/qc.json via the init event", "outputs_posthoc/final_config.json", "outputs/online/forward_test.json"));
  mountEl(controlsHost, card({ title: "Ride settings", body: skeleton(3) }));

  try { rides = await getRides(); }
  catch (e) {
    mountEl(controlsHost, errorCard({ message: e.message, title: "Ride catalogue not available", retry: () => location.reload(), fallback: () => playPre(), fallbackLabel: "Play PRECOMPUTED" }));
  }

  // ------------------------------------------------------------ selection state
  const q = ctx.query;
  const last = store.get("lastRide", null) || {};
  const defRide = rides?.defaults?.demo || { run: "R9", track: "EB LST->BTH" };
  const stopDef = rides?.defaults?.stop_demo;
  const demoStop = demo && state.demo.settings.ride === "r5stop" && stopDef;
  const sel = {
    run: demoStop ? stopDef.run : q.get("run") || (demo ? defRide.run : last.run) || defRide.run,
    track: demoStop ? stopDef.track : q.get("track") || (demo ? defRide.track : last.track) || defRide.track,
    tracker: q.get("tracker") || (demo ? "v2" : last.tracker) || "v2",
    speed: Number(q.get("speed")) || (demo ? state.demo.settings.speed : last.speed) || 8,
    stop: demoStop ? stopDef.synthetic_stop : q.get("stop") ? Number(q.get("stop")) : null,
    variant: demoStop ? stopDef.stop_variant : q.get("variant") || "quiet",
    truth: true,
  };
  const rideEntry = () => rides?.rides.find((r) => r.run_id === sel.run && r.track === sel.track);

  // ------------------------------------------------------------ components
  strip = new StripMap(); geo = new GeoMap(); yaw = new YawChart();
  disposers.push(() => { strip.destroy(); geo.destroy(); yaw.destroy(); });
  const refToggle = h("label", { class: "check" }, h("input", { type: "checkbox", checked: true, onchange: (e) => strip.setShowRef(e.target.checked) }), "show reference ring");
  const caption = h("p", { class: "stripmap__caption help" }, "squares: noise this ride is measuring now, painted only where the tracker is confident.");
  mountEl(stripCard, card({ title: "Strip map", cls: "ride-strip", actions: refToggle,
    body: [strip.el, caption] }));
  const R = {};   // readout cells
  const cell = (key, label, tip) => { R[key] = h("dd", { class: "readout__v" }, "–"); return h("div", { class: "readout__row" }, h("dt", { title: tip }, label), R[key]); };
  R.status = h("div", { class: "readout__status", role: "status" }, "IDLE");
  const readout = h("dl", { class: "readout" },
    cell("t", "time"), cell("pos", "position"), cell("conf", "tracker confidence"), cell("speed", "speed"),
    cell("noise", "noise now"), cell("map", "map here"), cell("gate", "stop gate"), cell("ref", "vs curve-edge ref", REF_TIP));
  const endHost = h("div");
  const readoutCard = card({ title: "Readout", cls: "ride-readout", chips: chip("relative"), body: [R.status, readout, endHost],
    footer: "relative levels, uncalibrated · no GPS: gyro curves + speed model" });
  const geoCard = card({ title: "Geographic view (schematic)", cls: "ride-geo", actions: geo.toggle, body: geo.el });
  lower.append(geoCard, readoutCard);
  mountEl(yawHost, card({ title: "Yaw chart", chips: chip("neutral", "deg/s"), body: yaw.el, cls: "ride-yaw" }));

  // ------------------------------------------------------------ 3D map (design v2): same ticks as the strip map, drawn over a generated city
  const m3 = (() => {
    const stage = h("div", { class: "ride-3d__stage" });
    const next = h("div", { class: "ride-3d__next" }, h("b", null, "–"), h("span", null, "waiting for a ride"));
    const noiseNow = h("b", null, "–"), noiseMap = h("b", null, "–");
    const tiles = h("div", { class: "ride-3d__noise", role: "group", "aria-label": "Noise now and the map's usual level here" },
      h("div", { class: "ride-3d__now" }, noiseNow, h("span", null, "noise now")), h("div", { class: "ride-3d__usual" }, noiseMap, h("span", null, "usual here")));
    const status = h("span", { class: "ride-3d__status" }, "Idle");
    const note = h("p", { class: "ride-3d__note" }, "Schematic: station positions are real, the line between them is smoothed and the buildings are generated. Position = distance along the section, not GPS. Levels: dBFS(A), relative, uncalibrated.");
    const pref = store.get("ride3d", true);
    const toggle = h("button", { type: "button", class: "ride-3d__toggle", "aria-pressed": String(pref), onclick: () => setOn(!on) }, pref ? "Hide 3D" : "Show 3D");
    const box = h("div", { class: "ride-3d__box" }, stage, h("div", { class: "ride-3d__hud" }, next, status), tiles, toggle);
    map3dHost.append(box, note);
    let map = null, on = pref, cur = null, failed = false;
    function setOn(v) {
      on = v; store.set("ride3d", v); toggle.textContent = v ? "Hide 3D" : "Show 3D"; toggle.setAttribute("aria-pressed", String(v));
      map3dHost.classList.toggle("is-off", !v);
      if (v && !map && !failed) build();
    }
    async function build() {
      try {
        map = await createMap3D(stage, { theme: "dark", follow: false, zoom: 1.45, focus: "LST-BTH", stations: session.init?.stations });
        disposers.push(() => map?.destroy());
        if (session.init) api.init(session.init);
        if (session.last) api.tick(session.last);
      } catch (e) {
        failed = true; map3dHost.classList.add("is-failed");
        stage.append(h("p", { class: "ride-3d__fail" }, `3D map unavailable (${e.message || e}). The strip map and geographic view below show the same ride.`));
      }
    }
    const api = {
      init(init) {
        cur = init;
        map?.focus(init.from, init.to);
        map?.setNoise({ from: init.from, to: init.to, edges: init.map?.bin_edges_m, db: init.map?.noise_db, vmin: init.map?.vmin, vmax: init.map?.vmax, length: init.length_m });
      },
      tick(t) {
        const init = cur || session.init; if (!init) return;
        map?.setTrain(t.s_m === null ? null : { from: init.from, to: init.to, s_m: t.s_m, lo: t.band_lo, hi: t.band_hi, length: init.length_m, label: `${fmtNum(t.s_m, 0)} m` });
        if (t.s_m !== null && t.s_m !== undefined) {
          next.firstChild.textContent = `${fmtNum(Math.max(0, init.length_m - t.s_m), 0)} m`;
          next.lastChild.textContent = `to ${init.to_name || init.to}`;
        }
        noiseNow.textContent = t.level_rel === null || t.level_rel === undefined ? "n/a" : fmtNum(t.level_rel, 1);
        noiseMap.textContent = t.level_map === null || t.level_map === undefined ? "n/a" : fmtNum(t.level_map, 1);
        status.textContent = t.status ? t.status.charAt(0) + t.status.slice(1).toLowerCase() : "";
        status.dataset.state = t.status === "TRACKING" ? "live" : String(t.status || "").startsWith("HOLDING") ? "hold" : "";
      },
      reset() { cur = null; map?.setTrain(null); next.firstChild.textContent = "–"; next.lastChild.textContent = "waiting for a ride"; noiseNow.textContent = noiseMap.textContent = "–"; status.textContent = "Idle"; delete status.dataset.state; },
    };
    setOn(pref);
    return api;
  })();

  // ------------------------------------------------------------ header
  function renderHead() {
    const init = session.init, e = rideEntry();
    const title = init ? `${dirOf(init.track)} ${init.from_name} → ${init.to_name} · ride ${init.run_id} · ${init.day}`
      : e ? `${dirOf(e.track)} ${fixArrow(e.track.split(" ")[1] || "")} · ride ${e.run_id} · ${e.day}` : "Live tracking";
    const chips = [];
    if (init || session.busy) {
      const rid = init?.run_id || sel.run, day = init?.day || rideEntry()?.day || "";
      chips.push(chip("replay", `REPLAY of recorded ride ${rid}${day ? ` (${day})` : ""}`));
      if (MOCK) chips.push(chip("mock"));
      else if (session.source === "precomputed") chips.push(chip("precomputed", `PRECOMPUTED · ${fmtUTC(session.generated)}`));
      else chips.push(chip("live", "LIVE compute"));
      const trk = init?.tracker || sel.tracker;
      chips.push(trk === "v2" ? chip("posthoc", `POST-HOC tracker v2${init?.config_hash ? `, config ${init.config_hash}` : ""}`) : chip("prereg", "PRE-REGISTERED tracker v1"));
      chips.push(chip("relative"));
      if (init?.synthetic_stop || (!init && sel.stop)) chips.push(chip("synthetic", "SYNTHETIC stop"));
    } else chips.push(chip("relative"));
    mountEl(head, screenHeader({ title, chips, subtitle: init?.sample_note || e?.sample_note || "Replays a recorded ride through the tracker, step by step, paced by the clock." }));
    document.title = `${title.split(" · ")[0]} · Tubesense`;
  }

  // ------------------------------------------------------------ controls
  let startBtn, pauseBtn, stopBtn, rideSel, speedSeg, trackerSeg, stopSeg, variantSel, preLink, timerId = 0;
  function buildControls() {
    if (!rides) return;
    rideSel = h("select", { id: "ride-select", "aria-label": "Ride", onchange: () => { const [run, track] = rideSel.value.split("|"); Object.assign(sel, { run, track }); onSelChange(); } });
    for (const day of ["24 Oct", "23 Oct"]) {
      const g = h("optgroup", { label: day === "24 Oct" ? "24 Oct (out-of-sample for the tracker settings and map)" : "23 Oct (dev data, map is leave-one-out)" });
      for (const r of rides.rides.filter((x) => x.day === day)) {
        const sc = r.offline_score?.v2 ? ` · offline v2 score ${fmtNum(r.offline_score.v2.median_m, 1)} m` : "";
        const missed = r.departure_missed_online ? " · online departure missed" : "";
        const txt = `${r.run_id} · ${fixArrow(r.track)} · ${r.day}${sc}${missed}${r.available ? "" : ` · unavailable: ${r.reason}`}`;
        g.append(h("option", { value: keyOf(r), disabled: !r.available, selected: keyOf(r) === `${sel.run}|${sel.track}` }, txt));
      }
      rideSel.append(g);
    }
    trackerSeg = segmented({ label: "Tracker", value: sel.tracker, options: [{ value: "v2", label: "v2 (post-hoc)" }, { value: "v1", label: "v1 (pre-registered)" }], onChange: (v) => { sel.tracker = v; persist(); renderHead(); } });
    speedSeg = segmented({ label: "Speed", value: sel.speed, options: (rides.speeds || [1, 4, 8]).map((n) => ({ value: n, label: `${n}×` })),
      onChange: async (v) => { sel.speed = v; persist(); if (session.busy) { try { await session.setSpeed(v); } catch (e) { toast(e.message); } } } });
    stopSeg = segmented({ label: "Synthetic stop", value: sel.stop === null ? "off" : String(sel.stop), options: [
      { value: "off", label: "off" }, { value: "0.3", label: "30 %" }, { value: "0.5", label: "50 %" }, { value: "0.7", label: "70 %" }],
      onChange: (v) => { sel.stop = v === "off" ? null : Number(v); onSelChange(); renderHead(); } });
    variantSel = h("select", { id: "variant-select", "aria-label": "Stop variant", onchange: () => { sel.variant = variantSel.value; onSelChange(); } },
      ["quiet", "ideal", "dwell"].map((v) => h("option", { value: v, selected: v === sel.variant }, v)));
    startBtn = h("button", { class: "btn", type: "button", onclick: () => start(false) }, icon("play", 16), "Start");
    pauseBtn = h("button", { class: "btn btn--secondary", type: "button", onclick: () => session.toggle().catch((e) => toast(e.message)) }, icon("pause", 16), "Pause");
    stopBtn = h("button", { class: "btn btn--secondary", type: "button", onclick: () => session.stop() }, icon("stop", 16), "Stop");
    preLink = h("button", { class: "btn btn--small btn--secondary", type: "button", hidden: true, onclick: () => playPre() }, "Play PRECOMPUTED instead");
    const body = h("div", { class: "ride-controls__grid" },
      h("label", { class: "field ride-controls__ride" }, "Ride", rideSel),
      h("div", { class: "field" }, h("span", null, "Tracker"), trackerSeg),
      h("div", { class: "field" }, h("span", null, "Speed"), speedSeg),
      h("div", { class: "field" }, h("span", null, "Synthetic stop (30 s, inserted)"), stopSeg),
      h("label", { class: "field" }, "Stop variant", variantSel),
      h("div", { class: "ride-controls__buttons row" }, startBtn, pauseBtn, stopBtn, preLink));
    const help = h("p", { class: "help", id: "variant-help" });
    const det = h("details", { class: "settings" , open: !matchMedia("(max-width: 639px)").matches },
      h("summary", null, "Ride settings"), body, help, h("p", { class: "help" }, "Speed changes apply immediately. Ride, tracker and stop apply at the next Start."));
    mountEl(controlsHost, card({ cls: "ride-controls-card", body: det }));
    ctl.help = help;
    onSelChange(true);
  }
  const ctl = {};
  function persist() { store.set("lastRide", { run: sel.run, track: sel.track, tracker: sel.tracker, speed: sel.speed }); }
  function onSelChange(silent) {
    const e = rideEntry();
    if (variantSel) {
      for (const o of variantSel.options) o.disabled = o.value !== "ideal" && e && !e.has_dwell;
      if (e && !e.has_dwell && sel.variant !== "ideal") { sel.variant = "ideal"; variantSel.value = "ideal"; }
      variantSel.disabled = sel.stop === null;
      ctl.help.textContent = sel.stop === null ? "" : `${VARIANT_HELP[sel.variant]}${e && !e.has_dwell ? ". This ride has no recorded platform dwell, so only the ideal variant is available." : ""}`;
    }
    if (!silent) { persist(); renderHead(); }
    if (e?.departure_missed_online) setWarn("The online departure detector missed this departure in the forward test; expect a late or wrong start.");
    else setWarn(null);
    syncButtons();
  }
  function setWarn(msg, kind = "amber") {
    clear(warnHost);
    if (msg) { const b = banner(kind, icon("warn", 16), msg); b.classList.add("banner--inline"); warnHost.append(b); }
  }

  function params(hold = false) {
    return { run: sel.run, track: sel.track, tracker: sel.tracker, speed: sel.speed, synthetic_stop: sel.stop, stop_variant: sel.variant, truth: true, hold };
  }
  async function start(hold) {
    clear(errHost);
    try {
      const job = await startWithReplace((replace) => demo && !replace ? session.startOrAdopt(params(hold)) : session.start({ ...params(hold), replace }));
      if (!job) return null;
      persist();
      return job;
    } catch (e) {
      mountEl(errHost, errorCard({ message: e.message, title: "The ride did not start", retry: () => start(hold), fallback: () => playPre(), fallbackLabel: "Play PRECOMPUTED" }));
      return null;
    }
  }
  async function playPre() {
    clear(errHost);
    const items = await listPrecomputed();
    const want = items.find((i) => i.run === sel.run && i.track === sel.track && i.tracker === sel.tracker && (i.synthetic_stop ?? null) === (sel.stop ?? null)) ||
                 items.find((i) => i.run === sel.run && i.track === sel.track) || items[0];
    if (!want) { mountEl(errHost, emptyState("No PRECOMPUTED stream is stored on the server (webapp/precomputed).")); return; }
    try { await session.playPrecomputed(want, sel.speed); } catch (e) { mountEl(errHost, errorCard({ message: e.message })); }
  }

  let items = [];
  listPrecomputed().then((l) => { items = l; if (preLink) preLink.hidden = !l.length; }).catch(() => {});

  // ------------------------------------------------------------ session -> screen
  const applyReadout = rafThrottle(() => {
    const t = session.last, init = session.init; if (!t || !init) return;
    const win = init.paint_rule?.lock_window_m ?? 50;
    R.status.textContent = t.status;
    R.status.className = ["readout__status", t.status === "TRACKING" && "is-live", t.status.startsWith("HOLDING") && "is-hold", t.status === "ARRIVED" && "is-done"].filter(Boolean).join(" ");
    R.t.textContent = t.departure !== undefined && t.s_m !== null ? `${t.t >= 0 ? "+" : "−"}${Math.abs(t.t).toFixed(1)} s since departure` : `${t.t.toFixed(1)} s`;
    R.pos.textContent = t.s_m === null ? "n/a" : `${fmtNum(t.s_m, 0)} m · 90 %: ${fmtNum(t.band_lo, 0)}–${fmtNum(t.band_hi, 0)} m`;
    R.conf.textContent = t.lock === null ? "n/a" : `lock ${Math.round(t.lock * 100)} % within ±${win} m${t.confident ? " · confident" : ""}`;
    R.speed.textContent = t.speed_kmh === null ? "n/a" : `${fmtNum(t.speed_kmh, 0)} km/h`;
    R.noise.textContent = t.level_rel === null ? (t.in_synthetic_stop ? "n/a (synthetic stop)" : "n/a") : `${fmtNum(t.level_rel, 1)} dBFS(A) · relative`;
    R.map.textContent = t.level_map === null || t.level_map === undefined ? "n/a" : `${fmtNum(t.level_map, 1)} dBFS(A)`;
    R.gate.textContent = { none: "none (v1 has no gate)", warming: "warming up", off: `off (z ${fmtNum(t.gate_z, 2)})`, engaged: `ENGAGED (z ${fmtNum(t.gate_z, 2)})` }[t.gate_state] || t.gate_state;
    R.ref.textContent = t.truth_s_m === null || t.truth_s_m === undefined || t.s_m === null ? "n/a" : `${fmtNum(Math.abs(t.s_m - t.truth_s_m), 0)} m`;
    R.ref.parentElement.hidden = t.truth_s_m === null || t.truth_s_m === undefined;
  });
  let lastStatus = "";
  let statusTimer = 0;

  function onInit(init) {
    strip.setInit(init); geo.setInit(init); yaw.setInit(init);
    m3.init(init);
    mountEl(caption, paintRuleText(init.paint_rule));
    if (init.live_only) caption.append(" (LIVE ONLY: ride excluded from noise averages)");
    renderHead(); renderQc(init);
    if (!init.truth_available) setWarn(`No reference for this ride: ${init.truth_label || "curve-edge reference unavailable"}.`);
  }
  function onTick(t) {
    m3.tick(t);
    strip.setTarget({ s: t.s_m, lo: t.band_lo, hi: t.band_hi, ref: t.truth_s_m ?? null });
    geo.update({ s: t.s_m, lo: t.band_lo, hi: t.band_hi });
    strip.setSynthetic(t.in_synthetic_stop ? "SYNTHETIC 30 s STOP (inserted for the demo)" : null);
    yaw.add(t);
    if (t.paint) { strip.addPaint(t.paint); geo.addPaint(t.paint); }
    applyReadout();
    if (t.status !== lastStatus) { lastStatus = t.status; announce(`Ride status: ${t.status}`); }
  }
  function renderEnd() {
    const sm = session.summary; if (!sm) return;
    const init = session.init, trk = init?.tracker || sel.tracker, off = trk === "v1" ? rideEntry()?.offline_score?.v1 : init?.offline_score?.v2;
    const lines = [h("li", null, `${sm.steps} steps, ${fmtNum(sm.ride_s, 1)} s of ride, ${fmtNum(sm.wall_s, 1)} s wall time${session.source === "live" ? "" : " when it was generated"}, ${fmtNum(sm.tracker_ms_per_step, 2)} ms per step`)];
    if (sm.vs_truth_median_m !== null && sm.vs_truth_median_m !== undefined) lines.push(h("li", null, `median |dot − reference| ${fmtNum(sm.vs_truth_median_m, 1)} m over ${sm.vs_truth_n} steps (agreement with the offline curve-edge reference, not ground truth)`));
    if (off) lines.push(h("li", null, trk === "v1"
      ? `pre-registered forward test, tracker v1, this ride: median ${fmtNum(off.median_m, 1)} m against the independent reference, 90 % coverage ${fmtNum(off.coverage, 3)}. ${off.source}`
      : `sealed v2 test of the recorded chain, this ride: median ${fmtNum(off.median_m, 1)} m, 90 % coverage ${fmtNum(off.coverage, 3)}. ${off.source}`));
    if (sm.stop) lines.push(h("li", null, (sm.stop.end_err_m === null || sm.stop.end_err_m === undefined ? "end of stop: no reference available; " : `end of stop: dot ${sm.stop.end_err_m >= 0 ? "+" : "−"}${Math.abs(sm.stop.end_err_m)} m from the reference; `) +
      (sm.stop.gate_engaged_after_s === null || sm.stop.gate_engaged_after_s === undefined ? "the stop gate never engaged" : `gate engaged ${fmtNum(sm.stop.gate_engaged_after_s, 2)} s into the stop`) + " (synthetic stop, not a real one)"));
    mountEl(endHost, h("div", { class: "endcard" }, h("h3", null, "Ride complete"), h("ul", null, lines),
      h("div", { class: "row" },
        h("button", { class: "btn btn--small", type: "button", onclick: () => start(false) }, "Replay"),
        h("button", { class: "btn btn--small btn--secondary", type: "button", onclick: () => { clear(endHost); controlsHost.scrollIntoView({ behavior: "smooth", block: "start" }); rideSel?.focus(); } }, "Change ride"))));
  }
  function renderQc(init) {
    const qc = init.qc || {}, flags = qc.flags || [];
    const sevOf = new Map((qc.this_ride_items || []).map((x) => [x.text, x.severity]));
    const li = (line) => { const sev = sevOf.get(line) || severityFor(line, flags); return h("li", null, h("span", { class: ["sev", `sev--${sev}`], "aria-hidden": "true" }), h("span", { class: "sr-only" }, `${sev}: `), line); };
    const det = h("details", { class: "qc", open: !matchMedia("(max-width: 639px)").matches && !demo },
      h("summary", null, "MESSY-INPUT QC (automatic, outputs/qc.json) ", h("span", { class: "badge" }, String((qc.this_ride || []).length))),
      h("h3", null, "This ride"), (qc.this_ride || []).length ? h("ul", { class: "qc__list" }, qc.this_ride.map(li)) : h("p", { class: "muted" }, "No flags for this ride."),
      (qc.other || []).length ? [h("h3", null, "Other rides"), h("ul", { class: "qc__list qc__list--brief" }, qc.other.map(li))] : null);
    mountEl(qcHost, card({ title: null, body: det, cls: "ride-qc" }));
  }
  function renderPhase() {
    syncButtons();
    const p = session.phase;
    if (p === "error") mountEl(errHost, errorCard({ message: session.error || "The ride worker failed.", title: "The ride stopped with an error", retry: () => start(false), fallback: () => playPre(), fallbackLabel: "Play PRECOMPUTED" }));
    else if (session.lost) mountEl(errHost, errorCard({ message: "Connection lost to the event stream.", title: "Connection lost", retry: () => session.reconnect(), fallbackLabel: "" }));
    else if (p !== "error") clear(errHost);
    if (session.warnings.length) setWarn(session.warnings[session.warnings.length - 1]);
    if (p === "idle" || p === "starting") { R.status.className = "readout__status"; R.status.textContent = session.lost ? "CONNECTION LOST" : p === "starting" ? "STARTING" : "IDLE"; }
    if (["loading", "starting", "holding"].includes(p)) {
      R.status.className = "readout__status";
      R.status.textContent = p === "holding" ? "READY (waiting for go)" : session.statusText.toUpperCase();
    }
  }
  function syncButtons() {
    if (!startBtn) return;
    const p = session.phase, busy = session.busy, down = !state.serverOk && !MOCK;
    const loading = ["starting", "loading"].includes(p);
    startBtn.disabled = down || loading || p === "running" || p === "paused";
    startBtn.lastChild.textContent = loading ? ` ${session.statusText}…` : p === "holding" ? " Start (held)" : p === "ended" ? " Replay" : " Start";
    pauseBtn.disabled = !(p === "running" || p === "paused" || p === "holding");
    pauseBtn.lastChild.textContent = p === "paused" ? " Resume" : p === "holding" ? " Go" : " Pause";
    stopBtn.disabled = !busy;
    const locked = busy;
    [rideSel, trackerSeg, stopSeg, variantSel].forEach((n) => { if (!n) return; if (n.setDisabled) n.setDisabled(locked); else n.disabled = locked || (n === variantSel && sel.stop === null); });
    if (speedSeg) speedSeg.set(session.busy ? session.speed : sel.speed);
    clearInterval(timerId);
    if (loading) timerId = setInterval(() => { startBtn.lastChild.textContent = ` ${session.statusText} · ${Math.round((Date.now() - session.statusAt) / 1000)} s…`; }, 1000);
  }

  const unsub = session.subscribe((kind, ev) => {
    switch (kind) {
      case "reset": strip.reset(); geo.reset(); yaw.reset(); m3.reset(); clear(endHost); clear(qcHost); lastStatus = ""; renderHead(); renderPhase(); break;
      case "init": onInit(ev); break;
      case "tick": onTick(ev); break;
      case "end": renderEnd(); renderPhase(); break;
      case "warning": setWarn(ev.message); break;
      default: renderPhase(); if (kind === "phase" || kind === "status") renderHead(); break;
    }
  });
  disposers.push(unsub);
  disposers.push(on("health", syncButtons));

  // catch up with a session that already exists (demo pre-warm, or leaving and returning)
  buildControls();
  renderHead();
  if (session.init) {
    onInit(session.init);
    session.paints.forEach((p) => { strip.addPaint(p); geo.addPaint(p); });
    yaw.load(session.ticks);
    if (session.last) { strip.cur = { s: null, lo: null, hi: null, ref: null }; onTick({ ...session.last, paint: null }); }
    if (session.summary) renderEnd();
  }
  renderPhase();

  // ------------------------------------------------------------ demo and deep-link behaviour
  if (demo) {
    state.demo.hooks = { playPause: () => session.toggle().catch(() => {}), label: "ride" };
    const matches = session.params && session.params.run === sel.run && session.params.track === sel.track && (session.params.synthetic_stop ?? null) === (sel.stop ?? null);
    const goWhenHeld = () => { if (session.phase === "holding") session.go().catch((e) => toast(e.message)); };
    if (session.busy && matches) { goWhenHeld(); disposers.push(session.subscribe((k) => { if (k === "phase") goWhenHeld(); })); }
    else if (session.phase === "ended" && matches) { /* already played */ }
    else if (rides) {
      disposers.push(session.subscribe((k) => { if (k === "phase") goWhenHeld(); }));
      start(false).then(goWhenHeld);        // a re-attached tracker may be held
    }
  } else if (q.get("autostart") === "1" && rides && !session.busy) start(false);

  return () => {
    clearInterval(timerId);
    disposers.forEach((d) => { try { d(); } catch (e) { console.error(e); } });
    if (state.demo.hooks?.label === "ride") state.demo.hooks = null;
    // a held or still-loading job would sit on RAM: cancel it unless the demo is driving
    if (!state.demo.active && ["holding", "loading", "starting"].includes(session.phase)) session.stop();
  };
}
