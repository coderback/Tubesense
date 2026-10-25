// Argon at work: three more real Argon jobs, each with the deterministic check that judges it and its honest status.
//  1. Roughness: Argon reads TfL's own 2018 rail roughness charts (FOI-1730-1819) from the PDF; code checks every reading
//     against the pixels and against TfL's own stated stretch. PRECOMPUTED (2 runs); PENDING HUMAN CHECK until HUMAN_CHECK.md is filled.
//  2. Recheck: Argon re-derives our headline numbers in its code sandbox, never shown ours; code compares under fixed tolerances.
//  3. Ride reports: Argon writes a plain account of one ride; every number is bound to a stored stat by schema and checked.
//     DRAFT until a person approves it with the CLI (the web app never approves a ride report).
// Saved results load at once; the buttons run the real CLIs as server jobs (one heavy job at a time, Cancel kills the process).
import { h, mount as mountEl, clear, fmtNum, fmtUTC, fmtDur, fmtTimeLocal, announce, icon } from "../util.js";
import { MOCK, fetchJSON, postJSON, openJobStream } from "../api.js";
import { state, setAtlasStatus, getHealth } from "../state.js";
import { card, chip, provChip, screenHeader, whereFrom, verdict, quoteFrame, errorCard, emptyState, skeleton, tabs, modal, startWithReplace, toast, statTile } from "../ui.js";
import { markdown } from "../md.js";

const TS_RX = /^(\d{4})(\d\d)(\d\d)T(\d\d)(\d\d)(\d\d)Z$/;
const isoOf = (ts) => { const m = TS_RX.exec(String(ts || "")); return m ? `${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6]}+00:00` : ts; };
const pendingChip = (hc) => hc?.state === "done" ? chip("prereg", `HUMAN CHECKED${hc.checked_by ? ` · ${hc.checked_by}` : ""}`)
  : chip("concept", hc?.state === "partial" ? `PENDING HUMAN CHECK · ${hc.n_filled}/${hc.n_rows} rows` : "PENDING HUMAN CHECK", { large: true });
const rng = (v) => (Array.isArray(v) && v.length ? (Math.min(...v) === Math.max(...v) ? fmtNum(v[0], 0) : `${fmtNum(Math.min(...v), 0)}–${fmtNum(Math.max(...v), 0)}`) : "?");

// ---------------------------------------------------------------- shared: run a CLI as a server job, stream its events
function jobRunner({ label, start, onEvent, onDone, note }) {
  const host = h("div", { class: "jobrun", role: "status" });
  let stream = null, job = null, t0 = 0, timer = 0;
  const line = h("p", { class: "jobrun__line mono" });
  const cancelBtn = h("button", { class: "btn btn--small btn--secondary", type: "button", onclick: () => cancel() }, icon("stop", 14), "Cancel");
  const tickLine = () => { if (job) line.textContent = `${label} · job ${job.id} · ${fmtDur((Date.now() - t0) / 1000)} elapsed`; };
  async function run(body = {}) {
    stream?.close(); clearInterval(timer);
    mountEl(host, line, h("div", { class: "row" }, cancelBtn, note ? h("span", { class: "help" }, note) : null));
    line.textContent = `${label} · starting…`;
    try {
      job = await startWithReplace((replace) => postJSON(start, { ...body, replace }));
      if (!job) { clear(host); return; }
    } catch (e) { mountEl(host, errorCard({ title: `Could not start: ${label}`, message: e.message })); return; }
    t0 = Date.now(); tickLine(); timer = setInterval(tickLine, 1000);
    announce(`${label} started`);
    stream = openJobStream(job, (ev) => {
      if (ev.type === "job_end") return;
      onEvent?.(ev);
    }, (end) => {
      clearInterval(timer);
      const ok = end?.status === "done";
      mountEl(host, h("p", { class: "jobrun__line" }, ok ? verdict("verified", "finished") : end?.status === "lost" ? verdict("OPEN", "connection lost") : verdict("FAILS", end?.status || "ended"),
        ` ${label} · ${end?.seconds !== undefined ? fmtDur(end.seconds) : ""}${end?.exit_code ? ` · exit code ${end.exit_code}` : ""}`));
      announce(`${label} ${ok ? "finished" : "ended"}`);
      onDone?.(end);
    });
  }
  async function cancel() { if (job) { try { await postJSON(`/api/jobs/${job.id}/cancel`, {}); } catch (e) { toast(e.message); } } }
  host.run = run;
  host.dispose = () => { stream?.close(); clearInterval(timer); };
  return host;
}

function logBox() {
  const box = h("pre", { class: "joblog mono", tabindex: 0, "aria-label": "Job output" });
  box.add = (text, cls) => { box.append(h("span", { class: cls }, text + "\n")); box.scrollTop = box.scrollHeight; };
  return box;
}

function figureModal(src, title) {
  let m;
  m = modal({ title, cls: "overlay--wide", labelledby: "fig-zoom", body: h("div", { class: "box__full" },
    h("img", { src, alt: `${title}, as printed in TfL's PDF`, class: "fig-full" }),
    h("button", { class: "btn btn--secondary", type: "button", onclick: () => m.close() }, "Close")) });
}

