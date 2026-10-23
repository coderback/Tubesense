"""GO/NO-GO (a) gyro yaw-rate and (b) magnetometer |B| repeatability across runs.

Reads outputs/sections.json + outputs/qc.json, writes outputs/gonogo/repeatability.json,
outputs/gonogo/gyro_*.png and outputs/gonogo/mag_*.png.
Run: .venv/Scripts/python scripts/repeatability.py
"""
from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from tubesense import features as F
from tubesense import repeat as R
from tubesense.ingest import ROOT, load_run
from tubesense.stops import pauses

OUT = ROOT / "outputs" / "gonogo"
LINE_RUNS = {"R0.5", "R1", "R1.5", "R2", "R3", "R4", "R5", "R6"}  # Central line
TRACKS = [("EB", "LST", "BTH"), ("EB", "BTH", "MLE"), ("WB", "MLE", "BTH"), ("WB", "BTH", "LST")]
QUALITY_FLAGS = {"handheld", "handled_mid_ride", "floor_like_placement", "section_truncated",
                 "section_overlaps_pause", "atypical_coupling"}
EXCLUDE = {"handheld", "section_truncated", "section_overlaps_pause"}  # from the "clean" subset
STEP_MASK = (-15.0, 25.0)  # s around a magnetometer calibration jump masked (0.05 Hz high-pass ringing, re-convergence)
N_SURR = 200


def track_id(sec):  # physical track, direction-free
    return "-".join(sorted([sec["from"], sec["to"]]))


def section_flags(sec, qc_run, sig):
    """QC flags overlapping this section, with the overlap fraction."""
    t0, t1 = sec["depart_t"], sec["arrive_t"]
    out = []
    for f in qc_run["flags"]:
        if f["type"] not in QUALITY_FLAGS or f["t0"] is None:
            continue
        ov = max(0.0, min(t1, f["t1"]) - max(t0, f["t0"])) if f["t1"] > f["t0"] else float(t0 <= f["t0"] <= t1)
        if ov > 0:
            out.append({"type": f["type"], "overlap_frac": round(ov / (t1 - t0), 2) if f["t1"] > f["t0"] else None})
    if sec["confidence"] < 0.5 and not any(f["type"] == "section_truncated" for f in out):
        out.append({"type": "section_truncated", "overlap_frac": None})
    return out


def is_clean(flags):
    return not any(f["type"] in EXCLUDE and (f["overlap_frac"] is None or f["overlap_frac"] > 0.05) for f in flags)


def build():
    secs = json.load(open(ROOT / "outputs" / "sections.json"))["sections"]
    qc = json.load(open(ROOT / "outputs" / "qc.json"))["runs"]
    raw_vecs = [np.array(v["magnetometer"]["mean_vec_uT"]) for v in qc.values() if v["magnetometer"]["mode"] == "raw"]
    raw_centre = np.mean(raw_vecs, 0) if raw_vecs else np.zeros(3)
    sig, meta, cache = {}, {}, {}
    for sec in secs:
        if sec["run_id"] not in LINE_RUNS:
            continue
        stem = sec["stem"]
        if stem not in cache:
            run = load_run(stem)
            dw = next(r for r in json.load(open(ROOT / "outputs" / "sections.json"))["runs"] if r["stem"] == stem)["dwells"]
            bias = F.gyro_bias(run, [(d["t0"], d["t1"]) for d in dw])
            mag = F.mag_norm(run)
            mode = qc[stem]["magnetometer"]["mode"]
            if mode == "raw":  # bias-corrected |B| with the raw-group hard-iron estimate
                mag["B"] = np.linalg.norm(mag[["x", "y", "z"]].values - raw_centre, axis=1)
            cache[stem] = (run, F.yaw_rate(run, bias), mag, [(p["t"], p["dur"]) for p in pauses(run)], mode,
                           bias, dw)
        run, yaw, mag, gaps, mode, bias, dw = cache[stem]
        key = f"{sec['run_id']}:{sec['from']}-{sec['to']}"
        sig[key] = R.section_signals(run, sec, yaw, mag, gaps)
        # iOS calibration jumps (qc.json): mask |B| and d|B| around each; levels across a jump differ
        steps = [st["t"] for st in qc[stem]["magnetometer"].get("steps", [])] if mode == "calibrated" else []
        for g in ("time", "dist"):
            tg = sig[key][f"t_{g}"]
            bad = np.zeros(len(tg), bool)
            for st in steps:
                bad |= (tg >= st + STEP_MASK[0]) & (tg <= st + STEP_MASK[1])
            for name in ("B", "dB"):
                sig[key][f"{name}_{g}"] = np.where(bad, np.nan, sig[key][f"{name}_{g}"])
        n_steps = sum(sec["depart_t"] - 20 <= st <= sec["arrive_t"] + 5 for st in steps)
        masked = float(np.mean(~np.isfinite(sig[key]["dB_dist"])))
        flags = section_flags(sec, qc[stem], sig[key])
        meta[key] = {"run": sec["run_id"], "dir": sec["direction"], "from": sec["from"], "to": sec["to"],
                     "track": track_id(sec), "duration_s": sec["duration_s"], "mag_mode": mode,
                     "mag_conf": qc[stem]["magnetometer"]["confidence"], "mag_steps_in_section": int(n_steps),
                     "mag_step_free": bool(mode == "calibrated" and n_steps == 0),
                     "mag_masked_frac": round(masked, 2),
                     "mag_usable_masked": bool(mode == "calibrated" and masked < 0.5),
                     "flags": flags, "clean": is_clean(flags),
                     "gyro_bias_rad_s": np.round(bias, 5).tolist(),
                     "heading_total_deg": round(sig[key]["heading_total_deg"], 1),
                     "dr_len_m_unscaled": round(sig[key]["dr_len_m"], 0), "dr_vmax_m_s": round(sig[key]["v_max"], 1)}
        # train-state test inputs: |B| at the platform dwells vs on the move
        meta[key]["B_median_moving_uT"] = round(float(np.nanmedian(sig[key]["B_time"])), 1)
    return sig, meta, cache, raw_centre


