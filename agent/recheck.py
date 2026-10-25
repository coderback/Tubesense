"""Independent re-derivation: Argon recomputes our headline statistics in its own code sandbox, we compare.

What: five checks (within-trip loudness contrast, the frozen loudness verdict, the Central section means,
the pre-registered H2b forward test, the H5 NIOSH arithmetic). For each, gemini-4-argon (thinking medium)
gets ONLY a derived table (per-section-run levels, per-section positioning errors, per-trip NIOSH/Voice
Memos levels: no audio, no sensor arrays, no speech, no notes) plus the definitions and, for the
verdicts, the frozen rule text verbatim, and must compute every quantity with the code_execution tool.
It is never shown our values. This script then compares each returned value with ours under the
tolerances declared below (fixed before any run), records match / mismatch, keeps Argon's executed code
and sandbox output, and checks that each returned number is traceable to what the sandbox printed.
Why: a verification loop with two independent computations (our frozen pipeline vs Argon's own code)
over the numbers the evidence pack cites; a mismatch is reported, never hidden. Post-hoc (declared
25 Oct, after prereg-24oct); inputs are precomputed outputs of the frozen pipeline.
Writes outputs_posthoc/recheck/<ts>.json, latest.json, latest.md; optionally Atlas `rechecks`.
Run: PYTHONPATH=. .venv/Scripts/python -m agent.recheck [--jsonl] [--only contrast,h2b] [--no-atlas]
     PYTHONPATH=. .venv/Scripts/python -m agent.recheck --canary   (offline comparator test, no API call)
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs_posthoc" / "recheck"
LOUD = ROOT / "outputs" / "gonogo" / "loudness.json"
LOUD_CSV = ROOT / "outputs" / "gonogo" / "loudness_sections.csv"
FWD = ROOT / "outputs" / "online" / "forward_test.json"
H5 = ROOT / "outputs_posthoc" / "h5_niosh.json"
PLAN = ROOT / "outputs" / "PLAN_24Oct.md"
FROZEN_LOUDNESS = ROOT / "scripts" / "loudness.py"
MODEL, THINKING = "gemini-4-argon", "medium"   # owner's requirement: every call Argon at medium

# Tolerances, declared before any run. A value matches when |argon - ours| <= TOL[kind] + half a unit
# in the last decimal we publish (ours is often rounded in its source file). Why these sizes:
#   db       loudness_sections.csv is rounded to 3 dp, so a within-trip difference can move by 0.001 dB
#   m        forward_test.json per-section medians are 0.1 m values; medians of them are exact
#   fraction coverage means of 3-dp values
#   p        the sign test is an exact binomial tail of integer counts
#   count/label must be identical
TOL = {"db": 0.002, "m": 0.01, "fraction": 0.0005, "p": 1e-6, "count": 0.0, "label": None}

SYSTEM = """You are an independent re-checker for Tubesense, a hackathon project measuring relative noise on
London Underground sections from phone recordings. You are given a small DERIVED table (no raw data) and
exact definitions. Compute every requested quantity yourself by writing and running Python with the code
execution tool (numpy/pandas/scipy are available). Rules:
- The table is attached as a CSV file; in your sandbox it is input_file_0.csv. Load it with
  pandas.read_csv('input_file_0.csv'); never retype numbers by hand.
- Print every quantity you report, at full precision; report exactly what your code printed (no rounding).
- Follow the definitions literally. If a definition is ambiguous, pick the most literal reading and say
  what you chose in definition_notes.
- You have not been told the expected answers; do not guess them. If you cannot compute a quantity,
  leave its value null and say why in definition_notes.