// ---------------------------------------------------------------- 1. roughness
function headline(R) {
  const st = R.tfl?.stated || {}, both = R.tfl?.all_figures_overlap, px = R.tfl?.pixels || {};
  const onsets = Object.values(px).map((p) => p.onset_m).filter((x) => x !== null && x !== undefined);
  return h("div", { class: "grid grid--3" },
    statTile({ value: st.zone_m ? `${fmtNum(st.zone_m[0], 0)}–${fmtNum(st.zone_m[1], 0)}` : "?", unit: "m", caption: "TfL's own words: elevated rail roughness", source: `${st.chunk || "FOI-1730-1819#000"} (TfL FOI response)` }),
    statTile({ value: both ? `${fmtNum(both[0], 0)}–${fmtNum(both[1], 0)}` : "none", unit: "m", tone: "amber", caption: "Argon: stretch above the threshold in all 4 figures, both runs, passing the zone rule", source: "roughness.json tfl_anchor (overlap common to all four)" }),
    statTile({ value: onsets.length ? `${fmtNum(Math.min(...onsets), 0)}–${fmtNum(Math.max(...onsets), 0)}` : "?", unit: "m", tone: "slate", caption: "Model-free pixel reader: where the trace rises and stays above 2× the threshold", source: "agent/roughness.py digitizer (no model)" }));
}

function caughtCard(R) {
  const runs = R.runs || [];
  const rows = runs.map((r) => {
    const rej = (r.summary || []).filter((x) => x.status === "rejected");
    return h("tr", null, h("td", { class: "mono" }, `run ${r.run}`),
      h("td", null, `${r.readings.fail} of ${r.readings.n}`), h("td", null, `${r.zones.fail} of ${r.zones.n}`),
      h("td", null, `${rej.length} of ${(r.summary || []).length}`), h("td", null, String((r.colours || []).length)),
      h("td", { class: "mono" }, r.seconds ? fmtDur(r.seconds) : ""));
  });
  const shared = (R.check_notes || []).find((t) => t.startsWith("Run-vs-run"));
  const detail = runs.map((r) => h("details", { class: "caught__run" }, h("summary", null, `Run ${r.run}: what the checks rejected`),
    r.readings.failed.length ? [h("h4", null, "Readings that do not match the pixels"), h("div", { class: "table-wrap" }, h("table", null,
      h("thead", null, h("tr", null, ["figure", "kind", "Argon read", "pixels give", "|Δ| vs tolerance"].map((t) => h("th", null, t)))),
      h("tbody", null, r.readings.failed.map((x) => h("tr", null, h("td", { class: "mono" }, `Fig ${x.figure} ${x.panel || ""}`), h("td", null, x.kind),
        h("td", { class: "mono" }, `${x.argon_y} ${x.y_units} at ${x.x} ${x.x_units}`), h("td", { class: "mono" }, `${x.pixel} ${x.y_units} ${x.where || ""}`),
        h("td", { class: "mono" }, `${x.abs_err} > ${x.tolerance}`))))))] : null,
    (r.summary || []).some((x) => x.status === "rejected") ? [h("h4", null, "Summary sentences rejected"), h("ul", { class: "plain" }, r.summary.filter((x) => x.status === "rejected").map((x) =>
      h("li", { class: "claim claim--bad" }, h("p", { class: "claim__text" }, h("span", { class: "struck" }, x.sentence)), h("ul", { class: "claim__reasons" }, x.reasons.map((t) => h("li", null, t))))))] : null,
    (r.colours || []).length ? [h("h4", null, "Line colours named wrongly or not at all"), h("ul", null, r.colours.map((c) => h("li", null,
      c.wrong_words?.length ? `called ${c.wrong_words.join("/")} on the ${c.panel} panel, but the pixels hold only pink` : `the ${c.colour} line on the ${c.panel} panel is not named by its colour`)))] : null,
    r.zones.failed.length ? [h("h4", null, "Rough zones that fail the zone rule (fixed before the first run)"), h("p", { class: "mono help" }, r.zones.failed.map((z) => `Fig ${z.figure} ${fmtNum(z.start, 0)}–${fmtNum(z.end, 0)} m`).join(" · "))] : null,
    r.other_failures.length ? [h("h4", null, "Other"), h("ul", null, r.other_failures.map((t) => h("li", null, t)))] : null));
  return card({ title: "The checks caught Argon's mistakes", chips: [chip("live", "no model in the checks"), verdict("REJECTED", "kept out of the result")],
    body: [h("p", null, "Every maximum Argon reported is checked against the pixels by a model-free reader, every rough zone against a rule fixed before the first run, every summary sentence against the pixels and the banned-phrasing list. What fails is shown here and never used."),
      h("div", { class: "table-wrap" }, h("table", null, h("thead", null, h("tr", null, ["", "readings off the pixels", "zones failing the rule", "summary sentences rejected", "colour errors", "Argon call"].map((t) => h("th", null, t)))), h("tbody", null, rows))),
      shared ? h("p", { class: "warn" }, h("strong", null, "Agreement is not proof: "), shared.replace(/^Run-vs-run agreement cannot catch an error both runs make\. Found: /, "both runs made the same mistake. ")) : null,
      detail] });
}

