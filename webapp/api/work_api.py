"""Argon at work: three more real Argon jobs, each with its deterministic check, read from their saved outputs or run as jobs.

What:
- Roughness (agent/roughness.py): Argon read TfL's 2018 Bethnal Green -> Mile End rail roughness charts (FOI-1730-1819) from
  the PDF; code checked every reading against the pixels, TfL's own stated stretch and the printed text. GET
  /api/work/roughness summarises outputs_posthoc/roughness/roughness.json (+ the check table parsed from roughness.md, and
  HUMAN_CHECK.md's fill state); GET /api/work/roughness/file/<name> serves the figure images from a fixed allow-list;
  POST /api/work/roughness/recheck re-runs `python -m agent.roughness --cached` (the saved runs re-checked, no Gemini).
- Recheck (agent/recheck.py): Argon re-derives our headline numbers with its own code (code execution). GET
  /api/work/recheck summarises outputs_posthoc/recheck/latest.json; POST /api/work/recheck/start runs
  `python -m agent.recheck --jsonl` (live, about 35 s); POST /api/work/recheck/canary runs `--canary` (offline, plain-text
  output relayed as log lines: planted errors must be "caught").
- Ride reports (agent/ride_report.py, being redesigned): GET /api/work/reports lists outputs_posthoc/ride_reports/*.json,
  GET /api/work/reports/<run> returns one record as stored plus a lookup of each declared fact in the stored
  ride_stats file (a lookup only: the check itself is the CLI's) and the CLI's own .md; POST /api/work/reports/start runs
  `python -m agent.ride_report <run> --jsonl`. The web app never approves a ride report (CLI only: --approve).
Why: each job is shown with its check and its honest status (PRECOMPUTED with its time, PENDING HUMAN CHECK, DRAFT), and
every live call goes through the job manager (one heavy job at a time, cancel = kill) and the egress log. These modules
are not imported here (numpy, pandas, pdfplumber): only their JSON and Markdown outputs are read, tolerantly.
"""
from __future__ import annotations

import re

from . import egress
from .util import OUT_DIR, POSTHOC, ApiError, has_module, have_env, mtime_iso, read_json, rel

ROUGH = POSTHOC / "roughness"
FIGURES = {f"figure{n}.png": f"pages/figure{n}.png" for n in (1, 2, 3, 4)} | {f"page{n}.png": f"pages/page{n}.png" for n in (1, 2)}
RECHECK = POSTHOC / "recheck"
REPORTS = POSTHOC / "ride_reports"
RIDE_STATS = POSTHOC / "ride_stats"
RUNS = {f"R{n}" for n in range(1, 16)} | {"R1.5"}
_RUN = re.compile(r"^R\d{1,2}(\.5)?$")
TEST_MODE = OUT_DIR != POSTHOC          # a test server never writes Atlas from these jobs


# --------------------------------------------------------------------------- small parsers
def _sections(md: str) -> dict[str, list[str]]:
    """'## Heading' -> its lines (until the next '## ')."""
    out, cur = {}, None
    for line in md.splitlines():
        if line.startswith("## "):
            cur = line[3:].strip()
            out[cur] = []
        elif cur is not None:
            out[cur].append(line)
    return out


