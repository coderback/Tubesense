"""Forward-in-time test: track new rides with the maps frozen before they were recorded.

The maps in outputs/prereg/maps_23Oct.json were built from the 23 Oct rides only and frozen (git tag
prereg-24oct) before the 24 Oct rides. Every clean section of a later ride on a mapped track is tracked
with the frozen map (online.track, frozen settings) and scored like scripts/online_eval.py: MAP vs the
independent truth (the ride's curve edges matched to the map's base run, interpolated, no DTW), the
timetable baseline, and 90 % band coverage. Pre-registered criteria: outputs/PLAN_24Oct.md (H2b).
Writes outputs/online/forward_test.json. Run: .venv/Scripts/python scripts/forward_test.py
"""
from __future__ import annotations

import json

import numpy as np

from online_eval import summary, truths  # sibling script: same truth and summaries as the LOO test
from tubesense import online as O
from tubesense.ingest import ROOT

FROZEN = ROOT / "outputs" / "prereg" / "maps_23Oct.json"
OUT = ROOT / "outputs" / "online" / "forward_test.json"


def main():
    if not FROZEN.exists():
        print("no frozen maps:", FROZEN)
        return
    maps = {k: O.Template.from_json(v) for k, v in json.load(open(FROZEN)).items()}
    S = O.section_runs()
    rows = []
    for trk, tpl in maps.items():
        srs = S.get(trk, [])
        base = next((s for s in srs if s.key == tpl.base), None)
        new = [s for s in srs if s.key not in tpl.runs]
        if base is None or not new:
            continue
        m = tpl.metres
        for a in new:
            _, indep, edges = truths(a, base)
            onset, arrive = a.sec["depart_wall"], a.sec["arrive_wall"]
            est = O.track(tpl, a.feat, onset, arrive)
            wall = est.wall.values
            ti = indep(wall)
            tt = np.clip((wall - onset) / tpl.duration_s, 0, 1)
            rows.append({"held_out": a.key, "track": trk, "map_runs": tpl.runs, "n_curve_edges": len(edges),
                         "departure_missed_online": bool(np.isnan(a.depart_shift_s)),
                         "map_vs_independent_m": summary(np.abs(m(est["map"].values) - m(ti))),
                         "timetable_vs_independent_m": summary(np.abs(m(tt) - m(ti))),
                         "band90_coverage_independent": round(float(((ti >= est.q05.values - 1e-9) &
                                                                      (ti <= est.q95.values + 1e-9)).mean()), 3)})
            r = rows[-1]
            print(f"{a.key:14s} {trk:12s} tracker {r['map_vs_independent_m'].get('median')} / timetable "
                  f"{r['timetable_vs_independent_m'].get('median')} m | coverage {r['band90_coverage_independent']}"
                  f"{' | departure missed' if r['departure_missed_online'] else ''}")
    med = lambda k: round(float(np.median([r[k]["median"] for r in rows if r[k].get("n")])), 1) if rows else None
    overall = {"n_sections": len(rows), "tracker_median_of_runs_m": med("map_vs_independent_m"),
               "timetable_median_of_runs_m": med("timetable_vs_independent_m"),
               "band90_coverage_mean": round(float(np.mean([r["band90_coverage_independent"] for r in rows])), 3) if rows else None}
    json.dump({"frozen_maps": FROZEN.relative_to(ROOT).as_posix(), "overall": overall, "runs": rows, "doc": __doc__},
              open(OUT, "w"), indent=1, default=float)
    print("OVERALL", overall)


if __name__ == "__main__":
    main()