function checkTable(R) {
  const t = R.check_table || [];
  if (t.length < 2) return emptyState("The check table was not found in roughness.md.");
  const cell = (c) => { const bad = /\*\*/.test(c) || /fail|rejected/i.test(c); const txt = c.replace(/\*\*/g, ""); return h("td", { class: ["mono", bad ? "cell--bad" : /^pass$/i.test(txt) ? "cell--ok" : null] }, /^pass$/i.test(txt) ? verdict("verified", "pass") : bad ? verdict("FAILS", txt) : txt); };
  return h("div", { class: "table-wrap" }, h("table", { class: "checktable" }, h("thead", null, h("tr", null, t[0].map((c) => h("th", null, c)))),
    h("tbody", null, t.slice(1).map((r) => h("tr", null, h("td", null, r[0]), r.slice(1).map(cell))))));
}

function figureGrid(R) {
  return h("div", { class: "figs" }, (R.figures || []).map((f) => {
    const title = `Fig ${f.n} · ${f.rail || ""} ${f.band || ""} · p.${f.page ?? "?"}`;
    const okRuns = (f.max_by_run || []).map((v, i) => v ? `${fmtNum(v[0], 0)} m / ${fmtNum(v[1], 0)} µm${f.max_pixel_ok_by_run?.[i] === false ? " ✗ fails the pixel check" : ""}` : null).filter(Boolean);
    return h("figure", { class: "fig" },
      f.image ? h("button", { type: "button", class: "fig__btn", "aria-label": `Open ${title} full size`, onclick: () => figureModal(f.image, title) },
        h("img", { src: f.image, alt: `${title}: TfL's printed chart (position panel above, spectrum below)`, loading: "lazy", width: 1600, height: 598 })) : null,
      h("figcaption", null, h("strong", null, title), f.header ? h("div", { class: "mono help" }, f.header) : null,
        f.max_digitizer ? h("div", { class: "help" }, `Tallest spike (pixels): ${fmtNum(f.max_digitizer.y, 1)} µm at ${(f.max_digitizer.tied_x || [f.max_digitizer.x]).map((x) => fmtNum(x, 0)).join(" and ")} m; Argon: ${okRuns.join("; ")}`) : null,
        f.zones_confirmed?.length ? h("div", { class: "help" }, `Confirmed stretch: ${f.zones_confirmed.map((z) => `${rng(z.start_by_run)} to ${rng(z.end_by_run)} m`).join("; ")}`) : null));
  }));
}

function humanCheckCard(R) {
  const hc = R.human_check || {};
  return card({ title: "Human check", chips: pendingChip(hc), cls: "humancheck",
    body: [h("p", null, hc.state === "done" ? `Checked by ${hc.checked_by}${hc.date ? ` on ${hc.date}` : ""}: every row filled in.`
        : "Nothing from this extraction goes into the evidence pack until a person reads these off the charts by eye and fills in HUMAN_CHECK.md (written once by the CLI, never overwritten)."),
      (hc.rows || []).length ? h("ol", { class: "hc" }, hc.rows.map((r) => h("li", { class: r.filled ? "is-done" : "" }, h("span", null, `Fig ${r.figure}, ${r.panel}: ${r.what}`),
        h("span", { class: ["mono", r.filled ? "ok" : "muted"] }, r.filled ? `${r.reading} · agrees: ${r.agrees}` : "not filled")))) : emptyState("HUMAN_CHECK.md not found."),
      h("p", { class: "help mono" }, R.files?.human_check || "outputs_posthoc/roughness/HUMAN_CHECK.md")] });
}

/** Compact card for the demo's agent beat: the stretch, what the checks caught, the human-check status. */
export function roughnessMini(R) {
  if (!R?.available) return null;
  const caught = (R.runs || []).reduce((a, r) => ({ rd: a.rd + r.readings.fail, s: a.s + r.summary.filter((x) => x.status === "rejected").length, c: a.c + r.colours.length }), { rd: 0, s: 0, c: 0 });
  const both = R.tfl?.all_figures_overlap, st = R.tfl?.stated;
  const f1 = (R.figures || [])[0];
  return card({ title: "Argon reads TfL's own roughness charts", cls: "rough-mini", chips: [provChip("PRECOMPUTED", R.ts), chip("posthoc"), pendingChip(R.human_check)],
    body: h("div", { class: "rough-mini__grid" },
      f1?.image ? h("img", { src: f1.image, alt: "TfL's 2018 rail roughness chart, Bethnal Green to Mile End, figure 1", loading: "lazy", width: 1600, height: 598 }) : null,
      h("div", { class: "stack" },
        h("p", { class: "lead" }, `TfL says elevated roughness at ${st?.zone_m ? `${st.zone_m[0]}–${st.zone_m[1]}` : "?"} m. Argon found ${both ? `${both[0]}–${both[1]}` : "no common"} m in all four charts.`),
        h("p", null, verdict("REJECTED", `${caught.rd} readings off the pixels, ${caught.s} summary sentences, ${caught.c} colour errors`), ` caught by code across the ${(R.runs || []).length} runs and kept out.`),
        h("p", { class: "help" }, "Read from a picture of TfL's plot (FOI-1730-1819); not our measurement; not in the evidence pack until a person checks it."))),
    footer: h("a", { href: "#/work?tab=roughness" }, "Open Argon at work → roughness") });
}

