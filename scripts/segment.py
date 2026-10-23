"""Detect station stops and cut every run into station-to-station sections.

Writes outputs/sections.json and one diagnostic plot per run in outputs/segment/.
Run: .venv/Scripts/python scripts/segment.py
"""
from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy import signal

from tubesense.ingest import ROOT, load_all
from tubesense.stops import pauses

OUT = ROOT / "outputs"


def yaw_rate(run) -> tuple[np.ndarray, np.ndarray]:
    """Gyro projected on the (low-passed) gravity direction = rotation about the vertical, rad/s."""
    a, g = run.sensors["acc"], run.sensors["gyr"]
    t = g.t.to_numpy()
    fs = 1 / np.median(np.diff(t))
    A = np.column_stack([np.interp(t, a.t, a[c]) for c in "xyz"])
    up = signal.sosfiltfilt(signal.butter(2, 0.2, fs=fs, output="sos"), A, axis=0)
    up /= np.linalg.norm(up, axis=1, keepdims=True)
    G = g[["x", "y", "z"]].to_numpy()
    yr = (G * up).sum(1)
    k = int(fs)
    return t, np.convolve(yr, np.ones(k) / k, "same")


def plot_run(run, res, path):
    f, score = res["_features"], res["_score"]
    t = f.t.to_numpy()
    fig, ax = plt.subplots(4, 1, figsize=(14, 10), sharex=True)
    ax[0].semilogy(t, f.vib, lw=0.8, label="vibration 3-40 Hz")
    ax[0].semilogy(t, f.body, lw=0.8, label="car-body motion 0.5-3 Hz")
    ax[0].set_ylabel("accel RMS (m/s²)")
    ax[0].legend(loc="upper right", fontsize=8)
    ty, yr = yaw_rate(run)
    ax[1].plot(ty, np.degrees(yr), lw=0.7, color="tab:purple")
    ax[1].set_ylabel("yaw rate (°/s)")
    ax[1].set_ylim(-8, 8)
    b = run.sensors["baro"]
    ax[2].plot(b.t, b.p, color="tab:brown", lw=1)
    ax[2].set_ylabel("pressure (hPa)")
    ax[3].plot(t, score, color="k", lw=0.8)
    ax[3].axhline(0.45, color="grey", ls="--", lw=0.8)
    ax[3].set_ylabel("motion score")
    ax[3].set_ylim(-0.5, 2.5)
    ax[3].set_xlabel("phyphox experiment time (s)")
    h = f.handled.to_numpy()
    for x in ax:
        for d in res["dwells"]:
            if not d["virtual"]:
                x.axvspan(d["t0"], d["t1"], color="tab:green", alpha=0.25, lw=0)
        for s in res["sections"]:
            for t0, t1 in s["tunnel_stops"]:
                x.axvspan(t0, t1, color="tab:red", alpha=0.25, lw=0)
            for t0, t1 in s["approach_stops"]:
                x.axvspan(t0, t1, color="tab:olive", alpha=0.25, lw=0)
        x.fill_between(t, 0, 1, where=h, color="orange", alpha=0.15, transform=x.get_xaxis_transform(), lw=0)
        for p in pauses(run):
            x.axvline(p["t"], color="red", lw=1.2, ls=":")
        x.grid(alpha=0.3)
    for s in res["sections"]:
        ax[2].annotate(f"{s['from']}→{s['to']}\n{s['duration_s']:.0f} s", ((s["depart_t"] + s["arrive_t"]) / 2, 0.92),
                       xycoords=("data", "axes fraction"), ha="center", va="top", fontsize=9)
    for d in res["dwells"]:
        ax[0].annotate(d["station"], ((d["t0"] + d["t1"]) / 2, 0.97), xycoords=("data", "axes fraction"),
                       ha="center", va="top", fontsize=9, color="darkgreen", weight="bold")
    ax[0].set_title(f"{run.stem}: station dwells (green), tunnel stops (red), stop-short at platform (olive), handled/taps (orange), "
                    f"phyphox pause (red dotted)", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=90)
    plt.close(fig)


def main():
    (OUT / "segment").mkdir(parents=True, exist_ok=True)
    from tubesense.stops import segment_run

    sections, runs = [], []
    for run in load_all():
        res = segment_run(run)
        plot_run(run, res, OUT / "segment" / f"{run.stem}.png")
        sections += res["sections"]
        runs.append({k: v for k, v in res.items() if not k.startswith("_") and k != "sections"})
        for s in res["sections"]:
            print(f"{run.stem:18s} {s['from']}->{s['to']}  {s['depart_t']:7.1f} -> {s['arrive_t']:7.1f}  "
                  f"{s['duration_s']:6.1f} s (exp {s['expected_s']:.0f})  conf {s['confidence']}")
    out = {"time_base": "depart_t/arrive_t: phyphox experiment time (s); *_wall: Unix s; "
                        "duration_s is wall-clock (includes any phyphox pause)",
           "sections": sections, "runs": runs}
    (OUT / "sections.json").write_text(json.dumps(out, indent=1))
    print(f"wrote {OUT / 'sections.json'} ({len(sections)} sections)")


if __name__ == "__main__":
    main()
