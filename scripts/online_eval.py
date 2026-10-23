"""Leave-one-run-out test of the online tracker, plus the maps for the live demo.

For each track with >= 2 clean runs and each held-out run A: build the map from the OTHER clean runs,
replay A's causal features step by step from its departure (online.track), and score the posterior
MAP (headline; chosen after seeing that the posterior mean trails the train at curve edges, both are
reported). Metres are the map's dead-reckoned distance (online.Template.metres), scaled to the
straight-line section length. Two truths:
- proxy: A's offline alignment onto the map's base run (zero-phase yaw DTW, step penalty, open
  ends). It shares the base run and the yaw signal with the tracker, so agreement with it is
  optimistic;
- independent: linear interpolation between A's curve edges matched to the base run's
  (position.curve_landmarks, no DTW), anchored at departure and arrival.
Also: the timetable baseline (time since departure / the map's mean section time); a masked test
(A's yaw hidden within +-MASK of the section around each curve edge, read at that edge: positioning
between curves); 90 % band coverage against both truths; clip mode from several start positions with
a uniform prior (time to lock, and how often the demo's "confident" display is > 50 m wrong);
departure detection armed at the previous arrival / recording start, unhinted; a stress test on
excluded sections that still have both ends (pause, handling, floor: today R4).
Writes outputs/online/{maps.json, online_eval.json, online_eval.png, clip_lock.png, track_<key>.png}.
Run: .venv/Scripts/python scripts/online_eval.py
"""
from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from tubesense import online as O
from tubesense import position as P
from tubesense import repeat as R
from tubesense.ingest import ROOT
from tubesense.stops import STATIONS

OUT = ROOT / "outputs" / "online"
MASK = 0.08                      # as the ablation headline
LOCK_ERR_M = 50.0
CLIP_STARTS = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7)
LOCKED, PAINT_BAND_M, HOLD = 0.5, 200.0, 8  # the demo's "confident" rule (scripts/demo.py)


def lengths(tracks) -> dict:
    """Track name -> metres, from the ablation's track_lengths.json (physical track, stations in STATIONS order)."""
    tl = json.load(open(ROOT / "outputs" / "positioning" / "track_lengths.json"))["used_m"]
    key = lambda t: "-".join(sorted(t.split()[1].split("->"), key=list(STATIONS).index))
    return {t: tl[key(t)] for t in tracks}


def noise_of(srs):
    return {s.key: ns for s in srs if (ns := O.noise_series(s)) is not None}


def truths(a: O.SectionRun, base: O.SectionRun):
    """(proxy, independent, matched edges): wall time -> base fraction."""
    w = O.warp_onto(a, base)
    da, db = (a.sec["arrive_wall"] - a.sec["depart_wall"]), (base.sec["arrive_wall"] - base.sec["depart_wall"])
    proxy = lambda wall: np.interp(wall, a.sec["depart_wall"] + R.GRID * da, w)
    la = P.curve_landmarks(a.run, a.sec, a.yaw0)
    lb = P.curve_landmarks(base.run, base.sec, base.yaw0)
    pairs = P.match_landmarks(la, lb)
    edges = [(a.sec["depart_wall"] + la[i]["frac"] * da, lb[j]["frac"]) for i, j in pairs]
    xs = np.r_[a.sec["depart_wall"], [e[0] for e in edges], a.sec["arrive_wall"]]
    ys = np.maximum.accumulate(np.r_[0.0, [e[1] for e in edges], 1.0])
    indep = lambda wall: np.interp(wall, xs, ys)
    return proxy, indep, edges


def summary(x):
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    if not len(x):
        return {"n": 0}
    return {"n": int(len(x)), "median": round(float(np.median(x)), 1), "mean": round(float(x.mean()), 1),
            "p90": round(float(np.percentile(x, 90)), 1), "max": round(float(x.max()), 1)}


def confident(est, L_band):
    """The demo's paint/lock rule: lock >= LOCKED held HOLD steps and a narrow 90 % band."""
    ok = (est["lock"].values >= LOCKED) & ((est.q95 - est.q05).values * L_band < PAINT_BAND_M)
    return np.convolve(ok, np.ones(HOLD), "full")[: len(ok)] >= HOLD


