// Live sensors: what the phone senses now, from phyphox over the local network (LIVE), a labelled MOCK replay of a recorded
// ride, or the Tubesense mobile app prototype posting one row per second (APP). Per-second summaries only: rotation about
// gravity (yaw rate), vibration RMS, MOVING / STOPPED, knocks (double = sync knock), placement, pressure, |B|, sensor rates.
// It never positions the train (positioning runs on recorded rides, Ride page) and never touches the microphone.
// Capture = server job `live` (python -m live.capture --jsonl, one heavy job at a time, Stop kills it, the server cancels it
// after 60 s with no page following it). App rows = GET /api/live/app/rows polled once a second.
import { h, s, mount as mountEl, clear, fmtNum, fmtDur, fmtTimeLocal, announce, icon, store } from "../util.js";
import { MOCK, fetchJSON, postJSON, openJobStream } from "../api.js";
import { state, setAtlasStatus, getHealth, on } from "../state.js";
import { card, chip, screenHeader, whereFrom, errorCard, emptyState, startWithReplace, toast, eventFeed, segmented } from "../ui.js";

const DEFAULT_URL = "http://172.20.10.1";
const WINDOW_S = 60;
const KEEP = 900;
const LIVE_ST = ["starting", "holding", "running"];
const num = (v) => (typeof v === "number" && isFinite(v) ? v : null);

// ---------------------------------------------------------------- 60 s SVG trace
class Trace {
  constructor({ title, sub, unit, color, sym, minSpan, digits }) {
    Object.assign(this, { title, unit, color, sym, minSpan, digits });
    this.now = h("span", { class: "lv-trace__now mono" }, "—");
    this.svg = s("svg", { class: "lv-trace__svg", role: "img", "aria-label": `${title}: no data yet` });
    this.el = h("figure", { class: "lv-trace" },
      h("figcaption", { class: "lv-trace__head" }, h("span", null, h("strong", null, title), h("span", { class: "muted" }, ` · ${sub}`)), this.now),
      this.svg);
    this.args = null;
    this.ro = new ResizeObserver(() => this.args && this.draw(...this.args));
    this.ro.observe(this.el);
  }
  destroy() { this.ro.disconnect(); }
  draw(buf, key, tEnd) {
    this.args = [buf, key, tEnd];
    const W = Math.max(240, Math.round(this.el.clientWidth || 600)), H = W < 560 ? 104 : 132;
    const L = 44, R = 8, T = 8, B = 20, pw = W - L - R, ph = H - T - B, t0 = tEnd - WINDOW_S;
    const pts = buf.filter((d) => d.t > t0 - 1 && d.t <= tEnd);
    const vals = pts.map((d) => num(d[key])).filter((v) => v !== null);
    let hi = Math.max(this.minSpan, ...vals.map((v) => Math.abs(v) * 1.15));
    const mag = 10 ** Math.floor(Math.log10(hi)), step = [1, 2, 2.5, 5, 10].find((m) => m * mag >= hi / 2) * mag;
    hi = Math.ceil(hi / step) * step;
    const lo = this.sym ? -hi : 0;
    const X = (t) => L + ((t - t0) / WINDOW_S) * pw, Y = (v) => T + ph * (1 - (Math.min(hi, Math.max(lo, v)) - lo) / (hi - lo));
    const kids = [];
    // stopped spans (shaded) and knock marks
    let a = null;
    pts.forEach((d, i) => {
      const st = d.state === "stopped";
      if (st && a === null) a = d.t - 0.5;
      if ((!st || i === pts.length - 1) && a !== null) { const b = st ? d.t + 0.5 : d.t - 0.5; kids.push(s("rect", { class: "lv-stop", x: X(Math.max(t0, a)), y: T, width: Math.max(1, X(b) - X(Math.max(t0, a))), height: ph })); a = null; }
    });
    for (const v of this.sym ? [lo, lo / 2, 0, hi / 2, hi] : [0, hi / 2, hi]) {
      kids.push(s("line", { class: v === 0 ? "lv-axis lv-axis--zero" : "lv-axis", x1: L, x2: L + pw, y1: Y(v), y2: Y(v) }));
      kids.push(s("text", { class: "lv-tick", x: L - 6, y: Y(v) + 4, "text-anchor": "end" }, fmtNum(v, (String(+v.toFixed(3)).split(".")[1] || "").length)));
    }
    for (const dt of [-60, -45, -30, -15, 0]) kids.push(s("text", { class: "lv-tick", x: X(tEnd + dt), y: H - 5, "text-anchor": dt === -60 ? "start" : dt === 0 ? "end" : "middle" }, dt === 0 ? "now" : `${dt} s`));
    pts.filter((d) => d.knock).forEach((d) => kids.push(s("line", { class: "lv-knock", x1: X(d.t), x2: X(d.t), y1: T, y2: T + ph })));
    let path = "", prevT = null;
    for (const d of pts) {                 // a null value or a > 1.6 s hole breaks the line (no invented samples)
      const v = num(d[key]);
      if (v === null) { prevT = null; continue; }
      path += `${prevT === null || d.t - prevT > 1.6 ? "M" : "L"}${X(d.t).toFixed(1)},${Y(v).toFixed(1)}`;
      prevT = d.t;
    }
    if (path) kids.push(s("path", { d: path, class: "lv-line", stroke: this.color }));
    const last = pts[pts.length - 1];
    if (last && num(last[key]) !== null) kids.push(s("circle", { cx: X(last.t), cy: Y(last[key]), r: 3.5, fill: this.color }));
    this.svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    this.svg.setAttribute("height", H);
    mountEl(this.svg, kids);
    const v = last ? num(last[key]) : null;
    this.now.textContent = v === null ? "—" : `${fmtNum(v, this.digits ?? 2)} ${this.unit}`;
    this.svg.setAttribute("aria-label", `${this.title}, last 60 s: ${v === null ? "no value" : `now ${fmtNum(v, this.digits ?? 2)} ${this.unit}`}`);
  }
}

