"""GO/NO-GO (c): loudness per section. Is Liverpool Street-Bethnal Green louder than Bethnal Green-Mile End?

Reads outputs/sections.json, outputs/qc.json, outputs/sync.json and the per-second noise cache
(tubesense.noise.levels; rebuilt from audio in memory if missing). Writes
outputs/gonogo/loudness.json, loudness_sections.csv, loudness_sections.png, loudness_<stem>.png.
Run: .venv/Scripts/python scripts/loudness.py
"""
from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from tubesense.ingest import ROOT, list_runs, load_run
from tubesense.loudness import (FLOOR_EXCLUDE, TOP_FRAC, TRIM_S, aggregate, build, paired, ranking)
from tubesense.noise import levels
from tubesense.sync import load_params

OUT = ROOT / "outputs" / "gonogo"
BLUE, ORANGE, GREY, INK, MUTED = "#2a78d6", "#eb6834", "#8a8984", "#0b0b0b", "#52514e"
VARIANTS = {"LAeq": "LAeq\n(energy)", "nospeech_LAeq": "LAeq,\nno speech s",
            "core_LAeq": f"core LAeq\n(-{TRIM_S:.0f} s/end)", "top25_LAeq": f"loudest\n{TOP_FRAC:.0%} of s",
            "L10": "L10", "LAeq_1s_mean": "mean of\n1-s LAeq", "LAeq_limiter_ub": "limiter\nupper bound"}
MIN_DB = 1.0   # a difference below this is inside the limiter compression (~1-3 dB) and run-to-run sd


