"""Magnetometer follow-up to GO/NO-GO (b): de-stepped |B| and the bias-free field anomaly.

The calibrated iOS stream re-estimates its offset mid-ride (one-sample vector steps), and the raw
stream carries a fixed hard-iron bias, so |B| is not comparable across runs. The field anomaly
|B - section mean| cancels any constant bias. Driving (traction current) is regressed out to see
how much of what repeats is position-locked rather than the train's own field.

Reads outputs/sections.json, outputs/qc.json and outputs/gonogo/repeatability.json (clean flags);
writes outputs/gonogo/mag_anomaly.json and outputs/gonogo/mag_anomaly.png.
Run: .venv/Scripts/python scripts/mag_anomaly.py
"""
from __future__ import annotations

import itertools
import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from tubesense import features as F
from tubesense import repeat as R
from tubesense.ingest import ROOT, load_run

OUT = ROOT / "outputs" / "gonogo"
LINE_RUNS = {"R0.5", "R1", "R1.5", "R2", "R3", "R4", "R5", "R6"}  # Central line
N_SURR = 200
MID = (0.25, 0.85)  # section fraction away from the pull-away / braking traction peaks


def section_features(run, sec, mag, mag_fixed):
    """|B| (de-stepped), field anomaly with and without de-stepping, and driving proxies on the L0 grid."""
    tg = R.time_grid(run, sec)

    def grid(df):
        return np.column_stack([R.sample(df.t.values, df[c].values, tg) for c in "xyz"])
    V, V0 = grid(mag_fixed), grid(mag)
    dr = F.dead_reckon(run, sec["depart_t"], sec["arrive_t"])
    a = R.sample(dr.t.values, dr.a_long.values, tg)
    v = R.sample(dr.t.values, dr.v.values, tg)
    anom = F.field_anomaly(V)
    # traction proxy: linear model of the anomaly on |a|, traction-only a, and speed
    X = np.column_stack([np.ones_like(a), np.abs(a), np.clip(a, 0, None), v / (np.nanmax(v) + 1e-9)])
    ok = np.isfinite(anom) & np.isfinite(X).all(1)
    beta = np.linalg.lstsq(X[ok], anom[ok], rcond=None)[0]
    drive = np.full_like(anom, np.nan)
    drive[ok] = X[ok] @ beta
    return {"B": np.linalg.norm(V, axis=1), "anom": anom, "anom_nodestep": F.field_anomaly(V0),
            "drive": drive, "resid": anom - drive, "r2_drive": float(1 - np.nanvar(anom - drive) / np.nanvar(anom))}


def score(sig, meta, key, rng, modes=("raw", "calibrated")):
    """Same-section pairs (surrogate p) vs the different-section null, clean sections only."""
    keys = [k for k in sig if meta[k]["clean"] and meta[k]["mag_mode"] in modes]
    trk = lambda k: (meta[k]["dir"], meta[k]["from"], meta[k]["to"])
    same, diff = [], []
    for a, b in itertools.combinations(keys, 2):
        r = R.best_shift_r(sig[a][key], sig[b][key])[0]
        if trk(a) != trk(b):
            diff.append(r)
            continue
        sur = [R.best_shift_r(sig[a][key], R.phase_surrogate(np.nan_to_num(sig[b][key]), rng))[0] for _ in range(N_SURR)]
        same.append({"pair": f"{a} vs {b}", "modes": [meta[a]["mag_mode"], meta[b]["mag_mode"]], "r": round(r, 3),
                     "surrogate_p": round(float(np.mean(np.array(sur) >= r)), 3),
                     "surrogate_p99": round(float(np.percentile(sur, 99)), 3)})
    rs = np.array([s["r"] for s in same])
    return {"pairs": same, "same_mean_r": round(float(rs.mean()), 3), "same_min_r": round(float(rs.min()), 3),
            "n_significant": int(sum(s["surrogate_p"] < 0.05 for s in same)), "n_pairs": len(same),
            "null_mean_r": round(float(np.mean(diff)), 3), "null_p95_r": round(float(np.percentile(diff, 95)), 3),
            "null_n": len(diff)}


def verdict(res):
    a, r, m = res["anom"], res["resid"], res["mid_section"]["resid"]
    if a["n_significant"] >= 0.6 * a["n_pairs"] and r["same_mean_r"] > r["null_p95_r"] and m["same_mean_r"] > m["null_p95_r"]:
        v = "WEAK"
        why = (f"The bias-free field anomaly repeats on the same section (mean r {a['same_mean_r']}, "
               f"{a['n_significant']}/{a['n_pairs']} pairs beat phase surrogates at p < 0.05; different-section null "
               f"mean {a['null_mean_r']}). Driving proxies explain a median {res['r2_drive_median']:.0%} of it (the "
               f"train's own traction field), but the residual still repeats (mean r {r['same_mean_r']}, "
               f"{r['n_significant']}/{r['n_pairs']} significant, null mean {r['null_mean_r']}, p95 {r['null_p95_r']}), "
               f"also in the mid-section away from the pull-away traction peak (mean r {m['same_mean_r']}, "
               f"{m['n_significant']}/{m['n_pairs']} significant, null p95 {m['null_p95_r']}). "
               "So there is a position-locked component, modest and not yet separable from non-linear traction "
               "effects that repeat because drivers run each section alike. Use it as a low-weight DTW feature; the "
               "ablation decides. |B| itself stays NO-GO.")
    else:
        v, why = "NO-GO", "The field anomaly does not repeat beyond the different-section null once driving is removed."
    return v, why


