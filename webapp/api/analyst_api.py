"""Analyst endpoints: the recorded Argon session with its checks recomputed now, and the live mini session.

What: GET /api/analyst/session builds one payload from demo.replay_analyst (jsonl, integrity, verdict, recheck,
sealed_test, failed_session, cfg_diff, ci, atlas_mirror) and agent.harness (LOG, ALLOWED, BASELINE and the
acceptance constants). Nothing here calls Gemini; the verdicts are re-derived from the logged metrics by the
harness's own accept(). POST /api/analyst/live/start launches `python -m agent.live_analyst --jsonl`.
Why: the Agent screen replays the recorded session with compressed time while the integrity checks run now.
`checked_at` is the time the checks were actually computed (the payload is cached for CACHE_S seconds, so it can
be a few seconds old; it is never a made-up "now").
"""
from __future__ import annotations

import threading
import time

from . import egress
from .util import ApiError, has_module, have_env, now_iso, read_json

CACHE_S = 30.0
_cache: dict = {}
_lock = threading.Lock()


def _caveat(RA, H, att: list[dict]) -> str:
    dev = [r for r in att if r["kind"] == "dev" and r["vs_baseline"]]
    acc = [r for r in dev if r["vs_baseline"]["accepted"]]
    sub = next((r for r in att if r["kind"] == "submit"), None)
    a = next((r for r in acc if sub and r["config_hash"] == sub["config_hash"]), acc[0] if acc else None)
    tested = any(r["kind"] == "test" for r in att)
    if not a:
        return f"Caveat: 0 of {len(dev)} configurations accepted; the baseline stands."
    c = a["vs_baseline"]["error_delta_ci_m"]
    return (f"Caveat: a small gain - error CI upper bound {c[2]:+.2f} m (only just below 0), {len(acc)} of {len(dev)} "
            f"configurations accepted, {a['metrics']['n_runs']} dev runs from one morning, errors vs a yaw curve-edge "
            "proxy; " + ("see the single sealed test above." if tested else "the single sealed 24 Oct test has not been scored yet."))