def held_out(trk, srs, L, a):
    train = [s for s in srs if s is not a]
    tpl = O.build_template(trk, train, L, noise_of(train))
    base = next(s for s in train if s.key == tpl.base)
    proxy, indep, edges = truths(a, base)
    m = tpl.metres
    onset, arrive = a.sec["depart_wall"], a.sec["arrive_wall"]
    est = O.track(tpl, a.feat, onset, arrive)
    wall = est.wall.values
    tp, ti = proxy(wall), indep(wall)
    tt = np.clip((wall - onset) / tpl.duration_s, 0, 1)
    E = lambda f, tr: np.abs(m(f) - m(tr))
    inside = lambda tr: float(((tr >= est.q05.values - 1e-9) & (tr <= est.q95.values + 1e-9)).mean())
    lm = [(np.interp(w_, wall, est["map"]), fb) for w_, fb in edges]
    lm_mean = [(np.interp(w_, wall, est.frac), fb) for w_, fb in edges]
    # masked: yaw hidden around each curve edge, MAP read at that edge (positioning between curves)
    masked, masked_tt, masked_mean = [], [], []
    da = arrive - onset
    for w_, fb in edges:
        f2 = a.feat.copy()
        f2.loc[(f2.wall - w_).abs() <= MASK * da, "yaw"] = np.nan
        e2 = O.track(tpl, f2, onset, arrive)
        masked.append(float(m(np.interp(w_, e2.wall, e2["map"])) - m(fb)))
        masked_mean.append(float(m(np.interp(w_, e2.wall, e2.frac)) - m(fb)))
        masked_tt.append(float(m(np.clip((w_ - onset) / tpl.duration_s, 0, 1)) - m(fb)))
    dep = a.departure
    row = {"held_out": a.key, "track": trk, "map_runs": tpl.runs, "map_base": tpl.base,
           "departure_missed_online": bool(np.isnan(a.depart_shift_s)),
           "departure": {"onset_minus_offline_s": None if np.isnan(a.depart_shift_s) else round(a.depart_shift_s, 1),
                         "armed_event_minus_offline_s": None if dep is None else
                         round(dep["wall_onset"] - (onset - (0 if np.isnan(a.depart_shift_s) else a.depart_shift_s)), 1)},
           "map_vs_proxy_m": summary(E(est["map"].values, tp)),
           "map_vs_independent_m": summary(E(est["map"].values, ti)),
           "mean_vs_proxy_m": summary(E(est.frac.values, tp)),
           "mean_vs_independent_m": summary(E(est.frac.values, ti)),
           "timetable_vs_independent_m": summary(E(tt, ti)),
           "timetable_vs_proxy_m": summary(E(tt, tp)),
           "edges_unmasked_map_m": summary([abs(m(f) - m(fb)) for f, fb in lm]),
           "edges_unmasked_map_signed_median_m": round(float(np.median([m(f) - m(fb) for f, fb in lm])), 1) if lm else None,
           "edges_unmasked_mean_signed_median_m": round(float(np.median([m(f) - m(fb) for f, fb in lm_mean])), 1) if lm else None,
           "edges_masked_map_m": summary(np.abs(masked)), "edges_masked_mean_m": summary(np.abs(masked_mean)),
           "edges_masked_timetable_m": summary(np.abs(masked_tt)),
           "band90_coverage_proxy": round(inside(tp), 3), "band90_coverage_independent": round(inside(ti), 3),
           "band90_width_m": summary(m(est.q95.values) - m(est.q05.values)),
           "map_jump_m": summary(np.abs(np.diff(m(est["map"].values))))}
    row["clip"], clips = {}, []
    for f0 in CLIP_STARTS:
        w0 = float(np.interp(f0, ti, wall))
        ce = O.track(tpl, a.feat, w0, arrive, start="unknown")
        cerr = E(ce["map"].values, indep(ce.wall.values))
        bad = np.flatnonzero(cerr >= LOCK_ERR_M)
        k = 0 if not len(bad) else bad[-1] + 1
        conf = confident(ce, L)
        row["clip"][str(f0)] = {"lock_s": round(float(ce.wall.values[k] - w0), 1) if k < len(cerr) else None,
                                "left_s_at_lock": round(float(arrive - ce.wall.values[k]), 1) if k < len(cerr) else None,
                                "confident_steps": int(conf.sum()),
                                "confident_but_wrong_steps": int((conf & (cerr > LOCK_ERR_M)).sum())}
        clips.append((a.key, f0, ce.wall.values - w0, cerr))
    return row, est, tpl, tp, ti, clips