// ---------------------------------------------------------------- screen
export async function mount(root) {
  const screen = h("div", { class: "screen live" });
  root.append(screen);
  const disposers = [];
  let health = null;
  try { health = await getHealth(); } catch { health = null; }

  // ---- state
  let mode = "idle";            // idle | job | app
  let src = null;               // {kind: live|mock|app, label}
  let phase = "stopped";        // stopped | connecting | streaming | ended
  let job = null, stream = null, endNote = "", jobParams = null, tStart = 0;
  let buf = [], lastSample = null, tick = 0, appSession = null, appAfter = 0, appTimer = 0, appListTimer = 0, leaseTimer = 0;

  // ---- header and honest copy
  const statusEl = h("div", { class: "livestat", role: "status", "aria-live": "polite" });
  const head = screenHeader({ title: "Live sensors", subtitle: "What the phone senses right now: rotation about gravity, vibration, stops, knocks, placement, pressure and the magnetic field. One summary per second.",
    chips: [chip("neutral", "no microphone"), chip("neutral", "per-second summaries only")] });
  const honest = h("div", { class: "lv-honest" },
    h("p", { class: "lead" }, "This shows what the phone senses now. Off a train it cannot place the train; positioning runs on recorded rides (", h("a", { href: "#/ride" }, "Ride page"), ")."),
    h("p", { class: "lv-privacy" }, icon("method", 16), h("span", null, "No microphone. Raw samples stay in memory; only per-second summaries leave the laptop (to Atlas only when the toggle is on).")));

  // ---- big status bar
  const motionBadge = h("div", { class: "lv-motion", "data-state": "none" }, "—");
  const flash = h("div", { class: "lv-flash", "aria-live": "assertive" });
  const sourceLine = h("p", { class: "lv-source mono" });
  const timeLine = h("p", { class: "help mono lv-time" });
  const stopBtn = h("button", { class: "btn btn--danger", type: "button", onclick: () => stopAll() }, icon("stop", 16), "Stop");
  const bar = h("section", { class: "card lv-bar" },
    h("div", { class: "lv-bar__status" }, statusEl, h("div", { class: "lv-bar__text" }, sourceLine, timeLine)),
    h("div", { class: "lv-bar__right" }, flash, motionBadge, stopBtn));

  // ---- controls
  const urlIn = h("input", { type: "text", inputmode: "url", spellcheck: false, autocomplete: "off", value: store.get("live.url", DEFAULT_URL) || DEFAULT_URL, "aria-label": "Phone address shown by phyphox", placeholder: DEFAULT_URL });
  const probeBtn = h("button", { class: "btn btn--secondary", type: "button", onclick: () => doProbe() }, icon("zoom", 16), "Probe");
  const probeOut = h("div", { class: "lv-probe", "aria-live": "polite" });
  const liveBtn = h("button", { class: "btn", type: "button", onclick: () => startJob({ url: urlIn.value.trim() }) }, icon("play", 16), "Start live");
  const mockBtn = h("button", { class: "btn btn--secondary", type: "button", onclick: () => startJob({ mock: "R9", speed: mockSpeed }) }, icon("play", 16), "Start mock (R9)");
  let mockSpeed = 1;
  const speedSeg = segmented({ label: "Mock replay speed", value: 1, options: [{ value: 1, label: "1×" }, { value: 4, label: "4×" }], onChange: (v) => { mockSpeed = v; } });
  const secSel = h("select", { "aria-label": "Capture length" }, [60, 120, 300, 600].map((n) => h("option", { value: n, selected: n === 120 }, n < 120 ? `${n} s` : `${n / 60} min`)));
  const atlasIn = h("input", { type: "checkbox", id: "lv-atlas", role: "switch", checked: false, onchange: () => { renderAtlasNote(); appLease(); } });
  const atlasNote = h("div", { class: "lv-atlasnote" });
  const busyNote = h("p", { class: "help" });
  const controls = card({ title: "Source", cls: "lv-controls", chips: chip("neutral", "phyphox · Allow remote access"), body: [
    h("label", { class: "field" }, h("span", null, "Phone address (as phyphox shows it)"), h("div", { class: "lv-urlrow" }, urlIn, probeBtn)),
    probeOut,
    h("div", { class: "row lv-startrow" }, liveBtn, h("span", { class: "lv-or muted" }, "or"), mockBtn, speedSeg),
    h("div", { class: "row" }, h("label", { class: "field-inline" }, h("span", null, "length"), secSel),
      h("label", { class: "lv-switch", for: "lv-atlas" }, atlasIn, h("span", { class: "lv-switch__track", "aria-hidden": "true" }), h("span", null, "Send to Atlas"))),
    atlasNote, busyNote,
    h("aside", { class: "lv-tip" }, icon("warn", 16), h("p", null, h("strong", null, "Tip: "), "Venue Wi-Fi often isolates devices: put the laptop on the phone's hotspot. Keep phyphox open with Allow remote access on and the screen awake."))] });

  // ---- readouts
  const ro = {};
  const tile = (k, label, cls) => {
    ro[k] = h("dd", { class: "readout__v" }, "—");
    return h("div", { class: ["readout__row", cls] }, h("dt", null, label), ro[k]);
  };
  const readouts = card({ title: "Now", cls: "lv-now", body: h("dl", { class: "readout lv-readout" },
    tile("yaw", "rotation about gravity"), tile("vib", "vibration RMS"), tile("p", "pressure"), tile("dp", "pressure change, 10 s"),
    tile("b", "|B| magnetic field"), tile("place", "placement"), tile("rates", "sensor rates", "lv-wide")) });

  // ---- traces
  const yawTr = new Trace({ title: "Yaw rate", sub: "rotation about gravity, deg/s, + = turning left", unit: "deg/s", color: "#FFB74D", sym: true, minSpan: 2, digits: 1 });
  const vibTr = new Trace({ title: "Vibration RMS", sub: "m/s², 2 s window", unit: "m/s²", color: "#4FC3F7", sym: false, minSpan: 0.05, digits: 3 });
  disposers.push(() => { yawTr.destroy(); vibTr.destroy(); });
  const traces = card({ title: "Last 60 seconds", cls: "lv-traces", body: [yawTr.el, vibTr.el,
    h("p", { class: "help lv-legend" }, h("i", { class: "sw sw--stop" }), "shaded = STOPPED (stop detector)", h("i", { class: "sw sw--knock" }), "amber line = knock",
      h("span", null, "Time axis = the phone's experiment seconds (a mock at 4× fills 60 s in 15 s).")) ] });

  // ---- events
  const feed = eventFeed({ height: "300px" });
  const logBox = h("pre", { class: "joblog mono" });
  const events = card({ title: "Events", cls: "lv-events", body: [feed, h("details", { class: "lv-logs" }, h("summary", null, "Process log"), logBox)] });

  // ---- mobile app (prototype)
  const appBody = h("div", { class: "stack" });
  const appCard = card({ title: "Tubesense mobile app", cls: "lv-app", chips: h("span", { class: "chip lv-chip--app" }, "APP · prototype"), body: appBody,
    footer: "The app posts one derived row per second to POST /api/live/ingest (no audio field; anything outside the schema is refused). It reaches this laptop only when the server runs with --lan. Rows are kept in memory, 300 per session." });

  screen.append(head, honest, bar,
    h("div", { class: "lv-grid" }, h("div", { class: "lv-col" }, controls, appCard), h("div", { class: "lv-col" }, readouts, traces)),
    events,
    whereFrom("POST /api/live/start → python -m live.capture --jsonl (live/detect.py: frozen tubesense logic, causal)", "GET /api/live/probe (live/phyphox.py)",
      "GET /api/live/app/rows (mobile app rows, memory only)", "events file: outputs_posthoc/live_capture/<ride_id>.jsonl (derived values)"));

  // ---------------------------------------------------------------- rendering
  function renderStatus() {
    const st = phase === "stopped" || phase === "ended" ? "stopped" : phase === "connecting" ? "connecting" : src?.kind || "live";
    statusEl.dataset.state = st;
    mountEl(statusEl, st === "live" ? h("span", { class: "live-dot", "aria-hidden": "true" }) : null,
      { live: "LIVE", mock: "MOCK", app: "APP", connecting: "CONNECTING", stopped: "STOPPED" }[st]);
    sourceLine.textContent = phase === "stopped" ? "Nothing is being captured." : phase === "ended" ? endNote
      : src?.kind === "app" ? `APP: Tubesense mobile (prototype) · session ${appSession}${phase === "connecting" ? " · no row for more than 5 s" : ""}`
      : phase === "connecting" && src?.kind === "live" && jobParams?.url ? `Trying the phone at ${jobParams.url} (retrying every 2 s; reasons under Events)`
      : src?.label || "Starting the capture…";
    const running = mode !== "idle";
    stopBtn.hidden = !running;
    const blockedWhy = MOCK ? "Mock mode (?mock=1): live capture is not simulated." : !state.serverOk ? "Server not reachable." :
      !health?.modules?.["live.capture"] ? "live.capture is not installed on the server." : null;
    [liveBtn, mockBtn].forEach((b) => { b.disabled = running || !!blockedWhy; });
    probeBtn.disabled = !!blockedWhy;
    urlIn.disabled = mode === "job";
    secSel.disabled = running;
    speedSeg.setDisabled(running);
    atlasIn.disabled = mode === "job" || !!MOCK;
    busyNote.textContent = blockedWhy || (mode === "job" ? "Capture running. Leaving this page for more than 60 s stops it (the server cancels captures nobody follows)." : mode === "app" ? "Following the mobile app. Stop to start a phyphox capture." : "");
    renderAtlasNote();
    renderTime();
  }
  function renderTime() {
    if (mode === "job" && phase !== "ended" && jobParams) {
      const el = (Date.now() - tStart) / 1000;
      timeLine.textContent = `${fmtDur(el)} of ${fmtDur(jobParams.seconds)} · ${buf.length} seconds summarised · job ${job?.id || ""}${jobParams.atlas ? " · Atlas on" : ""}`;
    } else if (mode === "app") timeLine.textContent = lastSample ? `last row ${fmtTimeLocal(lastSample.ts)} · ${buf.length} rows held here` : "waiting for rows";
    else timeLine.textContent = "";
  }
  function renderAtlasNote() {
    const onNow = mode === "job" ? !!jobParams?.atlas : atlasIn.checked;
    mountEl(atlasNote, onNow ? h("p", { class: "lv-atlason" }, icon("atlas", 16), h("span", null, "Rows go to Atlas ", h("code", null, "tubesense.ingest_live"), " (one per second, derived values, no audio). The Stream Processing card on the ",
      h("a", { href: "#/atlas", target: "_blank", rel: "noopener" }, "Atlas page"), " shows their windows when the processor runs; it is stopped now unless someone started it.", mode === "job" ? " Set at start." : ""))
      : h("p", { class: "help" }, "Atlas off: nothing leaves the laptop except to this page."));
  }
  function setMotion(stv) {
    motionBadge.dataset.state = stv || "none";
    motionBadge.textContent = stv === "moving" ? "MOVING" : stv === "stopped" ? "STOPPED" : stv === "unknown" ? "LEARNING" : "—";
    motionBadge.title = stv === "unknown" ? "The stop detector needs a few seconds of motion and stillness before it decides" : "";
  }
  let flashT = 0;
  function doFlash(text, kind) {
    clearTimeout(flashT);
    mountEl(flash, h("span", { class: ["lv-flash__in", `lv-flash--${kind}`] }, icon(kind === "sync" ? "check" : "dot", 16), text));
    flashT = setTimeout(() => clear(flash), kind === "sync" ? 5000 : 1800);
    announce(text);
  }
  function feedItem(kind, text, t) {
    feed.push(h("div", { class: ["lv-ev", `lv-ev--${kind}`] }, h("span", { class: "lv-ev__t mono" }, t !== undefined && t !== null ? `t ${fmtNum(t, 0)} s` : fmtTimeLocal()), h("span", null, text)));
  }
  function d10(d) {
    if (num(d.d10) !== null) return d.d10;
    if (num(d.p) === null) return null;
    const old = buf.filter((x) => num(x.p) !== null && x.t <= d.t - 10);
    const ref = old[old.length - 1];
    return ref && d.t - ref.t < 15 ? d.p - ref.p : null;
  }
  function addSample(d) {
    if (buf.length && d.t < buf[buf.length - 1].t - 5) buf = [];      // a new phyphox session restarted experiment time
    buf.push(d);
    if (buf.length > KEEP) buf.splice(0, buf.length - KEEP);
    d.dp = d10(d);
    lastSample = d;
    tick++;
    setMotion(d.state);
    ro.yaw.textContent = num(d.yaw) === null ? "—" : `${fmtNum(d.yaw, 1)} deg/s`;
    ro.vib.textContent = num(d.vib) === null ? "—" : `${fmtNum(d.vib, 3)} m/s²`;
    ro.p.textContent = num(d.p) === null ? "—" : `${fmtNum(d.p, 2)} hPa`;
    ro.dp.textContent = num(d.dp) === null ? "—" : `${d.dp >= 0 ? "+" : ""}${fmtNum(d.dp, 2)} hPa`;
    ro.b.textContent = num(d.b) === null ? "—" : `${fmtNum(d.b, 1)} µT`;
    ro.place.textContent = d.screen ? `screen ${d.screen}${num(d.tilt) !== null ? ` · tilt ${fmtNum(d.tilt, 0)}°` : ""}${d.handled ? " · handled" : ""}` : src?.kind === "app" ? "not sent by the app" : "—";
    ro.rates.textContent = d.rates ? Object.entries(d.rates).map(([k, v]) => `${k} ${v === null || v === undefined ? "—" : fmtNum(v, v < 10 ? 1 : 0)}`).join(" · ") + " Hz" : "—";
    if (d.knock && src?.kind === "app") doFlash("knock", "knock");
    yawTr.draw(buf, "yaw", d.t);
    vibTr.draw(buf, "vib", d.t);
    renderTime();
  }

  // ---------------------------------------------------------------- capture job events
  function onEvent(ev) {
    switch (ev.type) {
      case "hello":
        src = { kind: ev.source === "mock" ? "mock" : "live", label: ev.label };
        phase = "streaming";
        feedItem("info", `Connected: ${ev.label}. Sensors: ${Object.keys(ev.sensors || {}).join(", ") || "none"}.${ev.device?.deviceModel ? ` Device ${ev.device.deviceModel}.` : ""}`);
        renderStatus();
        break;
      case "sample": {
        if (phase !== "streaming") { phase = "streaming"; src ||= { kind: ev.source === "mock" ? "mock" : "live", label: ev.source === "mock" ? "MOCK" : "LIVE" }; renderStatus(); }
        addSample({ t: ev.t, yaw: ev.yaw_rate_dps, vib: ev.vib_rms, state: ev.state, knock: !!ev.knock, p: ev.pressure_hpa, b: ev.b_abs_ut,
          rates: ev.rates_hz, screen: ev.screen, tilt: ev.tilt_deg, handled: ev.handled, gap: ev.gap, ts: ev.ts });
        if (ev.knock) doFlash("knock", "knock");
        break;
      }
      case "knock":
        if (ev.sync_marker || ev.pattern === "double") doFlash("sync knock detected", "sync");
        feedItem(ev.sync_marker ? "sync" : "knock", `${ev.pattern} knock${ev.n > 1 ? ` (${ev.n}, gaps ${(ev.gaps_s || []).map((g) => fmtNum(g, 2)).join(", ")} s)` : ""}, peak ${fmtNum(ev.peak_ms2, 1)} m/s²${ev.sync_marker ? ": sync knock detected" : ""}`, ev.t);
        break;
      case "state":
        feedItem("state", `${String(ev.state).toUpperCase()}${ev.from && ev.from !== "unknown" ? ` (was ${ev.from})` : ""}${ev.dwell_s !== undefined ? `, stopped for ${ev.dwell_s} s` : ""}${num(ev.door_step_hpa) !== null ? `, door pressure step ${ev.door_step_hpa > 0 ? "+" : ""}${fmtNum(ev.door_step_hpa, 2)} hPa` : ""}; detected at t ${ev.detected_t} s`, ev.t);
        break;
      case "placement_change":
        feedItem("place", ev.initial ? `Placement settled: screen ${ev.screen}, tilt ${fmtNum(ev.tilt_deg, 0)}°` : `Placement changed: re-oriented ${fmtNum(ev.reorient_deg, 0)}°, now screen ${ev.screen}, tilt ${fmtNum(ev.tilt_deg, 0)}°`, ev.t);
        break;
      case "warning": case "error": case "info":
        if (ev.kind === "atlas") setAtlasStatus(ev.type === "info");
        if (ev.type === "error" && ev.kind === "connect" && phase !== "streaming") phase = "connecting";
        feedItem(ev.type, `${ev.message || ev.kind || ev.type}`, ev.t);
        renderStatus();
        break;
      case "bye":
        endNote = `Ended: ${ev.reason}. ${ev.n_samples} seconds summarised${jobParams?.atlas ? `, ${ev.n_inserted} rows inserted into Atlas` : ""}.`;
        feedItem("info", endNote);
        break;
      case "log":
        logBox.append(h("span", null, (ev.text || "") + "\n"));
        break;
      default: break;
    }
  }
  function attachJob(j) {
    stream?.close();
    job = j; jobParams = j.params || {}; mode = "job"; phase = "connecting"; endNote = "";
    src = jobParams.mock ? { kind: "mock", label: `MOCK: replaying recorded ${jobParams.mock}…` } : { kind: "live", label: `LIVE: phone at ${jobParams.url}` };
    tStart = Date.parse(j.created) || Date.now();
    buf = []; lastSample = null; feed.clear(); clear(logBox); setMotion(null);
    renderStatus();
    stream = openJobStream(j, (ev) => { if (ev.type !== "job_end") onEvent(ev); }, (end) => {
      mode = "idle"; phase = "ended";
      if (!endNote) endNote = end?.status === "cancelled" ? "Stopped (capture killed)." : end?.status === "lost" ? "Connection to the server lost." : `Ended (${end?.status || "?"}${end?.exit_code ? `, exit code ${end.exit_code}` : ""}).`;
      if (end?.status === "cancelled") feedItem("warning", "Capture stopped.");
      setMotion(null);
      renderStatus();
      announce(endNote);
    });
  }
  async function startJob(body) {
    stopApp(false);
    if (body.url !== undefined) store.set("live.url", body.url || DEFAULT_URL);
    const payload = { ...body, seconds: Number(secSel.value), atlas: atlasIn.checked };
    phase = "connecting"; src = body.mock ? { kind: "mock", label: `MOCK: starting a replay of ${body.mock}…` } : { kind: "live", label: `LIVE: connecting to ${body.url}…` };
    renderStatus();
    try {
      const j = await startWithReplace((replace) => postJSON("/api/live/start", { ...payload, replace }));
      if (!j) { phase = "stopped"; renderStatus(); return; }
      attachJob(j);
      announce("Capture started");
    } catch (e) {
      phase = "stopped"; renderStatus();
      mountEl(probeOut, errorCard({ title: "Could not start the capture", message: e.message }));
    }
  }
  async function stopAll() {
    if (mode === "app") { stopApp(true); return; }
    try { await postJSON("/api/live/stop", {}); } catch (e) { toast(e.message); }
  }

  // ---------------------------------------------------------------- probe
  async function doProbe() {
    const url = urlIn.value.trim() || DEFAULT_URL;
    store.set("live.url", url);
    probeBtn.disabled = true;
    mountEl(probeOut, h("p", { class: "help" }, `Asking ${url} … (up to a few seconds)`));
    let P;
    try { P = await fetchJSON(`/api/live/probe?url=${encodeURIComponent(url)}`); }
    catch (e) { mountEl(probeOut, errorCard({ title: "Probe refused", message: e.message })); probeBtn.disabled = false; return; }
    probeBtn.disabled = false;
    if (!P.ok) {
      mountEl(probeOut, h("div", { class: "lv-probe__res lv-probe__res--bad" },
        h("p", null, h("span", { class: "verdict verdict--bad" }, icon("cross", 14), h("span", null, "not reachable")), ` ${P.url}${P.laptop_ip ? ` · this laptop is ${P.laptop_ip}` : ""} · ${fmtNum(P.seconds, 1)} s`),
        h("ul", null, (P.advice || []).map((t) => h("li", null, t))),
        h("details", null, h("summary", null, "Exact error"), h("p", { class: "mono help" }, P.error))));
      return;
    }
    mountEl(probeOut, h("div", { class: "lv-probe__res lv-probe__res--ok" },
      h("p", null, h("span", { class: "verdict verdict--ok" }, icon("check", 14), h("span", null, P.mock ? "reachable (MOCK server)" : "phyphox reachable")),
        ` ${P.experiment ? `“${P.experiment}”` : ""}${P.device?.deviceModel ? ` · ${P.device.deviceModel}` : ""}${P.device?.version ? ` · phyphox ${P.device.version}` : ""} · ${P.measuring ? "measuring" : P.measuring === false ? "not measuring" : "status unknown"}`),
      h("p", { class: "mono help" }, `sensors: ${Object.keys(P.sensors || {}).join(", ") || "none"} · rates over 1 s: ${Object.entries(P.rates_hz || {}).map(([k, v]) => `${k} ${fmtNum(v, v < 10 ? 1 : 0)} Hz`).join(", ") || "n/a"}`),
      (P.warnings || []).length ? h("ul", { class: "warn" }, P.warnings.map((t) => h("li", null, t))) : null));
  }

  // ---------------------------------------------------------------- mobile app rows
  async function listApp() {
    if (MOCK || mode === "app") return;
    let A;
    try { A = await fetchJSON("/api/live/app"); } catch (e) { mountEl(appBody, emptyState(`App rows not available: ${e.message}`)); return; }
    const fresh = (A.sessions || []).filter((x) => x.age_s < 600);
    mountEl(appBody, fresh.length ? h("ul", { class: "plain lv-sessions" }, fresh.map((x) => h("li", null,
      h("span", null, h("span", { class: "mono" }, x.session), h("span", { class: "help" }, ` · ${x.n} rows · last ${fmtNum(x.age_s, 0)} s ago${x.client_local ? " · from this laptop (test)" : ""}`)),
      h("button", { class: "btn btn--small", type: "button", disabled: mode === "job", onclick: () => followApp(x.session) }, "Follow")))) :
      h("p", { class: "help" }, "No app has posted rows in the last 10 minutes. Start the app on the phone (laptop server started with --lan, phone and laptop on the same hotspot)."));
    appListTimer = setTimeout(listApp, 3000);
  }
  async function appLease() {
    clearTimeout(leaseTimer);
    if (mode !== "app") return;
    try { await postJSON("/api/live/app/atlas", { on: atlasIn.checked }); } catch (e) { toast(`Atlas toggle: ${e.message}`); }
    if (atlasIn.checked) leaseTimer = setTimeout(appLease, 10000);
  }
  async function pollApp() {
    if (mode !== "app") return;
    try {
      const R = await fetchJSON(`/api/live/app/rows?session=${encodeURIComponent(appSession)}&after=${appAfter}`);
      for (const r of R.rows || []) {
        appAfter = Math.max(appAfter, r.seq);
        addSample({ t: r.t, yaw: r.yaw_rate_dps, vib: r.vib_rms, state: r.state, knock: r.knock, p: r.pressure_hpa, d10: r.d_pressure_10s, b: r.b_abs_ut, rates: r.rates, ts: r.ts });
      }
      const stale = R.age_s > 5;
      if (stale !== (phase === "connecting")) { phase = stale ? "connecting" : "streaming"; renderStatus(); }
      if (R.atlas?.inserted) setAtlasStatus(true);
    } catch (e) {
      if (e.status === 404) { feedItem("warning", "The app session is gone from the server's memory."); stopApp(true); return; }
    }
    appTimer = setTimeout(pollApp, 1000);
  }
  function followApp(session) {
    clearTimeout(appListTimer);
    appSession = session; appAfter = 0; mode = "app"; phase = "connecting"; endNote = "";
    src = { kind: "app", label: `APP: Tubesense mobile (prototype) · session ${session}` };
    buf = []; lastSample = null; feed.clear(); setMotion(null);
    feedItem("info", `Following app session ${session} (rows the phone posts to this server).`);
    mountEl(appBody, h("p", null, "Following ", h("span", { class: "mono" }, session), ". ", h("button", { class: "linkbtn", type: "button", onclick: () => stopApp(true) }, "Stop following")));
    renderStatus(); appLease(); pollApp();
  }
  function stopApp(relist) {
    if (mode !== "app") return;
    clearTimeout(appTimer); clearTimeout(leaseTimer);
    if (atlasIn.checked) postJSON("/api/live/app/atlas", { on: false }).catch(() => {});
    mode = "idle"; phase = "ended"; endNote = `Stopped following app session ${appSession}.`;
    setMotion(null); renderStatus();
    if (relist) listApp();
  }

  // ---------------------------------------------------------------- boot
  const clock = setInterval(() => { if (mode !== "idle") renderTime(); }, 1000);
  const offHealth = on("health", () => { health = state.health || health; if (mode === "idle") renderStatus(); });
  disposers.push(() => { clearInterval(clock); offHealth(); stream?.close(); clearTimeout(appTimer); clearTimeout(appListTimer); clearTimeout(leaseTimer); clearTimeout(flashT);
    if (mode === "app" && atlasIn.checked) postJSON("/api/live/app/atlas", { on: false }).catch(() => {}); });
  renderStatus();
  yawTr.draw([], "yaw", 0); vibTr.draw([], "vib", 0);
  if (MOCK) mountEl(appBody, emptyState("Mock mode: app rows are not simulated."));
  else {
    try {
      const { jobs } = await fetchJSON("/api/jobs");
      const running = (jobs || []).find((j) => j.kind === "live" && LIVE_ST.includes(j.status));
      if (running) attachJob(running);
    } catch { /* server down: banner says so */ }
    listApp();
  }
  return () => disposers.forEach((d) => d());
}
