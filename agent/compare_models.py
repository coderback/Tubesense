"""Why Argon? The same investigator task run with gemini-4-argon and gemini-3.8-flash, scored by the verifier.

Post-hoc comparison; the protocol is fixed at the top of outputs_posthoc/model_comparison.md. Each run is a
fresh `agent.investigator --model M --out outputs_posthoc/compare/<slot> --no-atlas --thinking medium` in a
subprocess, one at a time, in counterbalanced ABBA order (argon, flash, flash, argon, ...). The prompt is
byte-identical across runs (same corpus, stats and CACHED retrieval hits), and nothing is written to Atlas
or to outputs_posthoc/investigation.json. Transport errors (429/5xx/timeouts) are retried; schema/parse
failures count as model outcomes. `summarise` re-runs the verifier on every stored round, counts rejection
reasons by category, and writes outputs_posthoc/model_comparison.json plus the Results tables of the .md.
`blind` exports the final verified claims under shuffled labels, with claim ids renumbered K1..Kn (and the
asks' references rewritten; ids of dropped claims and unknown id-like tokens in the asks become K?) so the id style
does not reveal the model, for the single-judge framing check,
whose judgements go in compare/framing_judgements.json. Write the key (and the id map beside it) somewhere
the judge does not read (--key PATH) and pass the same --key to `summarise`. `blind` refuses to overwrite an
export or key once framing_judgements.json exists, and `summarise` stops if the judgements do not match the
runs they are mapped to (claims/asks reviewed vs the run's final verified claims/asks). `freeze` copies the
protocol section of the .md to compare/protocol_frozen.md once; `summarise` records its sha256 and whether the
.md still matches it. The recorded experiment is 3 ABBA blocks (12 runs), hence the --blocks default of 3.
Run: PYTHONPATH=. .venv/Scripts/python -m agent.compare_models run [--blocks 3]
     PYTHONPATH=. .venv/Scripts/python -m agent.compare_models blind [--blocks 3] [--out-dir DIR] [--key PATH]
     PYTHONPATH=. .venv/Scripts/python -m agent.compare_models summarise [--blocks 3] [--key PATH]
     PYTHONPATH=. .venv/Scripts/python -m agent.compare_models freeze
"""
import argparse
import hashlib
import json
import os
import random
import re
import statistics as st
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import verify as V
from .investigator import as_verifier

MODELS = {"argon": "gemini-4-argon", "flash": "gemini-3.8-flash"}
DIR = V.ROOT / "outputs_posthoc" / "compare"
JSON_OUT = V.ROOT / "outputs_posthoc" / "model_comparison.json"
MD = V.ROOT / "outputs_posthoc" / "model_comparison.md"
PROTOCOL = DIR / "protocol_frozen.md"
SJ = "unblinded single judge"
TRANSIENT = re.compile(r"\b(429|500|502|503|504)\b|UNAVAILABLE|RESOURCE_EXHAUSTED|DEADLINE|timed? ?out|"
                       r"Connection|RemoteProtocol|ReadError", re.I)
API_TYPES = {"ServerError", "ClientError", "APIError", "ReadTimeout", "ConnectError", "ConnectTimeout",
             "RemoteProtocolError", "ReadError", "TimeoutException"}


def slots(blocks: int) -> list[tuple[str, str]]:
    order = [m for _ in range(blocks) for m in ("argon", "flash", "flash", "argon")]
    return [(f"{i + 1:02d}_{m}", m) for i, m in enumerate(order)]


def check_slots(blocks: int):
    """Stop if slot folders exist beyond the requested blocks (a stale --blocks would silently drop runs)."""
    have = {p.name for p in DIR.iterdir() if p.is_dir() and re.fullmatch(r"\d\d_(argon|flash)", p.name)}
    extra = sorted(have - {s for s, _ in slots(blocks)})
    if extra:
        raise SystemExit(f"slot folders {extra} exist beyond --blocks {blocks}: pass --blocks {-(-len(have) // 4)}")


def load(slot: str) -> dict | None:
    p = DIR / slot / "investigation.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


