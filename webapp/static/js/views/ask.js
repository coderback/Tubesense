// Ask Argon: one question, Atlas hybrid retrieval, Argon drafts claims, a deterministic verifier checks them (fix rounds),
// a framing review (and a final check of its revision), and an answer built only from verified claims. Live runs stream in;
// cached runs come from agent.ask.replay() on the server: re-verified and re-gated now, labelled CACHED with the original time.
import { h, mount as mountEl, clear, fmtNum, fmtUTC, fmtDur, announce, icon, fixArrow } from "../util.js";
import { MOCK, fetchJSON } from "../api.js";
import { state, on, getHealth, DEMO_QUESTION } from "../state.js";
import { card, chip, provChip, screenHeader, whereFrom, verdict, claimCard, openSource, errorCard, emptyState, skeleton, startWithReplace, toast } from "../ui.js";
import { askStore, startAsk, loadCachedRun, eventsFromResult, AskRun } from "../askctl.js";

export { eventsFromResult };
const SUGGESTIONS = [
  DEMO_QUESTION,
  "What noise levels has TfL itself reported for Liverpool Street to Bethnal Green?",
  "Did TfL refuse to share noise data with Assembly Members?",
];
const TRIGGER = { initial: "first draft", verifier: "fix round: the verifier rejected claims", review: "revision after the framing review",
  "answer-fix": "answer line rewritten (claims pinned)" };

/** How the re-check of a CACHED answer may be described. "re-checked now" only when the verifier is unchanged and everything passed. */
export function recheckState(res) {
  const rv = res?.reverified;
  if (!res?.cached) return { kind: "live" };
  if (!rv) return { kind: "none", text: "verified when saved (not re-checked)" };
  if (rv.all_ok === true && rv.same_verifier === true) return { kind: "ok", text: "verified · re-checked now" };
  return { kind: "partial", text: rv.same_verifier === false ? "passes the current verifier (changed since saved)" : "passes the verifier now" };
}

// ---------------------------------------------------------------- pipeline rendering (also used by the demo composite)
const reviewOf = (run) => run.review || (run.result?.review ? { ...run.result.review, cached: run.result.cached } : null);
const finalReviewOf = (run) => run.finalReview || (run.result?.final_review ? { ...run.result.final_review, cached: run.result.cached } : null);

function stepper(run) {
  const r0 = run.rounds[0] || {}, lastR = run.rounds[run.rounds.length - 1] || {};
  const stages = [
    ["Retrieval", !!run.retrieval], ["Draft", !!r0.draft], ["Verifier", !!r0.verdicts],
    ...(run.rounds.length > 1 ? [["Fix round", !!(lastR.verdicts && run.rounds.length > 1)]] : []),
    ...(reviewOf(run) ? [["Review", true]] : []),
    ...(finalReviewOf(run) ? [["Final check", true]] : []),
    ["Final", run.status === "done"],
  ];
  let active = -1;
  if (run.busy) active = stages.findIndex(([, done]) => !done);
  return h("ol", { class: "stepper", "aria-label": "Ask pipeline" }, stages.map(([label, done], i) =>
    h("li", { class: [done && "is-done", i === active && "is-active"], "aria-current": i === active ? "step" : null },
      h("span", { class: "stepper__n" }, done ? icon("check", 14) : String(i + 1)), h("span", null, label))));
}

function retrievalCard(run) {
  const r = run.retrieval;
  if (!r) return card({ title: "1. Retrieval", body: run.busy ? skeleton(3) : emptyState("No retrieval event.") });
  const fallback = /local fallback/i.test(r.source || "");
  return card({ title: "1. Retrieval", cls: "stage", chips: [r.cached ? chip("cached", "CACHED") : chip("live"), fallback ? chip("cached", "LOCAL FALLBACK") : null],
    body: [
      h("p", null, h("strong", null, r.source || "retrieval"), r.seconds !== undefined && r.seconds !== null ? ` · ${fmtNum(r.seconds, 1)} s` : "",
        r.cached ? h("span", { class: "muted" }, " · as recorded when the answer was saved") : null, r.reason ? h("span", { class: "warn" }, ` · ${r.reason}`) : null),
      h("div", { class: "table-wrap" }, h("table", null,
        h("thead", null, h("tr", null, ["document", "publisher", "date", "page", "score"].map((c) => h("th", null, c)))),
        h("tbody", null, (r.hits || []).map((x) => h("tr", { class: "clickrow", tabindex: 0, role: "button", "aria-label": `Open source ${x.chunk_id}`,
          onclick: () => openSource(x.chunk_id), onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openSource(x.chunk_id); } } },
          h("td", null, x.title || x.chunk_id, h("div", { class: "muted mono" }, x.chunk_id)), h("td", null, x.publisher || ""), h("td", null, x.doc_date || ""),
          h("td", { class: "mono" }, x.page ?? ""), h("td", { class: "mono" }, x.score !== undefined ? fmtNum(x.score, 4) : "")))))),
      h("div", { class: "chip-row" }, h("span", { class: "chip chip--relative" }, `${(r.stats || []).length} section statistics available`),
        ...(r.stats || []).slice(0, 6).map((k) => h("span", { class: "chip chip--relative mono" }, k)), (r.stats || []).length > 6 ? h("span", { class: "muted" }, `+${r.stats.length - 6} more`) : null)] });
}

