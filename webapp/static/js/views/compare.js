// Why Argon: the same investigator task run with gemini-4-argon and gemini-3.8-flash, judged by the same deterministic verifier.
// Renders outputs_posthoc/model_comparison.json when it has the expected shape, always the Markdown protocol and results.
import { h, mount as mountEl, fmtNum, fmtUTC } from "../util.js";
import { fetchJSON } from "../api.js";
import { card, chip, screenHeader, whereFrom, errorCard, emptyState, skeleton } from "../ui.js";
import { markdown } from "../md.js";

const mr = (x, nd = 0) => (!x || typeof x !== "object" || x.median === undefined ? "n/a" : x.min === x.max ? fmtNum(x.median, nd) : `${fmtNum(x.median, nd)} [${fmtNum(x.min, nd)}–${fmtNum(x.max, nd)}]`);
const PRIMARY = [["r0_rate", "round-0 rejection rate", 2], ["final_dropped", "final dropped claims", 0], ["converged", "converged within the round budget", -1], ["seconds_total", "total seconds, wall clock", 0]];
const SECOND = [["r0_claims", "round-0 claims proposed", 0], ["r0_rejected", "round-0 claims rejected", 0], ["rounds_used", "rounds used", 0], ["final_verified", "final verified claims", 0], ["asks", "information requests", 0],
  ["seconds_round0", "seconds, round 0", 0], ["output_tokens", "output tokens per round", 0], ["framing_major", "framing issues, major (single judge)", 1], ["framing_minor", "framing issues, minor (single judge)", 1], ["framing_per_claim", "framing issues per verified claim (single judge)", 2]];

function isShape(j) { return j && typeof j.summary === "object" && Array.isArray(j.runs) && Object.values(j.summary).every((s) => s && typeof s === "object"); }

function summaryCard(j) {
  const models = Object.keys(j.summary);
  const cell = (m, k, nd) => (k === "converged" ? `${j.summary[m].converged}/${j.summary[m].runs} runs` : mr(j.summary[m][k], nd));
  const rows = (list, cls) => list.filter(([k]) => models.some((m) => j.summary[m][k] !== undefined && j.summary[m][k] !== null)).map(([k, label, nd]) =>
    h("tr", { class: cls }, h("td", null, label), models.map((m) => h("td", { class: "mono" }, cell(m, k, nd)))));
  return card({ title: "Primary and secondary measures", chips: chip("posthoc", "medians [min–max]"), body: h("div", { class: "table-wrap" }, h("table", { class: "compare" },
    h("thead", null, h("tr", null, h("th", null, "metric"), models.map((m) => h("th", null, m)))),
    h("tbody", null, h("tr", { class: "grouprow" }, h("td", { colspan: models.length + 1 }, "Primary (fixed in the protocol before any run)")), rows(PRIMARY, "primary"),
      h("tr", { class: "grouprow" }, h("td", { colspan: models.length + 1 }, "Secondary")), rows(SECOND, "")))),
    footer: j.all_reverify_match ? "Every stored round was re-verified now and matches the recorded verdicts." : "Re-verification did not match the recorded verdicts for every round: see the Markdown." });
}

function reasonsCard(j) {
  const models = Object.keys(j.summary);
  const cats = [...new Set(models.flatMap((m) => Object.keys(j.summary[m].r0_reasons_total || {})))].sort();
  if (!cats.length) return null;
  return card({ title: "Why round-0 claims were rejected", body: h("div", { class: "table-wrap" }, h("table", null,
    h("thead", null, h("tr", null, h("th", null, "verifier reason"), models.map((m) => h("th", null, m)))),
    h("tbody", null, cats.map((c) => h("tr", null, h("td", null, c), models.map((m) => h("td", { class: "mono" }, String(j.summary[m].r0_reasons_total?.[c] ?? 0)))))))),
    footer: "Counts over all runs; one claim can have several reasons. Rejections are the point: the verifier is what makes either model's output usable." });
}