def groups(meta):
    g = {}
    for k, m in meta.items():
        t = (m["dir"], m["from"], m["to"])
        if t in TRACKS:
            g.setdefault(t, []).append(k)
    return g


def to_eb(meta):
    """Map a westbound signal into eastbound orientation: reverse fraction, flip yaw sign."""
    def tf_yaw(k, x):
        return -x[::-1] if meta[k]["dir"] == "WB" else x

    def tf_mag(k, x):
        return x[::-1] if meta[k]["dir"] == "WB" else x
    return tf_yaw, tf_mag


def analyse(sig, meta):
    rng = np.random.default_rng(0)
    G = groups(meta)
    res = {"groups": {}, "cross_direction": {}, "different_section_null": {}}
    sigs = {"yaw": ["yaw_time", "yaw_dist"], "head": ["head_time", "head_dist"],
            "B": ["B_time", "B_dist"], "dB": ["dB_time", "dB_dist"]}
    for (d, a, b), keys in G.items():
        gname = f"{d} {a}->{b}"
        clean = [k for k in keys if meta[k]["clean"]]
        cal = {"B": [k for k in keys if meta[k]["mag_step_free"]],
               "dB": [k for k in keys if meta[k]["mag_usable_masked"]]}
        out = {"runs": keys, "clean_runs": clean, "calibrated_step_free_runs": cal["B"],
               "calibrated_masked_runs": cal["dB"],
               "heading_total_deg": {k: meta[k]["heading_total_deg"] for k in keys}}
        for fam, ks in sigs.items():
            for s in ks:
                rows = R.pair_table(sig, keys, s, rng=rng)
                out[s] = {"all": R.summarise(rows), "pairs": [{k: (round(v, 3) if isinstance(v, float) else v)
                                                              for k, v in r.items() if k != "null"} for r in rows]}
                out[s]["clean"] = R.summarise(R.pair_table(sig, clean, s, rng=rng)) if len(clean) >= 2 else None
                if fam in ("B", "dB"):
                    c = cal[fam]
                    out[s]["calibrated_only"] = R.summarise(R.pair_table(sig, c, s, rng=rng)) if len(c) >= 2 else None
        h = np.array([meta[k]["heading_total_deg"] for k in clean])
        out["heading_total_clean_mean_sd"] = [round(float(h.mean()), 1), round(float(h.std(ddof=1)), 1)] if len(h) > 1 else None
        # per-pair spectrum-matched surrogate tests (the honest chance level for smooth signals)
        out["surrogate"] = {"yaw_dist": R.surrogate_pairs(sig, clean, None, "yaw_dist", rng, n=N_SURR),
                            "B_dist": R.surrogate_pairs(sig, cal["B"], None, "B_dist", rng, n=N_SURR),
                            "dB_dist": R.surrogate_pairs(sig, cal["dB"], None, "dB_dist", rng, n=N_SURR)}
        res["groups"][gname] = out

    # cross-direction: EB vs WB over the same physical track, WB mapped into EB orientation
    tf_yaw, tf_mag = to_eb(meta)
    for eb, wb in ((("EB", "LST", "BTH"), ("WB", "BTH", "LST")), (("EB", "BTH", "MLE"), ("WB", "MLE", "BTH"))):
        sets = {"yaw": ([k for k in G[eb] if meta[k]["clean"]], [k for k in G[wb] if meta[k]["clean"]]),
                "B": ([k for k in G[eb] if meta[k]["mag_step_free"]], [k for k in G[wb] if meta[k]["mag_step_free"]]),
                "dB": ([k for k in G[eb] if meta[k]["mag_usable_masked"]],
                       [k for k in G[wb] if meta[k]["mag_usable_masked"]])}
        name = f"{eb[1]}-{eb[2]} EB vs WB(reversed)"
        res["cross_direction"][name] = {s: R.summarise(R.cross_table(sig, *sets[s.split("_")[0]], s, tf)) for s, tf in
                                        (("yaw_dist", tf_yaw), ("yaw_time", tf_yaw), ("B_dist", tf_mag), ("dB_dist", tf_mag))}
        # plain r, fitted shift and a +-25 % search (is the +-10 % result bound-limited?), with surrogates
        res["cross_direction"][name]["yaw_dist_surrogate_shift25"] = R.surrogate_pairs(
            sig, *sets["yaw"], "yaw_dist", rng, tf_yaw, max_shift=0.25, n=N_SURR)

    # different-section null: every pair of sections on different physical track (incl. R0.5 sections)
    keys = list(meta)
    for s in ("yaw_time", "yaw_dist", "head_dist", "B_dist", "dB_dist"):
        rows = [r for r in R.cross_table(sig, keys, keys, s)
                if r["a"] < r["b"] and meta[r["a"]]["track"] != meta[r["b"]]["track"]]
        res["different_section_null"][s] = R.summarise(rows)
    # calibrated-only null for the magnetometer (same masking/step rules as the headline sets)
    res["different_section_null_calibrated"] = {}
    for s, ok in (("B_dist", "mag_step_free"), ("dB_dist", "mag_usable_masked")):
        ks = [k for k in keys if meta[k][ok]]
        rows = [r for r in R.cross_table(sig, ks, ks, s)
                if r["a"] < r["b"] and meta[r["a"]]["track"] != meta[r["b"]]["track"]]
        res["different_section_null_calibrated"][s] = R.summarise(rows)
        v = np.array([r["r_best10"] for r in rows])
        if len(v):
            res["different_section_null_calibrated"][s]["r_best10"]["p90"] = float(np.percentile(v, 90))
    return res


