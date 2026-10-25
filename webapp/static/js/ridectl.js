// Ride session: one tracker replay, held outside any view so the demo can pre-warm it and the Ride screen can attach later.
// Sources: LIVE compute (job events over SSE), PRECOMPUTED (a stored JSONL replayed on a timer), or MOCK (fixture).
import { fetchJSON, postJSON, openJobStream, ApiError } from "./api.js";
import { emit } from "./state.js";

/** Replays stored ride events on a virtual clock: tick.t (ride seconds) divided by speed. Supports pause and speed changes. */
export class Replayer {
  constructor(events, { speed = 8, onEvent, onEnd, paused = false }) {
    this.speed = speed; this.onEvent = onEvent; this.onEnd = onEnd; this.paused = paused;
    this.q = []; let at = null;
    for (const ev of events) {
      if (ev.type === "tick") at = ev.t;
      else if (ev.type === "end" && at !== null) at += 0.01;
      this.q.push({ ev, at: ev.type === "tick" || (at !== null && ["departure", "warning", "end"].includes(ev.type)) ? at : null });
    }
    this.i = 0; this.vt = null; this.last = performance.now();
    this.timer = setInterval(() => this.step(), 30);
  }
  step() {
    const now = performance.now(), dt = (now - this.last) / 1000;
    this.last = now;
    if (!this.paused && this.vt !== null) this.vt += dt * this.speed;
    while (this.i < this.q.length) {
      const { ev, at } = this.q[this.i];
      if (at === null) { this.i++; this.onEvent(ev); continue; }        // setup and status events: immediately
      if (this.vt === null) this.vt = at;
      if (this.paused || at > this.vt) break;
      this.i++; this.onEvent(ev);
    }
    if (this.i >= this.q.length) { this.stop(); this.onEnd?.({ type: "job_end", status: "done", exit_code: 0 }); }
  }
  setSpeed(n) { this.speed = n; }
  pause() { this.paused = true; }
  resume() { this.paused = false; this.last = performance.now(); }
  stop() { clearInterval(this.timer); this.timer = null; }
}

const PHASE_TEXT = { loading: "Loading run", map: "Building leave-one-out map", features: "Computing features", departure: "Finding departure", ready: "Ready" };

class RideSession {
  constructor() { this.subs = new Set(); this.stream = null; this.replayer = null; this.reset(); }
  reset() {
    this.stop(false);
    Object.assign(this, { phase: "idle", job: null, params: null, source: null, generated: null, init: null, ticks: [], paints: [], last: null,
      departure: null, summary: null, error: null, warnings: [], logs: [], statusText: "", statusAt: 0, lost: false, paused: false, speed: 8, jobEnd: null });
    this._emit("reset");
  }
  subscribe(fn) { this.subs.add(fn); return () => this.subs.delete(fn); }
  _emit(kind, ev) { for (const f of [...this.subs]) { try { f(kind, ev, this); } catch (e) { console.error(e); } } emit("ride", kind); }
  get busy() { return ["starting", "loading", "holding", "running", "paused"].includes(this.phase); }

  async start(params) {
    this.reset();
    this.params = { ...params }; this.speed = params.speed || 8; this.source = "live"; this.phase = "starting"; this.statusAt = Date.now();
    this.statusText = "Starting the tracker process";
    this._emit("phase");
    try {
      const job = await postJSON("/api/ride/start", params);
      this.job = job; this._follow(job);
      return job;
    } catch (e) { this.phase = "idle"; this._emit("phase"); throw e; }
  }
  /** Re-attach to a job that already runs on the server (e.g. after a page reload). */
  attach(job) {
    this.reset(); this.job = job; this.params = { ...(job.params || {}) }; this.speed = job.params?.speed || 8;
    this.source = "live"; this.phase = "loading"; this.statusText = "Re-attaching to the held tracker"; this.statusAt = Date.now(); this._follow(job);
  }
  /** Start a tracker, or take over one the server still holds: after a reload (or a second demo page) the earlier page's held
   *  tracker is still there. Same ride: re-attach. Different ride that nobody follows any more: replace it. Anything else: the 409. */
  async startOrAdopt(params) {
    try { return await this.start({ ...params, replace: false }); }
    catch (e) {
      const run = e.status === 409 && e.error === "busy" ? e.body?.running : null;
      if (!run || run.kind !== "ride" || run.status !== "holding") throw e;
      const p = run.params || {};
      const same = ["run", "track", "tracker"].every((k) => p[k] === params[k]) && (p.synthetic_stop ?? null) === (params.synthetic_stop ?? null)
        && (params.synthetic_stop == null || p.stop_variant === params.stop_variant);
      if (same) {
        this.attach(run);
        if (p.speed !== params.speed) { try { await this.setSpeed(params.speed); } catch { /* keeps the held job's speed */ } }
        return run;
      }
      if (!run.subscribers) return this.start({ ...params, replace: true });
      throw e;
    }
  }
  _follow(job) { this.stream = openJobStream(job, (ev) => this.handle(ev), (end) => this._ended(end)); }