function reasonList(reasons) {
  return (reasons || []).length ? h("ul", { class: "claim__reasons" }, reasons.map((r) => h("li", null, typeof r === "string" ? r : JSON.stringify(r)))) : null;
}

function roundCard(run, i) {
  const rd = run.rounds[i]; if (!rd?.draft) return null;
  const d = rd.draft, v = rd.verdicts, byId = Object.fromEntries((v?.results || []).map((x) => [x.id, x]));
  const nOk = v ? v.results.filter((x) => x.ok).length : 0;
  const trig = TRIGGER[d.trigger] || d.trigger || "";
  const pinned = d.trigger === "answer-fix";
  const secs = d.seconds ? ` (${fmtNum(d.seconds, 1)} s)` : "";
  return card({ title: i === 0 ? "2. Draft and 3. Verifier" : `Round ${i + 1}: ${trig}`, cls: "stage", level: 3,
    chips: [d.cached ? chip("cached", "CACHED") : chip("live"), v ? verdict(nOk === v.results.length ? "verified" : "FAILS", `${nOk} of ${v.results.length} pass`) : chip("neutral", "verifying…")],
    body: [
      h("p", { class: "muted" }, pinned
        ? `Only the answer line was asked for again${secs}; the claims are carried over unchanged (pinned) and verified again. The verifier is agent/verify.py: deterministic code, no model.`
        : `Argon's draft${secs}. The verifier is agent/verify.py: deterministic code, no model.`),
      d.answer ? h("p", { class: "answer-line" }, h("span", { class: "muted" }, pinned ? "Rewritten answer line: " : "Draft answer line: "), d.answer) : null,
      pinned ? h("p", { class: "muted" }, `${(d.claims || []).length} pinned claim${(d.claims || []).length === 1 ? "" : "s"}: ${(d.claims || []).map((c) => c.id).join(", ")}`)
        : h("div", { class: "stack" }, (d.claims || []).map((c) => claimCard(c, byId[c.id] ? { ok: byId[c.id].ok, reasons: byId[c.id].reasons } : null, { verifiedText: "passes the verifier" }))),
      v?.answer_check ? h("div", null, h("p", null, v.answer_check.ok ? verdict("verified", "answer line passes the answer gate") : verdict("FAILS", "answer line rejected by the answer gate")),
        reasonList(v.answer_check.reasons)) : null] });
}

function reviewCard(r, { final = false } = {}) {
  if (!r) return null;
  const findings = r.findings || [];
  return card({ title: final ? "Final check of the revision" : "Framing review", cls: "stage", level: 3,
    chips: [r.cached ? chip("cached", "CACHED") : chip("live"), chip("posthoc", "MODEL JUDGEMENT"), final ? chip("neutral", "can only withhold") : null],
    body: [h("p", { class: "muted" }, r.note || "A separate Argon call reviews verified claims against their full cited sources for what the verifier cannot check (attribution, dropped hedges, a verdict's stated reason)."),
      final ? h("p", { class: "muted" }, "What the revision added or changed is reviewed once more. A finding here withholds the claim; there is no further revision.") : null,
      r.ok === false ? h("p", { class: "warn" }, `The review call failed and is recorded as failed: ${r.error}`)
        : findings.length ? h("ul", null, findings.map((f) => h("li", null, h("strong", { class: "mono" }, f.claim_id), `: ${f.problem} `, f.fix ? h("em", null, `Fix: ${f.fix}`) : null)))
        : h("p", null, verdict("verified", "no findings"), ` ${r.seconds ? fmtNum(r.seconds, 1) + " s" : ""}${typeof r.reviewed_round === "number" ? ` · reviewed round ${r.reviewed_round + 1}` : ""}`)] });
}