function runsCard(j) {
  return card({ title: `Every run (${j.runs.length})`, body: h("div", { class: "table-wrap" }, h("table", null,
    h("thead", null, h("tr", null, ["slot", "model", "status", "r0 claims", "r0 rejected", "rounds", "converged", "verified", "dropped", "s total", "framing maj/min"].map((t) => h("th", null, t)))),
    h("tbody", null, j.runs.map((r) => h("tr", null, h("td", { class: "mono" }, r.slot), h("td", null, r.model), h("td", null, r.status), h("td", { class: "mono" }, r.r0_claims ?? ""), h("td", { class: "mono" }, r.r0_rejected ?? ""),
      h("td", { class: "mono" }, r.rounds_used ?? ""), h("td", null, r.converged ? "yes" : "no"), h("td", { class: "mono" }, r.final_verified ?? ""), h("td", { class: "mono" }, r.final_dropped ?? ""),
      h("td", { class: "mono" }, r.seconds_total !== undefined ? fmtNum(r.seconds_total, 0) : ""), h("td", { class: "mono" }, r.framing ? `${r.framing.major}/${r.framing.minor}` : "")))))) });
}

function genericCard(j) {
  const rows = Object.entries(j).filter(([, v]) => v === null || ["string", "number", "boolean"].includes(typeof v));
  return card({ title: "model_comparison.json (unrecognised shape: top-level values)", body: rows.length ? h("dl", { class: "kv" }, rows.flatMap(([k, v]) => [h("dt", null, k), h("dd", { class: "mono" }, String(v))])) : emptyState("No scalar values at the top level; see the Markdown below.") });
}

export async function mount(root, ctx) {
  const screen = h("div", { class: "screen compare" });
  root.append(screen);
  const head = h("div"), body = h("div", { class: "stack" });
  screen.append(head, body, whereFrom("GET /api/compare", "outputs_posthoc/model_comparison.json", "outputs_posthoc/model_comparison.md (protocol written before any run)", "agent/compare_models.py"));
  mountEl(head, screenHeader({ title: "Why Argon", subtitle: "The same task, run with gemini-4-argon and gemini-3.8-flash, scored by the same verifier. If Flash does as well as Argon, this page says so (protocol rule).", chips: [chip("posthoc")] }));
  mountEl(body, card({ body: skeleton(6) }));
  let C;
  try { C = await fetchJSON("/api/compare"); }
  catch (e) { mountEl(body, errorCard({ message: e.message, title: "Comparison not available", retry: () => mount(root, ctx) })); return; }
  if (!C.available) { mountEl(body, emptyState("The comparison has not been run yet. Protocol written before any run: outputs_posthoc/model_comparison.md")); return; }
  const j = C.json, n = isShape(j) ? Math.max(...Object.values(j.summary).map((s) => s.runs)) : null;
  mountEl(head, screenHeader({ title: "Why Argon", subtitle: "The same task, run with gemini-4-argon and gemini-3.8-flash, scored by the same verifier. If Flash does as well as Argon, this page says so (protocol rule).",
    chips: [chip("posthoc"), chip("neutral", n ? `n = ${n} per model, one task` : "one task"), j?.ts ? chip("neutral", `generated ${fmtUTC(j.ts)}`) : null] }));
  mountEl(body,
    j ? (isShape(j) ? [summaryCard(j), reasonsCard(j), runsCard(j)] : genericCard(j)) : emptyState("model_comparison.json is not there yet; the Markdown protocol is shown below."),
    C.md ? card({ title: "Protocol and results (Markdown)", body: markdown(C.md), cls: "md-card", footer: C.md_mtime ? `file written ${fmtUTC(C.md_mtime)}` : null }) : null,
    h("p", { class: "help" }, "n is small and this is one task on one day: differences are described, not tested. The single-judge framing check is labelled as such."));
}