def plot_run(row, est, tpl, tp, ti, onset):
    t = est.wall.values - onset
    m = tpl.metres
    tt = np.clip(t / tpl.duration_s, 0, 1)
    fig, ax = plt.subplots(3, 1, figsize=(11, 8.5), sharex=True)
    ax[0].fill_between(t, m(est.q05), m(est.q95), color="tab:blue", alpha=0.2, label="tracker 90 % band")
    ax[0].plot(t, m(est["map"]), color="tab:blue", label="tracker MAP (online, causal)")
    ax[0].plot(t, m(est.frac), color="tab:blue", lw=0.8, ls=":", label="tracker posterior mean")
    ax[0].plot(t, m(ti), "k--", lw=1, label="independent truth (curve-edge interpolation)")
    ax[0].plot(t, m(tp), color="tab:green", lw=0.8, label="proxy truth (offline DTW)")
    ax[0].plot(t, m(tt), color="0.6", lw=1, label="timetable baseline")
    ax[0].set_ylabel("metres along map (dead-reckoned)")
    ax[0].legend(fontsize=7)
    ax[1].plot(t, np.abs(m(est["map"]) - m(ti)), color="tab:blue",
               label=f"tracker vs independent, median {row['map_vs_independent_m']['median']} m")
    ax[1].plot(t, np.abs(m(tt) - m(ti)), color="0.6", label=f"timetable, median {row['timetable_vs_independent_m']['median']} m")
    ax[1].set_ylabel("error (m)")
    ax[1].legend(fontsize=8)
    ax[2].plot(t, est.yaw, color="tab:orange", lw=1, label="observed yaw (causal)")
    ax[2].plot(t, np.interp(est["map"], R.GRID, tpl.yaw_mu), color="k", lw=0.8, label="map yaw at the estimate")
    ax[2].set_ylabel("yaw (deg/s)")
    ax[2].set_xlabel("time since departure (s)")
    ax[2].legend(fontsize=8)
    for a in ax:
        a.grid(alpha=0.3)
    flag = "  [departure MISSED online: offline departure used]" if row["departure_missed_online"] else ""
    fig.suptitle(f"Online tracker, {row['track']}, held out {row['held_out']} (map: {', '.join(tpl.runs)}){flag}\n"
                 f"90 % band covers the independent truth {row['band90_coverage_independent']:.0%} of the time", fontsize=10)
    fig.tight_layout()
    fig.savefig(OUT / f"track_{row['held_out'].replace(':', '_')}.png", dpi=100)
    plt.close(fig)


def plot_summary(rows):
    fig, ax = plt.subplots(1, 1, figsize=(11, 4.5))
    x = np.arange(len(rows))
    sets = [("map_vs_independent_m", "tracker, all times", "tab:blue"),
            ("edges_masked_map_m", "tracker at curve edges, yaw hidden (between curves)", "tab:cyan"),
            ("timetable_vs_independent_m", "timetable, all times", "0.6"),
            ("edges_masked_timetable_m", "timetable at the same edges", "0.8")]
    for k, (key, lab, c) in enumerate(sets):
        ax.bar(x + (k - 1.5) * 0.2, [r[key].get("median", np.nan) for r in rows], 0.2, color=c, label=lab)
        ax.scatter(x + (k - 1.5) * 0.2, [r[key].get("p90", np.nan) for r in rows], color="k", marker="_", s=60)
    ax.set_xticks(x, [r["held_out"] + (" *" if r["departure_missed_online"] else "") for r in rows], rotation=25, fontsize=8)
    ax.set_ylabel("median error (m); tick = p90")
    ax.set_title("Online tracker, leave-one-run-out, vs the independent truth. * = departure missed online", fontsize=9)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(OUT / "online_eval.png", dpi=110)
    plt.close(fig)


def plot_clip(clips):
    fig, ax = plt.subplots(1, 1, figsize=(10, 4))
    for key, f0, t, cerr in clips:
        if f0 in (0.3, 0.5):
            ax.plot(t, cerr, lw=1, label=f"{key} from {f0:.0%}")
    ax.axhline(LOCK_ERR_M, color="k", ls=":", lw=0.8)
    ax.set_yscale("log")
    ax.set_xlabel("time since the clip started (s)")
    ax.set_ylabel("error vs independent truth (m)")
    ax.set_title("Clip mode: unknown start position (uniform prior). Dotted: 50 m lock threshold", fontsize=9)
    ax.legend(fontsize=7, ncol=2)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "clip_lock.png", dpi=110)
    plt.close(fig)


