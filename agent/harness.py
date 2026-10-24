"""Fixed scoring harness for the Argon analyst: the agent calls these functions, it cannot edit them.

Rules (stated before any 24 Oct data exists; docs/ARCHITECTURE.md section 5):
- Dev split = the 23 Oct runs, leave-one-run-out (posthoc.eval_v2). Test split = the 24 Oct runs,
  forward-in-time with maps from 23 Oct only. The agent never sees test numbers.
- Only allow-listed settings, only allow-listed values (ALLOWED). Everything else is rejected.
- Every call is appended to outputs_posthoc/attempts.jsonl as a hash chain (each line carries the
  previous line's hash and this file's SHA-256) and mirrored to Atlas `attempts` when reachable.
- One final configuration (submit_final), then ONE test scoring in total (score_test): a global
  budget enforced from the log, not per configuration.
- Acceptance against the baseline is decided by the pre-declared rule in accept(), not by the agent.
Each experiment runs in a subprocess (agent._eval) with a timeout, so module settings never leak.
Run a quick check: PYTHONPATH=. .venv/Scripts/python -m agent.harness --baseline
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
LOG = Path(os.environ.get("TUBESENSE_HARNESS_LOG", ROOT / "outputs_posthoc" / "attempts.jsonl"))
FINAL = LOG.with_name("final_config.json")
TIMEOUT_S = 600
HARNESS_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

ALLOWED = {
    "GAMMA": [0.1, 0.25, 0.5, 1.0],          # likelihood tempering
    "YAW_SD_FLOOR": [0.2, 0.35, 0.5, 0.8],   # deg/s added in quadrature to the map's sd
    "P_SPEED": [0.01, 0.03, 0.1],            # per-step speed-level change probability
    "stop_gate": [True, False],              # posthoc.tracker_v2 gate (attempt D settings) on/off
}
BASELINE = {"GAMMA": 0.25, "YAW_SD_FLOOR": 0.35, "P_SPEED": 0.03, "stop_gate": True}
MIN_COVERAGE = 0.85      # mean 90 % band coverage a configuration must keep
TRACK_COV_DROP = 0.03    # and no track's coverage may fall more than this below the baseline's
WIDTH_GAIN = 0.10        # a >= 10 % narrower band counts as a gain ...
ERR_SLACK_M = 0.5        # ... if the error CI upper bound stays within this
N_BOOT, SEED = 4000, 0


# --------------------------------------------------------------------------- log
def _lines() -> list[dict]:
    if not LOG.exists():
        return []
    return [json.loads(x) for x in LOG.read_text(encoding="utf-8").splitlines() if x.strip()]


def _append(rec: dict) -> dict:
    LOG.parent.mkdir(exist_ok=True)
    prev = _lines()
    rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "attempt": len(prev),
           "harness_sha": HARNESS_SHA, **rec, "prev_hash": prev[-1]["hash"] if prev else None}
    rec["hash"] = hashlib.sha256(json.dumps(rec, sort_keys=True, default=float).encode()).hexdigest()
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, default=float) + "\n")
    try:  # mirror for the demo; the local file is the record of truth
        from atlas.db import db
        db().attempts.insert_one({**rec, "_id": rec["hash"]})
    except Exception as e:  # noqa: BLE001  (offline is fine)
        print(f"(atlas mirror skipped: {type(e).__name__})", file=sys.stderr)
    return rec


def verify_log() -> dict:
    """Check the hash chain: every line's hash and its link to the previous line."""
    bad, prev = [], None
    for r in _lines():
        h = r.pop("hash")
        ok = hashlib.sha256(json.dumps(r, sort_keys=True, default=float).encode()).hexdigest() == h and r["prev_hash"] == prev
        if not ok:
            bad.append(r["attempt"])
        prev = h
    return {"lines": len(_lines()), "broken": bad}


# --------------------------------------------------------------------------- running
def _check(cfg: dict) -> dict:
    unknown = set(cfg) - set(ALLOWED)
    if unknown:
        raise ValueError(f"not allowed: {sorted(unknown)}; allowed settings: {sorted(ALLOWED)}")
    full = {**BASELINE, **cfg}
    for k, v in full.items():
        if v not in ALLOWED[k]:
            raise ValueError(f"{k}={v!r} not allowed; choose one of {ALLOWED[k]}")
    # canonical types, so 1 and 1.0 hash (and cache) as the same configuration
    return {k: bool(v) if isinstance(BASELINE[k], bool) else float(v) for k, v in full.items()}


def _chash(cfg: dict) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:12]


def _run(cfg: dict, split: str) -> dict:
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    p = subprocess.run([sys.executable, "-m", "agent._eval", "--config", json.dumps(cfg), "--split", split],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=TIMEOUT_S)
    if p.returncode != 0 or "@@RESULT@@" not in p.stdout:
        raise RuntimeError(f"evaluation failed (exit {p.returncode}): {p.stderr[-800:]}")
    return json.loads(p.stdout.split("@@RESULT@@", 1)[1])


def _summary(res: dict) -> dict:
    r = res["runs"]
    med = lambda k: round(float(np.median([x[k] for x in r if x[k] is not None])), 2)
    out = {"n_runs": len(r), "median_error_m": med("median_m"), "timetable_median_m": med("timetable_median_m"),
           "coverage_mean": round(float(np.mean([x["coverage"] for x in r])), 3),
           "band_width_mean_m": round(float(np.mean([x["width_m"] for x in r])), 1),
           "per_run": {x["held_out"]: {k: x[k] for k in ("median_m", "coverage", "width_m")} for x in r}}
    ss = res.get("synthetic_stop") or {}
    if ss:
        out["synthetic_stop"] = {v: (ss.get(v) or {}).get("v2", {}).get("err_end_of_stop_m_median")
                                 for v in ("ideal", "quiet", "dwell")}
    if res.get("per_track"):
        out["per_track"] = {t: {k: v for k, v in d.items() if "v2" in k} for t, d in res["per_track"].items()}
    return out