def _build(atlas: bool) -> dict:
    from agent import harness as H
    from demo import replay_analyst as RA
    att, ses = RA.jsonl(H.LOG), RA.jsonl(RA.SESSION)
    if not att or not ses:
        raise ApiError(503, "unavailable", "The recorded analyst session files are not present.")
    by_attempt = {r["attempt"]: r for r in att}
    base = next(r for r in att if r["kind"] == "dev" and r["config"] == H._check({}))
    g = RA.integrity(att)
    ch = g["chain"]
    integrity = {"chain_ok": not ch["broken"], "lines": ch["lines"], "broken": ch["broken"], "harness_sha": g["disk"],
                 "sha_ok": g["sha_ok"], "tag": RA.TAG if g["tag"] else None, "tag_commit": g["tag_commit"],
                 "tag_sha_ok": g["tag_sha_ok"], "final_hash_ok": g["final_hash_ok"], "final_at_tag_ok": g["final_at_tag_ok"]}
    events, n_exp = [], 0
    for e in ses:
        name = e.get("name")
        if e["kind"] == "start":
            events.append({"ts": e["ts"], "kind": "start", "max_experiments": e.get("max_experiments")})
        elif e["kind"] == "tool" and name in ("list_experiments", "get_baseline"):
            events.append({"ts": e["ts"], "kind": "tool", "name": name})
        elif e["kind"] == "tool" and name == "run_experiment":
            n_exp += 1
            res = e.get("result") or {}
            ev = {"ts": e["ts"], "kind": "tool", "name": name, "n": n_exp, "rationale": e.get("rationale"),
                  "change": RA.cfg_diff(e["args"]), "args": e["args"], "attempt": None, "hash": None, "prev_hash": None,
                  "config_hash": res.get("config_hash"), "metrics": None, "vs": None, "verdict": None, "refused": None}
            if "error" in res:
                ev["refused"] = str(res["error"])
            else:
                line = by_attempt[res["attempt"]]
                m, vs = line["metrics"], line["vs_baseline"]
                acc, why = RA.verdict(vs, m)
                ev.update({"attempt": line["attempt"], "hash": line["hash"], "prev_hash": line["prev_hash"],
                           "metrics": {k: m.get(k) for k in ("median_error_m", "coverage_mean", "band_width_mean_m", "n_runs",
                                                             "timetable_median_m", "synthetic_stop")},
                           "vs": {"width_change": vs["width_change"], "error_delta_ci_m": vs["error_delta_ci_m"],
                                  "accepted": vs["accepted"], "coverage_ok": vs.get("coverage_ok"),
                                  "track_coverage_drop": vs["track_coverage_drop"]},
                           "ci_text": RA.ci(vs["error_delta_ci_m"]),
                           "verdict": {"accepted": acc, "reason": why, "recheck_same": RA.recheck(base["metrics"], m, vs)}})
            events.append(ev)
        elif e["kind"] == "tool" and name == "submit_final":
            res = e.get("result") or {}
            events.append({"ts": e["ts"], "kind": "tool", "name": name, "change": RA.cfg_diff(e["args"]), "args": e["args"],
                           "rationale": e.get("rationale"), "config_hash": res.get("config_hash"),
                           "final_matches_log": g["final_hash_ok"], "final_matches_tag": g["final_at_tag_ok"],
                           "tag": RA.TAG, "tag_commit": g["tag_commit"]})
        elif e["kind"] == "report":
            events.append({"ts": e["ts"], "kind": "report", "text": e.get("text"), "seconds": e.get("seconds"),
                           "usage": e.get("usage")})
    bm = base["metrics"]
    test = next((r for r in att if r["kind"] == "test"), None)
    v1 = (read_json(RA.V1_FORWARD, {}) or {}).get("overall", {})
    sealed = None
    rows = RA.sealed_test(att)
    if rows is not None:
        tm = test["metrics"]
        sealed = {"rows": rows, "v2": {"attempt": test["attempt"], "ts": test["ts"], "median_error_m": tm["median_error_m"],
                                       "coverage_mean": tm["coverage_mean"], "band_width_mean_m": tm["band_width_mean_m"],
                                       "timetable_median_m": tm.get("timetable_median_m"), "n_runs": tm["n_runs"]},
                  "v1": {"median_m": v1.get("tracker_median_of_runs_m"), "coverage": v1.get("band90_coverage_mean"),
                         "n_sections": v1.get("n_sections"), "timetable_median_m": v1.get("timetable_median_of_runs_m")}}
    rep = ses[-1] if ses[-1]["kind"] == "report" else {}
    payload = {"label": "REPLAY", "model": ses[0].get("model"), "thinking": ses[0].get("thinking"),
               "recorded": {"start": ses[0]["ts"], "end": ses[-1]["ts"], "seconds": rep.get("seconds")},
               "checked_at": now_iso(), "integrity": integrity, "atlas_mirror": None,
               "allowed": H.ALLOWED, "baseline_config": H.BASELINE,
               "rule": {"text": (f"ACCEPT only if mean 90% coverage >= {H.MIN_COVERAGE:g} and no track's coverage drops > "
                                 f"{H.TRACK_COV_DROP:g} below baseline, AND (95% CI of the paired per-run error delta entirely "
                                 f"< 0, or band >= {H.WIDTH_GAIN:.0%} narrower with CI upper <= {H.ERR_SLACK_M:g} m). Fixed in "
                                 "the harness before the session; Argon cannot edit it."),
                        "min_coverage": H.MIN_COVERAGE, "track_cov_drop": H.TRACK_COV_DROP, "width_gain": H.WIDTH_GAIN,
                        "err_slack_m": H.ERR_SLACK_M},
               "baseline": {"attempt": base["attempt"], "ts": base["ts"], "config": base["config"],
                            "metrics": {k: bm.get(k) for k in ("median_error_m", "coverage_mean", "band_width_mean_m", "n_runs")}},
               "events": events,
               "chain": [{"attempt": r["attempt"], "kind": r["kind"], "hash": r["hash"], "prev_hash": r["prev_hash"],
                          "ts": r["ts"]} for r in att],
               "sealed": sealed, "failed_session": RA.failed_session(att), "caveat": _caveat(RA, H, att)}
    return payload


def _mirror(atlas: bool) -> str | None:
    if not atlas:
        return None
    from agent import harness as H
    from demo import replay_analyst as RA
    egress.add("MongoDB Atlas", "hashes of the analyst log lines (read-only count in `attempts`)", "/api/analyst/session")
    return RA.atlas_mirror(RA.jsonl(H.LOG))


def session(atlas: bool = False) -> dict:
    with _lock:
        c = _cache.get("base")
        if c is None or time.time() - c["t"] > CACHE_S:
            c = _cache["base"] = {"t": time.time(), "value": _build(False)}
        m = _cache.get("mirror")
        if atlas and (m is None or time.time() - m["t"] > 60):
            m = _cache["mirror"] = {"t": time.time(), "value": _mirror(True), "at": now_iso()}
    out = {**c["value"], "cached_age_s": round(time.time() - c["t"], 1)}
    if atlas and m:
        out["atlas_mirror"] = m["value"]
        out["atlas_mirror_checked_at"] = m["at"]
    return out


def live_start(jobs, body: dict):
    mx = body.get("max_experiments", 2)
    if isinstance(mx, bool) or mx not in (1, 2):
        raise ApiError(400, "bad_request", "max_experiments must be 1 or 2")
    if not has_module("agent.live_analyst"):
        raise ApiError(503, "unavailable", "agent.live_analyst not installed yet")
    if not have_env("GEMINI_API_KEY"):
        raise ApiError(503, "unavailable", "GEMINI_API_KEY is not set (.env)")
    replace = bool(body.get("replace", False))
    job = jobs.start("analyst", "agent.live_analyst", ["--jsonl", "--max-experiments", str(mx)],
                     {"max_experiments": mx}, f"live analyst session, up to {mx} experiment(s)", replace=replace)
    egress.add("Google Gemini API (gemini-4-argon)", "tracker settings and metrics (no sensor data)", "/api/analyst/live/start")
    return job
