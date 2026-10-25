// Agent: the recorded Argon analyst session replayed in compressed time (integrity checks recomputed now),
// and a short live session (agent.live_analyst) whose events stream in over SSE.
import { h, mount as mountEl, clear, fmtNum, fmtSigned, fmtUTC, fmtTimeLocal, fmtDur, fmtClock, fixArrow, announce, icon } from "../util.js";
import { MOCK, postJSON, openJobStream } from "../api.js";
import { state, getAnalyst, getHealth, saveDemoSettings } from "../state.js";
import { card, chip, chipRow, provChip, screenHeader, whereFrom, verdict, hashChain, eventFeed, quoteFrame, errorCard, emptyState, skeleton, segmented, tabs, banner, startWithReplace, toast } from "../ui.js";
import { markdown } from "../md.js";

const CAVEAT_FALLBACK = "Caveat: a small gain - error CI upper bound -0.05 m (only just below 0), 1 of 6 configurations accepted, 8 dev runs from one morning, errors vs a yaw curve-edge proxy; see the single sealed test above.";
const ci = (c) => (c ? `mean ${fmtSigned(c[1], 2)} m, 95 % CI [${fmtSigned(c[0], 2)}, ${fmtSigned(c[2], 2)}] m` : "n/a");

function diffChips(change) {
  if (!change) return null;
  return h("div", { class: "chip-row" }, String(change).split(/,\s*/).map((p) => {
    const m = /^(\S+)\s+(.+?)->(.+)$/.exec(p.trim());
    return h("span", { class: "chip chip--diff mono" }, m ? `${m[1]} ${m[2]}→${m[3]}` : p);
  }));
}
function configChips(cfg) { return h("div", { class: "chip-row" }, Object.entries(cfg || {}).map(([k, v]) => h("span", { class: "chip chip--diff mono" }, `${k} ${v}`))); }

function metricsRow(m, vs, base) {
  if (!m) return null;
  const wc = vs?.width_change;
  const cells = [
    ["median error", m.median_error_m !== undefined ? `${fmtNum(m.median_error_m, 2)} m` : null],
    ["90 % coverage", m.coverage_mean !== undefined ? fmtNum(m.coverage_mean, 3) : null],
    ["band width", m.band_width_mean_m !== undefined ? `${fmtNum(m.band_width_mean_m, 1)} m${wc !== undefined && wc !== null ? ` (${fmtSigned(wc * 100, 0)} %)` : ""}` : null],
  ].filter(([, v]) => v !== null);
  return h("dl", { class: "metrics" }, cells.flatMap(([k, v]) => [h("div", null, h("dt", null, k), h("dd", { class: "mono" }, v))]));
}

function clampText(text, lines = 4) {
  const p = h("p", { class: "clamp", style: { "--lines": lines } }, text);
  const more = h("button", { class: "linkbtn", type: "button", onclick: () => { const open = p.classList.toggle("clamp--open"); more.textContent = open ? "less" : "more"; more.setAttribute("aria-expanded", String(open)); } }, "more");
  more.setAttribute("aria-expanded", "false");
  return h("div", null, p, text.length > 220 ? more : null);
}

function experimentCard(e, session) {
  if (e.refused) {
    return h("article", { class: "exp exp--refused" }, h("header", { class: "exp__head" }, h("strong", null, `Experiment ${e.n ?? ""}`), verdict("REJECTED", "refused by the harness")),
      h("p", null, e.refused), e.change ? diffChips(e.change) : null);
  }
  const v = e.verdict || {};
  return h("article", { class: ["exp", v.accepted ? "exp--ok" : "exp--no"] },
    h("header", { class: "exp__head" }, h("strong", null, `Experiment ${e.n}`), h("span", { class: "muted mono" }, `attempt ${e.attempt} · ${(e.hash || "").slice(0, 12)}`),
      v.accepted ? verdict("ACCEPTED") : verdict("REJECTED")),
    e.rationale ? clampText(e.rationale) : null,
    diffChips(e.change),
    metricsRow(e.metrics, e.vs, session.baseline?.metrics),
    e.vs?.error_delta_ci_m ? h("p", { class: "exp__ci" }, `paired per-run error delta: ${ci(e.vs.error_delta_ci_m)} (the rule uses this, not the median)`) : null,
    h("p", { class: "exp__reason" }, h("strong", null, v.accepted ? "Why accepted: " : "Why rejected: "), v.reason || ""),
    v.recheck_same === true ? h("p", { class: "exp__recheck ok" }, icon("check", 14), "recomputed now: accept() on the logged metrics → same verdict")
      : v.recheck_same === false ? h("p", { class: "exp__recheck bad" }, verdict("DIFFERENT VERDICT"), " recomputed now, the verdict differs from the log") : null);
}

