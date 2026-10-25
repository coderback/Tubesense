// Mock mode (?mock=1): serves static/mock fixtures instead of the backend so every screen can be developed and checked offline.
// Fixtures are real responses captured from the backend on 25 Oct and replayed here; the MOCK DATA banner is on every page.
import { ApiError } from "./api.js";
import { Replayer } from "./ridectl.js";
import { eventsFromResult } from "./askctl.js";

const cache = new Map();
async function fixture(name) {
  if (!cache.has(name)) {
    const p = fetch(`mock/${name}`, { cache: "no-store" }).then((r) => { if (!r.ok) throw new ApiError(404, "not_found", `mock fixture ${name} missing`); return r.text(); });
    cache.set(name, p); p.catch(() => cache.delete(name));
  }
  return cache.get(name);
}
const json = async (name) => JSON.parse(await fixture(name));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const jobs = new Map(), ctl = new Map();
let seq = 0;

export async function get(path) {
  await sleep(60);
  const u = new URL(path, location.origin), p = u.pathname;
  if (p === "/api/health") return json("health.json");
  if (p === "/api/rides") return json("rides.json");
  if (p === "/api/pack") return json("pack.json");
  if (p === "/api/pack/history") return json("pack_history.json");
  if (p === "/api/canary") return { ...(await json("canary.json")), checked_at: new Date().toISOString() };
  if (p === "/api/analyst/session") return json("analyst_session.json");
  if (p === "/api/atlas/search") { const a = await json("atlas_search.json"); return u.searchParams.get("q") && u.searchParams.get("q") !== a.search.question ? { ...a, question_mismatch: true } : a; }
  if (p === "/api/stream/status") return json("stream_status.json");
  if (p === "/api/compare") return json("compare.json");
  if (p === "/api/method") return json("method.json");
  if (p === "/api/egress") return json("egress.json");
  if (p === "/api/ask/status") return json("ask_status.json");
  if (p === "/api/ask/cached") return json("ask_cached.json");
  if (p.startsWith("/api/ask/cached/")) return json("ask_result.json");
  if (p === "/api/ride/precomputed") return { items: [] };
  if (p.startsWith("/api/source/")) {
    const id = decodeURIComponent(p.slice("/api/source/".length)), s = (await json("sources.json"))[id];
    if (!s) throw new ApiError(404, "not_found", `unknown chunk ${id}`);
    const q = u.searchParams.get("quote");
    return q ? { ...s, quote_found: s.text.toLowerCase().replace(/\s+/g, " ").includes(q.toLowerCase().replace(/\s+/g, " ")) } : s;
  }
  if (p === "/api/jobs") return { jobs: [...jobs.values()] };
  const m = /^\/api\/jobs\/([\w]+)$/.exec(p);
  if (m && jobs.has(m[1])) return jobs.get(m[1]);
  throw new ApiError(404, "not_found", `mock mode has no ${p}`);
}

export async function text(path) { return fixture(path.replace(/^\/?/, "")); }

function newJob(kind, params, label) {
  const id = `jmock${++seq}`;
  const job = { id, kind, status: "starting", params, label, created: new Date().toISOString(), started: new Date().toISOString(), ended: null, n_events: 0, events_url: `mock:${kind}:${id}`, exit_code: null, error: null };
  jobs.set(id, job); ctl.set(id, { go: null, speed: params.speed || 8, replayer: null });
  return job;
}

export async function post(path, body) {
  await sleep(80);
  if (path === "/api/ride/start") {
    for (const j of jobs.values()) if (j.kind === "ride" && ["starting", "holding", "running"].includes(j.status)) {
      if (body.replace) { j.status = "cancelled"; ctl.get(j.id)?.replayer?.stop(); } else throw new ApiError(409, "busy", "A ride job is running", { running: j });
    }
    return newJob("ride", body, `${body.run} ${body.track}, tracker ${body.tracker}, ${body.speed}x`);
  }
  if (path === "/api/ask/start") return newJob("ask", body, `ask: ${body.question.slice(0, 40)}`);
  let m = /^\/api\/jobs\/(\w+)\/(go|control|cancel)$/.exec(path);
  if (m) {
    const [, id, act] = m, j = jobs.get(id), c = ctl.get(id);
    if (!j) throw new ApiError(404, "not_found", "unknown job");
    if (act === "go") { if (j.status !== "holding") throw new ApiError(409, "busy", "job is not holding"); j.status = "running"; c.replayer?.resume(); }
    if (act === "control") { if (body.speed) c.replayer?.setSpeed(body.speed); if (body.action === "pause") c.replayer?.pause(); if (body.action === "resume") c.replayer?.resume(); }
    if (act === "cancel") { j.status = "cancelled"; c.replayer?.stop(); c.end?.({ type: "job_end", status: "cancelled" }); }
    return j;
  }
  if (path === "/api/analyst/live/start") return newJob("analyst", body, "mock live analyst");
  if (path === "/api/stream/start") throw new ApiError(503, "unavailable", "Mock mode: streaming is not simulated.");
  if (/\/api\/pack\/(approve|reject)$/.test(path)) throw new ApiError(403, "forbidden", "Mock mode: nothing is written. Approval is disabled.");
  throw new ApiError(404, "not_found", `mock mode has no ${path}`);
}