# --------------------------------------------------------------------------- run
def run_slot(slot: str, model: str, retries: int = 2) -> str:
    d = DIR / slot
    d.mkdir(parents=True, exist_ok=True)
    for attempt in range(retries + 1):
        cmd = [sys.executable, "-m", "agent.investigator", "--model", MODELS[model], "--out", str(d),
               "--no-atlas", "--thinking", "medium"]
        t = time.time()
        try:
            p = subprocess.run(cmd, cwd=V.ROOT, env={**os.environ, "PYTHONPATH": str(V.ROOT)},
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1200)
            code, log = p.returncode, p.stdout + "\n--- stderr ---\n" + p.stderr[-4000:]
        except subprocess.TimeoutExpired:
            code, log = -9, "harness timeout (1200 s)"
        with open(d / "log.txt", "a", encoding="utf-8") as f:
            f.write(f"=== attempt {attempt} {datetime.now(timezone.utc).isoformat(timespec='seconds')} "
                    f"exit {code} {time.time() - t:.0f} s\n{log}\n")
        rec = load(slot)
        if code == 0:
            return "ok"
        err = (rec or {}).get("error") or {}
        transient = code == -9 or err.get("type") in API_TYPES and TRANSIENT.search(err.get("message", "")) or \
            (not err and TRANSIENT.search(log))
        if err and not transient and err.get("type") not in API_TYPES:
            return f"model failure: {err['type']} in round {err['round']}"
        if not transient:
            raise SystemExit(f"{slot}: non-transient harness/API error, stopping (see {d / 'log.txt'})")
        if rec:
            (d / "investigation.json").rename(d / f"investigation_apierr{attempt}.json")
        print(f"  {slot}: transient error, retry {attempt + 1}", flush=True)
        time.sleep(30)
    return "api failure (retries exhausted)"


def cmd_run(a):
    for slot, model in slots(a.blocks):
        rec = load(slot)
        if rec is not None:
            print(f"{slot}: already on record ({'error' if rec.get('error') else 'ok'}), skipped")
            continue
        print(f"{slot}: {MODELS[model]} ...", flush=True)
        t = time.time()
        status = run_slot(slot, model)
        print(f"{slot}: {status} ({time.time() - t:.0f} s)", flush=True)


# --------------------------------------------------------------------------- metrics
def category(reason: str) -> str:
    for pre, cat in (("banned:", "banned phrasing"), ("derived:", "formula"), ("numbers not supported", "unsupported number"),
                     ("quote not found", "quote not found"), ("quote too short", "quote too short"),
                     ("unknown chunk_id", "unknown chunk"), ("unknown stat key", "unknown stat key"),
                     ("a sourced claim needs", "missing citation/stat"), ("a measured claim needs", "missing citation/stat"),
                     ("type must be", "bad type")):
        if reason.startswith(pre):
            return cat
    return "other"