- Answer in the JSON schema. Use value_number for numbers and counts, value_text for labels
  (verdicts, true/false, lists). code_used = the final code you executed."""

SCHEMA = {"type": "object", "properties": {
    "results": {"type": "array", "items": {"type": "object", "properties": {
        "quantity": {"type": "string"}, "value_number": {"type": "number", "nullable": True},
        "value_text": {"type": "string", "nullable": True}}, "required": ["quantity"]}},
    "code_used": {"type": "string"}, "definition_notes": {"type": "string"}},
    "required": ["results", "code_used", "definition_notes"]}

# Columns of loudness_sections.csv sent to Argon: identifiers, the QC exclusion flag and the level
# variants (dBFS(A) per section-run). Speech/handling fractions and timings stay local.
LEVEL_COLS = ["trip", "run_id", "direction", "from", "to", "placement_excluded", "LAeq", "nospeech_LAeq",
              "core_LAeq", "top25_LAeq", "L10", "LAeq_1s_mean", "LAeq_limiter_ub"]
VARIANTS = ["LAeq", "nospeech_LAeq", "core_LAeq", "top25_LAeq", "L10", "LAeq_1s_mean", "LAeq_limiter_ub"]
CENTRAL = [("EB", "LST->BTH"), ("EB", "BTH->MLE"), ("WB", "MLE->BTH"), ("WB", "BTH->LST")]

PAIRING = """Definitions (from the frozen pipeline, restated in words):
- Each row is one section-run: a train run between two adjacent stations; `trip` groups the sections of one
  continuous journey (R1 and R1.5 are one trip recorded in two files). Levels are dB(A) relative to digital
  full scale, uncalibrated: only differences are meaningful.
- Within-trip contrast: for each trip with direction EB, diff = (row with from=LST, to=BTH) minus (row with
  from=BTH, to=MLE); for direction WB, diff = (row from=BTH, to=LST) minus (row from=MLE, to=BTH). Trips of
  any other direction, or missing either row, are not paired. Compute diff separately for each level column.
- A paired trip is placement-excluded if EITHER of its two rows has placement_excluded true. The
  "placement_ok" set = paired trips that are not placement-excluded.
- Over the placement_ok trips, per level column: n = number of trips, n_positive = count of diff > 0
  (strictly), mean, sample sd (ddof=1), min, max, and the one-sided sign test p = P(X >= n_positive) for
  X ~ Binomial(n, 0.5) (scipy.stats.binomtest(n_positive, n, 0.5, alternative="greater").pvalue)."""


def _csv(df: pd.DataFrame) -> str:
    b = io.StringIO()
    df.to_csv(b, index=False)
    return b.getvalue()


def _levels_csv() -> str:
    return _csv(pd.read_csv(LOUD_CSV)[LEVEL_COLS])


def _frozen_rule() -> str:
    """The loudness verdict exactly as frozen in scripts/loudness.py (MIN_DB, VARIANTS, verdict())."""
    src = FROZEN_LOUDNESS.read_text(encoding="utf-8")
    tree = ast.parse(src)
    parts = [ast.get_source_segment(src, n) for n in tree.body
             if (isinstance(n, ast.Assign) and any(getattr(t, "id", "") in ("MIN_DB", "VARIANTS") for t in n.targets))
             or (isinstance(n, ast.FunctionDef) and n.name == "verdict")]
    min_db_line = next(l for l in src.splitlines() if l.startswith("MIN_DB"))   # keep its comment
    return "\n\n".join([min_db_line] + [p for p in parts if not p.startswith("MIN_DB")])


def _plan_h2b() -> str:
    t = PLAN.read_text(encoding="utf-8")
    return t[t.index("### H2b"):t.index("### H3")].strip()


# ---- the checks: (prompt, our values {quantity: (value, kind, published decimals or None, source)}, table CSV)
def contrast_task() -> tuple[str, dict, str]:
    L = json.loads(LOUD.read_text())
    s = L["paired_differences"]["summary"]["placement_ok"]
    la, src = s["LAeq"], "outputs/gonogo/loudness.json paired_differences.summary.placement_ok.LAeq"
    ours = {"n_trips": (s["n"], "count", None, src), "n_positive": (la["n_positive"], "count", None, src),
            "mean_db": (la["mean_db"], "db", None, src), "sd_db": (la["sd_db"], "db", None, src),
            "min_db": (la["min_db"], "db", None, src), "max_db": (la["max_db"], "db", None, src),
            "sign_test_p_one_sided": (la["sign_test_p_one_sided"], "p", None, src)}
    for t in L["paired_differences"]["per_trip"]:
        ours[f"diff_db:{t['trip']}"] = (t["diff_LAeq"], "db", None, "outputs/gonogo/loudness.json per_trip")
    ours["placement_excluded_trips"] = (",".join(sorted(t["trip"] for t in L["paired_differences"]["per_trip"]
                                                        if t["placement_excluded"])), "label", None,
                                        "outputs/gonogo/loudness.json per_trip")
    prompt = f"""{PAIRING}

