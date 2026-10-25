// Evidence pack and approval. Claims are re-verified by the current verifier on every load. Approval is a human click:
// the only call that approves lives in the confirm dialog's submit handler below, and nothing else in the app touches it.
import { h, mount as mountEl, clear, fmtNum, fmtUTC, fmtTimeLocal, fixArrow, icon, announce } from "../util.js";
import { postJSON, MOCK, fetchJSON } from "../api.js";
import { state, on, getPack, getCanary } from "../state.js";
import { card, chip, provChip, screenHeader, whereFrom, verdict, claimCard, citationButton, statPill, modal, errorCard, emptyState, skeleton, copyButton, typeLabel, toast, banner } from "../ui.js";
import { markdown } from "../md.js";

// ---------------------------------------------------------------- canary (also used by the demo composite)
export function canaryCard({ auto = false, demo = false } = {}) {
  const out = h("div"), btn = h("button", { class: "btn btn--secondary", type: "button", onclick: run }, icon("check", 16), "Run the verifier on it");
  const text = h("blockquote", { class: "answer answer--seeded" }, "Loading the seeded claim…");
  const body = h("div", { class: "stack" }, h("p", { class: "muted" }, "A deliberately false claim. A verifier that cannot reject this is worthless, so it is tested on every load."), text, h("div", { class: "row" }, btn), out);
  const wrap = card({ title: "Seeded test claim (deliberately false)", chips: chip("seeded"), body, cls: "canary" });
  async function run() {
    btn.disabled = true; mountEl(out, skeleton(2));
    try {
      const c = await getCanary(true);
      text.textContent = c.claim?.text || "";
      mountEl(out, h("div", { class: "canary__result" }, verdict(c.ok ? "verified" : "REJECTED", `Verifier: ${c.ok ? "ACCEPTED (this is a problem)" : "REJECTED"}`), chip("live"),
        h("ul", { class: "claim__reasons" }, (c.reasons || []).map((r) => h("li", null, r))),
        h("p", { class: "muted mono" }, `checked now at ${fmtTimeLocal(c.checked_at)} · verifier ${String(c.verifier_sha || "").slice(0, 12)}`)));
      announce(c.ok ? "The verifier accepted the seeded claim" : "The verifier rejected the seeded claim");
    } catch (e) { mountEl(out, errorCard({ message: e.message, retry: run })); }
    btn.disabled = false;
  }
  getCanary().then((c) => { text.textContent = c.claim?.text || ""; }).catch(() => { text.textContent = "The seeded claim could not be loaded."; });
  if (auto) run();
  return wrap;
}

// ---------------------------------------------------------------- history
function historyCard(hist) {
  const steps = hist.steps || [];
  const li = (s) => {
    if (s.kind === "draft") return h("li", { class: "tl tl--draft" }, h("strong", null, s.label), s.ts ? h("span", { class: "muted mono" }, ` ${fmtUTC(s.ts)}`) : null,
      h("p", null, `${s.verified} verified, ${s.dropped} dropped. `, (s.rounds || []).map((r) => `round ${r.round + 1}: ${r.proposed} proposed, ${r.rejected} rejected by the verifier`).join("; ")));
    if (s.kind === "review") return h("li", { class: "tl tl--review" }, h("strong", null, s.label), h("p", null, `${s.n} findings (${s.major} major, ${s.minor} minor). Reviewed by: ${s.reviewer || "Claude Code review agents run by the developer, against the full sources"}.`),
      (s.examples || []).length ? h("ul", { class: "plain" }, s.examples.map((x) => h("li", null, h("span", { class: "chip chip--cached" }, `${x.severity} · ${x.claim_id}`), " ", x.problem))) : null);
    return h("li", { class: "tl tl--final" }, h("strong", null, s.label), s.ts ? h("span", { class: "muted mono" }, ` ${fmtUTC(s.ts)}`) : null,
      h("p", null, `${s.verified} verified, ${s.dropped} dropped.`, s.revision ? ` Targeted revision of the ${fmtUTC(s.revision.of)} draft against ${s.revision.n_findings} review findings.` : ""));
  };
  return card({ title: "How this pack got here", body: steps.length ? h("ol", { class: "timeline" }, steps.map(li)) : emptyState("No draft history files found."), cls: "history" });
}