function rejectedLine(rl) {
  if (!rl) return null;
  const text = typeof rl === "string" ? rl : rl.text;
  const reasons = typeof rl === "string" ? [] : rl.reasons || [];
  return h("div", { class: "banner banner--inline banner--amber rejected-line" }, icon("warn", 16), h("div", null,
    h("strong", null, "Argon's own answer line was not shown"),
    text ? h("p", { class: "rejected-line__text" }, h("span", { class: "sr-only" }, "Rejected line: "), h("span", { class: "struck" }, text)) : null,
    reasonList(reasons), h("p", { class: "muted" }, "The answer above is the text of a verified claim instead.")));
}

function finalCard(run, opts = {}) {
  const res = run.result; if (!res) return null;
  const a = res.answer || {};
  const dropped = res.dropped_claims || [];
  const rs = recheckState(res), rv = res.reverified;
  const status = res.review_status || {};
  const supName = res.superseded_by ? String(res.superseded_by).split("/").pop() : null;
  const claimOpts = (c) => ({ verifiedText: res.cached ? rs.text : "verified",
    stage: status[c.id] ? `review: ${status[c.id]}` : res.review ? "review: no status recorded" : "review: not reviewed" });
  return card({ title: "5. Final answer", cls: "stage stage--final",
    chips: [res.cached ? provChip("CACHED", res.ts) : chip("live", "LIVE"), res.cached && rv ? chip("live", "verifier re-run now") : null,
      res.incomplete ? chip("synthetic", "INCOMPLETE") : null],
    body: [
      res.incomplete ? h("div", { class: "banner banner--inline banner--red", role: "status" }, icon("warn", 16),
        h("span", null, h("strong", null, "INCOMPLETE: "), String(res.incomplete), " · Only claims that passed the verifier are shown; claims the review flagged are withheld.")) : null,
      res.cached && res.exact_match === false ? h("div", { class: "banner banner--inline banner--amber" }, icon("warn", 16),
        h("span", null, res.label || "CACHED answer to a different saved question; not an answer to what was asked.")) : null,
      supName ? h("div", { class: "banner banner--inline banner--amber" }, icon("warn", 16), h("span", null, "A newer saved answer to this question exists. ",
        opts.onOpenCached ? h("button", { class: "linkbtn", type: "button", onclick: () => opts.onOpenCached(supName) }, `Open it (${supName})`) : h("span", { class: "mono break" }, supName))) : null,
      h("blockquote", { class: "answer" }, a.text || "No answer line could be verified."),
      h("p", { class: "muted" }, `Restates ${(a.claim_ids || []).join(", ") || "no claims"} · ${a.source || ""}${a.review ? ` · answer line ${a.review}` : ""}`),
      rejectedLine(a.rejected_line),
      h("h3", null, `${(res.verified_claims || []).length} verified claims`),
      h("div", { class: "stack" }, (res.verified_claims || []).map((c) => claimCard(c, { ok: true, reasons: [] }, claimOpts(c)))),
      dropped.length ? [h("h3", null, `${dropped.length} dropped or withheld claim${dropped.length === 1 ? "" : "s"}`),
        h("div", { class: "stack" }, dropped.map((d) => claimCard(d.claim, { ok: false, reasons: d.reasons }, { struck: true, reasons: d.reasons, stage: d.stage })))] : null,
      rv ? h("p", { class: rs.kind === "ok" ? "muted" : "warn" },
        `Re-checked by this replay at ${fmtUTC(rv.ts)}: verifier ${rv.same_verifier ? "unchanged" : "CHANGED since the answer was saved"}, corpus ${rv.same_corpus ? "unchanged" : "CHANGED"}; `,
        rv.all_ok ? "every claim and the answer line still pass." : "not everything passes now: a claim that fails is listed above as dropped, and a failing answer line is replaced by a verified claim's text.") : null,
      res.cached && !rv ? h("p", { class: "warn" }, "This cached answer was not re-checked by the server.") : null],
    footer: `${fmtDur(res.elapsed_s)} · ${res.rounds?.length ?? 0} round${res.rounds?.length === 1 ? "" : "s"} · ${res.model} (thinking ${res.thinking}) · ${res.file || ""}` });
}

/** Updates the elapsed seconds in any "Waiting for ..." banner under rootEl, once a second. Returns a stop function. */
export function startElapsedTicker(rootEl) {
  const id = setInterval(() => rootEl.querySelectorAll(".ask-wait").forEach((n) => {
    n.textContent = `Waiting for ${n.dataset.what} · ${fmtDur((Date.now() - Number(n.dataset.t0)) / 1000)} elapsed. Argon is thinking (30–60 s per turn).`; }), 1000);
  return () => clearInterval(id);
}