def run_metrics(slot: str, model: str, rec: dict) -> dict:
    rounds = rec.get("rounds", [])
    chunks, stats = V.load_chunks(), V.load_stats()
    m = {"slot": slot, "model": MODELS[model], "ts": rec.get("ts"),
         "model_version": sorted({r["usage"].get("model_version") for r in rounds} - {None}),
         "status": "parse/model failure" if rec.get("error") else "ok", "error": rec.get("error"),
         "rounds_used": len(rounds) + (1 if rec.get("error") else 0),
         "seconds_per_round": [r["seconds"] for r in rounds], "seconds_total": rec.get("seconds_total"),
         "tokens": [{k: r["usage"].get(k) for k in ("prompt", "output", "thoughts")} for r in rounds],
         "per_round": [], "reverify_match": True}
    for r in rounds:
        claims = r["investigation"]["claims"]
        again = [V.verify_claim(as_verifier(c), chunks, stats) for c in claims]
        m["reverify_match"] &= [x["ok"] for x in again] == [x["ok"] for x in r["verification"]]
        bad = [v for v in r["verification"] if not v["ok"]]
        ids = [c["id"] for c in claims]
        m["per_round"].append({
            "claims": len(claims), "rejected": len(bad), "rejected_ids": [b["id"] for b in bad],
            "reasons": dict(Counter(category(x) for b in bad for x in b["reasons"])),
            "formula_reasons": [x for b in bad for x in b["reasons"] if x.startswith("derived:")],
            "types": dict(Counter(c["type"] for c in claims)), "duplicate_ids": len(ids) - len(set(ids)),
            "asks": len(r["investigation"]["asks"])})
    if rounds:
        r0, last = m["per_round"][0], m["per_round"][-1]
        m |= {"r0_claims": r0["claims"], "r0_rejected": r0["rejected"],
              "r0_rate": round(r0["rejected"] / r0["claims"], 3) if r0["claims"] else None,
              "r0_reasons": r0["reasons"], "converged": not rec.get("error") and last["rejected"] == 0}
    if not rec.get("error"):
        fin = {c["id"] for c in rec["verified_claims"]}
        last_ids = {c["id"] for c in rounds[-1]["investigation"]["claims"]}
        r0_bad = m["per_round"][0]["rejected_ids"]
        m |= {"final_verified": len(rec["verified_claims"]), "final_dropped": len(rec["dropped_claims"]),
              "asks": len(rec["asks"]), "headline_kept": len(rec["headline_claim_ids"]),
              "headline_asked": len(rounds[-1]["investigation"]["headline_claim_ids"]),
              "verified_types": dict(Counter(c["type"] for c in rec["verified_claims"])),
              "verified_sections": dict(Counter(c["section"] for c in rec["verified_claims"])),
              "r0_rejected_fate": dict(Counter("fixed" if i in fin else "removed" if i not in last_ids else "dropped"
                                               for i in r0_bad)),
              "dropped": [{"id": d["claim"]["id"], "reasons": d["reasons"]} for d in rec["dropped_claims"]],
              "content": content(rec["verified_claims"]),
              "integrity": {k: rec.get(k) for k in ("verifier_sha", "corpus_hash", "stats_hash", "retrieval_source")}
              | {"retrieval_hash": hashlib.sha256(json.dumps(rec.get("retrieval"), sort_keys=True).encode()).hexdigest()[:16]}}
    return m


KEY_STATS = {"loudness.verdict": "pre-registered loudness verdict stated", "niosh.WB.MLE-LST": "both NIOSH trips"}
KEY_CHUNKS = {"MQT-2026-2156#002": "the Mayor's 16 Jul 2026 transparency promise", "MQT-2026-2156#001": "Baker's refused requests"}


def content(claims: list[dict]) -> dict:
    """Exploratory (not in the protocol): how much each verified claim says and how many sources it joins."""
    docs = [{c["chunk_id"].split("#")[0] for c in cl["citations"]} for cl in claims]
    n = len(claims) or 1
    return {"words": sum(len(c["text"].split()) for c in claims),
            "words_per_claim": round(sum(len(c["text"].split()) for c in claims) / n, 1),
            "numbers_per_claim": round(sum(len(V.numbers(c["text"])) for c in claims) / n, 2),
            "citations_per_claim": round(sum(len(c["citations"]) for c in claims) / n, 2),
            "distinct_chunks": len({q["chunk_id"] for c in claims for q in c["citations"]}),
            "distinct_documents": len({q["chunk_id"].split("#")[0] for c in claims for q in c["citations"]}),
            "multi_document_claims": sum(len(d) >= 2 for d in docs),
            "issues_per_100_words": None,
            "sources": sorted({q["chunk_id"].split("#")[0] for c in claims for q in c["citations"]} |
                              {k for c in claims for k in c["stat_keys"] if k in KEY_STATS} |
                              {q["chunk_id"] for c in claims for q in c["citations"] if q["chunk_id"] in KEY_CHUNKS}),
            "claims_by_type": dict(Counter(c["type"] for c in claims))}


def med_range(xs: list) -> dict | None:
    xs = [x for x in xs if x is not None]
    return {"median": st.median(xs), "min": min(xs), "max": max(xs), "n": len(xs)} if xs else None