def pooled(rows, key):
    v = [r[key]["median"] for r in rows if r[key].get("n")]
    return round(float(np.median(v)), 1) if v else None


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    S = O.section_runs()
    S_all = O.section_runs(clean_only=False)
    L = lengths(S_all)
    rows, clips = [], []
    for trk, srs in S.items():
        if len(srs) < 2:
            continue
        for a in srs:
            row, est, tpl, tp, ti, cl = held_out(trk, srs, L[trk], a)
            plot_run(row, est, tpl, tp, ti, a.sec["depart_wall"])
            rows.append(row)
            clips += cl
            print(f"{a.key:13s}{'*' if row['departure_missed_online'] else ' '} vs indep {row['map_vs_independent_m']['median']:5.1f}"
                  f"/{row['map_vs_independent_m']['p90']:5.1f} | vs proxy {row['map_vs_proxy_m']['median']:5.1f} | "
                  f"timetable {row['timetable_vs_independent_m']['median']:5.1f} | masked edges map "
                  f"{row['edges_masked_map_m']['median']} tt {row['edges_masked_timetable_m']['median']} | "
                  f"cov {row['band90_coverage_independent']:.2f} | clip lock {[c['lock_s'] for c in row['clip'].values()]}")
    stress = []
    # stress test: excluded sections that still have both ends (pause, handling, floor), against the clean map
    for trk, srs in S_all.items():
        for a in [s for s in srs if not s.clean and not s.truncated]:
            clean = S.get(trk, [])
            if clean:
                row, est, tpl, tp, ti, _ = held_out(trk, clean + [a], L[trk], a)
                plot_run(row, est, tpl, tp, ti, a.sec["depart_wall"])
                stress.append(row)
                print(f"STRESS {a.key} vs indep {row['map_vs_independent_m']} cov {row['band90_coverage_independent']}")
    plot_summary(rows)
    plot_clip(clips)
    ok = [r for r in rows if not r["departure_missed_online"]]
    clip_all = [c for r in rows for c in r["clip"].values()]
    overall = {"n_runs": len(rows), "n_departure_detected": len(ok),
               **{f"{k}_median_of_runs": pooled(rows, k) for k in
                  ("map_vs_independent_m", "map_vs_proxy_m", "mean_vs_independent_m", "timetable_vs_independent_m",
                   "edges_masked_map_m", "edges_masked_mean_m", "edges_masked_timetable_m", "edges_unmasked_map_m")},
               "map_vs_independent_m_detected_only_median_of_runs": pooled(ok, "map_vs_independent_m"),
               "band90_coverage_independent_mean": round(float(np.mean([r["band90_coverage_independent"] for r in rows])), 3),
               "band90_coverage_proxy_mean": round(float(np.mean([r["band90_coverage_proxy"] for r in rows])), 3),
               "clip_confident_steps": int(sum(c["confident_steps"] for c in clip_all)),
               "clip_confident_but_wrong_steps": int(sum(c["confident_but_wrong_steps"] for c in clip_all)),
               "clip_lock_s_by_start": {str(f): [r["clip"][str(f)]["lock_s"] for r in rows] for f in CLIP_STARTS}}
    maps = {trk: O.build_template(trk, srs, L[trk], noise_of(srs)).to_json() for trk, srs in S.items()}
    json.dump(maps, open(OUT / "maps.json", "w"))
    settings = {k: getattr(O, k) for k in ("STEP", "TAU_ATT", "JUMP", "JUMP_MIN", "JUMP_WIN", "HANDLED", "P_SPEED",
                                            "GAMMA", "YAW_SD_FLOOR", "OUTLIER", "NOISE_BINS", "MATCH_S")}
    settings.update(SPEEDS=O.SPEEDS.round(3).tolist(), MASK=MASK, demo_confident_rule=[LOCKED, PAINT_BAND_M, HOLD])
    json.dump({"settings": settings, "lengths_m": L, "overall": overall, "runs": rows, "stress": stress,
               "doc": __doc__}, open(OUT / "online_eval.json", "w"), indent=1, default=float)
    print("OVERALL", json.dumps(overall, default=float))


if __name__ == "__main__":
    main()