// ---------------------------------------------------------------- claims list
function claimsSection(P) {
  const types = ["all", ...new Set(P.claims.map((c) => c.type_label))];
  const secs = ["all", ...new Set(P.claims.map((c) => c.section_title))];
  const f = { type: "all", sec: "all" };
  const host = h("div");
  const chips = (list, key) => h("div", { class: "row filter" }, list.map((v) => h("button", { type: "button", class: "btn btn--small btn--secondary", "aria-pressed": String(f[key] === v), onclick: () => { f[key] = v; paint(); } }, v === "all" ? `all ${key === "type" ? "types" : "sections"}` : v)));
  const typeBar = h("div"), secBar = h("div");
  function paint() {
    mountEl(typeBar, chips(types, "type")); mountEl(secBar, chips(secs, "sec"));
    const list = P.claims.filter((c) => (f.type === "all" || c.type_label === f.type) && (f.sec === "all" || c.section_title === f.sec));
    mountEl(host,
      h("div", { class: "table-wrap only-desktop" }, h("table", { class: "claims-table" },
        h("thead", null, h("tr", null, ["id", "type", "section", "claim", "sources", "stats", "check"].map((c) => h("th", null, c)))),
        h("tbody", null, list.map((c) => h("tr", null,
          h("td", { class: "mono" }, c.id), h("td", null, h("span", { class: ["chip", "chip--type", `chip--t-${c.type}`] }, typeLabel(c))),
          h("td", { class: "muted" }, c.section_title), h("td", { class: "claim-cell" }, c.text),
          h("td", null, h("div", { class: "claim__cites" }, c.citations.map(citationButton))),
          h("td", null, h("div", { class: "claim__stats" }, c.stats.map(statPill))),
          h("td", null, c.check.ok ? verdict("verified", "verified now") : [verdict("FAILS"), h("ul", { class: "claim__reasons" }, c.check.reasons.map((r) => h("li", null, r)))])))))),
      h("div", { class: "stack only-phone" }, list.map((c) => claimCard(c, c.check))),
      list.length ? null : emptyState("No claims match this filter."));
  }
  paint();
  return card({ title: `All ${P.claims.length} claims`, chips: chip("live", "LIVE verifier"), body: [typeBar, secBar, host], cls: "claims" });
}

