"""Magnetometer repeatability by line: deep tube (Central) vs sub-surface (Hammersmith & City).

Per line, the field anomaly |B - section mean| (tubesense.magnetics) is scored on the same section
(pairs of clean runs, phase-surrogate p) against that line's own different-section null, for:
anom, resid (driving regressed out), and both on the mid-section only. Summary per line and feature:
excess r = mean same-section r - mean different-section r, with a bootstrap 95 % CI (pairs resampled;
runs are shared between pairs, so the CI is optimistic). Shown separately for all modes, raw-only and
calibrated-only pairs. With two lines, the difference in excess r between them, with its CI.
Lines differ in tunnel construction AND rolling stock, so a difference is "these two lines differ",
not "sub-surface tunnels differ". Shorter sections give noisier r: compare excess r and surrogate p,
not raw r across lines.

Platform signatures (|B - hard iron| at still platform dwells, raw runs only) need a validated
hard-iron estimate. It comes from a street-level figure-of-eight recording, data/raw/phyphox/
CAL_<anything>.xlsx (raw magnetometer, phone rotated through all orientations in the open for ~30 s):
accepted if the fitted field strength is 35-65 uT (London ~49 uT) with a relative misfit < 10 %.
Without one, signatures are not computed (in-train sphere fits on 23 Oct failed validation: they gave
30-50 uT offsets on calibrated runs, where the offset should be ~0).

Reads outputs/sections.json, outputs/qc.json, outputs/gonogo/repeatability.json; writes
outputs/gonogo/mag_compare.json and outputs/gonogo/mag_compare.png.
Run: .venv/Scripts/python scripts/mag_compare.py
"""
from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from tubesense import features as F
from tubesense import magnetics as MG
from tubesense.ingest import RAW, ROOT, Run, load_run
from tubesense.stops import line_and_route

OUT = ROOT / "outputs" / "gonogo"
EXCLUDE_RUNS = {"R0"}  # Victoria-line noise reference, not a repeat ride
FEATURES = ("anom", "resid", "anom_mid", "resid_mid")
PRIMARY = ("resid_mid", "raw")  # pre-registered (outputs/PLAN_24Oct.md): driving removed, mid-section, raw pairs
MODES = {"all": ("raw", "calibrated"), "raw": ("raw",), "calibrated": ("calibrated",)}
CAL_FIELD_UT = (35.0, 65.0)
CAL_MISFIT = 0.10
N_BOOT = 2000


def excess(sc: dict, rng) -> dict:
    """Mean same-section r minus mean different-section r, with a bootstrap 95 % CI."""
    same = np.array([p["r"] for p in sc["pairs"]])
    null = np.array(sc["null_r"])
    bs = [rng.choice(same, len(same)).mean() - rng.choice(null, len(null)).mean() for _ in range(N_BOOT)]
    return {"excess_r": round(float(same.mean() - null.mean()), 3),
            "ci95": [round(float(x), 3) for x in np.percentile(bs, [2.5, 97.5])], "boot": bs}


def calibration() -> dict:
    """Hard iron from street-level figure-of-eight recordings (CAL_*.xlsx), validated, or a reason why not."""
    files = sorted((RAW / "phyphox").glob("CAL_*.xlsx"))
    if not files:
        return {"c": None, "reason": "no CAL_*.xlsx figure-of-eight recording"}
    wins = [w for f in files for w in MG.rotating_windows(load_run(Run("CAL", "", "", f.stem)))]
    fit = MG.hard_iron_joint(wins)
    if fit["c"] is None:
        return {**fit, "files": [f.name for f in files], "reason": "not enough rotation in the recording"}
    X = np.vstack(wins)
    rad = np.linalg.norm(X - np.array(fit["c"]), axis=1)
    field, misfit = float(np.median(rad)), float(np.median(np.abs(rad - np.median(rad))) / np.median(rad))
    ok = CAL_FIELD_UT[0] <= field <= CAL_FIELD_UT[1] and misfit < CAL_MISFIT
    return {**fit, "files": [f.name for f in files], "field_uT": round(field, 1), "misfit": round(misfit, 3),
            "valid": ok, "reason": None if ok else "field strength or misfit outside the acceptance range"}


def line_of(stem_route: str) -> str:
    return line_and_route(stem_route)[0]