function roughnessPanel(host, disposers) {
  const body = h("div", { class: "stack" }), recheckOut = logBox();
  mountEl(host, body);
  const runner = jobRunner({ label: "Re-check the saved runs (agent.roughness --cached)", start: "/api/work/roughness/recheck", note: "No Gemini call: the two saved Argon runs go through every check again.",
    onEvent: (ev) => { if (ev.type === "log") recheckOut.add(ev.text, /FAIL|x |REFUSED/.test(ev.text) ? "warn" : /PASS|ok/.test(ev.text) ? "ok" : ""); },
    onDone: (end) => { if (end?.status === "done") { recheckedAt = new Date().toISOString(); load(); } } });
  disposers.push(() => runner.dispose());
  let recheckedAt = null;
  async function load() {
    mountEl(body, skeleton(6));
    let R;
    try { R = await fetchJSON("/api/work/roughness"); } catch (e) { mountEl(body, errorCard({ title: "Roughness results not available", message: e.message, retry: load })); return; }
    if (!R.available) { mountEl(body, emptyState(R.reason || "No roughness results.")); return; }
    const st = R.tfl?.stated || {};
    mountEl(body,
      card({ title: "TfL's 2018 rail roughness charts, read by Argon", cls: "rough-head",
        chips: [provChip("PRECOMPUTED", R.ts, `${(R.runs || []).length} runs`), chip("posthoc"), recheckedAt ? chip("live", `checks re-run now ${fmtTimeLocal(recheckedAt)}`) : null, pendingChip(R.human_check)],
        body: [h("p", { class: "lead" }, "TfL's PDF of rail roughness for Bethnal Green → Mile End (eastbound, January 2018) is figure-only: every axis, number and trace is inside an image. Argon (", R.model, ", thinking ", R.thinking, ") read the four charts twice, independently. Code then checked every reading against the pixels, the printed text and TfL's own statement."),
          headline(R),
          quoteFrame(`TfL's own words (FOI-1730-1819, corpus chunk ${st.chunk || "FOI-1730-1819#000"}): an independent primary source, not produced by Argon or our checks`,
            h("div", null, st.zone_sentence ? h("p", null, `“${st.zone_sentence}”`) : null, st.grinding_sentence ? h("p", null, `“${st.grinding_sentence}”`) : null)),
          h("p", { class: "help" }, st.note || "The causal claim is TfL's; we did not link roughness to noise."),
          R.plain_words ? h("p", null, h("strong", null, "In plain words (code-written from the checked numbers): "), R.plain_words) : null] }),
      card({ title: "The four charts", chips: chip("neutral", "TfL FOI-1730-1819, as printed"), body: figureGrid(R),
        footer: "Click a chart for full size. Upper panel: roughness (µm) against position (m); lower panel: roughness spectrum. Position is TfL's plotted position; its zero point is not stated." }),
      caughtCard(R),
      card({ title: "Every check, per run", body: [checkTable(R), h("ul", { class: "help" }, (R.check_notes || []).filter((t) => !t.startsWith("Run-vs-run")).map((t) => h("li", null, t)))] }),
      humanCheckCard(R),
      card({ title: "Re-check the saved runs now", chips: chip("neutral", "no Gemini"), body: [
        h("p", null, "Runs python -m agent.roughness --cached on the server: the saved Argon extractions go through every check again (deterministic: same runs in, byte-identical outputs out). A few seconds."),
        h("div", { class: "row" }, h("button", { class: "btn", type: "button", disabled: MOCK, onclick: () => { clear(recheckOut); runner.run(); } }, icon("play", 16), "Re-check saved runs now")),
        runner, recheckOut] }),
      card({ title: "Limits", body: h("ul", null, (R.limits || []).map((t) => h("li", null, t.replace(/\*\*/g, "")))) }),
      R.md ? h("details", { class: "card md-card" }, h("summary", null, "Full report (roughness.md, written by the CLI)"), h("div", { class: "card__body" }, markdown(R.md))) : null);
  }
  load();
}

// ---------------------------------------------------------------- 2. recheck
const fmtVal = (v) => (typeof v === "number" ? (Number.isInteger(v) ? String(v) : String(+v.toPrecision(6))) : v === null || v === undefined ? "—" : String(v));
function compareTable(rows) {
  return h("div", { class: "table-wrap" }, h("table", { class: "cmp" }, h("thead", null, h("tr", null, ["quantity", "ours", "Argon's", "tolerance", "match", "in sandbox output"].map((t) => h("th", null, t)))),
    h("tbody", null, rows.map((r) => h("tr", { class: r.match === false ? "row--bad" : null }, h("td", { class: "mono" }, r.quantity), h("td", { class: "mono" }, fmtVal(r.ours)), h("td", { class: "mono" }, fmtVal(r.argon)),
      h("td", { class: "mono" }, r.tol !== undefined && r.tol !== null ? `±${fmtVal(r.tol)}` : r.kind === "label" ? "exact" : ""),
      h("td", null, r.match === true ? verdict("MATCH", "match") : r.match === false ? verdict("FAILS", "MISMATCH") : verdict("OPEN", "not compared")),
      h("td", null, r.traced_to_sandbox === true ? "yes" : r.traced_to_sandbox === false ? h("span", { class: "warn" }, "no") : "—"))))));
}