def _clean(o):
    """JSON-safe: NaN/inf -> None, numpy scalars -> Python."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.bool_, bool)):
        return bool(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        return float(o) if np.isfinite(o) else None
    return o


def verdict(P: dict, tab: pd.DataFrame) -> dict:
    s = P["summary"]["placement_ok"]
    n, la = s["n"], s["LAeq"]
    consistent = la["n_positive"] == n
    robust = {k: s[k]["n_positive"] == n for k in VARIANTS}
    if consistent and la["mean_db"] >= MIN_DB and all(robust.values()):
        v = "GO"
    elif consistent:
        v = "WEAK"
    else:
        v = "NO-GO"
    disagree = [k for k, ok in robust.items() if not ok]
    why = (f"LST-BTH is louder than BTH-MLE in {la['n_positive']}/{n} placement-ok trips "
           f"(mean within-trip difference {la['mean_db']:+.2f} dB, range {la['min_db']:+.2f} to {la['max_db']:+.2f}; "
           f"one-sided sign test p={la['sign_test_p_one_sided']:.3f}). "
           f"The difference is below {MIN_DB:.0f} dB, i.e. inside the Voice Memos limiter compression (~1-3 dB) "
           f"and the run-to-run sd of a section (0.4-0.8 dB)."
           + (f" Not robust across variants: {', '.join(disagree)} do not agree in every trip." if disagree else "")
           + " Absolute levels are uncalibrated dBFS(A), so nothing here can be compared with TfL's 92.8 dB.")
    return {"verdict": v, "justification": why, "variant_sign_consistent": robust}


def plot_sections(tab: pd.DataFrame, P: dict, rank: list[dict], path):
    fig = plt.figure(figsize=(15, 11))
    gs = fig.add_gridspec(2, 2, height_ratios=[1, 1.15], hspace=0.42, wspace=0.22)

    # (a) LST-MLE sections: mean +- sd over placement-ok runs, every run as a dot.
    ax = fig.add_subplot(gs[0, 0])
    order = [("EB", "LST->BTH"), ("EB", "BTH->MLE"), ("WB", "BTH->LST"), ("WB", "MLE->BTH")]
    for i, (d, sec) in enumerate(order):
        g = tab[(tab.direction == d) & (tab.section == sec)]
        inc = g[~g.placement_excluded]
        col = BLUE if "LST" in sec else ORANGE
        ax.bar(i, inc.LAeq.mean() + 30, bottom=-30, width=0.6, color=col, alpha=0.35, edgecolor="none")
        ax.errorbar(i, inc.LAeq.mean(), yerr=inc.LAeq.std(ddof=1), color=INK, capsize=6, lw=1.5)
        for j, (_, r) in enumerate(g.iterrows()):
            x = i - 0.2 + 0.2 * j
            ax.plot(x, r.LAeq, "o", ms=8, color=col if not r.placement_excluded else "white",
                    mec=INK, mew=1.2)
            ax.annotate(r.run_id + (" (floor, excl.)" if r.placement_excluded else ""), (x, r.LAeq),
                        xytext=(6, 0), textcoords="offset points", fontsize=8, color=MUTED, va="center")
    ax.set_xticks(range(4), [f"{d}\n{s.replace('->', ' → ')}" for d, s in order])
    ax.set_ylim(-21, -17.5)
    ax.set_ylabel("section LAeq, dB(A) re full scale (uncalibrated)")
    ax.set_title("(a) LST–BTH vs BTH–MLE, per section and direction\nbar = mean of placement-ok runs, "
                 "whisker = ±1 sd, dots = runs", fontsize=11)
    ax.grid(axis="y", alpha=0.3)

    # (b) Within-trip paired differences, per variant.
    ax = fig.add_subplot(gs[0, 1])
    D = pd.DataFrame(P["per_trip"])
    ax.axhline(0, color=INK, lw=1)
    ax.axhspan(-MIN_DB, MIN_DB, color=GREY, alpha=0.12, lw=0)
    for k, (v, lab) in enumerate(VARIANTS.items()):
        for j, (_, r) in enumerate(D.iterrows()):
            x = k - 0.25 + 0.1 * j
            mk = "s" if r.direction == "EB" else "^"
            ax.plot(x, r[f"diff_{v}"], mk, ms=8, color=BLUE if r.direction == "EB" else ORANGE,
                    mfc="white" if r.placement_excluded else None, mec=INK, mew=0.8)
    ax.set_xticks(range(len(VARIANTS)), list(VARIANTS.values()), fontsize=8)
    ax.set_ylabel("LST–BTH minus BTH–MLE, same trip (dB)")
    ax.set_title(f"(b) Within-trip difference (+ = LST–BTH louder)\nshaded band = ±{MIN_DB:.0f} dB, inside limiter "
                 "compression", fontsize=11)
    ax.plot([], [], "s", color=BLUE, mec=INK, label="EB trip (R1+R1.5 split files, R3, R5)")
    ax.plot([], [], "^", color=ORANGE, mec=INK, label="WB trip (R2, R6)")
    ax.plot([], [], "^", color="white", mec=INK, label="R4: floor placement, excluded")
    ax.legend(fontsize=8, loc="upper left")
    ax.grid(axis="y", alpha=0.3)

    # (c) Ranking of every section-run.
    ax = fig.add_subplot(gs[1, :])
    R = pd.DataFrame(rank)
    cols = [BLUE if s in ("LST->BTH", "BTH->LST") else ORANGE if s in ("BTH->MLE", "MLE->BTH")
            else GREY for s in R.section]
    y = np.arange(len(R))
    ax.barh(y, R.LAeq + 30, left=-30, color=cols, height=0.7,
            hatch=["//" if e else "" for e in R.placement_excluded], edgecolor="white")
    for yi, r in zip(y, R.itertuples()):
        ax.text(r.LAeq + 0.05, yi, f"{r.LAeq:.1f}  (vs own trip {r.LAeq_rel_trip:+.1f})", va="center",
                fontsize=8, color=MUTED)
    ax.set_yticks(y, [f"{r.run_id} {r.direction} {r.section.replace('->', ' → ')} "
                      f"[{'Victoria' if r.direction == 'NB' else 'Central'}]"
                      + (" (floor, hatched)" if r.placement_excluded else "") for r in R.itertuples()], fontsize=8)
    ax.invert_yaxis()
    ax.set_xlim(-24.5, -16.5)
    ax.set_xlabel("section LAeq, dB(A) re full scale (uncalibrated; cross-run comparisons are weak)")
    ax.set_title("(c) Every section-run ranked by LAeq. Blue = LST–BTH, orange = BTH–MLE, grey = other "
                 "sections. Bracket = level relative to the trip's running mean (within-file).", fontsize=11)
    ax.grid(axis="x", alpha=0.3)
    fig.suptitle("GO/NO-GO (c): section loudness from phone audio (Voice Memos, uncalibrated)", fontsize=13)
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)


def plot_run(run, df, tab, seg, qc_run, path):
    p = load_params()[run.stem]
    to_a = lambda wall: p["a"] + p["b"] * (wall - p["wall0"])
    from tubesense.sync import sensor_to_audio
    t = df.t_audio + 0.5
    fig, ax = plt.subplots(figsize=(15, 5.2))
    for d in seg["dwells"]:
        a0, a1 = sensor_to_audio(run, [d["t0"], d["t1"]])
        ax.axvspan(a0, a1, color=GREY, alpha=0.15, lw=0)
        ax.text((a0 + a1) / 2, -45.5, d["station"], ha="center", fontsize=9, color=MUTED)
    g = tab[tab.stem == run.stem]
    for k, r in enumerate(g.itertuples()):
        col = BLUE if r.track == "LST-BTH" else ORANGE if r.track == "BTH-MLE" else "#1baf7a"
        ax.axvspan(r.a0, r.a1, color=col, alpha=0.13, lw=0)
        ax.hlines(r.LAeq, r.a0, r.a1, color=col, lw=2.5)
        ax.text((r.a0 + r.a1) / 2, -8.2 - 1.6 * (k % 2), f"{r.section.replace('->', '→')}\nLAeq {r.LAeq:.1f}",
                ha="center", va="top", fontsize=8.5, color=INK)
    for f in qc_run["flags"]:
        if f["type"] == "floor_like_placement":
            a0, a1 = sensor_to_audio(run, [f["t0"], f["t1"]])
            ax.hlines(-43, a0, a1, color=INK, lw=4)
        if f["type"] == "pause":
            ax.axvspan(to_a(f["wall0"]), to_a(f["wall1"]), color="#e34948", alpha=0.22, lw=0)
    if (g.floor_frac > 0).any():
        ax.plot([], [], color=INK, lw=4, label="QC floor-like placement")
    if any(f["type"] == "pause" for f in qc_run["flags"]):
        ax.fill_between([], [], color="#e34948", alpha=0.22, label="phyphox pause (audio continues)")
    ax.plot(t, df.LAFmax_1s, color=GREY, lw=0.8, label="LAFmax per second")
    ax.plot(t, df.LAeq_1s, color=INK, lw=1.2, label="LAeq, 1 s")
    sp = df.speech_flag.to_numpy()
    ax.plot(t[sp], np.full(sp.sum(), -41), "|", color="#6250d6", ms=8, label="speech-flagged second (crude)")
    ax.set_ylim(-47, -4)
    ax.set_xlim(0, t.iloc[-1] + 0.5)
    ax.set_xlabel("audio time (s from start of Voice Memos file)")
    ax.set_ylabel("dB(A) re full scale (uncalibrated)")
    ax.set_title(f"{run.stem}: 1-s A-weighted level; coloured = sections (line = section LAeq), grey = dwells; "
                 f"sync {p.get('method')} ({p.get('confidence')} confidence)", fontsize=11)
    ax.legend(fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.13), ncol=6, frameon=False)
    ax.grid(alpha=0.25)
    fig.savefig(path, dpi=100, bbox_inches="tight")
    plt.close(fig)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    tab, ev, ctx = build()
    agg = {k: aggregate(tab, k).round(3).to_dict("records") for k in ("LAeq", "core_LAeq", "top25_LAeq", "L10")}
    P = paired(tab)
    rank = ranking(tab)
    V = verdict(P, tab)

    series_cols = ["series_t_rel", "series_LAeq_1s"]
    tab.drop(columns=series_cols).round(3).to_csv(OUT / "loudness_sections.csv", index=False)
    S = json.loads((ROOT / "outputs" / "sections.json").read_text())
    Q = json.loads((ROOT / "outputs" / "qc.json").read_text())
    for r in list_runs():
        run = load_run(r)
        seg = next(x for x in S["runs"] if x["stem"] == run.stem)
        plot_run(run, levels(run), tab, seg, Q["runs"][run.stem], OUT / f"loudness_{run.stem}.png")
    plot_sections(tab, P, rank, OUT / "loudness_sections.png")

    out = {
        "units": "dB(A) re digital full scale (dBFS(A)), uncalibrated; relative comparisons only",
        "method": {
            "mapping": "sections.json depart/arrive (phyphox experiment time) -> audio s via sync.sensor_to_audio "
                       "(wall time; R4 pauses respected). 1-s bins whose centre lies in [a0, a1); bins inside a "
                       "phyphox pause (no sensor data, phone handled) are dropped.",
            "edge_check": "coarse consistency check only (resolves the sync to ~+-5-10 s): offset of the quiet dip "
                          "from each sensor arrival/departure within +-10 s; hits at the window edge = no dip found",
            "variants": {"LAeq": "energy mean of 1-s LAeq over the section",
                         "nospeech_": "same, speech_flag seconds dropped (crude flag)",
                         "core_": f"same, first/last {TRIM_S:.0f} s dropped (accel/brake, sync error)",
                         "top25_LAeq": f"energy mean of the loudest {TOP_FRAC:.0%} of seconds (length-neutral)",
                         "LAeq_limiter_ub": "1-s LAeq + crest_deficit_db (indicative upper bound without limiter)",
                         "L10/L90": "90th/10th percentile of 1-s LAeq",
                         "n_s_within6_of_max": "seconds within 6 dB of the section-run max 1-s LAeq"},
            "placement_exclusion": f"section-run excluded from averages when >= {FLOOR_EXCLUDE:.0%} of it is "
                                   "covered by qc.json floor_like_placement flags",
            "trips": "qc.json split_runs pair R1 + R1.5 as one logical trip (separate audio files)",
        },
        "verdict": V,
        "paired_differences": P,
        "aggregate_by_section_direction": agg,
        "ranking": rank,
        "victoria_within_run": {
            "note": "R0 only (n=1). Consistent in direction with TfL's FOI figure ranking Green Park-Victoria "
                    "loudest on the network (92.9 dB), but a single run and within processing uncertainty; the FOI "
                    "ranking is network-wide, not a comparison with Green Park-Oxford Circus.",
            "VIC->GPK_minus_GPK->OXC_db": {k: float(tab.loc[(tab.run_id == "R0") & (tab.section == "VIC->GPK"), k].iloc[0]
                                                    - tab.loc[(tab.run_id == "R0") & (tab.section == "GPK->OXC"), k].iloc[0])
                                           for k in VARIANTS},
        },
        "run_context": ctx,
        "edge_check": ev.round(2).to_dict("records"),
        "sections": tab.round(3).to_dict("records"),   # includes the 1-s series (t from section start)
    }
    (OUT / "loudness.json").write_text(json.dumps(_clean(out), indent=1, allow_nan=False))
    print(V["verdict"], "-", V["justification"])


if __name__ == "__main__":
    main()
