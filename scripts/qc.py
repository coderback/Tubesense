"""Automatic messy-input QC for every run: pauses, placement (hand / resting / floor-like),
split runs, unplanned stops, magnetometer calibrated-vs-raw.

Writes outputs/qc.json and plots in outputs/qc/.
Run: .venv/Scripts/python scripts/qc.py
"""
from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy import signal

from tubesense import qc
from tubesense.ingest import ROOT, load_all
from tubesense.stops import motion_features, moving_score, segment_run

OUT = ROOT / "outputs"
KNOWN_CALIBRATED = "R0_NB_VIC_OXC"  # Tobi's note: R0 magnetometer calibrated


def main():
    (OUT / "qc").mkdir(parents=True, exist_ok=True)
    runs = load_all()
    feats, segs, epochs, handheld, mags = {}, {}, {}, {}, {}
    for r in runs:
        f = motion_features(r)
        s = moving_score(f)
        feats[r.stem] = (f, s)
        segs[r.stem] = segment_run(r)
        epochs[r.stem], handheld[r.stem] = qc.placement_epochs(r, f, s)
        mags[r.stem] = qc.magnetometer_mode(r, f)
    qc.classify_placement(epochs, {r.stem: segs[r.stem]["sections"] for r in runs})
    qc.classify_magnetometer(mags, KNOWN_CALIBRATED)
    splits = qc.split_runs(runs)
    slow = qc.slow_sections([s for r in runs for s in segs[r.stem]["sections"]])

    report = {"runs": {}, "split_runs": splits, "placement_cohort_note":
              "hf_vs_peers = vertical 20-50 Hz accel power while riding, per 4 s "
              "chunk, divided by the median of the OTHER runs over the same section and direction (isolated runs left "
              "out of the reference); epoch value = median over chunks. rattle_vs_median = (gyro 3-40 Hz / vertical "
              "20-50 Hz power) over the median of all resting epochs. floor_like = level (<2 deg) AND isolated "
              "(hf_vs_peers < 0.2); hard contact = hf_vs_peers > 3; rattling = rattle_vs_median > 3. Thresholds set on this data."}
    for r in runs:
        f, s = feats[r.stem]
        t = f.t.to_numpy()
        flags = qc.timing_flags(r)
        seg = segs[r.stem]
        for sec in seg["sections"]:
            if sec["overlaps_pause"]:
                flags.append({"type": "section_overlaps_pause", "t0": sec["depart_t"], "t1": sec["arrive_t"],
                              "severity": "medium", "evidence": f"{sec['from']}->{sec['to']}: {sec['evidence']}",
                              "recommendation": "positioning: bridge gap using wall time; noise: audio has no "
                                                "IMU position during the pause, interpolate or drop those seconds"})
            for t0, t1 in sec["tunnel_stops"]:
                flags.append({"type": "unplanned_stop", "t0": t0, "t1": t1, "severity": "low",
                              "evidence": f"stationary {t1 - t0:.0f} s between {sec['from']} and {sec['to']}",
                              "recommendation": "split section at the stop for positioning (v=0 constraint)"})
            for end, ok in (("depart", sec["depart_observed"]), ("arrive", sec["arrive_observed"])):
                if not ok:
                    flags.append({"type": "section_truncated", "t0": sec["depart_t"], "t1": sec["arrive_t"],
                                  "severity": "low", "evidence": f"{sec['from']}->{sec['to']}: {end} not recorded "
                                  "(recording starts/ends while the train is moving or during the taps)",
                                  "recommendation": "use for noise; for positioning anchor on the observed end only"})
        for d in seg["dwells"]:
            if d["duration_s"] > 60:
                flags.append({"type": "long_dwell", "t0": d["t0"], "t1": d["t1"], "severity": "low",
                              "evidence": f"{d['station']} dwell {d['duration_s']:.0f} s",
                              "recommendation": "none (stationary data is excluded from track noise)"})
        edge = 30
        for t0, t1 in handheld[r.stem]:
            mid = t0 > t[0] + 5 and t1 < t[-1] - 5
            flags.append({"type": "handheld", "t0": t0, "t1": t1, "severity": "medium" if mid else "low",
                          "evidence": "sustained 0.3-3 Hz rotation > 0.03 rad/s (hand/body motion, not the car)",
                          "recommendation": "exclude from vibration features; noise OK but mic handling noise likely"})
        w = f.walk.to_numpy()
        gn = f.gyr_norm.to_numpy() > 0.25
        for t0, t1 in qc._runs_of(gn & (t > t[0] + 8) & (t < t[-1] - 8), t, 0.5):
            flags.append({"type": "handled_mid_ride", "t0": t0, "t1": t1, "severity": "low",
                          "evidence": "gyro burst > 0.25 rad/s away from the start/end taps (phone touched/moved)",
                          "recommendation": "drop +-2 s around it from vibration features; new placement epoch"})
        for t0, t1 in qc._runs_of(w & (t > t[0] + edge) & (t < t[-1] - edge), t, 5):
            flags.append({"type": "walking_mid_file", "t0": t0, "t1": t1, "severity": "medium",
                          "evidence": "1.4-2.5 Hz step periodicity in |a|", "recommendation": "exclude interval"})
        for e in epochs[r.stem]:
            if e.get("reorient_deg", 0) > 10:
                flags.append({"type": "reorientation", "t0": e["t0"], "t1": e["t0"], "severity": "low",
                              "evidence": f"gravity direction in phone frame changed {e['reorient_deg']:.0f} deg",
                              "recommendation": "treat as a new placement epoch"})
            if e.get("floor_like"):
                flags.append({"type": "floor_like_placement", "t0": e["t0"], "t1": e["t1"], "severity": "high",
                              "evidence": f"tilt {e['tilt_med_deg']:.1f} deg (level), coupling {e['coupling']} "
                                          f"(20-50 Hz vertical power x{e['hf_vs_peers']:.3f} of same-section peers, "
                                          f"rattle x{e['rattle_vs_median']:.2f} of median); put down mid-ride after being "
                                          f"hand-held: {e['placed_mid_ride']}",
                              "recommendation": "exclude from noise averages and vibration/corrugation features; "
                                                "OK for positioning (yaw-rate signature unaffected)"})
            elif e.get("coupling", "typical") not in ("typical", "unknown"):
                flags.append({"type": "atypical_coupling", "t0": e["t0"], "t1": e["t1"], "severity": "medium",
                              "evidence": f"{e['coupling']}: 20-50 Hz vertical power x{e['hf_vs_peers']:.2f}, "
                                          f"of same-section peers, rattle (gyro 3-40 Hz / vertical 20-50 Hz) x{e['rattle_vs_median']:.2f} of median, "
                                          f"tilt {e['tilt_med_deg']:.1f} deg",
                              "recommendation": "normalise vibration per run before comparing; noise OK"})
        for sl in slow:
            if sl["stem"] == r.stem:
                flags.append({"type": "slow_running", "t0": sl["t0"], "t1": sl["t1"], "severity": "low",
                              "evidence": f"{sl['section']} took {sl['duration_s']:.0f} s vs median "
                                          f"{sl['median_s']:.0f} s for the same section and direction (signals / "
                                          "'Minor Delays'?)",
                              "recommendation": "positioning must not assume the usual speed profile here"})
        mg = mags[r.stem]
        if mg["mode"] == "calibrated":
            for st in mg["steps"]:
                flags.append({"type": "magnetometer_reset", "t0": st["t"], "t1": st["t"], "severity": "medium",
                              "evidence": f"field vector jumped {st['jump_uT']:.0f} uT in one sample with the phone "
                                          f"still; median |B| {st['B_before_uT']:.0f} -> {st['B_after_uT']:.0f} uT",
                              "recommendation": "split |B| into epochs here; do not compare levels across the jump"})
        flags.append({"type": f"magnetometer_{mg['mode']}", "t0": None, "t1": None,
                      "severity": "info" if mg["mode"] == "calibrated" else "medium",
                      "evidence": mg["evidence"], "confidence": mg["confidence"],
                      "recommendation": "do not pool |B| traces across modes; for raw, subtract the hard-iron "
                                        "bias (mean vector) before comparing" if mg["mode"] == "raw" else
                                        "comparable with other calibrated runs"})
        for sp in splits:
            if r.stem in sp["runs"]:
                flags.append({"type": sp["type"], "t0": None, "t1": None,
                              "severity": "medium" if sp["type"] == "split_run" else "info",
                              "evidence": sp["evidence"],
                              "recommendation": f"stitch as logical trip {sp['logical_trip_id']}" if sp["logical_trip_id"]
                              else "keep separate"})
        report["runs"][r.stem] = {
            "flags": flags,
            "placement_epochs": [{k: (float(f"{v:.4g}") if isinstance(v, float) else v) for k, v in e.items()
                                  if not k.startswith("_")}
                                 for e in epochs[r.stem]],
            "magnetometer": mg,
        }
        print(f"{r.stem:18s} mag={mg['mode']:10s} flags: " + ", ".join(sorted({fl['type'] for fl in flags})))
        for e in epochs[r.stem]:
            print(f"    epoch {e['t0']:6.1f}-{e['t1']:6.1f}  tilt {e['tilt_med_deg']:4.1f}  {e.get('label')}  "
                  f"hf x{e.get('hf_vs_peers', float('nan')):.3f}  rattle x{e.get('rattle_vs_median', float('nan')):.2f}  "
                  f"mid-ride {e['placed_mid_ride']}")
    for sp in splits:
        print(sp["type"], sp["runs"], sp["gap_s"], sp["logical_trip_id"])
    report["slow_sections"] = slow
    print("slow:", slow)
    (OUT / "qc.json").write_text(json.dumps(report, indent=1, default=float))
    plot_placement(runs, epochs, feats)
    plot_spectra(runs, epochs, feats)
    plot_mag(mags)
    print(f"wrote {OUT / 'qc.json'}")