/** The whole pipeline for a run. opts.compact folds the stage details away (demo); opts.progressOnly shows only the stepper and waiting banner. */
export function renderAskRun(run, opts = {}) {
  const wrap = h("div", { class: "stack askrun" });
  if (!run) return wrap;
  wrap.append(stepper(run));
  if (run.busy) wrap.append(h("div", { class: "banner banner--inline banner--amber", role: "status" }, icon("play", 14),
    h("span", { class: "ask-wait", dataset: { t0: run.t0, what: run.waiting } }, `Waiting for ${run.waiting} · ${fmtDur((Date.now() - run.t0) / 1000)} elapsed. Argon is thinking (30–60 s per turn).`),
    opts.onCancel ? h("button", { class: "btn btn--small btn--secondary", type: "button", onclick: opts.onCancel }, "Cancel") : null));
  if (opts.progressOnly) {
    if (run.retrieval) wrap.append(h("p", { class: "muted" }, `Retrieved ${(run.retrieval.hits || []).length} chunks via ${run.retrieval.source}. ${run.rounds.length} draft round${run.rounds.length === 1 ? "" : "s"} so far.`));
    if (run.status === "error") wrap.append(errorCard({ title: "The ask did not finish", message: run.error || "Unknown error" }));
    return wrap;
  }
  const rev = reviewOf(run), rev2 = finalReviewOf(run);
  const roundCards = run.rounds.map((_, i) => roundCard(run, i));
  const nRev = run.rounds.findIndex((rd) => rd?.draft?.trigger === "review");
  if (rev) roundCards.splice(nRev >= 0 ? nRev : roundCards.length, 0, reviewCard(rev));   // the review sits before the revision it caused
  const stages = [retrievalCard(run), ...roundCards, reviewCard(rev2, { final: true })].filter(Boolean);
  if (opts.compact && run.result) wrap.append(h("details", { class: "card details-stage" }, h("summary", null,
    `Pipeline details: retrieval, ${run.rounds.length} draft/verifier round${run.rounds.length === 1 ? "" : "s"}${rev ? ", framing review" : ""}${rev2 ? ", final check" : ""}`),
    h("div", { class: "card__body stack" }, stages)));
  else wrap.append(...stages);
  if (run.status === "error") wrap.append(errorCard({ title: run.errorCode === "busy" ? "An ask is already running" : "The ask did not finish", message: run.error || "Unknown error", retry: opts.onRetry, fallback: opts.onFallback, fallbackLabel: "Use a cached answer" }));
  const f = finalCard(run, opts); if (f) wrap.append(f);
  return wrap;
}