Task: using the LAeq column only, compute for the placement_ok trips: n_trips, n_positive, mean_db, sd_db,
min_db, max_db, sign_test_p_one_sided. Also report the diff for EVERY paired trip (including excluded ones)
as quantity "diff_db:<trip>" (e.g. "diff_db:R3_EB_LST-MLE"), and the placement-excluded paired trips as
quantity "placement_excluded_trips" (value_text: trip ids sorted alphabetically, comma-separated, no spaces).

The attached table has one row per section-run."""
    return prompt, ours, _levels_csv()


def verdict_task() -> tuple[str, dict, str]:
    L = json.loads(LOUD.read_text())
    V, s = L["verdict"], L["paired_differences"]["summary"]["placement_ok"]
    src = "outputs/gonogo/loudness.json verdict"
    ours = {"verdict": (V["verdict"], "label", None, src)}
    for v in VARIANTS:
        ours[f"n_positive:{v}"] = (s[v]["n_positive"], "count", None, "outputs/gonogo/loudness.json summary.placement_ok")
        ours[f"all_trips_positive:{v}"] = (str(V["variant_sign_consistent"][v]).lower(), "label", None, src)
    prompt = f"""{PAIRING}

The pre-registered decision rule, verbatim from the frozen script (Python; P is the paired-difference
result, P["summary"]["placement_ok"] holds the placement_ok statistics described above, keyed by level
column, each with n_positive and mean_db; `tab` is unused; the VARIANTS values are plot labels and only
its keys matter, so TRIM_S and TOP_FRAC, defined elsewhere, are irrelevant here):
```python
{_frozen_rule()}
```

Task: apply this rule to the table. Report quantity "verdict" (value_text: GO, WEAK or NO-GO), and for each
level column v in VARIANTS: "n_positive:<v>" (value_number) and "all_trips_positive:<v>" (value_text "true"
or "false", i.e. whether n_positive equals n for that column).

The attached table has one row per section-run."""
    return prompt, ours, _levels_csv()


def means_task() -> tuple[str, dict, str]:
    L = json.loads(LOUD.read_text())
    agg = {(a["direction"], a["section"]): a for a in L["aggregate_by_section_direction"]["LAeq"]}
    src = "outputs/gonogo/loudness.json aggregate_by_section_direction.LAeq (3 dp)"
    ours = {}
    for d, sec in CENTRAL:
        a = agg[(d, sec)]
        ours[f"n:{d} {sec}"] = (a["n"], "count", None, src)
        ours[f"mean_db:{d} {sec}"] = (a["mean"], "db", 3, src)
        ours[f"sd_db:{d} {sec}"] = (a["sd"], "db", 3, src)
    names = ", ".join(f"{d} {s}" for d, s in CENTRAL)
    prompt = f"""Each row of the table is one section-run (a train run between two adjacent stations). Levels are
dB(A) relative to digital full scale, uncalibrated (relative only). The `section` of a row is "<from>-><to>".