def fmt(mr: dict | None, nd: int = 0) -> str:
    if not mr:
        return "n/a"
    f = (lambda x: f"{x:.1f}" if not nd and x % 1 == 0.5 else f"{x:.{nd}f}")  # x.5 must not round to even
    return f"{f(mr['median'])} [{f(mr['min'])}-{f(mr['max'])}]"


def framing(key: dict) -> dict:
    p = DIR / "framing_judgements.json"
    if not p.exists():
        return {}
    j = json.loads(p.read_text(encoding="utf-8"))
    if set(j["runs"]) != set(key):
        raise SystemExit(f"judged labels {sorted(j['runs'])} differ from the key's labels {sorted(key)}: wrong --key?")
    out = {}
    for label, v in j["runs"].items():
        iss = v["issues"]
        out[key[label]] = {"label": label, "claims_reviewed": v["claims_reviewed"], "asks_reviewed": v["asks_reviewed"],
                           "major": sum(i["severity"] == "major" for i in iss), "minor": sum(i["severity"] == "minor" for i in iss),
                           "by_category": dict(Counter(i["category"] for i in iss)),
                           "by_rule": dict(Counter(i["rule"] for i in iss)), "issues": iss}
    return {"judge": j.get("judge"), "written": j.get("written"), "method": j.get("method"),
            "blinding_assessment": j.get("blinding_assessment"), "rescored_after_review": j.get("rescored_after_review"),
            "rules_clarified_after_review": j.get("rules_clarified_after_review"),
            "rules": j.get("rules"), "runs": out}


def check_framing(fr: dict, runs: list[dict]):
    """The judgements must describe the runs the key maps them to; otherwise every framing number is wrong."""
    by_slot = {r["slot"]: r for r in runs}
    for slot, f in fr.get("runs", {}).items():
        r = by_slot.get(slot)
        if r is None or r["status"] != "ok":
            raise SystemExit(f"framing judgements map {f['label']} to {slot}, which has no completed run")
        if (f["claims_reviewed"], f["asks_reviewed"]) != (r["final_verified"], r["asks"]):
            raise SystemExit(f"{slot}: judged {f['claims_reviewed']} claims / {f['asks_reviewed']} asks ({f['label']}), "
                             f"run has {r['final_verified']} / {r['asks']}: key and judgements do not match the runs")


def protocol_check() -> dict:
    head = MD.read_text(encoding="utf-8").partition("## Results")[0].rstrip("\n")
    frozen = PROTOCOL.read_text(encoding="utf-8").rstrip("\n") if PROTOCOL.exists() else None
    sha = lambda t: hashlib.sha256(t.encode("utf-8")).hexdigest()
    return {"file": str(PROTOCOL.relative_to(V.ROOT)).replace("\\", "/"), "frozen_sha256": sha(frozen) if frozen else None,
            "md_protocol_sha256": sha(head), "md_matches_frozen": frozen == head if frozen else None,
            "note": "the copy was taken after the results existed: the hash guards against later edits only, "
                    "it cannot show the protocol was unedited before the runs"}


def cmd_freeze(a):
    if PROTOCOL.exists():
        raise SystemExit(f"{PROTOCOL.name} exists; the protocol is frozen once")
    head = MD.read_text(encoding="utf-8").partition("## Results")[0].rstrip("\n")
    PROTOCOL.write_text(head + "\n", encoding="utf-8", newline="\n")
    print(f"{PROTOCOL.name}: sha256 {protocol_check()['frozen_sha256']}")