def plot(sig, meta, res):
    groups = {}
    for k in sig:
        if meta[k]["clean"]:
            groups.setdefault((meta[k]["dir"], meta[k]["from"], meta[k]["to"]), []).append(k)
    groups = {g: ks for g, ks in groups.items() if len(ks) >= 2}
    fig, ax = plt.subplots(len(groups), 2, figsize=(13, 3.0 * len(groups)), squeeze=False)
    x = R.GRID
    for i, ((d, a, b), ks) in enumerate(groups.items()):
        for k in ks:
            lab = f"{meta[k]['run']} ({meta[k]['mag_mode']})"
            ax[i, 0].plot(x, sig[k]["anom"], lw=1, label=lab)
            ax[i, 1].plot(x, sig[k]["resid"], lw=1, label=lab)
        ax[i, 0].set_title(f"{d} {a} -> {b}: field anomaly |B - section mean| (de-stepped)", fontsize=9)
        ax[i, 1].set_title(f"{d} {a} -> {b}: anomaly minus driving model (traction regressed out)", fontsize=9)
        ax[i, 0].set_ylabel("uT")
        for j in (0, 1):
            ax[i, j].legend(fontsize=7)
            ax[i, j].grid(alpha=0.3)
    for j in (0, 1):
        ax[-1, j].set_xlabel("section fraction (L0 time)")
    an, rs = res["anom"], res["resid"]
    fig.suptitle(f"Magnetometer field anomaly: same-section mean r {an['same_mean_r']} (null {an['null_mean_r']}); "
                 f"after removing driving {rs['same_mean_r']} (null {rs['null_mean_r']}). Verdict: {res['verdict']}",
                 fontsize=10)
    fig.tight_layout()
    fig.savefig(OUT / "mag_anomaly.png", dpi=110)
    plt.close(fig)


def main():
    secs = json.load(open(ROOT / "outputs" / "sections.json"))["sections"]
    qc = json.load(open(ROOT / "outputs" / "qc.json"))["runs"]
    meta = json.load(open(OUT / "repeatability.json"))["sections"]
    rng = np.random.default_rng(0)
    sig, steps, cache = {}, {}, {}
    for sec in secs:
        if sec["run_id"] not in LINE_RUNS:
            continue
        stem = sec["stem"]
        if stem not in cache:
            run = load_run(stem)
            mode = qc[stem]["magnetometer"]["mode"]
            # only the calibrated stream has iOS calibration updates; a raw stream's bias is fixed
            fixed, st = F.mag_destep(run) if mode == "calibrated" else (run.sensors["mag"], [])
            cache[stem] = (run, run.sensors["mag"], fixed)
            steps[stem] = {"mode": mode, "steps_removed": st}
        run, mag, fixed = cache[stem]
        sig[f"{sec['run_id']}:{sec['from']}-{sec['to']}"] = section_features(run, sec, mag, fixed)
    # the anomaly peaks while pulling away (peak traction current): re-score the mid-section alone
    mid = (R.GRID >= MID[0]) & (R.GRID <= MID[1])
    for s in sig.values():
        for f in ("anom", "resid", "drive"):
            s[f"{f}_mid"] = np.where(mid, s[f], np.nan)
    res = {"B": score(sig, meta, "B", rng, modes=("calibrated",)),
           "anom": score(sig, meta, "anom", rng), "anom_nodestep": score(sig, meta, "anom_nodestep", rng),
           "resid": score(sig, meta, "resid", rng), "drive": score(sig, meta, "drive", rng),
           "mid_section": {"fraction": MID, **{f: score(sig, meta, f"{f}_mid", rng) for f in ("anom", "resid", "drive")}},
           "r2_drive": {k: round(s["r2_drive"], 2) for k, s in sig.items() if meta[k]["clean"]}}
    res["r2_drive_median"] = float(np.median(list(res["r2_drive"].values())))
    res["verdict"], res["justification"] = verdict(res)
    res["steps"] = steps
    plot(sig, meta, res)
    json.dump(res, open(OUT / "mag_anomaly.json", "w"), indent=1)
    rows = [(k, res[k]) for k in ("B", "anom", "anom_nodestep", "resid", "drive")]
    rows += [(f"{k}_mid", res["mid_section"][k]) for k in ("anom", "resid", "drive")]
    for k, r in rows:
        print(f"{k:14s} same mean r {r['same_mean_r']:.2f} ({r['n_significant']}/{r['n_pairs']} p<0.05) | "
              f"null mean {r['null_mean_r']:.2f} p95 {r['null_p95_r']:.2f}")
    print(f"driving explains median {res['r2_drive_median']:.0%} of anomaly variance")
    print(res["verdict"], "-", res["justification"])


if __name__ == "__main__":
    main()