function recheckPanel(host, disposers) {
  const body = h("div", { class: "stack" }), liveHost = h("div", { class: "stack" }), canaryOut = logBox(), canarySum = h("div");
  mountEl(host, body);
  let liveRows = {}, liveCode = {};
  const drawLive = (final) => mountEl(liveHost, Object.entries(liveRows).map(([n, rows]) => card({ title: n, level: 3, cls: "stage", chips: [chip("live"), liveCode[n] ? chip("neutral", `${liveCode[n]} code block${liveCode[n] === 1 ? "" : "s"} executed`) : null], body: compareTable(rows) })),
    final ? h("p", null, final.n_mismatch === 0 ? verdict("MATCH", `${final.n_match} of ${final.n_compared} match`) : verdict("FAILS", `${final.n_mismatch} mismatch: ${(final.mismatches || []).join(", ")}`), ` · numbers traced to sandbox output ${final.numbers_traced_to_sandbox} · ${fmtDur(final.seconds_total)}`) : null);
  const runner = jobRunner({ label: "Argon re-derives our numbers (live)", start: "/api/work/recheck/start", note: "Gemini, code execution, about 35 s. Sent: derived tables and definitions only.",
    onEvent: (ev) => {
      if (ev.type === "start") { liveRows = {}; liveCode = {}; (ev.checks || []).forEach((n) => { liveRows[n] = []; }); drawLive(); }
      else if (ev.type === "argon_code") { liveCode[ev.check] = (liveCode[ev.check] || 0) + 1; drawLive(); }
      else if (ev.type === "compare") { (liveRows[ev.check] ||= []).push(ev); drawLive(); }
      else if (ev.type === "error") { liveHost.append(errorCard({ title: `Check ${ev.check || ""} failed`, message: ev.error || ev.message || "error" })); }
      else if (ev.type === "final") drawLive(ev);
      else if (ev.type === "log" && /atlas rechecks: upserted/.test(ev.text || "")) setAtlasStatus(true);
      else if (ev.type === "log" && /atlas skipped: \w*(Timeout|Connection|ServerSelection)/i.test(ev.text || "")) setAtlasStatus(false);
    },
    onDone: (end) => { if (end?.status === "done") load(); } });
  const canary = jobRunner({ label: "Recheck canary (offline, planted errors)", start: "/api/work/recheck/canary", note: "No API call.",
    onEvent: (ev) => { if (ev.type === "log") canaryOut.add(ev.text, /MISSED|MISMATCH|FAIL/.test(ev.text) ? "warn" : /caught|PASS|match/.test(ev.text) ? "ok" : ""); },
    onDone: () => {
      const t = canaryOut.textContent, caught = (t.match(/: caught/g) || []).length, missed = (t.match(/MISSED/g) || []).length, pass = /CANARY PASS/.test(t);
      mountEl(canarySum, h("p", null, pass ? verdict("verified", "CANARY PASS") : verdict("FAILS", /CANARY FAIL/.test(t) ? "CANARY FAIL" : "no verdict printed"),
        ` ${caught} planted error${caught === 1 ? "" : "s"} caught, ${missed} missed (counted from the CLI's plain-text output above).`));
    } });
  disposers.push(() => { runner.dispose(); canary.dispose(); });
  async function load() {
    let R;
    try { R = await fetchJSON("/api/work/recheck"); } catch (e) { mountEl(body, errorCard({ title: "Recheck results not available", message: e.message, retry: load })); return; }
    const s = R.summary || {};
    const liveCard = card({ title: "Run it live", chips: [R.gemini_api_key ? chip("neutral", "gemini-4-argon · thinking medium · code execution") : chip("neutral", "no Gemini key")], body: [
      h("p", null, "Starts python -m agent.recheck --jsonl on the server: five checks run in parallel, each a separate Argon call with code execution. The comparison table fills in as each answer arrives."),
      h("div", { class: "row" }, h("button", { class: "btn", type: "button", disabled: MOCK || !R.module || !R.gemini_api_key || !state.online, onclick: () => runner.run() }, icon("play", 16), "Run live (about 35 s)"),
        !state.online ? h("span", { class: "help" }, "Offline.") : null),
      runner, liveHost] });
    const canaryCardEl = card({ title: "Canary: does the comparator catch planted errors?", chips: [chip("seeded"), chip("neutral", "offline")], body: [
      h("p", null, "python -m agent.recheck --canary replays Argon's saved answers against our values (all must match), then plants errors in our values just outside tolerance, and drops one of Argon's answers. Every one must be caught."),
      h("div", { class: "row" }, h("button", { class: "btn btn--secondary", type: "button", disabled: MOCK || !R.available, onclick: () => { clear(canaryOut); clear(canarySum); canary.run(); } }, icon("play", 16), "Run the canary")),
      canary, canaryOut, canarySum] });
    if (!R.available) { mountEl(body, emptyState(R.reason || "No saved recheck."), liveCard, canaryCardEl); return; }
    mountEl(body,
      card({ title: "Argon re-derives our headline numbers with its own code", cls: "rough-head",
        chips: [provChip("PRECOMPUTED", isoOf(R.ts)), chip("posthoc"), chip("neutral", `${R.model} · thinking ${R.thinking}`)],
        body: [h("p", { class: "lead" }, "For each headline number in the evidence pack, Argon gets only a derived table and the definition (for verdicts, the frozen rule text), computes the value in its code sandbox, and is never shown our value. Code compares the two under tolerances fixed before any run, and checks that every number Argon returns appears in what its sandbox printed."),
          h("div", { class: "grid grid--4" },
            statTile({ value: `${s.n_match ?? "?"}/${s.n_compared ?? "?"}`, caption: "values match ours", source: "outputs_posthoc/recheck/latest.json", tone: s.n_mismatch ? "amber" : null }),
            statTile({ value: String(s.numbers_traced_to_sandbox ?? "?"), caption: "numbers found in the sandbox's printed output", source: "traced_to_sandbox" }),
            statTile({ value: String((s.checks_without_executed_code || []).length), caption: "checks answered without running code", source: "code_executed_ok" }),
            statTile({ value: fmtDur(s.seconds_total), caption: "for all five checks, in parallel", source: "seconds_total", tone: "slate" })),
          h("p", { class: "help" }, "Inputs sent: ", Object.values(R.inputs || {}).join(" · "), ". No audio, no sensor arrays, no speech. Tolerances: ", Object.entries(R.tolerances || {}).map(([k, v]) => `${k} ${v ?? "exact"}`).join(", "), " (+ half a unit in the last decimal we publish).")] }),
      ...Object.entries(R.checks || {}).map(([n, c]) => card({ title: c.title || n, level: 3, cls: "stage",
        chips: [c.error ? verdict("FAILS", "error") : c.comparisons.every((r) => r.match !== false) ? verdict("MATCH", `${c.comparisons.filter((r) => r.match).length} match`) : verdict("FAILS", "mismatch"),
          c.code_executed_ok ? chip("neutral", "code executed") : chip("cached", "no executed code"), c.seconds ? chip("neutral", fmtDur(c.seconds)) : null],
        body: [c.error ? errorCard({ title: "This check failed", message: c.error }) : compareTable(c.comparisons),
          c.notes ? h("p", { class: "help" }, h("strong", null, "Argon's definition notes: "), c.notes) : null,
          c.code?.length ? h("details", null, h("summary", null, "Argon's executed code"), c.code.map((x) => h("pre", { class: "json mono code" }, x))) : null,
          c.outputs?.length ? h("details", null, h("summary", null, "What the sandbox printed"), c.outputs.map((o) => h("pre", { class: "json mono" }, `[${o.outcome}]\n${o.output}`))) : null,
          c.prompt ? h("details", null, h("summary", null, "What Argon was told (definitions; the table is attached as a CSV)"), h("pre", { class: "json mono" }, c.prompt), c.table_sha256 ? h("p", { class: "help mono" }, `table sha256 ${c.table_sha256.slice(0, 16)}…`) : null) : null] })),
      liveCard, canaryCardEl,
      (R.history || []).length ? card({ title: "Earlier runs", body: h("ul", { class: "plain mono" }, R.history.map((x) => h("li", null, `${fmtUTC(isoOf(x.ts))} · ${x.n_match}/${x.n_compared} match${x.errors?.length ? ` · errors ${x.errors.join(", ")}` : ""}`))) }) : null);
  }
  mountEl(body, skeleton(6));
  load();
}