def cmd_summarise(a):
    check_slots(a.blocks)
    runs = [run_metrics(s, m, rec) for s, m in slots(a.blocks) if (rec := load(s)) is not None]
    key_p = Path(a.key) if a.key else DIR / "blind_key.json"
    fr = framing(json.loads(key_p.read_text(encoding="utf-8"))) if key_p.exists() else {}
    check_framing(fr, runs)
    for r in runs:
        if r["slot"] in fr.get("runs", {}):
            f = fr["runs"][r["slot"]]
            r["framing"] = {k: f[k] for k in ("label", "claims_reviewed", "major", "minor", "by_category", "by_rule")}
            words = r["content"]["words"]
            r["content"]["issues_per_100_words"] = round(100 * (f["major"] + f["minor"]) / words, 2) if words else None
    summary = {}
    for name, mid in MODELS.items():
        rs = [r for r in runs if r["model"] == mid]
        ok = [r for r in rs if r["status"] == "ok"]
        tok = [t for r in ok for t in r["tokens"]]
        summary[mid] = {
            "runs": len(rs), "ok": len(ok), "parse_failures": len(rs) - len(ok),
            "r0_claims": med_range([r.get("r0_claims") for r in rs]), "r0_rejected": med_range([r.get("r0_rejected") for r in rs]),
            "r0_rate": med_range([r.get("r0_rate") for r in rs]),
            "converged": sum(bool(r.get("converged")) for r in rs), "rounds_used": med_range([r["rounds_used"] for r in rs]),
            "final_verified": med_range([r.get("final_verified") for r in ok]),
            "final_dropped": med_range([r.get("final_dropped") for r in ok]), "asks": med_range([r.get("asks") for r in ok]),
            "seconds_total": med_range([r["seconds_total"] for r in ok]),
            "seconds_round0": med_range([r["seconds_per_round"][0] for r in rs if r["seconds_per_round"]]),
            "r0_reasons_total": dict(sum((Counter(r.get("r0_reasons", {})) for r in rs), Counter())),
            "prompt_tokens_r0": med_range([r["tokens"][0]["prompt"] for r in rs if r["tokens"]]),
            "output_tokens": med_range([t["output"] for t in tok]), "thought_tokens": med_range([t["thoughts"] for t in tok]),
            "framing_major": med_range([r["framing"]["major"] for r in ok if "framing" in r]),
            "framing_minor": med_range([r["framing"]["minor"] for r in ok if "framing" in r]),
            "framing_per_claim": med_range([(r["framing"]["major"] + r["framing"]["minor"]) / r["framing"]["claims_reviewed"]
                                            for r in ok if "framing" in r and r["framing"]["claims_reviewed"]]),
            "framing_runs_with_rule": dict(sum((Counter(set(r["framing"]["by_rule"])) for r in ok if "framing" in r), Counter())),
            "framing_issues_by_rule": dict(sum((Counter(r["framing"]["by_rule"]) for r in ok if "framing" in r), Counter())),
            "content": {k: med_range([r["content"][k] for r in ok]) for k in
                        ("words_per_claim", "numbers_per_claim", "citations_per_claim", "distinct_chunks", "distinct_documents",
                         "multi_document_claims",
                         "issues_per_100_words")},
            "sources_runs": dict(sum((Counter(r["content"]["sources"]) for r in ok), Counter()))}
    integ = {k: sorted({str(r.get("integrity", {}).get(k)) for r in runs if "integrity" in r})
             for k in ("verifier_sha", "corpus_hash", "stats_hash", "retrieval_source", "retrieval_hash")}
    res = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "post_hoc": True,
           "protocol": "outputs_posthoc/model_comparison.md (top section; stated to be written before the runs, not verifiable, see protocol_freeze)",
           "task": "agent.investigator fresh run, thinking_level medium, MAX_ROUNDS 2, --no-atlas (CACHED retrieval)",
           "order": [s for s, _ in slots(a.blocks)], "integrity_distinct_values": integ,
           "all_reverify_match": all(r["reverify_match"] for r in runs), "protocol_freeze": protocol_check(),
           "summary": summary, "runs": runs, "framing_check": fr or None}
    JSON_OUT.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    write_md(res)
    print(f"{len(runs)} runs -> {JSON_OUT.name}, {MD.name}")
    if res["protocol_freeze"]["md_matches_frozen"] is not True:
        print(f"WARNING: protocol section vs {PROTOCOL.name}: {res['protocol_freeze']['md_matches_frozen']}")
    for mid, s in summary.items():
        print(f"  {mid}: ok {s['ok']}/{s['runs']}  r0 rate {fmt(s['r0_rate'], 2)}  dropped {fmt(s['final_dropped'])}  "
              f"verified {fmt(s['final_verified'])}  total s {fmt(s['seconds_total'])}")