def _paired_ci(a: dict, b: dict, key: str) -> list[float]:
    """95 % bootstrap CI of the mean per-run difference b - a over the runs both have."""
    ks = sorted(set(a) & set(b))
    d = np.array([b[k][key] - a[k][key] for k in ks], float)
    rng = np.random.default_rng(SEED)
    boot = d[rng.integers(0, len(d), (N_BOOT, len(d)))].mean(1)
    return [round(float(np.percentile(boot, 2.5)), 2), round(float(d.mean()), 2), round(float(np.percentile(boot, 97.5)), 2)]


def accept(base: dict, cand: dict) -> dict:
    """Pre-declared rule: mean coverage >= MIN_COVERAGE and no track's coverage more than TRACK_COV_DROP
    below the baseline's (a sharper band must not buy its width with a worse-calibrated section); then
    either the error CI is entirely below 0, or the band is >= WIDTH_GAIN narrower with the error CI
    upper bound <= ERR_SLACK_M."""
    err = _paired_ci(base["per_run"], cand["per_run"], "median_m")
    wid = (cand["band_width_mean_m"] - base["band_width_mean_m"]) / base["band_width_mean_m"]
    k = "v2_band90_coverage_independent"
    drops = {t: round(base["per_track"][t][k] - d[k], 3) for t, d in cand.get("per_track", {}).items()
             if t in base.get("per_track", {})}
    cov_ok = cand["coverage_mean"] >= MIN_COVERAGE and all(x <= TRACK_COV_DROP for x in drops.values())
    better = err[2] < 0 or (wid <= -WIDTH_GAIN and err[2] <= ERR_SLACK_M)
    return {"error_delta_ci_m": err, "width_change": round(float(wid), 3), "track_coverage_drop": drops,
            "coverage_ok": cov_ok, "accepted": bool(cov_ok and better)}


# --------------------------------------------------------------------------- the agent's tools
def list_experiments() -> dict:
    """The settings the analyst may change, their allowed values, the baseline, and the acceptance rule."""
    return {"allowed": ALLOWED, "baseline": BASELINE, "dev_split": "23 Oct runs, leave-one-run-out",
            "acceptance": accept.__doc__, "min_coverage": MIN_COVERAGE,
            "already_run": {r["config_hash"]: r["config"] for r in _lines() if r["kind"] == "dev"}}


def baseline() -> dict:
    """Dev metrics of the baseline configuration (run once, then read from the log)."""
    h = _chash(_check({}))
    for r in _lines():
        if r["kind"] == "dev" and r["config_hash"] == h:
            return r["metrics"]
    return run_experiment({}, rationale="baseline")["metrics"]


def run_experiment(config: dict, rationale: str = "") -> dict:
    """Score one configuration on the 23 Oct dev split and compare it with the baseline."""
    cfg = _check(config)
    h = _chash(cfg)
    for r in _lines():
        if r["kind"] == "dev" and r["config_hash"] == h:
            return {k: r[k] for k in ("attempt", "config", "config_hash", "metrics", "vs_baseline")} | {"cached": True}
    m = _summary(_run(cfg, "dev"))
    is_base = cfg == BASELINE
    vs = None if is_base else accept(baseline(), m)
    rec = _append({"kind": "dev", "config": cfg, "config_hash": h, "rationale": rationale, "metrics": m,
                   "vs_baseline": vs})
    return {k: rec[k] for k in ("attempt", "config", "config_hash", "metrics", "vs_baseline")}


def submit_final(config: dict, rationale: str) -> dict:
    """Declare the one configuration that will be scored on the sealed 24 Oct test. Once only."""
    cfg = _check(config)
    h = _chash(cfg)
    if any(r["kind"] == "submit" for r in _lines()):
        raise RuntimeError("a final configuration was already submitted")
    if not any(r["kind"] == "dev" and r["config_hash"] == h for r in _lines()):
        raise RuntimeError("run this configuration on the dev split before submitting it")
    rec = _append({"kind": "submit", "config": cfg, "config_hash": h, "rationale": rationale})
    FINAL.write_text(json.dumps({"config": cfg, "config_hash": h, "submitted": rec["ts"], "log_hash": rec["hash"],
                                 "rationale": rationale}, indent=1), encoding="utf-8")
    return {"submitted": cfg, "config_hash": h}


def score_test() -> dict:
    """Score the submitted configuration once on the 24 Oct forward test (not an agent tool)."""
    lines = _lines()
    if any(r["kind"] == "test" for r in lines):
        raise RuntimeError("the test budget (1) is spent; see the log")
    sub = [r for r in lines if r["kind"] == "submit"]
    if not sub:
        raise RuntimeError("no submitted configuration")
    cfg = sub[-1]["config"]
    res = _run(cfg, "test")
    if not res["runs"]:
        raise RuntimeError("no clean 24 Oct sections on a mapped track: nothing scored, budget not spent")
    m = _summary(res)
    m["runs"] = res["runs"]
    _append({"kind": "test", "config": cfg, "config_hash": sub[-1]["config_hash"], "metrics": m})
    return m


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", action="store_true", help="run (or read) the baseline on dev")
    ap.add_argument("--verify", action="store_true", help="check the log's hash chain")
    a = ap.parse_args()
    if a.verify:
        print(verify_log())
    if a.baseline:
        print(json.dumps(baseline(), indent=1))