def _table(lines: list[str]) -> list[list[str]]:
    rows = []
    for line in lines:
        t = line.strip()
        if not t.startswith("|"):
            if rows:
                break
            continue
        cells = [c.strip() for c in t.strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", c) for c in cells if c):
            continue
        rows.append(cells)
    return rows


def _bullets(lines: list[str]) -> list[str]:
    return [line[2:].strip() for line in lines if line.startswith("- ")]


def human_check_state(text: str | None) -> dict:
    """HUMAN_CHECK.md is write-once by the CLI and filled in by a person. Done = every row has a reading and a y/n, and a name."""
    if text is None:
        return {"exists": False, "state": "missing", "rows": [], "n_rows": 0, "n_filled": 0, "checked_by": None}
    rows = []
    for cells in _table(text.splitlines())[1:]:
        if len(cells) < 7 or not cells[0].isdigit():
            continue
        rows.append({"n": int(cells[0]), "page": cells[1], "figure": cells[2], "panel": cells[3], "what": cells[4],
                     "reading": cells[5], "agrees": cells[6], "filled": bool(cells[5] and cells[6])})
    m = re.search(r"Checked by:\s*(.*?)\s*Date:\s*(.*)$", text, re.M)
    who = re.sub(r"[_\s]+", " ", m.group(1)).strip() if m else ""
    when = re.sub(r"[_\s]+", " ", m.group(2)).strip() if m else ""
    n_filled = sum(r["filled"] for r in rows)
    state = "done" if rows and n_filled == len(rows) and who else "partial" if n_filled or who else "pending"
    return {"exists": True, "state": state, "rows": rows, "n_rows": len(rows), "n_filled": n_filled,
            "checked_by": who or None, "date": when or None}


# --------------------------------------------------------------------------- roughness
def roughness() -> dict:
    p = ROUGH / "roughness.json"
    rec = read_json(p)
    hc_p = ROUGH / "HUMAN_CHECK.md"
    hc = human_check_state(hc_p.read_text(encoding="utf-8") if hc_p.is_file() else None)
    base = {"available": False, "module": has_module("agent.roughness"), "human_check": hc,
            "files": {"json": rel(p), "md": rel(ROUGH / "roughness.md"), "human_check": rel(hc_p)}}
    if not isinstance(rec, dict):
        return {**base, "reason": "outputs_posthoc/roughness/roughness.json is missing or unreadable"}
    md_p = ROUGH / "roughness.md"
    md = md_p.read_text(encoding="utf-8") if md_p.is_file() else None
    secs = _sections(md or "")
    table = _table(secs.get("Checks", []))
    check_bullets = _bullets(secs.get("Checks", []))
    runs = rec.get("runs") or []
    ex1 = {g.get("figure_no"): g for g in ((runs[0].get("extraction") or {}).get("figures") or [])} if runs else {}
    cons = rec.get("consolidated") or {}
    figures = []
    for n in sorted(cons, key=lambda k: int(k) if str(k).isdigit() else 99):
        c, g = cons[n] or {}, ex1.get(int(n)) if str(n).isdigit() else None
        figures.append({
            "n": n, "image": f"/api/work/roughness/file/figure{n}.png" if f"figure{n}.png" in FIGURES else None,
            "rail": (g or {}).get("rail_label"), "band": (g or {}).get("band_label"), "page": (g or {}).get("page"),
            "caption": (g or {}).get("figure_caption"), "header": c.get("header_text"),
            "max_digitizer": c.get("global_max_digitizer"), "max_by_run": c.get("global_max_by_run"),
            "max_pixel_ok_by_run": c.get("global_max_pixel_ok_by_run"),
            "zones_confirmed": [{"start_by_run": z.get("start_by_run"), "end_by_run": z.get("end_by_run"),
                                 "median_in": (z.get("zone_rule") or {}).get("median_in"),
                                 "median_out": (z.get("zone_rule") or {}).get("median_out")}
                                for z in c.get("zones") or [] if z.get("confirmed")],
            "zones_rejected": sum(1 for z in c.get("zones") or [] if not z.get("confirmed")),
            "spectrum_peaks": c.get("spectrum_peaks_all_agree")})
    run_rows = []
    for r in runs:
        ch = r.get("checks") or {}
        rd = ch.get("readings") or []
        summ = ch.get("summary") or []
        unl = ch.get("unlabelled") or []
        zones = ch.get("zones") or []
        run_rows.append({
            "run": r.get("run"), "ts": r.get("ts"), "seconds": r.get("seconds"), "usage": r.get("usage"),
            "model": r.get("model"), "thinking": r.get("thinking"), "ok": ch.get("ok"),
            "n_failures": len(ch.get("failures") or []),
            "readings": {"n": len(rd), "pass": sum(1 for x in rd if x.get("ok")), "fail": sum(1 for x in rd if x.get("ok") is False),
                         "failed": [{k: x.get(k) for k in ("figure", "panel", "kind", "x", "x_units", "argon_y", "y_units", "pixel", "where",
                                                            "abs_err", "tolerance")} for x in rd if x.get("ok") is False]},
            "zones": {"n": len(zones), "fail": sum(1 for z in zones if not z.get("ok")),
                      "failed": [{k: z.get(k) for k in ("figure", "start", "end", "median_in", "median_out")} for z in zones if not z.get("ok")]},
            "summary": [{"sentence": x.get("sentence"), "status": x.get("status"), "reasons": x.get("reasons") or []} for x in summ],
            "colours": [{"colour": u.get("colour"), "panel": u.get("panel"), "named": u.get("named_in_an_element_about_that_panel"),
                         "wrong_words": u.get("wrong_colour_words"), "element": (u.get("element") or "")[:240] or None}
                        for u in unl if u.get("named_in_an_element_about_that_panel") is False or u.get("wrong_colour_words")],
            "other_failures": [f for f in ch.get("failures") or []
                               if not re.search(r"not confirmed by the zone rule|summary sentence|fails the pixel check|unlabelled", f)]})
    tfl = rec.get("tfl_anchor") or {}
    return {**base, "available": True, "label": "PRECOMPUTED", "status": rec.get("status"), "ts": rec.get("ts"),
            "checked_file_mtime": mtime_iso(p), "model": rec.get("model"), "thinking": rec.get("thinking"),
            "source": rec.get("source"), "tolerances": rec.get("tolerances"),
            "tfl": {"stated": tfl.get("stated"), "pixels": tfl.get("pixels"), "by_figure": tfl.get("common_zone_by_figure"),
                    "all_figures_overlap": tfl.get("common_zone_all_figures_overlap_range"),
                    "union": tfl.get("common_zone_range"), "overlaps": tfl.get("common_zone_overlaps")},
            "figures": figures, "runs": run_rows, "check_table": table,
            "check_notes": [b for b in check_bullets if b.startswith(("Run-vs-run", "Every position", "Claude's independent", "Argon vs the model-free"))],
            "plain_words": next((line[len("In plain words: "):] for line in (secs.get("What the plots show (only what survived every check)") or [])
                                 if line.startswith("In plain words: ")), None),
            "limits": _bullets(secs.get("Limits", [])), "md": md}


def roughness_file(name: str):
    if name not in FIGURES:
        raise ApiError(404, "not_found", "not one of the roughness figure images")
    p = ROUGH / FIGURES[name]
    if not p.is_file():
        raise ApiError(404, "not_found", "figure image not present")
    return p


def roughness_recheck(jobs, body: dict):
    if not has_module("agent.roughness"):
        raise ApiError(503, "unavailable", "agent.roughness not installed")
    if not (ROUGH / "runs").is_dir():
        raise ApiError(503, "unavailable", "no saved roughness runs to re-check")
    return jobs.start("roughness", "agent.roughness", ["--cached"], {"cached": True},
                      "re-check the saved roughness runs (agent.roughness --cached, no Gemini)", replace=bool(body.get("replace")))


# --------------------------------------------------------------------------- recheck
def _trim(s, n: int = 6000):
    s = "" if s is None else str(s)
    return s if len(s) <= n else s[:n] + f"\n... ({len(s) - n} more characters in the file)"


def recheck() -> dict:
    p = RECHECK / "latest.json"
    rec = read_json(p)
    history = []
    for f in sorted(RECHECK.glob("2*.json"), reverse=True)[:8]:
        d = read_json(f) or {}
        s_ = d.get("summary") or {}
        history.append({"file": f.name, "ts": d.get("ts"), "n_match": s_.get("n_match"), "n_compared": s_.get("n_compared"),
                        "errors": s_.get("errors")})
    base = {"available": False, "module": has_module("agent.recheck"), "gemini_api_key": have_env("GEMINI_API_KEY"),
            "history": history, "file": rel(p)}
    if not isinstance(rec, dict):
        return {**base, "reason": "outputs_posthoc/recheck/latest.json is missing or unreadable"}
    checks = {}
    for n, c in (rec.get("checks") or {}).items():
        c = c or {}
        checks[n] = {"title": c.get("title"), "error": c.get("error"), "seconds": c.get("seconds"), "attempts": c.get("attempts"),
                     "usage": c.get("usage"), "code_executed_ok": c.get("code_executed_ok"),
                     "comparisons": c.get("comparisons") or [], "code": [_trim(x) for x in c.get("argon_code") or []],
                     "outputs": [{"outcome": o.get("outcome"), "output": _trim(o.get("output"), 3000)} for o in c.get("sandbox_outputs") or []],
                     "notes": (c.get("argon_answer") or {}).get("definition_notes"), "prompt": _trim(c.get("prompt"), 4000),
                     "table_sha256": c.get("table_sha256")}
    return {**base, "available": True, "label": "PRECOMPUTED", "ts": rec.get("ts"), "file_mtime": mtime_iso(p),
            "model": rec.get("model"), "thinking": rec.get("thinking"), "kind": rec.get("label"),
            "tolerances": rec.get("tolerances"), "inputs": rec.get("inputs"), "summary": rec.get("summary"), "checks": checks}


def recheck_start(jobs, body: dict):
    if not has_module("agent.recheck"):
        raise ApiError(503, "unavailable", "agent.recheck not installed")
    if not have_env("GEMINI_API_KEY"):
        raise ApiError(503, "unavailable", "GEMINI_API_KEY is not set (.env): the live recheck calls Gemini")
    args = ["--jsonl"] + (["--no-atlas"] if TEST_MODE else [])
    job = jobs.start("recheck", "agent.recheck", args, {"atlas": not TEST_MODE},
                     "Argon re-derives our headline numbers (agent.recheck, live)", replace=bool(body.get("replace")))
    egress.add("Google Gemini API (gemini-4-argon, code execution)",
               "derived tables only (per-section-run relative levels, per-section positioning errors, per-trip NIOSH levels) "
               "and the definitions / frozen rule text; no audio, no sensor arrays", "/api/work/recheck/start")
    if not TEST_MODE:
        egress.add("MongoDB Atlas `rechecks`", "the recheck record (values, Argon's code and sandbox output)", "/api/work/recheck/start")
    return job


def recheck_canary(jobs, body: dict):
    if not has_module("agent.recheck"):
        raise ApiError(503, "unavailable", "agent.recheck not installed")
    if not (RECHECK / "latest.json").is_file():
        raise ApiError(503, "unavailable", "no saved recheck (latest.json) to plant errors in")
    return jobs.start("recheck_canary", "agent.recheck", ["--canary"], {"canary": True},
                      "recheck canary: planted errors in the comparator (offline)", replace=bool(body.get("replace")))


# --------------------------------------------------------------------------- ride reports
def _lookup(stats: dict, key: str, field: str) -> dict:
    st = stats.get(key) if isinstance(stats, dict) else None
    if not isinstance(st, dict):
        return {"found": False}
    meta = (st.get("fields") or {}).get(field) or {}
    if field == "value":
        v = st.get("value")
    elif field in (st.get("detail") or {}):
        v = st["detail"][field]
    else:
        v = st.get(field)
    return {"found": field in (st.get("fields") or {}) or field == "value", "stored": v, "unit": meta.get("unit") or st.get("unit"),
            "label": meta.get("label"), "source": st.get("source")}


def _review(rec: dict) -> dict:
    rv = rec.get("review")
    if isinstance(rv, dict):
        return {"status": rv.get("status") or "pending", "reviewer": rv.get("reviewer"), "ts": rv.get("ts")}
    return {"status": "pending", "reviewer": None, "ts": None}


def reports() -> dict:
    items = []
    for p in sorted(REPORTS.glob("*.json")):
        rec = read_json(p)
        if not isinstance(rec, dict):
            items.append({"run": p.stem, "readable": False})
            continue
        items.append({"run": rec.get("run") or p.stem, "readable": True, "ts": rec.get("ts"), "model": rec.get("model"),
                      "review": _review(rec), "n_verified": len(rec.get("verified_claims") or []),
                      "n_dropped": len(rec.get("dropped_claims") or []), "claims_in_range": rec.get("n_claims_ok_range"),
                      "seconds": rec.get("seconds_total"), "schema": rec.get("schema"), "seeded_canary": rec.get("seeded_canary"),
                      "md": (REPORTS / f"{p.stem}.md").is_file(), "mtime": mtime_iso(p)})
    have = {i["run"] for i in items}
    stats_runs = sorted(({p.stem for p in RIDE_STATS.glob("*.json")} | RUNS) & RUNS,
                        key=lambda r: (float(r[1:]) if r[1:].replace(".", "", 1).isdigit() else 99))
    return {"module": has_module("agent.ride_report"), "gemini_api_key": have_env("GEMINI_API_KEY"), "items": items,
            "runs": [{"run": r, "has_report": r in have, "has_stats": (RIDE_STATS / f"{r}.json").is_file()} for r in stats_runs],
            "approval": "CLI only: python -m agent.ride_report <run> --approve NAME (the web app never approves a ride report)"}


def _run_name(run: str) -> str:
    if not isinstance(run, str) or not _RUN.match(run) or run not in RUNS:
        raise ApiError(404, "not_found", "no such ride (R1..R15 or R1.5)")
    return run


def report(run: str) -> dict:
    run = _run_name(run)
    p = REPORTS / f"{run}.json"
    rec = read_json(p) if p.is_file() else None
    if not isinstance(rec, dict):
        raise ApiError(404, "not_found", f"no ride report for {run} yet")
    stats = read_json(RIDE_STATS / f"{run}.json") or {}
    lookups = {}
    for c in (rec.get("verified_claims") or []) + [d.get("claim") or {} for d in rec.get("dropped_claims") or [] if isinstance(d, dict)]:
        if not isinstance(c, dict) or not c.get("id"):
            continue
        facts = []
        for f in c.get("facts") or []:
            if isinstance(f, dict) and f.get("stat_key"):
                facts.append({**f, **_lookup(stats, f["stat_key"], f.get("field") or "value")})
        lookups[c["id"]] = facts
    last = {}
    for rd in rec.get("rounds") or []:
        for v in (rd or {}).get("verdicts") or []:
            if isinstance(v, dict) and v.get("id"):
                last[v["id"]] = {"ok": v.get("ok"), "reasons": v.get("reasons") or [], "round": rd.get("round")}
    md_p = REPORTS / f"{run}.md"
    return {"run": run, "label": "PRECOMPUTED", "file": rel(p), "file_mtime": mtime_iso(p), "review": _review(rec),
            "record": {k: v for k, v in rec.items() if k != "rounds"},
            "rounds": [{k: v for k, v in (rd or {}).items() if k not in ("usage_raw",)} for rd in rec.get("rounds") or []],
            "lookups": lookups, "last_verdicts": last, "stats_file_present": bool(stats),
            "md": md_p.read_text(encoding="utf-8") if md_p.is_file() else None,
            "approval": "CLI only: python -m agent.ride_report <run> --approve NAME"}


def report_start(jobs, body: dict):
    run = body.get("run")
    try:
        run = _run_name(run)
    except ApiError:
        raise ApiError(400, "bad_request", "run must be one of R1..R15 or R1.5") from None
    if not has_module("agent.ride_report"):
        raise ApiError(503, "unavailable", "agent.ride_report not installed")
    if not have_env("GEMINI_API_KEY"):
        raise ApiError(503, "unavailable", "GEMINI_API_KEY is not set (.env): writing a ride report calls Gemini")
    args = [run, "--jsonl"] + (["--no-atlas"] if TEST_MODE else [])
    job = jobs.start("ride_report", "agent.ride_report", args, {"run": run, "atlas": not TEST_MODE},
                     f"Argon writes the ride report for {run} (agent.ride_report, live)", replace=bool(body.get("replace")))
    egress.add("Google Gemini API (gemini-4-argon)", f"{run}'s derived statistics only (QC flags, sync quality, section times, "
               "tracker errors, relative levels); no audio, no sensor arrays, no notes", "/api/work/reports/start")
    if not TEST_MODE:
        egress.add("MongoDB Atlas `ride_reports`", f"the {run} report record (claims, checks, status DRAFT)", "/api/work/reports/start")
    return job