// ---------------------------------------------------------------- recorded session
function integrityCard(S) {
  const i = S.integrity || {}, at = fmtTimeLocal(S.checked_at);
  const row = (ok, label) => h("li", { class: ok ? "ok" : "bad" }, verdict(ok ? "MATCH" : "BROKEN", ok ? "ok" : "FAILED"), h("span", null, label), h("span", { class: "muted mono" }, `checked now at ${at}`));
  const mirror = S.atlas_mirror;
  const rows = [
    h("li", { class: i.chain_ok ? "ok" : "bad" }, verdict(i.chain_ok ? "INTACT" : "BROKEN", i.chain_ok ? "INTACT" : `BROKEN at ${JSON.stringify(i.broken)}`),
      h("span", null, `hash chain ${i.chain_ok ? "INTACT" : "BROKEN"}, ${i.lines} lines`), h("span", { class: "muted mono" }, `checked now at ${at}`)),
    row(i.sha_ok, "harness SHA-256 on disk = every log line"),
    row(i.tag_sha_ok, `= harness at tag ${i.tag}${i.tag_commit ? ` (${i.tag_commit.slice(0, 7)})` : ""}`),
    i.final_hash_ok !== undefined ? row(i.final_hash_ok, "submitted config hash matches the log") : null,
    i.final_at_tag_ok !== undefined ? row(i.final_at_tag_ok, "submitted config matches the tagged commit") : null,
    mirror ? h("li", { class: /^\d+\/\d+/.test(mirror) && mirror.split("/")[0] === mirror.split("/")[1].split(" ")[0] ? "ok" : "warn" },
      verdict(/^(\d+)\/\1\b/.test(mirror) ? "MATCH" : "OPEN", /^(\d+)\/\1\b/.test(mirror) ? "ok" : "check"), h("span", null, `Atlas mirror: ${mirror}`), h("span", { class: "muted mono" }, `checked now at ${at}`)) : null,
  ];
  return card({ title: "Integrity checks", chips: chip("live", "LIVE checks"), cls: "integrity", body: h("ul", { class: "checks" }, rows) });
}

function sealedCard(S) {
  const s = S.sealed; if (!s) return null;
  const v2 = s.v2 || {}, v1 = s.v1 || {};
  return card({ title: "Sealed test", chips: [verdict("MATCHED, NOT BEATEN"), chip("posthoc")], cls: "sealed",
    body: [
      h("div", { class: "table-wrap" }, h("table", null,
        h("thead", null, h("tr", null, ["", "median error", "90 % coverage", "sections"].map((c) => h("th", null, c)))),
        h("tbody", null,
          h("tr", null, h("td", null, `v2-posthoc sealed test (attempt ${v2.attempt}, ${fmtUTC(v2.ts)})`), h("td", { class: "mono" }, `${fmtNum(v2.median_error_m, 1)} m`), h("td", { class: "mono" }, fmtNum(v2.coverage_mean, 3)), h("td", { class: "mono" }, `n = ${v2.n_runs}`)),
          h("tr", null, h("td", null, "v1 pre-registered"), h("td", { class: "mono" }, `${fmtNum(v1.median_m, 1)} m`), h("td", { class: "mono" }, fmtNum(v1.coverage, 3)), h("td", { class: "mono" }, `n = ${v1.n_sections}`))))),
      h("p", null, "The dev gain did not carry over; the log shows there was one try."),
      h("ul", { class: "plain mono" }, (s.rows || []).map((r) => h("li", null, r)))] });
}