def traction_test(sig, meta):
    """Does |B| follow the train's motor demand (|along-track accel|, speed) more than position?"""
    out = {"per_section": {}}
    rs_a, rs_v = [], []
    for k, s in sig.items():
        ra, rv = R.pearson(s["B_time"], s["along_time"]), R.pearson(s["B_time"], s["v_time"])
        rd = R.pearson(s["dB_time"], s["along_time"])
        out["per_section"][k] = {"r_B_absaccel": round(ra, 3), "r_B_speed": round(rv, 3), "r_dB_absaccel": round(rd, 3),
                                 "mag_mode": meta[k]["mag_mode"]}
        if meta[k]["clean"]:
            rs_a.append(ra), rs_v.append(rv)
    out["median_r_B_absaccel_clean"] = round(float(np.nanmedian(rs_a)), 3)
    out["median_abs_r_B_absaccel_clean"] = round(float(np.nanmedian(np.abs(rs_a))), 3)
    out["median_r_B_speed_clean"] = round(float(np.nanmedian(rs_v)), 3)
    out["median_abs_r_B_speed_clean"] = round(float(np.nanmedian(np.abs(rs_v))), 3)

    # positional residual: remove the train-state part (linear in |a|, v) then re-test repeatability
    def resid(x, a, v):
        m = np.isfinite(x) & np.isfinite(a) & np.isfinite(v)
        X = np.column_stack([a[m], v[m], np.ones(m.sum())])
        y = x.copy()
        y[m] = x[m] - X @ np.linalg.lstsq(X, x[m], rcond=None)[0]
        y[~m] = np.nan
        return y

    res_sig = {k: {"Bres_dist": resid(s["B_dist"], s["along_dist"], s["v_dist"]),
                   "B_dist": s["B_dist"], "along_dist": s["along_dist"]} for k, s in sig.items()}
    by_state = {"motoring": [], "braking": [], "coasting": []}
    out["local_sd_by_state_uT"] = {}
    for k, s in sig.items():
        if not meta[k]["mag_step_free"] or not meta[k]["clean"]:
            continue
        st = R.train_state(s["acc_time"], s["v_time"])
        sd = R.rolling_sd(s["B_time"])
        row = {}
        for name in by_state:
            if (st == name).sum() >= 10:
                row[name] = round(float(np.nanmedian(sd[st == name])), 1)
                by_state[name].append(row[name])
        out["local_sd_by_state_uT"][k] = row
    out["local_sd_by_state_median_uT"] = {n: float(np.median(v)) for n, v in by_state.items()}
    both = [r for r in out["local_sd_by_state_uT"].values() if "motoring" in r and "coasting" in r]
    out["sections_motoring_sd_gt_coasting"] = [sum(r["motoring"] > r["coasting"] for r in both), len(both)]
    coast_r = []
    for (d, a, b), keys in groups(meta).items():
        cal = [k for k in keys if meta[k]["mag_step_free"] and meta[k]["clean"]]
        for i, ka in enumerate(cal):
            for kb in cal[i + 1:]:
                m = (R.train_state(sig[ka]["acc_dist"], sig[ka]["v_dist"]) == "coasting") &                     (R.train_state(sig[kb]["acc_dist"], sig[kb]["v_dist"]) == "coasting")
                if m.sum() >= 40:
                    coast_r.append({"a": ka, "b": kb, "n_points": int(m.sum()),
                                    "r": round(R.pearson(sig[ka]["B_dist"][m], sig[kb]["B_dist"][m]), 3)})
    out["coasting_only_pairs"] = coast_r
    out["coasting_only_between_run_r"] = {"mean": float(np.nanmean([c["r"] for c in coast_r])) if coast_r else np.nan,
                                          "n_pairs": len(coast_r)}
    out["residual_repeatability"] = {}
    out["absaccel_repeatability"] = {}
    for gname, keys in ((f"{d} {a}->{b}", ks) for (d, a, b), ks in groups(meta).items()):
        cal = [k for k in keys if meta[k]["mag_step_free"] and meta[k]["clean"]]
        if len(cal) >= 2:
            out["residual_repeatability"][gname] = {
                "runs": cal, "B_dist": R.summarise(R.pair_table(res_sig, cal, "B_dist")),
                "Bres_dist": R.summarise(R.pair_table(res_sig, cal, "Bres_dist"))}
            out["absaccel_repeatability"][gname] = R.summarise(R.pair_table(res_sig, cal, "along_dist"))
    # |a_long| between DIFFERENT sections: the accelerate-cruise-brake shape is generic, so this is its chance level
    ks = list(sig)
    rows = [r for r in R.cross_table(res_sig, ks, ks, "along_dist")
            if r["a"] < r["b"] and meta[r["a"]]["track"] != meta[r["b"]]["track"]]
    out["absaccel_different_section_null"] = R.summarise(rows)
    return out


