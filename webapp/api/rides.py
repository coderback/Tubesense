"""Ride catalogue (GET /api/rides), ride start validation, and the PRECOMPUTED ride-stream listing.

What: builds the list of recorded Central line sections the web app can replay, from JSON files only
(outputs/sections.json, outputs/gonogo/repeatability.json, outputs/qc.json via the worker, outputs/online/
forward_test.json, outputs_posthoc/attempts.jsonl, outputs_posthoc/lengths.json) plus a file-existence
check of data/raw/phyphox. It never loads sensor data and never imports tubesense.* (pandas and scipy).
Why: the server process stays small; the ride worker (webapp/api/ride_worker.py) does the heavy work.
Station constants are copied from tubesense/stops.py STATIONS (same numbers).
"""
from __future__ import annotations

import json
import re
import threading
import time

from .util import ROOT, ApiError, mtime_iso, read_json, read_jsonl

PHYPHOX = ROOT / "data" / "raw" / "phyphox"
PRECOMPUTED = ROOT / "webapp" / "precomputed"
STATIONS = {"LST": {"name": "Liverpool Street", "lat": 51.517372, "lon": -0.083182},
            "BTH": {"name": "Bethnal Green", "lat": 51.527222, "lon": -0.055506},
            "MLE": {"name": "Mile End", "lat": 51.525122, "lon": -0.03364}}
TRACKS = (("EB", "LST", "BTH"), ("EB", "BTH", "MLE"), ("WB", "MLE", "BTH"), ("WB", "BTH", "LST"))
DEV_DAY = {"R1", "R1.5", "R2", "R3", "R4", "R5", "R6"}          # 23 Oct rides that build the maps
DEV_ALL = DEV_DAY | {"R0", "R0.5"}
RIDE_RUNS = DEV_DAY | {f"R{i}" for i in range(7, 13)}
SPEEDS = (1, 4, 8)
VARIANTS = ("ideal", "quiet", "dwell")
RECOMMENDED = {("R9", "EB LST->BTH"), ("R5", "EB LST->BTH")}
V2_SRC = "outputs_posthoc/attempts.jsonl attempt {n} (sealed test, scored once {ts})"
V1_SRC = "outputs/online/forward_test.json (pre-registered H2b)"
LEN_SRC = "TfL FOI-3481-2425, Central line Working Timetable Notice TTN 37/25"

_cache: dict = {"t": 0.0, "value": None}
_lock = threading.Lock()


def track_name(d: str, a: str, b: str) -> str:
    return f"{d} {a}->{b}"


def sample_note(run_id: str) -> str:
    if run_id in DEV_ALL:
        return f"{run_id} is 23 Oct dev data: in-sample for the v2 config; the map is leave-one-out (built without it)"
    return f"{run_id} is not in the 23 Oct dev data: out-of-sample for the tracker settings and the map"


def _reason(meta: dict | None) -> str:
    if not meta:
        return "section not in outputs/gonogo/repeatability.json"
    flags = meta.get("flags") or []
    out = []
    for f in flags:
        ev = (f.get("evidence") or "")
        t = f.get("type", "")
        if t == "section_truncated":
            out.append("section_truncated: " + ("arrival not recorded" if "arrive" in ev else "departure not recorded"))
        elif t in ("pause", "section_overlaps_pause", "floor_like_placement", "false_start", "split_run"):
            out.append(t)
        elif t.startswith("handled") or t == "handheld":
            out.append(t)
    return "; ".join(dict.fromkeys(out)) or "not clean in outputs/gonogo/repeatability.json"