def main():
    secs = json.load(open(ROOT / "outputs" / "sections.json"))
    qc = json.load(open(ROOT / "outputs" / "qc.json"))["runs"]
    meta = json.load(open(OUT / "repeatability.json"))["sections"]
    rng = np.random.default_rng(0)
    sig, line, dur, cache = {}, {}, {}, {}
    for sec in secs["sections"]:
        key = f"{sec['run_id']}:{sec['from']}-{sec['to']}"
        if sec["run_id"] in EXCLUDE_RUNS or key not in meta:
            continue
        stem = sec["stem"]
        if stem not in cache:
            run = load_run(stem)
            mode = qc[stem]["magnetometer"]["mode"]
            fixed = F.mag_destep(run)[0] if mode == "calibrated" else run.sensors["mag"]
            cache[stem] = (run, run.sensors["mag"], fixed)
        run, mag, fixed = cache[stem]
        sig[key] = MG.section_features(run, sec, mag, fixed)
        line[key] = sec.get("line") or line_of(run.route)
        dur[key] = sec["duration_s"]
    MG.add_mid(sig)
    lines = sorted(set(line.values()))
    res = {"lines": {}, "primary": {"feature": PRIMARY[0], "modes": PRIMARY[1]}}
    boots = {}
    for ln in lines:
        keys = [k for k in sig if line[k] == ln]
        out = {"sections": len(keys), "median_section_s": round(float(np.median([dur[k] for k in keys])), 1),
               "clean_by_mode": {m: sum(meta[k]["clean"] and meta[k]["mag_mode"] in ms for k in keys) for m, ms in MODES.items()}}
        for f in FEATURES:
            for m, ms in MODES.items():
                sc = MG.score(sig, meta, f, rng, ms, keys, with_null=True)
                if "note" not in sc:
                    ex = excess(sc, rng)
                    boots[(ln, f, m)] = ex.pop("boot")
                    sc.update(ex)
                sc.pop("null_r", None)
                out[f"{f}/{m}"] = sc
        res["lines"][ln] = out
    if len(lines) >= 2:
        res["difference"] = {}
        for f in FEATURES:
            for m in MODES:
                have = [ln for ln in lines if (ln, f, m) in boots]
                for i, a in enumerate(have):
                    for b in have[i + 1:]:
                        d = np.array(boots[(a, f, m)]) - np.array(boots[(b, f, m)])
                        res["difference"][f"{f}/{m}: {a} - {b}"] = {
                            "excess_r_diff": round(res["lines"][a][f"{f}/{m}"]["excess_r"] - res["lines"][b][f"{f}/{m}"]["excess_r"], 3),
                            "ci95": [round(float(x), 3) for x in np.percentile(d, [2.5, 97.5])]}
    cal = calibration()
    res["hard_iron"] = cal
    res["platform_signatures"] = {}
    if cal.get("valid"):
        for r in secs["runs"]:
            if r["run_id"] in EXCLUDE_RUNS or qc[r["stem"]]["magnetometer"]["mode"] != "raw":
                continue
            run = load_run(r["stem"])
            for s in MG.platform_signatures(run, r["dwells"], cal["c"]):
                k = f"{line_of(run.route)} {s['station']} {r['direction']}"
                res["platform_signatures"].setdefault(k, []).append({"run": r["run_id"], **s})
    else:
        res["platform_signatures_note"] = f"not computed: {cal['reason']}"
    plot(res, lines)
    json.dump(res, open(OUT / "mag_compare.json", "w"), indent=1, default=float)
    for ln in lines:
        o = res["lines"][ln]
        print(f"{ln}: {o['sections']} sections, median {o['median_section_s']} s, clean by mode {o['clean_by_mode']}")
        for f in FEATURES:
            for m in MODES:
                sc = o[f"{f}/{m}"]
                if "excess_r" in sc:
                    print(f"  {f:9s} {m:10s} same r {sc['same_mean_r']:.2f} ({sc['n_significant']}/{sc['n_pairs']} p<0.05) "
                          f"null {sc['null_mean_r']:.2f}  excess {sc['excess_r']:+.2f} {sc['ci95']}")
    print("hard iron:", {k: v for k, v in cal.items() if k != "files"}, "| platform signatures:", len(res["platform_signatures"]))


def plot(res, lines):
    fig, ax = plt.subplots(1, 2, figsize=(13, 4.6), gridspec_kw={"width_ratios": [2.2, 1]})
    labels = [f"{f}/{m}" for f in FEATURES for m in MODES]
    x = np.arange(len(labels))
    w = 0.8 / max(len(lines), 1)
    for i, ln in enumerate(lines):
        o = res["lines"][ln]
        ys = [o[l].get("excess_r", np.nan) for l in labels]
        lo = [o[l]["excess_r"] - o[l]["ci95"][0] if "ci95" in o[l] else 0 for l in labels]
        hi = [o[l]["ci95"][1] - o[l]["excess_r"] if "ci95" in o[l] else 0 for l in labels]
        ax[0].bar(x + (i - (len(lines) - 1) / 2) * w, ys, w, yerr=[lo, hi], capsize=2, label=ln)
    ax[0].axhline(0, color="k", lw=0.8)
    ax[0].set_xticks(x, labels, rotation=45, ha="right", fontsize=8)
    ax[0].set_ylabel("same-section r minus different-section r")
    ax[0].set_title(f"Field anomaly repeatability by line (bars: 95 % CI). Primary: {PRIMARY[0]}/{PRIMARY[1]}", fontsize=9)
    ax[0].legend(fontsize=8)
    ax[0].grid(alpha=0.3, axis="y")
    ps = res["platform_signatures"]
    if ps:
        for j, (k, v) in enumerate(sorted(ps.items())):
            ax[1].scatter([j] * len(v), [s["B_uT"] for s in v], s=25)
        ax[1].set_xticks(range(len(ps)), sorted(ps), rotation=60, ha="right", fontsize=7)
        ax[1].set_ylabel("|B - hard iron| at a still platform dwell (uT)")
        ax[1].set_title("Platform signatures, raw runs (dots = visits)", fontsize=9)
    else:
        ax[1].axis("off")
        ax[1].text(0.02, 0.5, "Platform signatures: " + res.get("platform_signatures_note", ""), wrap=True, fontsize=9,
                   transform=ax[1].transAxes)
    fig.tight_layout()
    fig.savefig(OUT / "mag_compare.png", dpi=110)
    plt.close(fig)


if __name__ == "__main__":
    main()