def plot_placement(runs, epochs, feats):
    fig, ax = plt.subplots(len(runs), 1, figsize=(13, 2.1 * len(runs)), sharex=False)
    for a, r in zip(ax, runs):
        f, _ = feats[r.stem]
        t = f.t.to_numpy()
        g = qc._gravity(r, t)
        tilt = np.degrees(np.arccos(np.clip(np.abs(g[:, 2]), 0, 1)))
        a.plot(t, tilt, color="tab:blue", lw=0.9)
        a.set_ylim(0, 20)
        a.set_ylabel("tilt (°)", color="tab:blue")
        b = a.twinx()
        b.semilogy(t, f.gyr_body, color="tab:red", lw=0.6)
        b.set_ylim(1e-3, 3)
        b.set_ylabel("gyro 0.3-3 Hz\n(rad/s)", color="tab:red", fontsize=8)
        for e in epochs[r.stem]:
            a.axvspan(e["t0"], e["t1"], color="tab:purple" if e.get("floor_like") else "tab:green",
                      alpha=0.12, lw=0)
            lab = (e.get("label", "").replace(" (soft/compliant support)", "").replace(" (rigid contact)", "")
                   .replace("too short to judge", "short").replace("resting; no same-section peers to compare", "no peers")
                   .replace("; FLOOR-LIKE (level + isolated)", "\nFLOOR-LIKE"))
            a.text((e["t0"] + e["t1"]) / 2, 18, lab, ha="center", va="top", fontsize=7)
        a.set_title(r.stem, fontsize=9, loc="left")
        a.grid(alpha=0.3)
    ax[-1].set_xlabel("phyphox experiment time (s)")
    fig.suptitle("Phone placement: tilt from level (blue), hand/body rotation (red); shaded = resting epochs "
                 "(purple = floor-like: level + isolated)", fontsize=10)
    fig.tight_layout()
    fig.savefig(OUT / "qc" / "placement_timeline.png", dpi=85)
    plt.close(fig)