def _build() -> dict:
    secs = read_json(ROOT / "outputs" / "sections.json", {"sections": [], "runs": []})
    rep = (read_json(ROOT / "outputs" / "gonogo" / "repeatability.json", {}) or {}).get("sections", {})
    fwd = read_json(ROOT / "outputs" / "online" / "forward_test.json", {}) or {}
    att = read_jsonl(ROOT / "outputs_posthoc" / "attempts.jsonl")
    lens = (read_json(ROOT / "outputs_posthoc" / "lengths.json", {}) or {}).get("sections", {})
    test = next((a for a in reversed(att) if a.get("kind") == "test"), None)
    tm = (test or {}).get("metrics", {})
    v2_runs = {r["held_out"]: r for r in tm.get("runs", [])}
    v1_runs = {r["held_out"]: r for r in fwd.get("runs", [])}
    dwells = {r["stem"]: [d for d in r.get("dwells", []) if not d.get("virtual")] for r in secs.get("runs", [])}
    tracks = {track_name(*t) for t in TRACKS}

    def key_of(x: dict) -> str:
        return f"{x['run_id']}:{x['from']}-{x['to']}"

    def is_map_run(x: dict) -> bool:
        k = key_of(x)
        return x["run_id"] in DEV_DAY and k in rep and bool(rep[k].get("clean"))

    sections = [x for x in secs["sections"]
                if x["run_id"] in RIDE_RUNS and track_name(x["direction"], x["from"], x["to"]) in tracks]
    map_keys: dict[str, list[str]] = {}
    for x in sections:
        if is_map_run(x):
            map_keys.setdefault(track_name(x["direction"], x["from"], x["to"]), []).append(key_of(x))

    rides = []
    for x in sections:
        trk = track_name(x["direction"], x["from"], x["to"])
        key = key_of(x)
        meta = rep.get(key)
        others = [k for k in map_keys.get(trk, []) if k.split(":")[0] != x["run_id"]]
        reason = None
        if not (PHYPHOX / f"{x['stem']}.xlsx").exists():
            reason = "phyphox file missing"
        elif not meta or not meta.get("clean"):
            reason = _reason(meta)
        elif not others:
            reason = "no other clean 23 Oct run for a leave-one-out map"
        day = "23 Oct" if x["run_id"] in DEV_DAY else "24 Oct"
        fw = v1_runs.get(key)
        score = None
        if day == "24 Oct" and test is not None and key in tm.get("per_run", {}):
            pr, rr = tm["per_run"][key], v2_runs.get(key, {})
            score = {"v2": {"median_m": pr["median_m"], "p90_m": rr.get("p90_m"), "coverage": pr["coverage"],
                            "width_m": pr["width_m"], "source": V2_SRC.format(n=test["attempt"], ts=test["ts"])}}
            if fw:
                score["v1"] = {"median_m": fw["map_vs_independent_m"]["median"], "p90_m": fw["map_vs_independent_m"].get("p90"),
                               "coverage": fw["band90_coverage_independent"], "source": V1_SRC}
        missed = bool(fw and fw.get("departure_missed_online")) if day == "24 Oct" else False
        rides.append({"run_id": x["run_id"], "stem": x["stem"], "key": key, "track": trk, "day": day,
                      "available": reason is None, "reason": reason,
                      "recommended": reason is None and (x["run_id"], trk) in RECOMMENDED and not missed,
                      "sample_note": sample_note(x["run_id"]), "departure_missed_online": missed,
                      "has_dwell": bool(dwells.get(x["stem"])), "duration_s": x.get("duration_s"),
                      "offline_score": score})
    trk_out = []
    for d, a, b in TRACKS:
        L = lens.get(f"{a}-{b}", lens.get(f"{b}-{a}", {}))
        trk_out.append({"track": track_name(d, a, b), "direction": d, "from": a, "to": b,
                        "length_wtt_m": L.get("m"), "length_chord_m": L.get("chord_m"),
                        "length_source": L.get("source", LEN_SRC), "map_runs_23oct": map_keys.get(track_name(d, a, b), [])})
    return {"stations": STATIONS, "tracks": trk_out, "rides": rides,
            "defaults": {"demo": {"run": "R9", "track": "EB LST->BTH", "tracker": "v2", "speed": 8, "truth": True},
                         "stop_demo": {"run": "R5", "track": "EB LST->BTH", "tracker": "v2", "speed": 8,
                                       "synthetic_stop": 0.5, "stop_variant": "quiet", "truth": True}},
            "speeds": list(SPEEDS)}


def catalogue(refresh: bool = False) -> dict:
    with _lock:
        if refresh or _cache["value"] is None:
            _cache["value"], _cache["t"] = _build(), time.time()
        return _cache["value"]