  async playPrecomputed(item, speed = 8, hold = false) {
    this.reset();
    this.source = "precomputed"; this.generated = item.generated; this.speed = speed; this.phase = "loading"; this.statusText = "Loading PRECOMPUTED stream";
    this._emit("phase");
    const res = await fetch(`/api/ride/precomputed?file=${encodeURIComponent(item.file)}`, { cache: "no-store" });
    if (!res.ok) throw new ApiError(res.status, "http_error", `Could not load ${item.file}`);
    const events = (await res.text()).split("\n").filter(Boolean).map((l) => JSON.parse(l));
    this._replay(events, speed, hold);
  }
  playEvents(events, speed, source, hold = false) { this.reset(); this.source = source; this.speed = speed; this._replay(events, speed, hold); }
  _replay(events, speed, hold) {
    this.replayer = new Replayer(events, { speed, paused: hold, onEvent: (ev) => this.handle(ev), onEnd: (e) => this._ended(e) });
    this.heldReplay = hold;
  }

  async go() {
    if (this.replayer) { this.replayer.resume(); this.heldReplay = false; if (this.phase === "holding") this.phase = "running"; this._emit("phase"); return; }
    if (!this.job) return;
    await postJSON(`/api/jobs/${this.job.id}/go`, {});
    if (this.phase === "holding") { this.phase = "running"; this._emit("phase"); }
  }
  async setSpeed(n) {
    this.speed = n;
    if (this.replayer) this.replayer.setSpeed(n);
    else if (this.job && this.busy) await postJSON(`/api/jobs/${this.job.id}/control`, { speed: n });
    this._emit("speed");
  }
  async pause() {
    if (this.phase !== "running") return;
    if (this.replayer) this.replayer.pause(); else await postJSON(`/api/jobs/${this.job.id}/control`, { action: "pause" });
    this.paused = true; this.phase = "paused"; this._emit("phase");
  }
  async resume() {
    if (this.phase !== "paused") return;
    if (this.replayer) this.replayer.resume(); else await postJSON(`/api/jobs/${this.job.id}/control`, { action: "resume" });
    this.paused = false; this.phase = "running"; this._emit("phase");
  }
  async toggle() { if (this.phase === "running") await this.pause(); else if (this.phase === "paused") await this.resume(); else if (this.phase === "holding") await this.go(); }

  async stop(notify = true) {
    this.stream?.close(); this.stream = null;
    this.replayer?.stop(); this.replayer = null;
    const job = this.job;
    if (job && this.busy) { try { await postJSON(`/api/jobs/${job.id}/cancel`, {}); } catch { /* already gone */ } }
    if (notify) { if (this.busy) this.phase = "idle"; this._emit("phase"); }
  }
  reconnect() { if (this.stream?.reconnect) { this.lost = false; this.stream.reconnect(); this._emit("phase"); } }

  handle(ev) {
    switch (ev.type) {
      case "status": this.phase = this.phase === "idle" || this.phase === "starting" ? "loading" : this.phase;
        this.statusText = PHASE_TEXT[ev.phase] || ev.message || ev.phase; this.statusAt = Date.now(); this._emit("status", ev); break;
      case "warning": this.warnings.push(ev.message); this._emit("warning", ev); break;
      case "meta": this.meta = ev; break;
      case "init": this.init = ev; this.ticks = []; this.paints = []; this._emit("init", ev); break;
      case "holding": this.phase = "holding"; this._emit("phase", ev); break;
      case "departure": this.departure = ev; this._emit("departure", ev); break;
      case "tick": {
        if (this.phase === "holding" && this.source === "live") this.phase = "running";
        if (this.phase === "loading" || this.phase === "starting" || (this.lost && this.phase === "idle")) { this.phase = "running"; this.lost = false; }
        this.last = ev; this.ticks.push(ev); if (ev.paint) this.paints.push({ ...ev.paint, confident: ev.confident });
        this._emit("tick", ev); break;
      }
      case "end": this.summary = ev.summary; this.phase = "ended"; this._emit("end", ev); break;
      case "error": this.error = ev.message; this.phase = "error"; this._emit("error", ev); break;
      case "log": if (this.logs.length < 200) this.logs.push(ev); this._emit("log", ev); break;
      case "job_end": this.jobEnd = ev; break;
      default: break;
    }
  }
  _ended(end) {
    if (end?.status === "lost") { this.lost = true; if (this.busy) this.phase = "idle"; this._emit("phase"); return; }
    if (this.phase === "running" || this.phase === "paused" || this.phase === "loading" || this.phase === "holding" || this.phase === "starting") {
      this.phase = end?.status === "cancelled" ? "idle" : end?.status === "error" ? "error" : this.phase === "running" ? "ended" : "idle";
    }
    this._emit("phase");
  }
}

export const rideSession = new RideSession();

export async function listPrecomputed() {
  try { return (await fetchJSON("/api/ride/precomputed")).items || []; } catch { return []; }
}