// ---------------------------------------------------------------- screen
export async function mount(root, ctx) {
  const screen = h("div", { class: "screen pack" });
  root.append(screen);
  const disposers = [];
  const head = h("div"), body = h("div", { class: "stack" });
  screen.append(head, body, whereFrom("GET /api/pack (agent.approve.load, agent.verify.verify_all)", "GET /api/pack/history", "GET /api/source/{chunk_id}", "GET /api/canary", "outputs_posthoc/investigation.json", "outputs_posthoc/approvals.jsonl"));
  mountEl(head, screenHeader({ title: "Evidence pack", subtitle: "A cited pack for Elly Baker AM's office. Code checks every quote and number; a human approves a draft. Nothing is sent.", chips: [chip("precomputed", "PRECOMPUTED")] }));
  mountEl(body, card({ body: skeleton(8) }));
  let P, hist;
  try { [P, hist] = await Promise.all([getPack(true), fetchJSON("/api/pack/history").catch(() => ({ steps: [] }))]); }
  catch (e) { mountEl(body, errorCard({ title: "The pack cannot be shown", message: e.message, retry: () => { disposers.forEach((d) => d()); mount(root, ctx); } })); return () => {}; }
  clear(root).append(screen);

  const statusChip = () => {
    const s = P.status || {}, l = s.latest;
    if (s.state === "approved") return chip("live", `APPROVED AS DRAFT by ${l.approver} · ${fmtUTC(l.ts)}`);
    if (s.state === "rejected") return chip("seeded", `REJECTED · ${fmtUTC(l.ts)}${l.reason ? ` · ${l.reason}` : ""}`);
    return chip("cached", "AWAITING HUMAN APPROVAL");
  };
  const headChips = h("div", { class: "chip-row" });
  const renderHead = () => mountEl(head, screenHeader({ title: "Evidence pack", subtitle: "A cited pack for Elly Baker AM's office. Code checks every quote and number; a human approves a draft. Nothing is sent.",
    chips: [provChip("PRECOMPUTED", P.generated, `${P.model} (thinking ${P.thinking})`), chip("live", "LIVE verifier"), statusChip(), MOCK ? chip("mock") : null] }));
  renderHead();
  const failing = P.claims.filter((c) => !c.check.ok);
  const cited = new Set(P.claims.flatMap((c) => c.citations.map((x) => x.chunk_id)));

  // ---- summary + headline
  const summary = card({ title: "Summary", cls: "summary", body: [
    h("p", { class: "lead" }, h("strong", null, `${P.n_verified - failing.length} of ${P.n_verified} claims verified now`), ` · ${P.n_dropped} dropped · ${(P.asks || []).length} information requests`),
    h("dl", { class: "kv" }, h("dt", null, "pack id"), h("dd", { class: "mono" }, P.pack_id), h("dt", null, "generated"), h("dd", null, `${fmtUTC(P.generated)} by ${P.model} (thinking ${P.thinking})`),
      h("dt", null, "verifier"), h("dd", { class: "mono" }, `${String(P.verifier_sha_now).slice(0, 12)}${P.verifier_sha === P.verifier_sha_now ? " (same as when generated)" : ""}`)),
    P.verifier_sha !== P.verifier_sha_now ? h("div", { class: "banner banner--inline banner--amber" }, icon("warn", 16), "The verifier has changed since the pack was generated; the checks below are re-run with the current verifier.") : null,
    failing.length ? h("div", { class: "banner banner--inline banner--red" }, icon("warn", 16), `${failing.length} ${failing.length === 1 ? "claim fails" : "claims fail"} the verifier now. Approval is refused until ${failing.length === 1 ? "it is" : "they are"} fixed.`) : null,
    h("p", null, "Draft for Elly Baker AM's office. Nothing is sent: approval creates a signed draft."),
  ] });
  const headlines = (P.headline_ids || []).map((id) => P.claims.find((c) => c.id === id)).filter(Boolean);
  const headlineCard = card({ title: "Headline claims", body: h("div", { class: "stack" }, headlines.map((c) => claimCard(c, c.check))), cls: "headlines" });
  const asks = card({ title: "Information requests", body: (P.asks || []).length ? h("ol", { class: "asks" }, P.asks.map((a) => h("li", null, h("strong", null, a.request), h("p", { class: "muted" }, a.why)))) : emptyState("None."), cls: "asks-card" });
  const md = h("details", { class: "card fullpack" }, h("summary", null, "Full pack (rendered Markdown)"), h("div", { class: "card__body" }, markdown(P.md || "")));

  // ---- approval form (human only)
  const form = approvalForm(P, cited, async () => { P = await getPack(true); renderHead(); renderApprovals(); }, () => renderApprovals());
  const approvalsHost = h("div");
  function renderApprovals() {
    mountEl(approvalsHost, (P.approvals || []).length ? card({ title: "Existing records for this pack", body: h("ul", { class: "plain" }, P.approvals.map((r) =>
      h("li", null, verdict(r.decision === "approved" ? "ACCEPTED" : "REJECTED", r.decision), ` ${r.approver ? r.approver + " · " : ""}${fmtUTC(r.ts)}`, r.signature ? h("span", { class: "mono muted" }, ` sig ${r.signature.slice(0, 16)}…`) : null, r.reason ? ` · ${r.reason}` : null))) })
      : emptyState("No approval yet: approval needs a human."));
  }
  renderApprovals();

  const testBanner = P.posthoc_out_is_default === false ? banner("red", `TEST MODE: approvals go to ${P.posthoc_out} and are not mirrored to Atlas`) : null;
  const left = h("div", { class: "stack" }, summary, headlineCard, claimsSection(P), asks, historyCard(hist), canaryCard(), md);
  const right = h("aside", { class: "pack-side", "aria-label": "Approval" }, form.el, approvalsHost);
  mountEl(body, testBanner, h("div", { class: "pack-layout" }, left, right));
  if (ctx.demo) { screen.classList.add("is-demo"); setTimeout(() => form.el.scrollIntoView({ block: "center", behavior: "smooth" }), 300); }
  disposers.push(on("source-opened", () => form.refreshCount()));
  return () => disposers.forEach((d) => d());
}