def find_ride(run: str, track: str) -> dict | None:
    return next((r for r in catalogue()["rides"] if r["run_id"] == run and r["track"] == track), None)


def validate_start(body: dict) -> dict:
    """Checks a POST /api/ride/start body; returns the normalised parameters."""
    run, track = body.get("run"), body.get("track")
    if not isinstance(run, str) or not isinstance(track, str):
        raise ApiError(400, "bad_request", "run and track are required strings")
    ride = find_ride(run, track)
    if ride is None:
        raise ApiError(400, "bad_request", f"{run} {track} is not in the ride catalogue")
    if not ride["available"]:
        raise ApiError(400, "bad_request", f"{run} {track} is not available: {ride['reason']}")
    tracker = body.get("tracker", "v2")
    if tracker not in ("v1", "v2"):
        raise ApiError(400, "bad_request", "tracker must be v1 or v2")
    speed = body.get("speed", 8)
    if isinstance(speed, bool) or speed not in SPEEDS:
        raise ApiError(400, "bad_request", "speed must be 1, 4 or 8")
    stop = body.get("synthetic_stop")
    if stop is not None and (isinstance(stop, bool) or not isinstance(stop, (int, float)) or not 0.1 <= stop <= 0.9):
        raise ApiError(400, "bad_request", "synthetic_stop must be null or a fraction from 0.1 to 0.9")
    variant = body.get("stop_variant", "quiet")
    if variant not in VARIANTS:
        raise ApiError(400, "bad_request", "stop_variant must be ideal, quiet or dwell")
    if stop is not None and variant != "ideal" and not ride["has_dwell"]:
        raise ApiError(400, "bad_request", f"{run} has no recorded platform dwell: use stop_variant ideal")
    flags = {}
    for k, default in (("truth", True), ("hold", False), ("replace", False)):
        v = body.get(k, default)
        if not isinstance(v, bool):
            raise ApiError(400, "bad_request", f"{k} must be true or false")
        flags[k] = v
    return {"run": run, "track": track, "tracker": tracker, "speed": int(speed),
            "synthetic_stop": None if stop is None else float(stop), "stop_variant": variant, **flags}


def worker_args(p: dict) -> list[str]:
    a = ["--run", p["run"], "--track", p["track"], "--tracker", p["tracker"], "--speed", str(p["speed"]),
         "--stdin-control"]
    if p["synthetic_stop"] is not None:
        a += ["--stop", str(p["synthetic_stop"]), "--stop-variant", p["stop_variant"]]
    if not p["truth"]:
        a.append("--no-truth")
    if p["hold"]:
        a.append("--hold")
    return a


def ride_label(p: dict) -> str:
    return (f"{p['run']} {p['track']}, tracker {p['tracker']}, {p['speed']}x"
            + (f", synthetic stop {p['synthetic_stop']:.0%}" if p["synthetic_stop"] is not None else ""))


# --------------------------------------------------------------------------- PRECOMPUTED streams
_FILE = re.compile(r"^[\w.\-]+\.jsonl$")


def precomputed_list() -> dict:
    items = []
    for f in sorted(PRECOMPUTED.glob("*.jsonl")) if PRECOMPUTED.exists() else []:
        try:
            with open(f, encoding="utf-8") as fh:
                meta = json.loads(fh.readline())
        except (OSError, ValueError):
            continue
        if meta.get("type") != "meta":
            continue
        a = meta.get("argv") or {}
        items.append({"file": f.name, "run": a.get("run"), "track": a.get("track"), "tracker": a.get("tracker"),
                      "synthetic_stop": a.get("stop"), "stop_variant": a.get("stop_variant") if a.get("stop") is not None else None,
                      "generated": meta.get("generated") or mtime_iso(f)})
    return {"items": items}


def precomputed_file(name: str):
    if not _FILE.match(name):
        raise ApiError(400, "bad_request", "bad file name")
    p = PRECOMPUTED / name
    if not p.is_file():
        raise ApiError(404, "not_found", f"no precomputed stream {name}")
    return p