// ---------------------------------------------------------------- 3. ride reports
const BADGE = { lower_bound: "lower bound", approximate: "approximate", relative_level: "relative level, uncalibrated", excluded_from_averages: "excluded from averages", stress_test: "stress test", single_ride: "single ride" };
function reportClaim(c, lookups, verdictOf, opts = {}) {
  const facts = lookups?.[c.id] || [];
  const v = verdictOf?.[c.id];
  return h("article", { class: ["claim", opts.dropped ? "claim--struck" : "claim--ok"] },
    h("header", { class: "claim__head" }, h("span", { class: "claim__id mono" }, c.id || "?"), c.type ? h("span", { class: ["chip", "chip--type", `chip--t-${c.type}`] }, c.type) : null,
      ...(c.qualifiers || []).map((q) => h("span", { class: "chip chip--relative" }, BADGE[q] || q)),
      opts.dropped ? verdict("DROPPED", "dropped") : verdict("verified", v?.round ? `passed the checker (round ${v.round + 1})` : "passed the checker")),
    h("p", { class: "claim__text" }, h("span", { class: opts.dropped ? "struck" : "" }, c.text || "")),
    facts.length ? h("div", { class: "table-wrap" }, h("table", { class: "facts" }, h("thead", null, h("tr", null, ["written", "stat . field", "stored value", "label", "named by"].map((t) => h("th", null, t)))),
      h("tbody", null, facts.map((f) => h("tr", null, h("td", { class: "mono" }, f.value_as_written ?? ""), h("td", { class: "mono" }, `${f.stat_key}.${f.field || "value"}`),
        h("td", { class: "mono" }, f.found === false ? h("span", { class: "warn" }, "not in the stats file") : `${fmtVal(f.stored)} ${f.unit || ""}`), h("td", null, f.label || ""), h("td", null, f.role ? `“${f.role}”` : "")))))) : null,
    (c.comparisons || []).length ? h("ul", { class: "help" }, c.comparisons.map((cm) => h("li", null, `comparison “${cm.relation_word || cm.relation}”: ${cm.a?.stat_key}.${cm.a?.field} vs ${cm.b?.stat_key}.${cm.b?.field} (stated ${cm.relation}${cm.difference_as_written ? `, difference ${cm.difference_as_written}` : ""})`))) : null,
    (c.cites || []).length ? h("div", { class: "help mono" }, `relies on: ${c.cites.join(", ")}`) : null,
    opts.reasons?.length ? h("ul", { class: "claim__reasons" }, opts.reasons.map((r) => h("li", null, String(r)))) : null);
}