def dwell_vs_moving(cache, meta):
    """|B| statistics at platform dwells vs between stations, per run (train state proxy)."""
    out = {}
    secs = json.load(open(ROOT / "outputs" / "sections.json"))["sections"]
    for stem, (run, yaw, mag, gaps, mode, bias, dw) in cache.items():
        dwell = np.zeros(len(mag), bool)
        for d in dw:
            if not d["truncated"]:
                dwell |= (mag.t.values > d["t0"] + 2) & (mag.t.values < d["t1"] - 2)
        mov = np.zeros(len(mag), bool)
        for s in secs:
            if s["stem"] == stem:
                mov |= (mag.t.values > s["depart_t"] + 5) & (mag.t.values < s["arrive_t"] - 5)
        if dwell.sum() < 300:
            continue
        B = mag.B.values
        out[stem] = {"mag_mode": mode, "dwell_median_uT": round(float(np.median(B[dwell])), 1),
                     "dwell_sd_uT": round(float(np.std(B[dwell])), 1),
                     "moving_median_uT": round(float(np.median(B[mov])), 1),
                     "moving_sd_uT": round(float(np.std(B[mov])), 1)}
    return out


# --------------------------------------------------------------------------- plots
COL = {"R1": "#1f77b4", "R1.5": "#1f77b4", "R2": "#1f77b4", "R3": "#d62728", "R4": "#ff7f0e",
       "R5": "#2ca02c", "R6": "#2ca02c"}


def label(k, m, mag=False):
    tags = sorted({f["type"].replace("section_", "").replace("_placement", "") for f in m["flags"]})
    if mag:
        tags = [m["mag_mode"]] + ([f"{m['mag_steps_in_section']} cal. jump"] if m["mag_steps_in_section"] else []) + tags
    return f"{m['run']}" + (f" [{', '.join(tags)}]" if tags else "")


def plot_gyro(sig, meta, G):
    for (d, a, b), keys in G.items():
        fig, ax = plt.subplots(3, 1, figsize=(11, 9), sharex=True)
        for k in keys:
            m, s = meta[k], sig[k]
            ls = "-" if m["clean"] else ":"
            c = COL.get(m["run"])
            ax[0].plot(R.GRID, np.degrees(s["yaw_time"]), ls, color=c, lw=1.3, label=label(k, m))
            ax[1].plot(R.GRID, np.degrees(s["yaw_dist"]), ls, color=c, lw=1.3)
            ax[2].plot(R.GRID, s["head_dist"], ls, color=c, lw=1.5, label=f"{m['run']}: total {m['heading_total_deg']:+.0f} deg")
        ref = [np.degrees(sig[k]["yaw_time"]) for k in keys if meta[k]["clean"]] or [np.degrees(sig[k]["yaw_time"]) for k in keys]
        lim = max(3.0, 1.3 * np.nanpercentile(np.abs(np.concatenate(ref)), 99.5))
        ax[0].set_ylim(-lim, lim)
        ax[1].set_ylim(-lim, lim)
        ax[0].set_title(f"Yaw rate, {d} {a} -> {b}: L0 time fraction (y clipped to clean runs)")
        ax[1].set_title("Yaw rate: L1 dead-reckoned distance fraction")
        ax[2].set_title("Cumulative heading change: L1 distance fraction")
        ax[0].set_ylabel("yaw rate (deg/s)\n+ = left turn")
        ax[1].set_ylabel("yaw rate (deg/s)\n+ = left turn")
        ax[2].set_ylabel("heading change (deg)")
        ax[2].set_xlabel(f"section fraction ({a} = 0, {b} = 1)")
        for x in ax:
            x.axhline(0, color="k", lw=0.5)
            x.grid(alpha=0.3)
        ax[0].legend(fontsize=8, loc="best")
        ax[2].legend(fontsize=8, loc="best")
        fig.text(0.01, 0.005, "dotted = flagged section (handheld / truncated / pause gap), excluded from 'clean' metrics",
                 fontsize=8)
        fig.tight_layout()
        fig.savefig(OUT / f"gyro_{d}_{a}-{b}.png", dpi=110)
        plt.close(fig)