// ---------------------------------------------------------------- screen
export async function mount(root, ctx) {
  const screen = h("div", { class: "screen ask" });
  root.append(screen);
  const disposers = [];
  const head = h("div"), pipeline = h("div"), cachedHost = h("div"), inputHost = h("div");
  screen.append(head, inputHost, pipeline, cachedHost, whereFrom("POST /api/ask/start (agent.ask, events over SSE)", "GET /api/ask/cached (agent.ask.replay: re-verified now)", "outputs_posthoc/asks/*.json", "agent/verify.py", "Atlas hybrid search"));
  let status = null, cachedList = [], run = askStore.current, unsub = null, tick = 0;

  const renderHead = () => {
    const chips = [];
    if (run?.cached) {
      chips.push(provChip("CACHED", run.result?.ts || run.start?.ts));
      const rs = recheckState(run.result);
      if (rs.kind === "ok") chips.push(chip("live", "re-checked now"));
      else if (rs.kind === "partial") chips.push(chip("cached", "re-check: see the answer"));
    } else if (run) chips.push(chip("live", `LIVE · ${run.start?.model || "gemini-4-argon"} · thinking ${run.start?.thinking || "medium"}`));
    else chips.push(chip("neutral", `${status?.model || "gemini-4-argon"} · thinking ${status?.thinking || "medium"}`));
    if (MOCK) chips.push(chip("mock"));
    mountEl(head, screenHeader({ title: "Ask Argon", subtitle: "Ask a question about the public record. Argon drafts claims; code checks every quote and number; only verified claims can appear in the answer.", chips }));
  };
  const openCached = async (name) => { try { attach(await loadCachedRun(name)); pipeline.scrollIntoView({ behavior: "smooth" }); } catch (e) { toast(e.message); } };
  const draw = () => { mountEl(pipeline, run ? renderAskRun(run, { onCancel: () => run.cancel(), onRetry: () => submit(run.question), onFallback: () => cachedHost.scrollIntoView({ behavior: "smooth" }), onOpenCached: openCached }) : emptyState("Ask a question, or open a cached answer below.")); };
  const attach = (r) => {
    unsub?.(); run = r; renderHead(); draw();
    unsub = r.subscribe(() => { cancelAnimationFrame(tick); tick = requestAnimationFrame(() => { draw(); renderHead(); if (r.status === "done") announce("Answer ready"); renderInput(); }); });
  };

  const q = h("input", { type: "text", id: "ask-q", maxlength: 300, placeholder: "Ask about grinding, noise figures or data requests…", "aria-describedby": "ask-help ask-count", autocomplete: "off",
    value: ctx.query.get("q") || (ctx.demo ? state.demo.settings.question : "") });
  const count = h("div", { class: "counter", id: "ask-count" });
  const btn = h("button", { class: "btn", type: "submit" }, icon("ask", 16), "Ask");
  const form = h("form", { class: "ask-form", onsubmit: (e) => { e.preventDefault(); submit(q.value); } },
    h("label", { class: "field", for: "ask-q" }, "Your question", q), count, h("div", { class: "row" }, btn,
      SUGGESTIONS.map((s) => h("button", { class: "btn btn--small btn--secondary suggestion", type: "button", onclick: () => { q.value = s; upd(); q.focus(); } }, s))));
  const upd = () => { count.textContent = `${q.value.length} / 300`; renderInput(); };
  q.addEventListener("input", upd);
  function liveBlock() {
    if (MOCK) return "Mock mode: a live ask is simulated from a saved answer.";
    if (!state.serverOk) return "Server not reachable.";
    if (!state.online) return "Offline: Gemini is unreachable. Cached answers still work.";
    if (status && !status.available) return status.reason || "agent.ask is not available.";
    if (status && status.gemini_api_key === false) return "GEMINI_API_KEY is not set on the server.";
    return null;
  }
  const blockHost = h("div"), chipHost = h("span");
  inputHost.append(card({ title: "Your question", chips: chipHost, body: [form, blockHost,
    h("p", { class: "help", id: "ask-help" }, "Your question is sent to Gemini with public TfL and London Assembly documents. Do not type personal data. A live ask is one paid Gemini call (1-3 minutes) and only starts when you press Ask.")] }));
  function renderInput() {
    const block = liveBlock(), busy = run?.busy, ok = q.value.trim().length >= 5;
    btn.disabled = !!block || busy || !ok;
    mountEl(chipHost, block ? chip("neutral", "live ask unavailable") : null);
    mountEl(blockHost, block ? h("div", { class: "banner banner--inline banner--amber" }, icon("warn", 16), block) : null);
  }
  async function submit(text) {
    text = String(text || "").trim();
    if (text.length < 5 || text.length > 300) { toast("A question needs 5 to 300 characters."); return; }
    const r = await startAsk(text);
    attach(r);
    announce("Ask started");
  }

  function renderCached() {
    mountEl(cachedHost, card({ title: "Cached answers", chips: chip("cached", "CACHED"), body: cachedList.length
      ? h("ul", { class: "cached-list" }, cachedList.map((c) => h("li", null, h("button", { class: "cached-item", type: "button", onclick: () => openCached(c.name) },
          h("span", { class: "cached-item__q" }, c.question), h("span", { class: "muted mono" }, `${fmtUTC(c.ts)} · ${c.n_verified} verified / ${c.n_dropped} dropped`)))))
      : emptyState("No cached answers yet. Live answers are saved to outputs_posthoc/asks and appear here."),
      footer: "A cached answer is a stored copy of an earlier live call, shown with its original time (the newest answer per question is listed). Opening one re-runs the verifier and the answer gate on it now; no Gemini call." }));
  }

  renderHead(); renderInput(); upd(); draw();
  if (run) attach(run);
  Promise.all([fetchJSON("/api/ask/status").catch(() => null), fetchJSON("/api/ask/cached").catch(() => ({ items: [] }))]).then(([s, c]) => {
    status = s; cachedList = c?.items || []; renderHead(); renderInput(); renderCached();
  });
  disposers.push(on("health", renderInput), on("online", renderInput));
  renderCached();
  disposers.push(startElapsedTicker(pipeline));
  return () => { unsub?.(); cancelAnimationFrame(tick); disposers.forEach((d) => d()); };
}