def plot_spectra(runs, epochs, feats):
    fig, ax = plt.subplots(1, 2, figsize=(14, 5.5))
    for r in runs:
        a = r.sensors["acc"]
        ta = a.t.to_numpy()
        fs = 1 / np.median(np.diff(ta))
        A = a[["x", "y", "z"]].to_numpy()
        f, s = feats[r.stem]
        for e in epochs[r.stem]:
            if "vert_20_50" not in e:
                continue
            ride = (f.t > e["t0"]) & (f.t < e["t1"]) & (s > 0.75)
            Ps = []
            for tc in f.t[ride].to_numpy()[::8]:
                m = (ta >= tc - 2) & (ta < tc + 2)
                if m.sum() < 300:
                    continue
                z = A[m] @ np.array(e["_g"])
                fr, P = signal.welch(z - z.mean(), fs=fs, nperseg=256)
                Ps.append(P)
            ls = "--" if e.get("floor_like") else "-"
            ax[0].loglog(fr, np.median(Ps, 0), ls, lw=1.6 if ls == "--" else 1,
                         label=f"{r.run_id} {e['t0']:.0f}-{e['t1']:.0f}s")
            if "hf_vs_peers" in e:
                ax[1].scatter(e["tilt_med_deg"], e["hf_vs_peers"], s=40)
                ax[1].annotate(f"{r.run_id}@{e['t0']:.0f}s", (e["tilt_med_deg"], e["hf_vs_peers"]), fontsize=8)
    ax[0].set_xlabel("frequency (Hz)")
    ax[0].set_ylabel("vertical accel PSD ((m/s²)²/Hz)")
    ax[0].set_title("Riding vibration spectrum per resting epoch (dashed = floor-like)")
    ax[0].legend(fontsize=7, ncol=2)
    ax[0].grid(alpha=0.3, which="both")
    ax[1].set_yscale("log")
    ax[1].axhline(0.2, color="grey", ls=":")
    ax[1].axhline(3.0, color="grey", ls=":")
    ax[1].axvline(2.0, color="grey", ls=":")
    ax[1].set_xlabel("median tilt from level (°)")
    ax[1].set_ylabel("20-50 Hz vertical power / same-section peers")
    ax[1].set_title("Placement map vs same-section peers: <0.2 isolated, >3 hard contact; <2° level")
    ax[1].grid(alpha=0.3, which="both")
    fig.tight_layout()
    fig.savefig(OUT / "qc" / "placement_spectra.png", dpi=85)
    plt.close(fig)