function reportsPanel(host, disposers) {
  const body = h("div", { class: "stack" }), listHost = h("div"), detailHost = h("div", { class: "stack" }), liveHost = h("div", { class: "stack" }), ctlHost = h("div");
  mountEl(host, body);
  body.append(card({ title: "Ride reports: Argon writes, code binds every number", cls: "rough-head", chips: [chip("posthoc"), chip("concept", "DRAFT · PENDING HUMAN APPROVAL")],
    body: [h("p", { class: "lead" }, "Argon reads one ride's derived statistics (QC flags, sync quality, section times, tracker errors, relative levels; no audio, no sensor arrays) and writes 4-8 one-sentence claims. Each claim declares, as data, what every number in it is: the stat, the field, the words naming it. Code checks each number against the stored field (value, sign, unit), recomputes every comparison and requires the qualifiers. A failing claim goes back to Argon once; still failing = dropped and listed."),
      h("p", { class: "help" }, "A report is a draft until a person approves it, on the command line only (python -m agent.ride_report <run> --approve NAME). This page never approves a ride report.")] }),
    ctlHost, liveHost, listHost, detailHost);
  let selected = null, liveLog = [];
  const runner = jobRunner({ label: "Argon writes a ride report (live)", start: "/api/work/reports/start", note: "Gemini, about 40-60 s. Sent: this ride's derived statistics only.",
    onEvent: (ev) => {
      if (ev.type === "log") return;
      liveLog.push(ev);
      if (ev.type === "final" && /upserted/.test(String(ev.atlas || ""))) setAtlasStatus(true);
      if (ev.type === "final" && /Timeout|Connection|ServerSelection/.test(String(ev.atlas || ""))) setAtlasStatus(false);
      drawLive();
    },
    onDone: (end) => { loadList().then(() => { const run = liveLog.find((e) => e.run)?.run; if (end?.status === "done" && run) show(run); }); } });
  disposers.push(() => runner.dispose());
  function drawLive() {
    mountEl(liveHost, liveLog.length ? card({ title: "Live run", level: 3, chips: chip("live"), body: h("ol", { class: "plain livelog" }, liveLog.map((e) => {
      if (e.type === "start") return h("li", null, `started · ${e.model || ""} · thinking ${e.thinking || ""}`);
      if (e.type === "stats") return h("li", null, `${e.n ?? (e.keys || []).length} stats loaded from ${e.file || "the stats file"} (sha ${String(e.sha || "").slice(0, 12)})`);
      if (e.type === "draft") return h("li", null, `round ${(e.round ?? 0) + 1}: Argon drafted ${(e.claims || []).length} claims${e.seconds ? ` in ${fmtDur(e.seconds)}` : ""}`);
      if (e.type === "verdicts") return h("li", null, `round ${(e.round ?? 0) + 1} checker: `, (e.results || []).map((v) => h("span", { class: "chip-row inline" }, v.ok ? verdict("verified", v.id) : verdict("FAILS", v.id))),
        h("ul", { class: "claim__reasons" }, (e.results || []).filter((v) => !v.ok).map((v) => h("li", null, `${v.id}: ${(v.reasons || []).join("; ")}`))));
      if (e.type === "final") return h("li", null, verdict("verified", `${e.verified} verified`), ` · ${e.dropped} dropped · ${e.rounds} round${e.rounds === 1 ? "" : "s"} · ${fmtDur(e.seconds)} · Atlas: ${e.atlas || "?"} · status ${e.review || "pending"}${e.claims_in_range === false ? " · WARNING: fewer than 4 claims verified" : ""}`);
      if (e.type === "error") return h("li", { class: "warn" }, `error: ${e.type_name || e.type}: ${e.message || ""}`);
      return h("li", { class: "muted" }, `${e.type}`);
    })) }) : null);
  }
  async function loadList() {
    let L;
    try { L = await fetchJSON("/api/work/reports"); } catch (e) { mountEl(listHost, errorCard({ title: "Ride reports not available", message: e.message, retry: loadList })); return; }
    const sel = h("select", { "aria-label": "Ride to report on" }, (L.runs || []).map((r) => h("option", { value: r.run }, `${r.run}${r.has_report ? " (has a report)" : ""}`)));
    const can = !MOCK && L.module && L.gemini_api_key && state.online;
    mountEl(ctlHost, card({ title: "Write one live", chips: chip("neutral", "gemini-4-argon · thinking medium"), body: [
      h("div", { class: "row" }, h("label", { class: "field-inline" }, h("span", null, "ride"), sel),
        h("button", { class: "btn", type: "button", disabled: !can, onclick: () => { liveLog = []; drawLive(); runner.run({ run: sel.value }); } }, icon("play", 16), "Write live"),
        !can ? h("span", { class: "help" }, !L.module ? "agent.ride_report not installed." : !L.gemini_api_key ? "No Gemini key on the server." : !state.online ? "Offline." : "") : null),
      h("p", { class: "help" }, "Writing a report replaces that ride's saved report (and its Atlas copy) with a new DRAFT."), runner] }));
    mountEl(listHost, card({ title: "Saved reports", chips: chip("precomputed", "PRECOMPUTED"), body: (L.items || []).length
      ? h("ul", { class: "cached-list" }, L.items.map((it) => h("li", null, h("button", { class: ["cached-item", selected === it.run && "is-selected"], type: "button", onclick: () => show(it.run) },
          h("span", { class: "cached-item__q" }, `${it.run} · ${it.n_verified ?? "?"} verified, ${it.n_dropped ?? "?"} dropped`, it.claims_in_range === false ? h("span", { class: "warn" }, " · fewer than 4 claims") : null),
          h("span", { class: "muted mono" }, `${fmtUTC(it.ts)} · ${it.review?.status === "approved" ? `APPROVED by ${it.review.reviewer}` : "DRAFT, pending human approval"}`)))))
      : emptyState("No ride reports yet."), footer: L.approval }));
    return L;
  }
  async function show(run) {
    selected = run;
    mountEl(detailHost, skeleton(6));
    let R;
    try { R = await fetchJSON(`/api/work/reports/${encodeURIComponent(run)}`); } catch (e) { mountEl(detailHost, errorCard({ title: `No report for ${run}`, message: e.message })); return; }
    const rec = R.record || {}, approved = R.review?.status === "approved";
    mountEl(detailHost, card({ title: `Ride report: ${R.run}`, cls: "stage stage--final",
      chips: [provChip("PRECOMPUTED", rec.ts), approved ? chip("prereg", `APPROVED by ${R.review.reviewer} · ${fmtUTC(R.review.ts)}`) : chip("concept", "DRAFT · PENDING HUMAN APPROVAL"), chip("relative")],
      body: [
        rec.n_claims_ok_range === false ? h("div", { class: "banner banner--inline banner--red" }, icon("warn", 16), h("span", null, `Only ${(rec.verified_claims || []).length} claims passed the checks (4-8 expected): the account of this ride is incomplete.`)) : null,
        h("p", { class: "help" }, `${rec.model || ""} (thinking ${rec.thinking || ""}) · ${rec.schema || ""} · stats ${rec.stats_file || ""} (sha ${rec.stats_sha || "?"}) · ${(R.rounds || []).length} round${(R.rounds || []).length === 1 ? "" : "s"} · ${fmtDur(rec.seconds_total)}`),
        h("div", { class: "stack" }, (rec.verified_claims || []).map((c) => reportClaim(c, R.lookups, R.last_verdicts))),
        (rec.dropped_claims || []).length ? [h("h3", null, `${rec.dropped_claims.length} dropped by the checker (after one retry)`),
          h("div", { class: "stack" }, rec.dropped_claims.map((d) => reportClaim(d.claim || {}, R.lookups, null, { dropped: true, reasons: d.reasons })))] : h("p", { class: "muted" }, "No claim was dropped."),
        h("p", { class: "help" }, "Stored values are looked up by the web app in the ride's stats file, to show what each number is bound to; the check itself was done by agent/ride_report.py (its own trace is in the report below)."),
        R.md ? h("details", null, h("summary", null, `The CLI's own report (${R.run}.md), with its per-number trace`), h("div", { class: "md-card" }, markdown(R.md))) : null],
      footer: R.approval }));
    loadList();
  }
  loadList().then((L) => { const first = L?.items?.[L.items.length - 1]; if (first && !selected) show([...L.items].sort((a, b) => String(b.ts).localeCompare(String(a.ts)))[0].run); });
}