Task: for each (direction, section) in [{names}], take the rows with that direction and section whose
placement_excluded is false, and compute from the LAeq column: n (row count), mean (arithmetic mean of the
row values, not an energy mean) and sample sd (ddof=1). Report quantities "n:<direction> <section>",
"mean_db:<direction> <section>", "sd_db:<direction> <section>", e.g. "mean_db:EB LST->BTH"."""
    return prompt, ours, _levels_csv()


def h2b_task() -> tuple[str, dict, str]:
    F = json.loads(FWD.read_text())
    rows = pd.DataFrame([{"held_out": r["held_out"], "track": r["track"], "n_curve_edges": r["n_curve_edges"],
                          "departure_missed_online": r["departure_missed_online"],
                          "tracker_median_error_m": r["map_vs_independent_m"]["median"],
                          "timetable_median_error_m": r["timetable_vs_independent_m"]["median"],
                          "band90_coverage": r["band90_coverage_independent"]} for r in F["runs"]])
    S = json.loads((ROOT / "outputs_posthoc" / "section_stats.json").read_text())
    o, h = F["overall"], S["positioning.h2b_verdict"]
    lb = rows[rows.track.isin(["EB LST->BTH", "WB BTH->LST"])]
    src_o, src_h = "outputs/online/forward_test.json overall", "outputs_posthoc/section_stats.json positioning.h2b_verdict"
    ours = {"n_sections": (o["n_sections"], "count", None, src_o),
            "overall_tracker_median_m": (o["tracker_median_of_runs_m"], "m", 1, src_o),
            "overall_timetable_median_m": (o["timetable_median_of_runs_m"], "m", 1, src_o),
            "overall_band90_coverage_mean": (o["band90_coverage_mean"], "fraction", 3, src_o),
            "lstbth_n_sections": (h["n"], "count", None, src_h),
            "lstbth_tracker_median_m": (h["detail"]["median_error_m"], "m", 2, src_h),
            "lstbth_band90_coverage_mean": (h["detail"]["band90_coverage_mean"], "fraction", 3, src_h),
            # not stored in section_stats; FINDINGS.md quotes it (8.2 m). Same numpy rule as agent/section_stats.py.
            "lstbth_timetable_median_m": (round(float(np.median(lb.timetable_median_error_m)), 1), "m", 1,
                                          "FINDINGS.md H2b row (recomputed locally with numpy.median)"),
            "h2b_verdict": (h["value"], "label", None, src_h)}
    prompt = f"""Context: an online tracker locates a Tube train between stations from a phone gyroscope, using maps
frozen from earlier rides. Each row is one held-out section of a later ride: the median absolute error (m)
of the tracker and of a timetable baseline against an independent curve-edge truth, and the share of
samples where the truth lay inside the tracker's 90 % band (band90_coverage).

Definitions: "median-of-runs" = the median over rows of each row's median error; "mean 90 % band
coverage" = the arithmetic mean of band90_coverage over rows. "Liverpool Street <-> Bethnal Green" = the
rows with track "EB LST->BTH" or "WB BTH->LST".

The pre-registered criterion, verbatim from the frozen plan (outputs/PLAN_24Oct.md):
---
{_plan_h2b()}
---
If the GO condition does not hold, the verdict is NO-GO.

Task: report n_sections (all rows), overall_tracker_median_m, overall_timetable_median_m,
overall_band90_coverage_mean (all rows); lstbth_n_sections, lstbth_tracker_median_m,
lstbth_timetable_median_m, lstbth_band90_coverage_mean (LST<->BTH rows); and h2b_verdict (value_text GO or
NO-GO)."""
    return prompt, ours, _csv(rows)


def niosh_task() -> tuple[str, dict, str]:
    H = json.loads(H5.read_text())
    src = "outputs_posthoc/h5_niosh.json"
    rows, ours = [], {}
    for t in H["trips"]:
        for stem, v in t["vm_same_evening_dbfs"].items():
            rows.append({"niosh_trip": t["run_id"], "direction": t["direction"], "niosh_LAeq_dBA": t["LAeq_dBA"],
                         "voice_memos_trip": stem, "voice_memos_trip_LAeq_dBFS": v})
        r = t["run_id"]
        ours[f"above_hse_upper_db:{r}"] = (t["above_hse_upper_db"], "db", 1, src)
        for k in ("median", "min", "max"):
            ours[f"offset_{k}_db:{r}"] = (t["offset_db"][k], "db", 2, src)
    ours["niosh_eb_minus_wb_db"] = (H["posthoc_direction_gap"]["niosh_eb_minus_wb_db"], "db", 1, src)
    prompt = f"""Context: two whole-trip readings from the NIOSH Sound Level Meter phone app (indicative, not a
certified meter) on the Central line, one eastbound (EB) and one westbound (WB), each on a different train,
and the whole-trip levels of Voice Memos recordings (dB relative to digital full scale, uncalibrated) of
rides in the same direction the same evening. The HSE upper exposure action value is {H['hse_upper_action_dBA']} dB(A).