def plot_mag(mags):
    stems = list(mags)
    fig, ax = plt.subplots(figsize=(11, 4.5))
    x = np.arange(len(stems))
    cols = ["tab:blue" if mags[s]["mode"] == "calibrated" else "tab:orange" for s in stems]
    ax.bar(x - 0.2, [mags[s]["median_B_uT"] for s in stems], 0.4, color=cols, label="median |B|")
    ax.bar(x + 0.2, [mags[s]["mean_vec_norm_uT"] for s in stems], 0.4, color=cols, alpha=0.5,
           hatch="//", label="|phone-frame mean vector|")
    ax.axhline(qc.EARTH_UT, color="k", ls="--", lw=0.8, label="Earth field ~49 µT")
    for i, s in enumerate(stems):
        ax.text(i, -45, f"mag ts = gyro ts: {'yes' if mags[s]['ts_shared_with_gyro'] > 0.9 else 'no'}\n"
                f"{mags[s]['rate_mag_hz']:.2f} Hz", ha="center", fontsize=7)
    ax.set_ylim(-70, None)
    ax.axhline(0, color="k", lw=0.6)
    ax.set_xticks(x, [s.split("_")[0] for s in stems])
    ax.set_ylabel("magnetic field (µT)")
    ax.set_title("Magnetometer mode: blue = calibrated stream (same signature as R0), orange = raw stream")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(OUT / "qc" / "magnetometer_mode.png", dpi=90)
    plt.close(fig)


if __name__ == "__main__":
    main()