def plot_mag(sig, meta, G):
    for (d, a, b), keys in G.items():
        fig, ax = plt.subplots(3, 1, figsize=(11, 9), sharex=True)
        for k in keys:
            m, s = meta[k], sig[k]
            ls = "-" if m["mag_step_free"] else ":" if m["mag_mode"] == "calibrated" else "--"
            c = COL.get(m["run"])
            ax[0].plot(R.GRID, s["B_dist"], ls, color=c, lw=1.3, label=label(k, m, mag=True))
            ax[1].plot(R.GRID, s["dB_dist"], ls, color=c, lw=1.3)
            ax[2].plot(R.GRID, s["along_dist"], ls, color=c, lw=1.3)
        ax[0].set_title(f"|B|, {d} {a} -> {b} (L1 distance fraction). Solid = calibrated, no calibration jump; "
                        "dotted = calibrated with jump(s), masked; dashed = raw, bias-corrected", fontsize=9)
        ax[1].set_title("Δ|B|: |B| high-passed at 0.05 Hz (slow drift removed)")
        ax[2].set_title("Train state: |along-track accel| (motoring / braking demand), low-passed 0.2 Hz")
        ax[0].set_ylabel("|B| (µT)")
        ax[1].set_ylabel("Δ|B| (µT)")
        ax[2].set_ylabel("|a_long| (m/s²)")
        ax[2].set_xlabel(f"section fraction ({a} = 0, {b} = 1)")
        for x in ax:
            x.grid(alpha=0.3)
        ax[0].legend(fontsize=8, loc="best")
        fig.tight_layout()
        fig.savefig(OUT / f"mag_{d}_{a}-{b}.png", dpi=110)
        plt.close(fig)


def matrix_order(meta, G):
    order = []
    for t in (("EB", "LST", "BTH"), ("WB", "BTH", "LST"), ("EB", "BTH", "MLE"), ("WB", "MLE", "BTH")):
        order += G[t]
    order += [k for k in meta if meta[k]["run"] == "R0.5"]
    return order