Definitions: above_hse_upper_db = NIOSH LAeq minus the HSE upper action value; offset = NIOSH LAeq minus
a Voice Memos trip level (one offset per Voice Memos trip), summarised per NIOSH trip as median, min, max;
niosh_eb_minus_wb_db = EB NIOSH LAeq minus WB NIOSH LAeq.

Task: report "above_hse_upper_db:<niosh_trip>", "offset_median_db:<niosh_trip>", "offset_min_db:<niosh_trip>",
"offset_max_db:<niosh_trip>" for each NIOSH trip, and "niosh_eb_minus_wb_db"."""
    return prompt, ours, _csv(pd.DataFrame(rows))


TASKS: dict[str, tuple[str, Callable[[], tuple[str, dict, str]]]] = {
    "contrast": ("Within-trip contrast LST-BTH minus BTH-MLE (LAeq, placement-ok trips)", contrast_task),
    "verdict": ("Loudness verdict under the frozen rule (GO / WEAK / NO-GO)", verdict_task),
    "means": ("Central section mean levels (LAeq, placement-included runs)", means_task),
    "h2b": ("H2b forward test: median-of-runs error, coverage, verdict", h2b_task),
    "niosh": ("H5 NIOSH arithmetic: above HSE 85 dB(A), offsets, EB-WB gap", niosh_task),
}


# ---- Argon call
def call_argon(client, prompt: str, table: str) -> dict:
    """One generate_content call, the table attached as a CSV file (input_file_0.csv in the sandbox), with
    code execution + JSON schema; returns executed code, sandbox outputs, parsed answer, timing."""
    from google.genai import types
    t0 = time.time()
    contents = [types.Part.from_bytes(data=table.encode("utf-8"), mime_type="text/csv"), prompt]
    r = client.models.generate_content(model=MODEL, contents=contents, config=types.GenerateContentConfig(
        system_instruction=SYSTEM, tools=[types.Tool(code_execution=types.ToolCodeExecution())],
        thinking_config=types.ThinkingConfig(thinking_level=THINKING), max_output_tokens=32768,
        response_mime_type="application/json", response_schema=SCHEMA))
    code, outputs, text = [], [], []
    for p in (r.candidates[0].content.parts or []) if r.candidates and r.candidates[0].content else []:
        if p.executable_code:
            code.append(p.executable_code.code)
        elif p.code_execution_result:
            outputs.append({"outcome": str(p.code_execution_result.outcome).split(".")[-1],
                            "output": p.code_execution_result.output or ""})
        elif p.text and not p.thought:
            text.append(p.text)
    raw = "".join(text).strip()
    m = re.search(r"\{.*\}", raw, re.S)
    answer = json.loads(m.group(0)) if m else None
    u = r.usage_metadata
    return {"seconds": round(time.time() - t0, 1), "code": code, "outputs": outputs, "answer": answer, "raw": raw,
            "usage": {"prompt": u.prompt_token_count, "output": u.candidates_token_count,
                      "thoughts": u.thoughts_token_count, "tool_use_prompt": u.tool_use_prompt_token_count}}


# ---- deterministic comparison
_NUM = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?")


def _traced(v: float, outputs: list[dict]) -> bool:
    """Is this returned number visible in what the sandbox printed (same value at its own precision)?"""
    s = repr(float(v))
    nd = len(s.split(".")[1]) if "." in s and "e" not in s else 12
    for o in outputs:
        for tok in _NUM.findall(o["output"]):
            x = float(tok)
            if abs(x - v) <= 1e-9 * max(1.0, abs(v)) or round(x, nd) == round(v, nd):
                return True
    return False


def compare(ours: dict, answer: dict | None, outputs: list[dict]) -> list[dict]:
    got = {}
    for r in (answer or {}).get("results", []):
        q = str(r.get("quantity", "")).strip()
        got[q] = r.get("value_number") if r.get("value_number") is not None else r.get("value_text")
    rows = []
    for q, (val, kind, dp, src) in ours.items():
        a = got.get(q)
        row = {"quantity": q, "ours": val, "argon": a, "kind": kind, "published_dp": dp, "source": src}
        if a is None:
            row.update(match=False, reason="missing in Argon's answer")
        elif kind == "label":
            norm = lambda x: re.sub(r"\s+", "", str(x)).lower()
            row.update(match=norm(a) == norm(val), reason="exact label")
        else:
            try:
                af = float(a)
            except (TypeError, ValueError):
                row.update(match=False, reason=f"not a number: {a!r}")
                rows.append(row)
                continue
            tol = TOL[kind] + (0.5 * 10 ** -dp if dp is not None else 0.0) + 1e-9
            d = af - float(val)
            row.update(match=abs(d) <= tol, diff=d, tol=round(tol, 9), traced_to_sandbox=_traced(af, outputs),
                       reason=f"|diff| {abs(d):.3g} vs tol {tol:.3g}")
        rows.append(row)
    for q in sorted(set(got) - set(ours)):
        rows.append({"quantity": q, "ours": None, "argon": got[q], "match": None, "reason": "extra (not compared)"})
    return rows


def canary(path: Path = OUT / "latest.json") -> bool:
    """Offline test of the comparator (no API call): replay Argon's saved answers against our current values
    (all must match), then plant errors in ours just outside tolerance, and in Argon's answer, and require
    every one to be caught."""
    rec = json.loads(path.read_text(encoding="utf-8"))
    ok = True
    plants = {"contrast": [("mean_db", 0.01), ("n_positive", 1), ("sign_test_p_one_sided", 0.001)],
              "verdict": [("verdict", "WEAK"), ("all_trips_positive:L10", "false")],
              "means": [("mean_db:EB LST->BTH", 0.005)], "h2b": [("lstbth_tracker_median_m", 0.02),
                                                                ("h2b_verdict", "NO-GO")],
              "niosh": [("offset_median_db:N1", 0.01)]}
    for n, c in rec["checks"].items():
        if "error" in c:
            continue
        ours = TASKS[n][1]()[1]
        clean = compare(ours, c["argon_answer"], c["sandbox_outputs"])
        bad = [r["quantity"] for r in clean if r.get("match") is False]
        print(f"{n:9s} replay: {len(clean) - len(bad)}/{len(clean)} match" + (f"  MISMATCH {bad}" if bad else ""))
        ok &= not bad
        for q, delta in plants.get(n, []):
            v, kind, dp, src = ours[q]
            planted = {**ours, q: (delta if isinstance(delta, str) else v + delta, kind, dp, src)}
            row = next(r for r in compare(planted, c["argon_answer"], c["sandbox_outputs"]) if r["quantity"] == q)
            print(f"          planted {q} -> {planted[q][0]!r}: {'caught' if row['match'] is False else 'MISSED'}")
            ok &= row["match"] is False
        ans = json.loads(json.dumps(c["argon_answer"]))
        dropped = ans["results"].pop(0)["quantity"]
        caught = any(r["quantity"] == dropped and r["match"] is False for r in compare(ours, ans, c["sandbox_outputs"]))
        print(f"          dropped {dropped} from Argon's answer: {'caught' if caught else 'MISSED'}")
        ok &= caught
    print(f"untraced number (123.456 not printed by the sandbox) flagged: {not _traced(123.456, [{'output': '1.0'}])}")
    print("CANARY", "PASS" if ok else "FAIL")
    return ok


# ---- run
def run(only: list[str] | None, emit: Callable[[dict], None]) -> dict:
    from google import genai
    from google.genai import types
    from atlas.db import load_env
    load_env()
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options=types.HttpOptions(timeout=180_000))
    names = [n for n in TASKS if not only or n in only]
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    t0 = time.time()
    emit({"type": "start", "ts": ts, "model": MODEL, "thinking": THINKING, "checks": names, "tolerances": TOL})
    built = {n: TASKS[n][1]() for n in names}
    for n in names:
        emit({"type": "quantity", "check": n, "title": TASKS[n][0], "quantities": list(built[n][1])})

    def job(n: str) -> dict:
        last = None
        for attempt in (1, 2):   # one retry on an API error or an unparsable answer
            try:
                res = call_argon(client, built[n][0], built[n][2])
                if res["answer"] is not None:
                    return res | {"attempts": attempt}
                last = f"unparsable answer: {res['raw'][:200]!r}"
            except Exception as e:  # noqa: BLE001
                last = f"{type(e).__name__}: {str(e)[:300]}"
        return {"error": last, "attempts": 2}

    checks = {}
    with ThreadPoolExecutor(max_workers=len(names)) as ex:
        futs = {ex.submit(job, n): n for n in names}
        for f in as_completed(futs):
            n, res = futs[f], f.result()
            if "error" in res:
                emit({"type": "error", "check": n, "error": res["error"]})
                checks[n] = {"title": TASKS[n][0], "error": res["error"], "comparisons": [], "prompt": built[n][0],
                             "table_sha256": hashlib.sha256(built[n][2].encode()).hexdigest()}
                continue
            for c in res["code"]:
                emit({"type": "argon_code", "check": n, "code": c})
            for o in res["outputs"]:
                emit({"type": "argon_result", "check": n, **o})
            rows = compare(built[n][1], res["answer"], res["outputs"])
            executed = any(o["outcome"] == "OUTCOME_OK" for o in res["outputs"])
            for r in rows:
                emit({"type": "compare", "check": n, **{k: r.get(k) for k in
                                                         ("quantity", "ours", "argon", "match", "reason")}})
            checks[n] = {"title": TASKS[n][0], "seconds": res["seconds"], "attempts": res["attempts"],
                         "usage": res["usage"], "code_executed_ok": executed, "argon_code": res["code"],
                         "sandbox_outputs": res["outputs"], "argon_answer": res["answer"],
                         "comparisons": rows, "prompt": built[n][0],
                         "table_sha256": hashlib.sha256(built[n][2].encode()).hexdigest(), "table": built[n][2]}
    comp = [r for n in names for r in checks[n]["comparisons"] if r.get("match") is not None]
    num = [r for r in comp if r["kind"] != "label"]
    summary = {"n_compared": len(comp), "n_match": sum(r["match"] for r in comp),
               "n_mismatch": sum(not r["match"] for r in comp),
               "mismatches": [f"{n}:{r['quantity']}" for n in names for r in checks[n]["comparisons"]
                              if r.get("match") is False],
               "numbers_traced_to_sandbox": f"{sum(bool(r.get('traced_to_sandbox')) for r in num)}/{len(num)}",
               "checks_without_executed_code": [n for n in names if not checks[n].get("code_executed_ok")],
               "errors": [n for n in names if "error" in checks[n]], "seconds_total": round(time.time() - t0, 1)}
    rec = {"ts": ts, "model": MODEL, "thinking": THINKING, "label": "post-hoc re-derivation of precomputed outputs",
           "tolerances": TOL, "inputs": {"levels": LOUD_CSV.relative_to(ROOT).as_posix() + f" columns {LEVEL_COLS}",
                                         "forward_test": FWD.relative_to(ROOT).as_posix() + " runs (per-section medians)",
                                         "niosh": H5.relative_to(ROOT).as_posix() + " trip levels only"},
           "summary": summary, "checks": checks}
    emit({"type": "final", **summary})
    return rec


def write_md(rec: dict) -> str:
    s = rec["summary"]
    L = [f"# Argon re-derivation of our headline numbers ({rec['ts']})", "",
         f"Post-hoc check of **precomputed** outputs of the frozen pipeline. `{rec['model']}` (thinking "
         f"`{rec['thinking']}`) got only derived tables and the definitions / frozen rule text, computed every value "
         "with its code-execution sandbox, and never saw our values. This script compared them under tolerances "
         "declared before the run.", "",
         f"**{s['n_match']}/{s['n_compared']} match**, {s['n_mismatch']} mismatch; numbers traced to sandbox output "
         f"{s['numbers_traced_to_sandbox']}; total {s['seconds_total']} s." +
         (f" Mismatches: {', '.join(s['mismatches'])}." if s["mismatches"] else "") +
         (f" Errors: {', '.join(s['errors'])}." if s["errors"] else ""), "",
         "Tolerances: " + ", ".join(f"{k} {v}" for k, v in rec["tolerances"].items()) +
         " (+ half a unit in the last decimal of our published value).", ""]
    fmt = lambda v: f"{v:.6g}" if isinstance(v, float) else str(v)
    for n, c in rec["checks"].items():
        L += [f"## {c['title']}", ""]
        if "error" in c:
            L += [f"Error: {c['error']}", ""]
            continue
        L += [f"{c['seconds']} s, attempts {c['attempts']}, code executed: {c['code_executed_ok']}", "",
              "| quantity | ours | Argon | match | traced | note |", "|---|---|---|---|---|---|"]
        for r in c["comparisons"]:
            m = {True: "yes", False: "**NO**", None: "-"}[r.get("match")]
            tr = {True: "yes", False: "no", None: "-"}[r.get("traced_to_sandbox")]
            L.append(f"| {r['quantity']} | {fmt(r['ours'])} | {fmt(r['argon'])} | {m} | {tr} | {r['reason']} |")
        notes = (c["argon_answer"] or {}).get("definition_notes", "")
        L += ["", f"Argon's definition notes: {notes or '(none)'}", "", "<details><summary>Argon's executed code"
              "</summary>", ""]
        for code in c["argon_code"]:
            L += ["```python", code.rstrip(), "```"]
        L += ["", "</details>", ""]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", action="store_true", help="print events as JSON lines")
    ap.add_argument("--only", default="", help=f"comma-separated subset of {','.join(TASKS)}")
    ap.add_argument("--no-atlas", action="store_true")
    ap.add_argument("--canary", action="store_true", help="offline comparator test on latest.json (no API call)")
    a = ap.parse_args()
    sys.path.insert(0, str(ROOT))
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    if a.canary:
        sys.exit(0 if canary() else 1)

    def emit(ev: dict):
        if a.jsonl:
            print(json.dumps(ev, default=float), flush=True)
        elif ev["type"] == "compare":
            ok = {True: "MATCH", False: "MISMATCH", None: "extra"}[ev["match"]]
            print(f"  [{ev['check']}] {ev['quantity']:38s} ours={ev['ours']!s:24.24s} argon={ev['argon']!s:24.24s} {ok}")
        elif ev["type"] == "argon_code":
            print(f"  [{ev['check']}] argon ran {len(ev['code'].splitlines())} lines of code")
        elif ev["type"] == "argon_result":
            print(f"  [{ev['check']}] sandbox {ev['outcome']}: {ev['output'].strip()[:160]!r}")
        else:
            print(json.dumps(ev, default=float)[:400], flush=True)

    only = [x for x in a.only.split(",") if x] or None
    rec = run(only, emit)
    OUT.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(rec, indent=1, default=float)
    (OUT / f"{rec['ts']}.json").write_text(blob, encoding="utf-8")
    (OUT / "latest.json").write_text(blob, encoding="utf-8")
    (OUT / "latest.md").write_text(write_md(rec), encoding="utf-8")
    if not a.no_atlas:
        try:
            from atlas.db import db
            db().rechecks.replace_one({"_id": rec["ts"]}, {"_id": rec["ts"], **json.loads(blob)}, upsert=True)
            print("atlas rechecks: upserted", rec["ts"], file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"(atlas skipped: {type(e).__name__})", file=sys.stderr)
    print(f"wrote {OUT.relative_to(ROOT).as_posix()}/{rec['ts']}.json, latest.json, latest.md", file=sys.stderr)


if __name__ == "__main__":
    main()