async function recordedTab(host, ctx, disposers) {
  mountEl(host, card({ body: skeleton(8) }));
  let S;
  try { S = await getAnalyst(1); }
  catch (e) { mountEl(host, errorCard({ message: e.message, title: "Recorded session not available", retry: () => recordedTab(host, ctx, disposers) })); return; }
  const settings = state.demo.settings;
  const events = S.events || [];
  const rootEl = h("div", { class: "stack" });
  mountEl(host, rootEl);
  const chainCard = hashChain([], null), chainPending = [...(S.chain || [])];
  const feed = eventFeed({ height: ctx.demo ? "min(56vh, 620px)" : "min(62vh, 720px)" });
  const tailHost = h("div", { class: "stack" });
  const ctrl = h("div", { class: "row playback" });
  let replay_s = settings.replay_s || 30, idx = 0, timer = 0, paused = false, mode = "auto", gapEl = null, done = false;
  const t0 = events.length ? Date.parse(events[0].ts) : 0, tN = events.length ? Date.parse(events[events.length - 1].ts) : 1;
  const scale = () => (replay_s * 1000) / Math.max(1, tN - t0);

  const revealChain = (upTo, includeTest) => {
    while (chainPending.length && (includeTest || chainPending[0].kind !== "test") && (upTo === Infinity || chainPending[0].attempt <= upTo)) chainCard.add(chainPending.shift());
    if (!chainPending.length || (!includeTest && chainPending.every((c) => c.kind === "test"))) chainCard.setOk(S.integrity?.chain_ok === true);
  };
  const addEvent = (e) => {
    gapEl?.remove(); gapEl = null;
    if (e.kind === "start") feed.push(h("div", { class: "feed__line" }, icon("play", 14), `Session started, up to ${e.max_experiments} experiments (${fmtTimeLocalUTC(e.ts)})`));
    else if (e.kind === "tool" && e.name === "run_experiment") { feed.push(h("div", { class: "feed__line" }, icon("agent", 14), `Argon called run_experiment(${e.change || ""})`)); feed.push(experimentCard(e, S)); revealChain(e.attempt ?? -1, false); announce(`Experiment ${e.n}: ${e.verdict?.accepted ? "accepted" : "rejected"}`); }
    else if (e.kind === "tool" && e.name === "submit_final") {
      feed.push(h("div", { class: "feed__line" }, icon("agent", 14), "Argon called submit_final()"));
      feed.push(h("article", { class: "exp exp--submit" }, h("header", { class: "exp__head" }, h("strong", null, "Submitted configuration"), chip("cached", `config ${e.config_hash}`)),
        e.change ? diffChips(e.change) : null, e.rationale ? clampText(e.rationale) : null,
        h("p", { class: "muted" }, `git tag check: harness at tag ${S.integrity?.tag} ${S.integrity?.tag_sha_ok ? "matches" : "DOES NOT match"} the harness on disk.`)));
      revealChain(Infinity, false);
    } else if (e.kind === "tool") feed.push(h("div", { class: "feed__line" }, icon("agent", 14), `Argon called ${e.name}()`));
    else if (e.kind === "report") { feed.push(h("div", { class: "feed__line" }, icon("check", 14), `Argon wrote its report (${fmtDur(e.seconds)} session)`)); }
  };
  const fmtTimeLocalUTC = (iso) => fmtUTC(iso, false);

  function finishTail() {
    if (done) return; done = true;
    revealChain(Infinity, true);
    const report = events.find((e) => e.kind === "report");
    const roughHost = h("div");
    if (ctx.demo) {   // demo beat 4 sub-card: a second Argon job whose checks caught its mistakes (PRECOMPUTED, PENDING HUMAN CHECK)
      Promise.all([import("./work.js"), import("../api.js").then((m) => m.fetchJSON("/api/work/roughness"))])
        .then(([W, R]) => { const c = W.roughnessMini(R); if (c) mountEl(roughHost, c); }).catch(() => {});
    }
    mountEl(tailHost, sealedCard(S), roughHost,
      S.failed_session ? h("div", { class: "banner banner--amber banner--inline" }, icon("warn", 16), h("span", null, h("strong", null, "Earlier failed session: "), S.failed_session)) : null,
      report ? quoteFrame("Written by gemini-4-argon, verbatim from analyst_session.jsonl. Its words, not ours.", markdown(report.text)) : null,
      h("p", { class: "caveat" }, S.caveat || CAVEAT_FALLBACK));
  }
  function showNext() {
    if (idx >= events.length) { finishTail(); updateCtrl(); return; }
    addEvent(events[idx]); idx++;
    if (idx >= events.length) { finishTail(); updateCtrl(); return; }
    if (mode === "auto" && !paused) schedule();
    updateCtrl();
  }
  function schedule() {
    clearTimeout(timer);
    const gap = Date.parse(events[idx].ts) - Date.parse(events[idx - 1].ts), ms = Math.max(220, gap * scale());
    const prev = events[idx - 1];
    gapEl = feed.push(h("div", { class: "gap", "aria-hidden": "true" }, h("div", { class: "gap__bar", style: { animationDuration: `${ms}ms` } }),
      h("span", null, `+${Math.round(gap / 1000)} s in the recording (${prev.kind === "tool" && prev.name === "run_experiment" ? "Argon reading the result" : "Argon reasoning + harness scoring the dev runs, leave-one-run-out"})`)));
    timer = setTimeout(showNext, ms);
  }
  function showAll() { clearTimeout(timer); mode = "all"; while (idx < events.length) { addEvent(events[idx]); idx++; } finishTail(); updateCtrl(); }
  function restart() { clearTimeout(timer); feed.clear(); chainCard.reset(); chainPending.splice(0, chainPending.length, ...(S.chain || [])); clear(tailHost); idx = 0; done = false; mode = "auto"; paused = false; revealChain(0, false); showNext(); }
  const playBtn = h("button", { class: "btn btn--secondary btn--small", type: "button", onclick: () => toggle() });
  const stepBtn = h("button", { class: "btn btn--secondary btn--small", type: "button", onclick: () => { clearTimeout(timer); mode = "step"; paused = true; showNext(); updateCtrl(); } }, "Step");
  const allBtn = h("button", { class: "btn btn--secondary btn--small", type: "button", onclick: showAll }, "Show all");
  const restartBtn = h("button", { class: "btn btn--secondary btn--small", type: "button", onclick: restart }, "Restart");
  const lenSeg = segmented({ label: "Replay length", value: replay_s, options: [20, 30, 45].map((n) => ({ value: n, label: `${n} s` })), onChange: (v) => { replay_s = v; settings.replay_s = v; saveDemoSettings(); } });
  function toggle() {
    if (idx >= events.length) return;
    if (paused || mode !== "auto") { paused = false; mode = "auto"; schedule(); } else { paused = true; clearTimeout(timer); }
    updateCtrl();
  }
  function updateCtrl() {
    const end = idx >= events.length;
    playBtn.replaceChildren(icon(paused || mode !== "auto" ? "play" : "pause", 14), paused || mode !== "auto" ? "Play" : "Pause");
    playBtn.disabled = end; stepBtn.disabled = end; allBtn.disabled = end;
  }
  mountEl(ctrl, playBtn, stepBtn, allBtn, restartBtn, h("span", { class: "field-inline" }, h("span", { class: "help" }, "replay length"), lenSeg));
  const ruleCard = card({ title: "Rule and allowed settings", chips: chip("posthoc"), body: [
    h("p", null, S.rule?.text), h("div", { class: "row" }, Object.entries(S.allowed || {}).map(([k, v]) => h("span", { class: "chip chip--diff mono" }, `${k}: ${v.join(", ")}`)))] });
  const base = S.baseline;
  const baseCard = card({ title: `Baseline (attempt ${base?.attempt ?? 0})`, body: [configChips(base?.config), metricsRow(base?.metrics), h("p", { class: "muted mono" }, `logged ${fmtUTC(base?.ts)}`)] });
  rootEl.append(
    h("div", { class: "grid grid--2" }, integrityCard(S), h("div", { class: "stack" }, ruleCard, baseCard)),
    card({ title: "Hash chain", body: chainCard, footer: "Each block is one logged call; each link is checked by recomputing the hash. Mirrored to Atlas `attempts`." }),
    card({ title: "Session replay", chips: [provChip("REPLAY", null, `recorded ${fmtUTC(S.recorded?.start)}–${fmtUTC(S.recorded?.end, false)}`), chip("posthoc")], actions: ctrl, body: feed }),
    tailHost);
  state.demo.hooks = { playPause: toggle, label: "agent" };
  revealChain(0, false);
  showNext();
  disposers.push(() => { clearTimeout(timer); if (state.demo.hooks?.label === "agent") state.demo.hooks = null; });
}