/** SSE stand-in. Returns a stop function. */
export function stream(job, onEvent, onEnd) {
  const c = ctl.get(job.id), live = jobs.get(job.id);
  let stopped = false;
  const end = (e) => { if (stopped) return; stopped = true; c.replayer?.stop(); if (live.status !== "cancelled") live.status = "done"; onEvent(e); onEnd?.(e); };
  c.end = end;
  if (job.kind === "ride") {
    fixture("ride_R9.jsonl").then((t) => {
      let ev = t.split("\n").filter(Boolean).map((l) => JSON.parse(l)).filter((e) => e.type !== "meta");
      const hold = !!job.params.hold, i = ev.findIndex((e) => e.type === "init");
      if (job.params.run !== "R9") ev[i] = { ...ev[i], sample_note: `MOCK: this fixture is always R9 (requested ${job.params.run}).` };
      if (hold) ev.splice(i + 1, 0, { type: "holding" });
      c.replayer = new Replayer(ev, { speed: c.speed, paused: hold, onEvent: (e) => { if (e.type === "holding") live.status = "holding"; else if (e.type === "tick" && live.status === "holding") live.status = "running"; onEvent(e); }, onEnd: () => end({ type: "job_end", status: "done", exit_code: 0 }) });
      if (!hold) live.status = "running";
    }).catch((e) => { onEvent({ type: "error", message: e.message }); end({ type: "job_end", status: "error" }); });
  } else if (job.kind === "ask") {
    json("ask_result.json").then(async (res) => {
      live.status = "running";
      for (const e of eventsFromResult(res)) { if (stopped) return; await sleep(e.type === "final" ? 500 : 900); onEvent(e); }
      end({ type: "job_end", status: "done", exit_code: 0 });
    });
  }
  if (job.kind === "analyst") {
    json("analyst_session.json").then(async (S) => {
      live.status = "running";
      const runs = S.events.filter((e) => e.kind === "tool" && e.name === "run_experiment" && e.metrics).slice(0, job.params.max_experiments || 1);
      const evs = [{ type: "start", ts: new Date().toISOString(), model: "gemini-4-argon", thinking: "medium", max_experiments: runs.length, label: "MOCK: replayed from the recorded session, no Gemini call", log: "outputs_posthoc/attempts_live.jsonl (mock)" },
        { type: "argon_text", text: "MOCK TEXT (replayed from the recorded session): the earlier results suggest testing one untried setting.", flags: [] }];
      for (const e of runs) {
        evs.push({ type: "tool_call", name: "run_experiment", args: { ...e.args, rationale: e.rationale, refuted_if: "coverage_ok is false" } },
          { type: "tool_result", name: "run_experiment", result: { attempt: e.attempt, metrics: e.metrics, vs_baseline: e.vs } },
          { type: "verdict", attempt: e.attempt, accepted: !!e.verdict?.accepted, reason: e.verdict?.reason, config: e.args, seconds: 45, changes: Object.fromEntries((e.change || "").split(/,\s*/).map((p) => p.split(/\s+/)).filter((p) => p[1]).map(([k, v]) => [k, v.split("->")])), metrics: { ...e.metrics, vs_baseline: e.vs } });
      }
      evs.push({ type: "chain", ok: true, lines: S.chain.length, file: "outputs_posthoc/attempts_live.jsonl (mock)", main_chain_unchanged: true }, { type: "final", summary: "MOCK summary: nothing was run live.", experiments: [], tokens: { prompt: 0, output: 0 }, files: {} });
      for (const e of evs) { if (stopped) return; await sleep(900); onEvent(e); }
      end({ type: "job_end", status: "done", exit_code: 0 });
    });
  }
  return () => { stopped = true; c.replayer?.stop(); };
}