// ---------------------------------------------------------------- screen
const TABS = [{ id: "roughness", label: "TfL roughness charts" }, { id: "recheck", label: "Re-derived numbers" }, { id: "reports", label: "Ride reports" }];
export async function mount(root, ctx) {
  const screen = h("div", { class: "screen work" });
  root.append(screen);
  const panel = h("div", { role: "tabpanel" });
  let disposers = [];
  const q = ctx.query.get("tab");
  let active = TABS.some((t) => t.id === q) ? q : "roughness";
  const load = (id) => {
    disposers.forEach((d) => d()); disposers = [];
    active = id; panel.id = `panel-${id}`; panel.setAttribute("aria-labelledby", `tab-${id}`);
    const host = h("div");
    mountEl(panel, host);
    ({ roughness: roughnessPanel, recheck: recheckPanel, reports: reportsPanel })[id](host, disposers);
  };
  const bar = tabs({ label: "Argon jobs", tabs: TABS, active, onChange: load });
  screen.append(screenHeader({ title: "Argon at work", subtitle: "Three more jobs Argon does for Tubesense. Each is judged by code that is not a model, and each shows what that code rejected.",
    chips: [chip("posthoc"), chip("neutral", "gemini-4-argon · thinking medium")] }), bar, panel,
    whereFrom("GET /api/work/roughness (outputs_posthoc/roughness: roughness.json, roughness.md, HUMAN_CHECK.md, pages/figure1-4.png)", "GET /api/work/recheck (outputs_posthoc/recheck/latest.json)",
      "GET /api/work/reports (outputs_posthoc/ride_reports, ride_stats)", "jobs: agent.roughness --cached, agent.recheck --jsonl / --canary, agent.ride_report <run> --jsonl"));
  load(active);
  return () => disposers.forEach((d) => d());
}
