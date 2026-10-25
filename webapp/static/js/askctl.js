// Ask Argon run state, shared by the Ask screen and the demo (which can start a question in the background).
// A run is built from agent.ask events: start, retrieval, draft, verdicts, review, final_review, final, error. Cached answers come
// from GET /api/ask/cached/<name>, which is agent.ask.replay(): re-verified and re-gated now, labelled CACHED with the original ts.
// They are turned into the same events (eventsFromResult), so one renderer serves live and cached runs.
import { fetchJSON, postJSON, openJobStream } from "./api.js";
import { setAtlasStatus } from "./state.js";

export function eventsFromResult(res) {
  const ev = [{ type: "start", question: res.question, model: res.model, thinking: res.thinking, ts: res.ts, cached: true,
    label: res.label || `CACHED answer from ${res.ts} (not a live Argon call)`, exact_match: res.exact_match, asked_question: res.asked_question }];
  const r = res.retrieval || {};
  ev.push({ type: "retrieval", source: r.source, reason: r.reason, seconds: r.seconds, hits: r.hits || [], stats: r.stats || [], cached: true });
  const rev = res.review, rev2 = res.final_review;
  for (const rd of res.rounds || []) {
    ev.push({ type: "draft", round: rd.round, trigger: rd.trigger, claims: rd.claims, answer: rd.answer, answer_claim_ids: rd.answer_claim_ids, seconds: rd.seconds, cached: true });
    ev.push({ type: "verdicts", round: rd.round, results: rd.verdicts, answer_check: rd.answer_check, cached: true });
    if (rev && rev.reviewed_round === rd.round) ev.push({ type: "review", ...rev, cached: true });
  }
  if (rev2) ev.push({ type: "final_review", final_check: true, ...rev2, cached: true });
  ev.push({ type: "final", incomplete: res.incomplete, result: { ...res, cached: true } });
  return ev;
}

export class AskRun {
  constructor(question, { cached = false } = {}) {
    this.question = question; this.cachedMode = cached; this.events = []; this.status = "starting"; this.start = null; this.retrieval = null;
    this.rounds = []; this.review = null; this.finalReview = null; this.result = null; this.error = null; this.t0 = Date.now(); this.t1 = null; this.job = null; this.stream = null; this.subs = new Set();
  }
  subscribe(fn) { this.subs.add(fn); return () => this.subs.delete(fn); }
  notify() { this.subs.forEach((f) => { try { f(this); } catch (e) { console.error(e); } }); }
  apply(ev) {
    this.events.push(ev);
    if (ev.type === "start") { this.start = ev; this.status = "running"; }
    else if (ev.type === "retrieval") {
      this.retrieval = ev;
      if (!ev.cached) setAtlasStatus(!/local fallback/i.test(ev.source || ""), false);   // a live ask's retrieval is an Atlas call
    }
    else if (ev.type === "draft") this.rounds[ev.round] = { ...(this.rounds[ev.round] || {}), draft: ev };
    else if (ev.type === "verdicts") this.rounds[ev.round] = { ...(this.rounds[ev.round] || {}), verdicts: ev };
    else if (ev.type === "review") this.review = ev;
    else if (ev.type === "final_review") this.finalReview = ev;
    else if (ev.type === "final") { this.result = ev.result; this.status = "done"; this.t1 = Date.now(); }
    else if (ev.type === "error") { this.error = ev.message; this.status = "error"; this.t1 = Date.now(); }
    this.notify();
  }
  get cached() { return this.cachedMode || this.start?.cached === true || this.result?.cached === true; }
  get busy() { return this.status === "starting" || this.status === "running"; }
  get waiting() {
    if (!this.busy) return null;
    if (!this.retrieval) return "retrieval (Atlas hybrid search)";
    const last = this.rounds[this.rounds.length - 1];
    if (!last) return "Argon's first draft";
    if (last.draft && !last.verdicts) return "the verifier";
    if (this.review && !this.finalReview && this.review.findings?.length) return "Argon's revision after the framing review";
    return "Argon's next turn (fix round, framing review or final answer)";
  }
  cancel() {
    this.stream?.close();
    if (this.job && this.busy) postJSON(`/api/jobs/${this.job.id}/cancel`, {}).catch(() => {});
    if (this.busy) { this.status = "error"; this.error = "Cancelled."; this.t1 = Date.now(); this.notify(); }
  }
}

export const askStore = { current: null };

export async function startAsk(question) {
  const run = new AskRun(question);
  askStore.current = run;
  try {
    follow(run, await postJSON("/api/ask/start", { question, cached: false }));
  } catch (e) {
    run.apply({ type: "error", message: e.message, status: e.status, code: e.error });
    run.errorCode = e.error; run.errorStatus = e.status;
  }
  return run;
}

function follow(run, job) {
  run.job = job;
  run.stream = openJobStream(job, (ev) => { if (ev.type !== "log" && ev.type !== "job_end") run.apply(ev); }, (end) => {
    if (run.busy) { run.status = "error"; run.error = end?.status === "lost" ? "Connection lost to the event stream." : "The ask process ended without an answer."; run.t1 = Date.now(); run.notify(); }
  });
}

/** A live ask already running on the server (started before a reload or from another device): follow it instead of paying for a second one. */
export async function adoptRunningAsk(question) {
  const jobs = (await fetchJSON("/api/jobs")).jobs || [];
  const job = jobs.find((j) => j.kind === "ask" && ["starting", "running"].includes(j.status) && !j.params?.cached
    && (!question || j.params?.question === question));
  if (!job) return null;
  const run = new AskRun(job.params?.question || question || "");
  run.t0 = Date.parse(job.created) || Date.now();
  run.adopted = true;
  askStore.current = run;
  follow(run, job);      // the server replays the buffered events from the start
  return run;
}

export async function loadCachedRun(name, { remember = true } = {}) {
  const res = await fetchJSON(`/api/ask/cached/${encodeURIComponent(name)}`);
  const run = new AskRun(res.question, { cached: true });
  eventsFromResult(res).forEach((e) => run.apply(e));
  run.cachedName = name;
  if (remember) askStore.current = run;
  return run;
}