def write_md(res: dict):
    S, R = res["summary"], res["runs"]
    A, F = MODELS["argon"], MODELS["flash"]
    row = lambda label, k, nd=0: f"| {label} | {fmt(S[A][k], nd)} | {fmt(S[F][k], nd)} |"
    L = [f"_Generated by `agent/compare_models.py summarise` at {res['ts']} (post-hoc). Medians [min-max]._", "",
         "### Summary by model", "", f"| metric | {A} | {F} |", "|---|---|---|",
         f"| runs (ok / parse failures) | {S[A]['ok']} / {S[A]['parse_failures']} | {S[F]['ok']} / {S[F]['parse_failures']} |",
         row("round-0 claims proposed", "r0_claims"), row("round-0 claims rejected", "r0_rejected"),
         row("round-0 rejection rate", "r0_rate", 2),
         f"| converged (0 rejections by last round) | {S[A]['converged']}/{S[A]['runs']} | {S[F]['converged']}/{S[F]['runs']} |",
         row("rounds used", "rounds_used"), row("final verified claims", "final_verified"),
         row("final dropped claims", "final_dropped"), row("asks", "asks"), row("seconds, round 0", "seconds_round0"),
         row("seconds, total", "seconds_total"), row("prompt tokens, round 0 (as reported)", "prompt_tokens_r0"),
         row("output tokens per round", "output_tokens"),
         f"| thinking tokens per round (as reported; n rounds reporting) | {fmt(S[A]['thought_tokens'])} (n={(S[A]['thought_tokens'] or {}).get('n', 0)}) | "
         f"{fmt(S[F]['thought_tokens'])} (n={(S[F]['thought_tokens'] or {}).get('n', 0)}) |"]
    if any(S[m]["framing_per_claim"] for m in S):
        L += [row(f"framing issues, major ({SJ})", "framing_major"), row(f"framing issues, minor ({SJ})", "framing_minor"),
              row(f"framing issues per verified claim ({SJ})", "framing_per_claim", 2)]
    L += ["", "### Round-0 rejection reasons (all runs, reason counts; a claim can have several)", "",
          f"| reason | {A} | {F} |", "|---|---|---|"]
    cats = sorted(set(S[A]["r0_reasons_total"]) | set(S[F]["r0_reasons_total"]))
    L += [f"| {c} | {S[A]['r0_reasons_total'].get(c, 0)} | {S[F]['r0_reasons_total'].get(c, 0)} |" for c in cats] or ["| (none) | 0 | 0 |"]
    if any(S[m]["framing_issues_by_rule"] for m in S):
        rules = (res.get("framing_check") or {}).get("rules") or {}
        L += ["", f"### Framing issues by checklist rule ({SJ}; runs with >= 1 issue / total issues)", "",
              f"| rule | {A} | {F} |", "|---|---|---|"]
        for k in sorted(set(S[A]["framing_issues_by_rule"]) | set(S[F]["framing_issues_by_rule"])):
            cell = lambda m: f"{S[m]['framing_runs_with_rule'].get(k, 0)}/{S[m]['ok']} runs, {S[m]['framing_issues_by_rule'].get(k, 0)}"
            L.append(f"| {k}: {rules.get(k, '')} | {cell(A)} | {cell(F)} |")
    L += ["", "### Claim content (exploratory, not in the protocol; final verified claims)", "",
          f"| metric | {A} | {F} |", "|---|---|---|"]
    L += [f"| {k.replace('_', ' ')}{' (' + SJ + ')' if 'issues' in k else ''} | {fmt(S[A]['content'][k], 2 if 'issues' in k else 1)} | "
          f"{fmt(S[F]['content'][k], 2 if 'issues' in k else 1)} |" for k in S[A]["content"]]
    L += ["", "Sources used by the final verified claims (runs out of " + f"{S[A]['ok']} / {S[F]['ok']}):", "",
          f"| source | {A} | {F} |", "|---|---|---|"]
    names = KEY_STATS | KEY_CHUNKS
    for k in sorted(set(S[A]["sources_runs"]) | set(S[F]["sources_runs"]), key=lambda k: (k in names, k)):
        L.append(f"| {k}{' (' + names[k] + ')' if k in names else ''} | {S[A]['sources_runs'].get(k, 0)} | {S[F]['sources_runs'].get(k, 0)} |")
    L += ["", "### Every run", "",
          "| slot | model | r0 claims | r0 rejected | rounds | converged | verified | dropped | asks | s total | s per round | r0 fate of rejected | framing maj/min |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in R:
        fr = r.get("framing")
        L.append(f"| {r['slot']} | {r['model']} | {r.get('r0_claims', '-')} | {r.get('r0_rejected', '-')} | {r['rounds_used']} | "
                 f"{'yes' if r.get('converged') else 'no'} | {r.get('final_verified', '-')} | {r.get('final_dropped', '-')} | "
                 f"{r.get('asks', '-')} | {r['seconds_total']} | {', '.join(map(str, r['seconds_per_round']))} | "
                 f"{r.get('r0_rejected_fate', r['status'])} | {str(fr['major']) + '/' + str(fr['minor']) if fr else '-'} |")
    errs = [r for r in R if r["error"]]
    L += ["", "Failures: " + ("; ".join(f"{r['slot']} round {r['error']['round']}: {r['error']['type']} "
                                       f"({(r['error']['message'] or '')[:160]})" for r in errs) if errs else "none."),
          "", "Integrity: distinct values across runs: " +
          "; ".join(f"{k} {len(v)}" for k, v in res["integrity_distinct_values"].items()) +
          f". Verifier re-run on every stored round matches the recorded verdicts: {res['all_reverify_match']}. "
          f"Protocol section matches compare/protocol_frozen.md (sha256 {str(res['protocol_freeze']['frozen_sha256'])[:12]}): "
          f"{res['protocol_freeze']['md_matches_frozen']}.", ""]
    text = MD.read_text(encoding="utf-8")
    head, _, rest = text.partition("## Results")
    tail = rest[rest.index("## Interpretation"):] if "## Interpretation" in rest else ""
    MD.write_text(head + "## Results\n\n" + "\n".join(L) + "\n" + tail, encoding="utf-8")


# --------------------------------------------------------------------------- blind export
UNMAPPED = "K?"
KNOWN_DOC = re.compile(r"^(FOI|MQT?|CSOPP|TfL|Weir|BrixtonBuzz)(?![a-z])")
ID_LIKE = re.compile(r"(?<![\w.-])(?:[A-Za-z]{1,2}\d{1,3}|[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+|"
                     r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*-[A-Za-z0-9]*\d[A-Za-z0-9]*(?:-[A-Za-z0-9]+)*)(?![\w-])")
AFTER_CLAIM = re.compile(r"\bclaims?\s+((?:[A-Za-z0-9_-]+)(?:(?:,\s*|\s+and\s+|\s*&\s*)(?!claims?\b)[A-Za-z0-9_-]+)*)", re.I)


def scrub_ids(text: str, ids: dict) -> tuple[str, list[str]]:
    """Rewrite known claim ids to their blind label; any other id-like token becomes K? (and is returned).
    Id-like: letter+digits (C9), anything with an underscore, a hyphenated slug with a digit, or a hyphenated
    word listed right after 'claim(s)'. Document ids (FOI-..., MQT-...) are left alone."""
    if ids:
        pat = re.compile(r"(?<![\w-])(" + "|".join(re.escape(k) for k in sorted(ids, key=len, reverse=True)) + r")(?![\w-])")
        text = pat.sub(lambda m: ids[m.group(1)], text)
    left = [t for t in ID_LIKE.findall(text) if not re.fullmatch(r"K\d+", t)]
    left += [t for m in AFTER_CLAIM.finditer(text) for t in re.split(r",\s*|\s+and\s+|\s*&\s*", m.group(1)) if "-" in t]
    left = [t for t in dict.fromkeys(left) if t != UNMAPPED and not KNOWN_DOC.match(t)]
    for t in left:
        text = re.sub(r"(?<![\w.-])" + re.escape(t) + r"(?![\w-])", UNMAPPED, text)
    return text, left


def blind_export(done: list[tuple[str, dict]], rng=None) -> tuple[list, dict, dict]:
    """Shuffled opaque labels P1..Pn; verified claim ids become K1..Kn, ids of dropped / earlier-round claims and any
    other id-like token in the asks become K?, so neither the id style nor a leaked id can reveal the model."""
    labels = [f"P{i + 1}" for i in range(len(done))]
    (rng or random.SystemRandom()).shuffle(labels)
    keep = ("section", "type", "text", "citations", "stat_keys", "formula")
    blind, key, idmap = [], {}, {}
    for lab, (slot, rec) in zip(labels, done):
        ids = {c["id"]: f"K{i + 1}" for i, c in enumerate(rec["verified_claims"])}
        ids |= {c["id"]: UNMAPPED for r in rec.get("rounds", []) for c in r["investigation"]["claims"] if c["id"] not in ids}
        ids |= {d["claim"]["id"]: UNMAPPED for d in rec.get("dropped_claims", []) if d["claim"]["id"] not in ids}
        asks, left = [], []
        for x in rec["asks"]:
            row = {}
            for k, v in x.items():
                row[k], l = scrub_ids(v, ids) if isinstance(v, str) else (v, [])
                left += l
            asks.append(row)
        blind.append({"label": lab,
                      "claims": [{"id": ids[c["id"]]} | {k: c[k] for k in keep} for c in rec["verified_claims"]],
                      "asks": asks, "headline_claim_ids": [ids.get(i, UNMAPPED) for i in rec["headline_claim_ids"]]})
        key[lab], idmap[lab] = slot, {"claims": ids, "unmapped_tokens_replaced": left}
    return sorted(blind, key=lambda x: int(x["label"][1:])), key, idmap


def cmd_blind(a):
    check_slots(a.blocks)
    out_dir = Path(a.out_dir) if a.out_dir else DIR
    key_p = Path(a.key) if a.key else DIR / "blind_key.json"
    if (DIR / "framing_judgements.json").exists() and (key_p.exists() or (out_dir / "blind_claims.json").exists()):
        raise SystemExit("framing_judgements.json exists: refusing to overwrite the blind export or key (a new shuffle "
                         "would map the recorded judgements to the wrong runs)")
    done = [(s, rec) for s, _ in slots(a.blocks) if (rec := load(s)) is not None and not rec.get("error")]
    blind, key, idmap = blind_export(done)
    for p in (out_dir, key_p.parent):
        p.mkdir(parents=True, exist_ok=True)
    (out_dir / "blind_claims.json").write_text(json.dumps(blind, indent=1, default=float), encoding="utf-8", newline="\n")
    key_p.write_text(json.dumps(key, indent=1), encoding="utf-8", newline="\n")
    key_p.with_name(key_p.stem + "_ids.json").write_text(json.dumps(idmap, indent=1), encoding="utf-8", newline="\n")
    print(f"{len(blind)} runs -> {out_dir / 'blind_claims.json'} (key {key_p}, id map beside it: do not open before judging)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("run", "blind", "summarise", "freeze"))
    ap.add_argument("--blocks", type=int, default=3, help="ABBA blocks of 4 runs (3 = 6 per model, the recorded experiment)")
    ap.add_argument("--key", default=None, help="blind: where to write the key; summarise: where to read it (default compare/blind_key.json)")
    ap.add_argument("--out-dir", default=None, help="blind: folder for blind_claims.json (default compare/)")
    a = ap.parse_args()
    {"run": cmd_run, "blind": cmd_blind, "summarise": cmd_summarise, "freeze": cmd_freeze}[a.cmd](a)


if __name__ == "__main__":
    main()