def plot_matrix(sig, meta, G, key, tf, fname, title):
    order = matrix_order(meta, G)
    M = np.full((len(order), len(order)), np.nan)
    for i, a in enumerate(order):
        for j, b in enumerate(order):
            if i != j:
                M[i, j] = R.best_shift_r(tf(a, sig[a][key]), tf(b, sig[b][key]))[0]
    fig, ax = plt.subplots(figsize=(9.5, 8))
    im = ax.imshow(M, vmin=-0.5, vmax=1, cmap="RdBu_r")
    labs = [f"{k}{'' if meta[k]['clean'] else ' *'}" for k in order]
    ax.set_xticks(range(len(order)), labs, rotation=90, fontsize=8)
    ax.set_yticks(range(len(order)), labs, fontsize=8)
    for edge in np.cumsum([len(G[t]) for t in (("EB", "LST", "BTH"), ("WB", "BTH", "LST"),
                                                ("EB", "BTH", "MLE"), ("WB", "MLE", "BTH"))]):
        ax.axhline(edge - 0.5, color="k", lw=1)
        ax.axvline(edge - 0.5, color="k", lw=1)
    for i in range(len(order)):
        for j in range(len(order)):
            if np.isfinite(M[i, j]):
                ax.text(j, i, f"{M[i, j]:.1f}", ha="center", va="center", fontsize=6)
    plt.colorbar(im, ax=ax, label="best Pearson r within ±10 % shift")
    ax.set_title(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(OUT / fname, dpi=110)
    plt.close(fig)


def plot_summary(res, fname, sigkeys, title, extra=None):
    """Bars: same-section r_best10 per group vs reversed / shuffled / different-section nulls."""
    fig, axes = plt.subplots(1, len(sigkeys), figsize=(5.5 * len(sigkeys), 4.8), sharey=True)
    for ax, s in zip(np.atleast_1d(axes), sigkeys):
        names, vals, lo, hi, nr, ns = [], [], [], [], [], []
        for g, d in res["groups"].items():
            src = d[s]["clean"] if "B" not in s else d[s]["calibrated_only"]
            if not src:
                continue
            names.append(g)
            vals.append(src["r_best10"]["mean"]), lo.append(src["r_best10"]["min"]), hi.append(src["r_best10"]["max"])
            nr.append(src["null_reversed"]["r_best10"]), ns.append(src["null_shuffled"]["r_best10"])
        for name, d in res["cross_direction"].items():
            if s in d and d[s]:
                names.append(name.replace(" vs WB(reversed)", "\nEB vs WB rev."))
                vals.append(d[s]["r_best10"]["mean"]), lo.append(d[s]["r_best10"]["min"]), hi.append(d[s]["r_best10"]["max"])
                nr.append(np.nan), ns.append(np.nan)
        x = np.arange(len(names))
        v = np.array(vals)
        ax.bar(x, v, color="#4c72b0", label="same section (mean, min-max)")
        ax.errorbar(x, v, yerr=[v - np.array(lo), np.array(hi) - v], fmt="none", ecolor="k", capsize=3)
        ax.scatter(x, nr, marker="v", color="#dd8452", zorder=3, label="null: other run time-reversed")
        ax.scatter(x, ns, marker="x", color="#c44e52", zorder=3, label="null: other run block-shuffled")
        dn = (res["different_section_null_calibrated"] if "B" in s else res["different_section_null"]).get(s)
        if dn:
            ax.axhspan(dn["r_best10"]["min"], dn["r_best10"]["max"], color="grey", alpha=0.15)
            ax.axhline(dn["r_best10"]["mean"], color="grey", ls="--", label="null: different sections (mean, range)")
        ax.set_xticks(x, names, rotation=30, ha="right", fontsize=8)
        ax.set_title(s.replace("_time", " (L0 time)").replace("_dist", " (L1 distance)"))
        ax.axhline(0, color="k", lw=0.5)
        ax.grid(alpha=0.3, axis="y")
    np.atleast_1d(axes)[0].set_ylabel("best Pearson r within ±10 % shift")
    np.atleast_1d(axes)[0].legend(fontsize=7, loc="lower left")
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(OUT / fname, dpi=110)
    plt.close(fig)


def plot_traction(sig, meta, trac):
    keys = [k for k in sig if meta[k]["clean"]]
    fig, ax = plt.subplots(1, 3, figsize=(17, 4.8), gridspec_kw={"width_ratios": [1.4, 1, 0.8]})
    ra = [trac["per_section"][k]["r_B_absaccel"] for k in keys]
    rv = [trac["per_section"][k]["r_B_speed"] for k in keys]
    x = np.arange(len(keys))
    ax[0].bar(x - 0.2, ra, 0.4, label="r(|B|, |along-track accel|)")
    ax[0].bar(x + 0.2, rv, 0.4, label="r(|B|, speed proxy)")
    ax[0].set_xticks(x, [f"{k} ({meta[k]['mag_mode'][:3]})" for k in keys], rotation=90, fontsize=7)
    ax[0].set_ylabel("Pearson r within section (L0 time grid)")
    ax[0].set_title("Does |B| follow the train's own motor demand?")
    ax[0].axhline(0, color="k", lw=0.5)
    ax[0].legend(fontsize=8)
    ax[0].grid(alpha=0.3, axis="y")
    names, b, br, aa = [], [], [], []
    for g, d in trac["residual_repeatability"].items():
        names.append(g)
        b.append(d["B_dist"]["r_best10"]["mean"]), br.append(d["Bres_dist"]["r_best10"]["mean"])
        aa.append(trac["absaccel_repeatability"][g]["r_best10"]["mean"])
    x = np.arange(len(names))
    ax[1].bar(x - 0.27, b, 0.27, label="|B| between runs")
    ax[1].bar(x, br, 0.27, label="|B| after removing linear fit on |a|, v")
    ax[1].bar(x + 0.27, aa, 0.27, label="|along-track accel| between runs")
    an = trac["absaccel_different_section_null"]["r_best10"]["mean"]
    ax[1].axhline(an, color="grey", ls="--", label=f"|along-track accel|, different sections (mean {an:.2f})")
    ax[1].set_xticks(x, names, fontsize=8, rotation=15)
    ax[1].set_ylabel("best Pearson r within ±10 % shift (L1)")
    ax[1].set_title("Between-run repeatability, calibrated step-free clean runs")
    ax[1].axhline(0, color="k", lw=0.5)
    ax[1].legend(fontsize=8)
    ax[1].grid(alpha=0.3, axis="y")
    rows = trac["local_sd_by_state_uT"]
    for i, name in enumerate(("motoring", "braking", "coasting")):
        v = [r[name] for r in rows.values() if name in r]
        ax[2].scatter(np.full(len(v), i) + np.linspace(-0.15, 0.15, len(v)), v, color="#4c72b0")
        ax[2].hlines(np.median(v), i - 0.3, i + 0.3, color="k")
    ax[2].set_xticks(range(3), ["motoring", "braking", "coasting"])
    ax[2].set_ylabel("local |B| sd over 2.5 s (µT)")
    ax[2].set_title("|B| variability by train state\n(calibrated clean sections; bar = median)")
    ax[2].grid(alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(OUT / "mag_traction_test.png", dpi=110)
    plt.close(fig)


# --------------------------------------------------------------------------- verdicts
def _fmt(x):
    return ", ".join(f"{v:.2f}" for v in x)


def verdict_gyro(res):
    """GO if every clean same-section pair beats the different-section null max AND its own
    spectrum-matched surrogates (p < 0.05); groups whose surrogate p99 > 0.8 are flagged
    low-information (one broad curve: repeatable, but weak for metre-level positioning)."""
    g = res["groups"]
    nul = res["different_section_null"]["yaw_dist"]["r_best10"]
    parts, ok_all, low_info, n_groups = [], True, [], 0
    for n, d in g.items():
        sp = d["surrogate"]["yaw_dist"]
        if not sp:
            parts.append(f"{n}: fewer than 2 clean runs, not scored")
            continue
        n_groups += 1
        rmin, pmax = min(x["r_best"] for x in sp), max(x["p"] for x in sp)
        p99 = float(np.median([x["null_p99"] for x in sp]))
        ok_all &= rmin > nul["max"] and pmax < 0.05
        if p99 > 0.8:
            low_info.append(n)
        parts.append(f"{n}: r = {_fmt([x['r_best'] for x in sp])} (surrogate p <= {pmax:.3f}, surrogate p99 {p99:.2f})")
    v = "GO" if ok_all and n_groups >= 2 else "WEAK"
    cross = []
    for name, d in res["cross_direction"].items():
        sp = d["yaw_dist_surrogate_shift25"]
        if sp:
            cross.append(f"{name.split(' ')[0]}: plain r {np.mean([x['r_plain'] for x in sp]):.2f}, best r within "
                         f"+-25% {np.mean([x['r_best'] for x in sp]):.2f} at shift {np.mean([x['shift'] for x in sp]):+.0%} "
                         f"(surrogate p {', '.join(f'{x['p']:.3f}' for x in sp)})")
    sds = {n: d["heading_total_clean_mean_sd"] for n, d in g.items() if d["heading_total_clean_mean_sd"]}
    txt = (f"Yaw-rate signatures repeat. Clean same-section pairs on the L1 distance grid (best r within +-10% shift): "
           f"{'; '.join(parts)}. Different-section null: mean {nul['mean']:.2f}, max {nul['max']:.2f}. "
           + (f"Low-information: {', '.join(low_info)} is essentially one broad curve, so even spectrum-matched random "
              f"signals reach r ~0.85; it repeats but carries little positional detail. " if low_info else "")
           + f"Total heading change, clean runs (mean +- sd, deg): "
           f"{', '.join(f'{n} {m:+.1f} +- {sd:.1f}' for n, (m, sd) in sds.items())}; part of the sd comes from "
           f"section-boundary placement (~+-5 s). Opposite direction over the same track (WB reversed, sign-flipped): "
           f"{'; '.join(cross)}. The EB/WB match needs a few-% shift and is weaker than same-direction; the two bores "
           f"are not identical near LST. Caveats: 2-3 runs per group, one phone, one morning; distance is a "
           f"dead-reckoned proxy, not ground truth.")
    return v, txt


def verdict_mag(res, trac):
    """|B| repeatability on calibrated runs, judged per pair against spectrum-matched surrogates."""
    g = res["groups"]
    nb = res["different_section_null_calibrated"]["B_dist"]["r_best10"]
    nd = res["different_section_null_calibrated"]["dB_dist"]["r_best10"]
    bp = [x for d in g.values() for x in d["surrogate"]["B_dist"]]
    dp = [x for d in g.values() for x in d["surrogate"]["dB_dist"]]
    sig_b = [x for x in bp if x["p"] < 0.05]
    sig_d = [x for x in dp if x["p"] < 0.05]
    signs = [np.sign(x["r_plain"]) for x in bp + dp if np.isfinite(x["r_plain"])]
    if bp and len(sig_b) == len(bp) and min(x["r_best"] for x in bp) > nb["max"]:
        v = "GO"
    elif bp + dp and (len(sig_b) + len(sig_d)) > 0.5 * len(bp + dp):
        v = "WEAK"
    else:
        v = "NO-GO"
    aa = [d["r_best10"]["mean"] for d in trac["absaccel_repeatability"].values()]
    an = trac["absaccel_different_section_null"]["r_best10"]["mean"]
    resid = [d["Bres_dist"]["r_best10"]["mean"] for d in trac["residual_repeatability"].values()]
    qc = json.load(open(ROOT / "outputs" / "qc.json"))["runs"]
    jumps = {s.split("_")[0]: [x["jump_uT"] for x in v["magnetometer"].get("steps", [])] for s, v in qc.items()
             if v["magnetometer"]["mode"] == "calibrated" and v["magnetometer"].get("steps")}
    allj = [j for v in jumps.values() for j in v]
    txt = (f"No usable position-locked |B| map from these runs. iOS re-estimated the calibration offset mid-ride on "
           f"{', '.join(jumps)} (one-sample jumps of {min(allj):.0f}-{max(allj):.0f} uT with the phone still, qc.json), so only "
           f"{len(bp)} calibrated same-section pair(s) are jump-free: "
           f"{'; '.join(f'{x['a']} vs {x['b']} r {x['r_best']:.2f} (p {x['p']:.3f})' for x in bp) or 'none'}, against "
           f"a calibrated-only different-section null of mean {nb['mean']:.2f}, max {nb['max']:.2f}. With the jumps "
           f"masked, high-passed d|B| gives {len(sig_d)}/{len(dp)} pairs at surrogate p < 0.05 "
           f"(r = {_fmt([x['r_best'] for x in dp])}; null mean {nd['mean']:.2f}, max {nd['max']:.2f}); "
           f"{sum(x > 0 for x in signs)}/{len(signs)} pairs have positive plain r. Scattered weak matches, not consistent "
           f"across runs. |along-track accel| repeats between runs (r {_fmt(aa)}) but scores {an:.2f} between DIFFERENT "
           f"sections too, so it is not position evidence and the contrast says nothing about the field source; after "
           f"regressing |B| on |a| and speed (a weak linear proxy for traction current) between-run r is {_fmt(resid)}. "
           f"Calibration jumps are a confound that has not been separated from train and tunnel fields. Raw runs "
           f"(R0.5, R1) are not pooled into these numbers.")
    return v, txt


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sig, meta, cache, raw_centre = build()
    G = groups(meta)
    res = analyse(sig, meta)
    trac = traction_test(sig, meta)
    dwm = dwell_vs_moving(cache, meta)
    tf_yaw, tf_mag = to_eb(meta)

    plot_gyro(sig, meta, G)
    plot_mag(sig, meta, G)
    plot_matrix(sig, meta, G, "yaw_dist", tf_yaw, "gyro_matrix.png",
                "Yaw-rate similarity, all Central sections (L1 distance grid; WB reversed + sign-flipped)\n"
                "blocks: EB LST-BTH | WB BTH-LST | EB BTH-MLE | WB MLE-BTH | R0.5 bonus sections   (* = flagged)")
    plot_matrix(sig, meta, G, "B_dist", tf_mag, "mag_matrix.png",
                "|B| similarity, all Central sections (L1 distance grid; WB reversed)\n"
                "blocks: EB LST-BTH | WB BTH-LST | EB BTH-MLE | WB MLE-BTH | R0.5 bonus sections   (* = flagged)")
    plot_summary(res, "gyro_summary.png", ["yaw_time", "yaw_dist"],
                 "(a) Gyro yaw rate: same section vs null baselines (clean runs)")
    plot_summary(res, "mag_summary.png", ["B_dist", "dB_dist"],
                 "(b) Magnetometer |B|: same section vs null baselines (calibrated runs)")
    plot_traction(sig, meta, trac)

    vg, tg = verdict_gyro(res)
    vm, tm = verdict_mag(res, trac)
    out = {"method": {
        "yaw_rate": "gyro (bias from quietest platform dwell) projected on unit up vector (accel low-passed 0.4 Hz, "
                    "continuous), low-passed 1 Hz; + = left turn regardless of phone orientation",
        "grids": f"N={R.N} section fraction depart->arrive; L0 = wall-clock fraction (pause -> NaN); "
                 "L1 = dead-reckoned distance fraction (gravity = section mean accel, PCA along-track axis, "
                 "v=0 both ends, speed held across pauses)",
        "metrics": "Pearson r; best r within +-10% shift; DTW (dtaidistance, window 10%) on z-normalised signals",
        "nulls": "partner run time-reversed; partner run block-shuffled (8 blocks, 20 draws); pairs of different "
                 "physical sections (incl. R0.5 OXC-LST sections)",
        "magnetometer": f"|B| of 1 Hz low-passed vector; raw runs bias-corrected by raw-group hard-iron centre "
                        f"{np.round(raw_centre, 1).tolist()} uT from outputs/qc.json; Δ|B| = |B| high-passed 0.05 Hz",
        "clean": f"sections without {sorted(EXCLUDE)} (>5% overlap)"},
        "sections": meta, "results": res, "traction_test": trac, "dwell_vs_moving": dwm,
        "verdicts": {"a_gyro_yaw": {"verdict": vg, "justification": tg},
                     "b_magnetometer": {"verdict": vm, "justification": tm}}}
    json.dump(out, open(OUT / "repeatability.json", "w"), indent=1, default=float)
    print(f"(a) gyro: {vg}\n  {tg}\n(b) mag: {vm}\n  {tm}")


if __name__ == "__main__":
    main()
