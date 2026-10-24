"""Subprocess entry point for agent/harness.py: one tracker configuration, one split, JSON on stdout.

dev:  leave-one-run-out on the 23 Oct runs (posthoc.eval_v2.main, synthetic stops included), with the
      configuration applied as tubesense.online module settings + posthoc.tracker_v2.GateParams.
test: forward-in-time on the 24 Oct runs (R7+): maps built from ALL clean 23 Oct runs of each track
      (WTT metres), every clean 24 Oct section of that track tracked with the configuration and
      scored against its independent curve-edge truth, as scripts/forward_test.py does for v1.
Run only through agent.harness (it owns the log and the test budget).
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import contextlib
import io
import json
import os
import tempfile

import numpy as np

from tubesense import online as O

from posthoc import eval_v2 as EV
from posthoc import lengths as LN
from posthoc import tracker_v2 as V2

OFF = -1e9  # z_on that never engages: the gate disabled, v2 == v1 arithmetic


def apply(cfg: dict) -> V2.GateParams:
    for k in ("GAMMA", "YAW_SD_FLOOR", "P_SPEED", "OUTLIER"):
        if k in cfg:
            setattr(O, k, float(cfg[k]))
    return V2.GateParams() if cfg.get("stop_gate", True) else V2.GateParams(z_on=OFF, z_off=OFF)


def dev(prm: V2.GateParams) -> dict:
    EV.OUT = __import__("pathlib").Path(tempfile.mkdtemp(prefix="tubesense_harness_"))
    with contextlib.redirect_stdout(io.StringIO()):
        res = EV.main(prm, note="harness", write=False, stops=True, log=False)
    runs = [{"held_out": r["held_out"], "track": r["track"],
             "median_m": r["v2"]["map_vs_independent_m"]["median"],
             "p90_m": r["v2"]["map_vs_independent_m"]["p90"],
             "coverage": r["v2"]["band90_coverage_independent"],
             "width_m": r["v2"]["band90_width_m"]["mean"],
             "timetable_median_m": r["v2"]["timetable_vs_independent_m"]["median"]} for r in res["runs"]]
    ss = res["overall"]["synthetic_stop"] or {}
    return {"runs": runs, "per_track": res["per_track"], "synthetic_stop": ss}


def test(prm: V2.GateParams) -> dict:
    secs, groups = EV.track_sections()                  # 23 Oct groups: the maps
    meta = json.load(open(EV.ROOT / "outputs" / "gonogo" / "repeatability.json"))["sections"]
    new = [x for x in secs["sections"] if x["run_id"] not in EV.DEV_RUNS and x["run_id"] not in O.EXCLUDE_RUNS]
    runs = []
    for trk, items in groups.items():
        tests = []
        for x in new:
            if O.track_name(x["direction"], x["from"], x["to"]) != trk:
                continue
            key = f"{x['run_id']}:{x['from']}-{x['to']}"
            if key in meta and meta[key]["clean"]:
                tests.append({**x, "_key": key, "_meta": meta[key]})
        if not tests:
            continue
        train = [s for s in EV.load_track(secs, items) if s.clean]
        if len(train) < 1:
            continue
        tpl = V2.scale_template(O.build_template(trk, train, LN.chord_m(*LN.stations_of(trk))), LN.wtt_m(trk))
        base = next(s for s in train if s.key == tpl.base)
        for a in EV.load_track(secs, tests):
            _, indep, edges = EV.E.truths(a, base)
            onset, arrive = a.sec["depart_wall"], a.sec["arrive_wall"]
            est = V2.track(tpl, a.feat, onset, arrive, prm=prm)
            ti = indep(est.wall.values)
            s = EV.score(est, tpl, ti, ti, onset)
            runs.append({"held_out": a.key, "track": trk, "map_runs": tpl.runs, "n_curve_edges": len(edges),
                         "median_m": s["map_vs_independent_m"].get("median"),
                         "p90_m": s["map_vs_independent_m"].get("p90"),
                         "coverage": s["band90_coverage_independent"], "width_m": s["band90_width_m"]["mean"],
                         "timetable_median_m": s["timetable_vs_independent_m"].get("median"),
                         "gate_steps": int(est.gate.sum())})
    return {"runs": runs}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--split", choices=("dev", "test"), required=True)
    a = ap.parse_args()
    prm = apply(json.loads(a.config))
    out = dev(prm) if a.split == "dev" else test(prm)
    out["settings"] = {k: getattr(O, k) for k in ("GAMMA", "YAW_SD_FLOOR", "P_SPEED", "OUTLIER")}
    out["gate"] = prm.to_json()
    sys.stdout.write("\n@@RESULT@@" + json.dumps(out, default=float))