// ---------------------------------------------------------------- live mini session
const live = { events: [], job: null, stream: null, status: "idle", t0: 0, maxExp: 2, error: null, subs: new Set() };
const liveEmit = () => live.subs.forEach((f) => f());
function liveReset() { live.stream?.close(); Object.assign(live, { events: [], job: null, stream: null, status: "idle", error: null }); }

function renderLiveEvent(e) {
  switch (e.type) {
    case "start": return card({ title: "Live session started", chips: [chip("live", `LIVE · ${e.model} · thinking ${e.thinking}`), chip("posthoc")], cls: "ev ev--start",
      body: [h("p", null, e.label || ""), h("dl", { class: "kv" }, Object.entries(e).filter(([k]) => !["type", "label", "elapsed_s"].includes(k)).flatMap(([k, v]) => [h("dt", null, k), h("dd", { class: "mono" }, typeof v === "object" ? JSON.stringify(v) : String(v))]))] });
    case "argon_text": return quoteFrame("gemini-4-argon (live)", [markdown(e.text), (e.flags || []).length ? h("div", { class: "chip-row" }, e.flags.map((f) => h("span", { class: "chip chip--cached" }, `wording flag: ${f}`))) : null]);
    case "tool_call": return h("div", { class: "feed__line" }, icon("agent", 14), `Argon called ${e.name}(`, h("span", { class: "mono" }, Object.entries(e.args || {}).map(([k, v]) => `${k}=${typeof v === "string" && v.length > 60 ? v.slice(0, 60) + "…" : v}`).join(", ")), ")");
    case "tool_result": {
      const r = e.result || {}, m = r.metrics || {};
      return h("details", { class: "ev ev--result" }, h("summary", null, `Result of ${e.name}`, r.error ? h("span", { class: "warn" }, ` · error: ${r.error}`) : null,
        m.median_error_m !== undefined ? h("span", { class: "mono" }, ` · median ${fmtNum(m.median_error_m, 2)} m, coverage ${fmtNum(m.coverage_mean, 3)}, band ${fmtNum(m.band_width_mean_m, 1)} m`) : null),
        h("pre", { class: "json mono" }, JSON.stringify(r, null, 2)));
    }
    case "verdict": return h("article", { class: ["exp", e.accepted ? "exp--ok" : "exp--no"] },
      h("header", { class: "exp__head" }, h("strong", null, `Verdict for ${e.label || `live #${e.attempt}`}`), e.accepted ? verdict("ACCEPTED") : verdict("REJECTED"), chip("live"), chip("posthoc", "live chain (attempts_live.jsonl), not the recorded chain")),
      h("div", { class: "chip-row" }, Object.entries(e.changes || {}).map(([k, [a, b]]) => h("span", { class: "chip chip--diff mono" }, `${k} ${a}→${b}`))),
      metricsRow(e.metrics, e.metrics?.vs_baseline),
      e.metrics?.vs_baseline?.error_delta_ci_m ? h("p", { class: "exp__ci" }, `paired per-run error delta: ${ci(e.metrics.vs_baseline.error_delta_ci_m)}`) : null,
      h("p", { class: "exp__reason" }, h("strong", null, "Harness rule: "), e.reason), e.note ? h("p", { class: "muted" }, e.note) : null);
    case "chain": return h("div", { class: ["banner banner--inline", e.ok ? "banner--ok" : "banner--red"] }, verdict(e.ok ? "INTACT" : "BROKEN"),
      h("span", null, `hash chain checked now: ${e.ok ? "ok" : "BROKEN"}, ${e.lines} lines in ${e.file || "the live log"}. Recorded chain unchanged: ${e.main_chain_unchanged ? "yes" : "NO"}.${e.atlas ? ` Atlas: ${typeof e.atlas === "string" ? e.atlas : JSON.stringify(e.atlas)}` : ""}`));
    case "final": return card({ title: "Session summary", chips: [chip("live"), chip("posthoc")], body: [markdown(e.summary || ""),
      h("ul", { class: "plain" }, (e.experiments || []).map((x) => h("li", null, `${x.label || `live #${x.attempt}`}: ${x.accepted ? "ACCEPTED" : "REJECTED"} · ${x.hypothesis || ""} · ${x.seconds} s`))),
      e.tokens ? h("p", { class: "muted mono" }, `tokens: ${e.tokens.prompt} in, ${e.tokens.output} out`) : null,
      e.files ? h("p", { class: "muted mono" }, Object.values(e.files).join(" · ")) : null] });
    default: return null;
  }
}

async function liveTab(host, ctx, disposers, goRecorded) {
  let health = null;
  try { health = await getHealth(); } catch { /* server down: shown below */ }
  const mod = health?.modules?.["agent.live_analyst"], key = health?.env?.gemini_api_key;
  const reason = MOCK ? null : !health ? "Server not reachable." : !mod ? "agent.live_analyst not installed yet." : !key ? "GEMINI_API_KEY is not set on the server." : !state.online ? "Offline: Gemini is unreachable." : null;
  const feed = eventFeed({ height: "min(62vh, 700px)" });
  const status = h("div", { class: "row" });
  let timerId = 0, rendered = 0;
  const maxSeg = segmented({ label: "Max experiments", value: live.maxExp, options: [{ value: 1, label: "1" }, { value: 2, label: "2" }], onChange: (v) => { live.maxExp = v; } });
  const startBtn = h("button", { class: "btn", type: "button", disabled: !!reason, onclick: start }, icon("play", 16), "Start live session");
  const stopBtn = h("button", { class: "btn btn--secondary", type: "button", disabled: true, onclick: stop }, icon("stop", 16), "Stop");
  const timer = h("span", { class: "mono muted" });
  const explainer = h("p", null, "Starts a new, short analyst session now: Argon proposes up to 2 experiments, a fixed harness scores each on the 23 Oct rides. Each Argon turn takes 30–60 s and scoring about 45 s, so expect 2–5 minutes. Sent to Gemini: tracker settings and metrics only.");
  const logNote = h("p", { class: "help" });
  const chipsHost = h("span");
  const errHost = h("div");
  const body = [explainer, logNote, h("div", { class: "row" }, h("label", { class: "field-inline" }, h("span", null, "max experiments"), maxSeg), startBtn, stopBtn, timer),
    reason ? emptyState(reason) : null, errHost, feed];
  mountEl(host, card({ title: "Live mini session", chips: chipsHost, body, cls: "live-analyst" }));

  function sync() {
    const running = ["starting", "running"].includes(live.status);
    startBtn.disabled = !!reason || running; stopBtn.disabled = !running;
    while (rendered < live.events.length) {
      const e = live.events[rendered++];
      if (e.type === "start") {
        mountEl(chipsHost, chip("live", `LIVE · ${e.model} · thinking ${e.thinking}`));
        logNote.textContent = e.log ? `This session writes its own log (${e.log}${e.record ? `, events in ${e.record}` : ""}); the recorded log and the sealed test are not touched.` : "";
        if (e.log) logNote.textContent += e.main_chain_sha256 ? " The recorded chain's SHA-256 is checked before and after." : "";
      }
      if (e.type === "error") mountEl(errHost, errorCard({ message: e.message, title: "The live session failed", retry: start, fallback: goRecorded, fallbackLabel: "Show the recorded session instead" }));
      const node = renderLiveEvent(e); if (node) feed.push(node);
      if (e.type === "argon_text" || e.type === "verdict") announce(e.type === "verdict" ? `Verdict: ${e.accepted ? "accepted" : "rejected"}` : "Argon wrote an update");
    }
    clearInterval(timerId);
    if (running) timerId = setInterval(() => { timer.textContent = `${fmtClock((Date.now() - live.t0) / 1000)} elapsed · Argon is thinking (30–60 s per turn)`; }, 1000);
    else timer.textContent = live.status === "done" ? `finished in ${fmtClock((live.t1 - live.t0) / 1000)}` : "";
  }
  async function start() {
    clear(errHost); liveReset(); feed.clear(); rendered = 0;
    live.status = "starting"; live.t0 = Date.now(); sync();
    try {
      const job = await startWithReplace((replace) => postJSON("/api/analyst/live/start", { max_experiments: live.maxExp, replace }));
      if (!job) { live.status = "idle"; sync(); return; }
      live.job = job; live.status = "running";
      live.stream = openJobStream(job, (e) => { if (e.type !== "log" && e.type !== "job_end") live.events.push(e); liveEmit(); }, (end) => {
        live.status = end.status === "lost" ? "lost" : live.events.some((x) => x.type === "error") ? "error" : "done"; live.t1 = Date.now(); liveEmit(); });
    } catch (e) { live.status = "error"; mountEl(errHost, errorCard({ message: e.message, title: "Could not start the live session", retry: start, fallback: goRecorded, fallbackLabel: "Show the recorded session instead" })); sync(); }
  }
  async function stop() { if (live.job) { try { await postJSON(`/api/jobs/${live.job.id}/cancel`, {}); } catch (e) { toast(e.message); } } live.status = "idle"; sync(); }
  const sub = () => sync();
  live.subs.add(sub);
  disposers.push(() => { live.subs.delete(sub); clearInterval(timerId); });
  sync();
}

export async function mount(root, ctx) {
  const screen = h("div", { class: "screen agent" });
  root.append(screen);
  const disposers = [];
  const panel = h("div", { id: "panel-recorded", role: "tabpanel", "aria-labelledby": "tab-recorded" });
  let active = ctx.query.get("tab") === "live" && !ctx.demo ? "live" : "recorded";
  let inner = [];
  const load = (id) => {
    inner.forEach((d) => d()); inner = [];
    active = id; panel.id = `panel-${id}`; panel.setAttribute("aria-labelledby", `tab-${id}`);
    if (id === "recorded") recordedTab(panel, ctx, inner); else liveTab(panel, ctx, inner, () => bar.set("recorded"));
  };
  const bar = tabs({ label: "Analyst session", tabs: [{ id: "recorded", label: "Recorded session" }, { id: "live", label: "Live mini session" }], active, onChange: load });
  screen.append(screenHeader({ title: "Agent", subtitle: "Argon proposes tracker settings; a fixed harness it cannot edit scores them; every call is hash-chained. Replayed from the log, with the checks recomputed now.",
    chips: [provChip("REPLAY"), chip("posthoc")] }), ...(ctx.demo ? [] : [bar]), panel,
    whereFrom("GET /api/analyst/session", "outputs_posthoc/analyst_session.jsonl", "outputs_posthoc/attempts.jsonl (attempt 8 is the sealed test)", "agent/harness.py"));
  load(active);
  return () => { inner.forEach((d) => d()); disposers.forEach((d) => d()); if (state.demo.hooks?.label === "agent") state.demo.hooks = null; };
}