function approvalForm(P, cited, onDone, onChange) {
  const boxes = P.checklist.map((t, i) => h("label", { class: "check check--row" }, h("input", { type: "checkbox", name: `c${i}`, onchange: sync }), h("span", null, t)));
  const counter = h("p", { class: "help", id: "src-count" });
  const who = h("input", { type: "text", id: "approver", name: "approver", maxlength: 80, autocomplete: "name", "aria-required": "true", oninput: sync });
  const approveBtn = h("button", { class: "btn", type: "submit", disabled: true }, icon("check", 16), "Approve as draft");
  const msg = h("div", { role: "status", "aria-live": "polite" });
  const rejectBox = h("div", { hidden: true, class: "stack" });
  const reason = h("textarea", { rows: 3, "aria-label": "Reason for rejecting", placeholder: "Why is this pack not ready? (required)" });
  const rejectGo = h("button", { class: "btn btn--danger", type: "button", onclick: doReject }, "Reject pack");
  rejectBox.append(h("label", { class: "field" }, "Reason", reason), rejectGo);
  const el = h("form", { class: "card approval", novalidate: true, onsubmit: (e) => { e.preventDefault(); confirm(); } },
    h("h2", { class: "card__title" }, "Human approval"),
    h("p", { class: "help" }, "Only a person can approve. Tick every item yourself; the pack is re-verified when you confirm."),
    h("fieldset", { class: "checklist" }, h("legend", { class: "sr-only" }, "Approval checklist"), boxes), counter,
    h("label", { class: "field" }, "Approver name", who),
    h("div", { class: "row" }, approveBtn, h("button", { class: "btn btn--danger btn--secondary", type: "button", onclick: () => { rejectBox.hidden = !rejectBox.hidden; if (!rejectBox.hidden) reason.focus(); } }, "Reject…")),
    rejectBox, msg);
  function refreshCount() {
    const n = [...cited].filter((id) => state.sourcesOpened.has(id)).length;
    counter.textContent = `You opened ${n} of ${cited.size} cited sources in this session (this does not block approval).`;
  }
  function allTicked() { return boxes.every((b) => b.querySelector("input").checked); }
  function sync() { approveBtn.disabled = !(allTicked() && who.value.trim().length > 0); }
  refreshCount();

  function confirm() {
    if (approveBtn.disabled) return;
    const name = who.value.trim().slice(0, 80);
    let m;
    const submitBtn = h("button", { class: "btn", type: "submit" }, "Approve");   // focus starts on Cancel: one stray Enter or Space must not sign
    const body = h("form", { onsubmit: async (e) => {
      e.preventDefault(); submitBtn.disabled = true;
      try {
        const checked = boxes.map((b, i) => (b.querySelector("input").checked ? i : -1)).filter((i) => i >= 0);
        const r = await postJSON('/api/pack/approve', { pack_id: P.pack_id, approver: name, checked });
        m.close(); showOk(r); onDone?.();
      } catch (err) { m.close(); showErr(err); }
    } },
      h("p", null, `Approve pack ${P.pack_id} as a DRAFT under the name '${name}'? This writes a signed record (SHA-256 over pack, name, time, verifier version) ${P.mirror === false ? "to the test output folder only (this is a test server: nothing goes to Atlas)" : "locally and to Atlas \`approvals\`"}. Nothing is sent to anyone.`),
      h("div", { class: "dialog__actions" }, h("button", { class: "btn btn--secondary", type: "button", autofocus: true, onclick: () => m.close() }, "Cancel"), submitBtn));
    m = modal({ title: "Confirm approval", body, labelledby: "approve-title" });
  }
  function showOk(r) {
    mountEl(msg, h("div", { class: "approval__ok" }, verdict("ACCEPTED", "approved as a draft"), h("p", null, "Signature (SHA-256):"),
      h("div", { class: "row" }, h("code", { class: "mono break" }, r.signature), copyButton(r.signature)),
      h("dl", { class: "kv" }, h("dt", null, "draft file"), h("dd", { class: "mono break" }, r.draft), h("dt", null, "Atlas"), h("dd", null, r.atlas)),
      h("p", { class: "strong" }, "Nothing was sent.")));
    announce("Approved as a draft. Nothing was sent.");
  }
  function showErr(e) {
    const b = e.body || {};
    if (e.status === 403) mountEl(msg, errorCard({ title: "Not allowed from here", message: "Approval is only allowed from the laptop running the server." }));
    else if (e.status === 400) mountEl(msg, errorCard({ title: "Approval is incomplete", message: b.pack_changed ? "The pack changed since you opened this page. Reload and read it again." : b.approver_missing ? "The approver name is missing." : (b.message || e.message),
      }), (b.missing || []).length ? h("ul", { class: "claim__reasons" }, b.missing.map((t) => h("li", null, t))) : null);
    else if (e.status === 409) mountEl(msg, errorCard({ title: "Approval refused", message: (n => n ? `${n} ${n === 1 ? "claim no longer verifies" : "claims no longer verify"}; fix and re-check before approving.` : (b.message || e.message))((b.fails || []).length) }), h("ul", { class: "claim__reasons" }, (b.fails || []).map((f) => h("li", null, h("strong", { class: "mono" }, f.id), `: ${(f.reasons || []).join("; ")}`))));
    else mountEl(msg, errorCard({ title: "Approval failed", message: e.message }));
  }
  async function doReject() {
    const why = reason.value.trim();
    if (!why) { toast("A reason is required to reject."); reason.focus(); return; }
    try { await postJSON("/api/pack/reject", { pack_id: P.pack_id, reason: why }); mountEl(msg, h("div", { class: "approval__ok" }, verdict("REJECTED", "rejection recorded"))); rejectBox.hidden = true; onDone?.(); }
    catch (e) { showErr(e); }
  }
  return { el, refreshCount };
}
