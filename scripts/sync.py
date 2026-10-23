"""Sync every run's sensors to its audio; writes outputs/sync.json and outputs/sync/<stem>.png."""
from __future__ import annotations

import json
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from tubesense.ingest import ROOT, list_runs, load_audio, load_run
from tubesense.sync import SYNC_JSON, audio_envelope, imu_band, imu_envelope, sync_run, _norm_log

OUT = ROOT / "outputs" / "sync"


def plot(run, y, sr, p):
    acc = run.sensors["acc"]
    w = run.to_wall(acc.t.values) - p["wall0"]
    ta_s = p["a"] + p["b"] * w                      # sensor samples on audio time axis
    hp = imu_band(run, 8, None, "acc")
    gap = np.flatnonzero(np.diff(w) > 0.5) + 1          # break lines across phyphox pauses
    ta_s, hp = np.insert(ta_s, gap, np.nan), np.insert(hp, gap, np.nan)
    T = p["sensor_span_wall_s"]
    fig, axs = plt.subplots(3, 1, figsize=(14, 11))
    hop = 0.005
    ea = 20 * np.log10(audio_envelope(y, sr, hop, 1000, 8000) + 1e-9)
    te = (np.arange(len(ea)) + 0.5) * hop
    for ax, (lo, hi), name, key in [(axs[0], (-2, 14), "start", "start"), (axs[1], (T - 14, T + 2), "end", "end")]:
        a0, a1 = p["a"] + lo, p["a"] + hi
        m = (ta_s >= a0) & (ta_s <= a1) | ~np.isfinite(ta_s)   # keep the NaN breaks at pauses
        ax.plot(ta_s[m], hp[m], color="tab:red", lw=0.8, label="accel |a| high-passed 8 Hz")
        ax.set_ylabel("accel (m/s²)", color="tab:red")
        ax2 = ax.twinx()
        mm = (te >= a0) & (te <= a1)
        ax2.plot(te[mm], ea[mm], color="k", lw=0.4, alpha=0.7, label="audio 1-8 kHz level")
        ax2.set_ylabel("audio level (dBFS)")
        st, at = p[f"{key}_taps_sensor_wall"], p[f"{key}_taps_audio"]
        if st:
            for s_, a_ in zip(st, at):
                ax.axvline(p["a"] + p["b"] * s_, color="tab:red", ls="--", lw=1)
                ax.axvline(a_, color="tab:blue", ls=":", lw=1.5)
        ax.set_xlim(a0, a1)
        ax.set_title(f"{name}: {'tap triple matched' if st else 'no tap triple matched'} "
                     f"(red dashed = accel taps mapped, blue dotted = audio taps)")
        ax.set_xlabel("audio time (s)")
    # whole run, 1 s envelopes
    dt = 0.1
    e, m = imu_envelope(run, dt, 15, 45, "acc")
    s = _norm_log(e, 10, 0)
    q = _norm_log(audio_envelope(y, sr, dt, 20, 200), 10, 0)
    tq = (np.arange(len(q)) + 0.5) * dt
    ts = p["a"] + p["b"] * (np.arange(len(s)) + 0.5) * dt
    ax = axs[2]
    ax.plot(tq, (q - q.mean()) / q.std(), "k", lw=0.6, label="audio 20-200 Hz log level")
    ax.plot(ts, np.where(m, (s - s[m].mean()) / s[m].std(), np.nan) + 4, color="tab:red", lw=0.6,
            label="accel 15-45 Hz log vibration (+4)")
    ax.set_xlabel("audio time (s)")
    ax.set_ylabel("z-scored log level")
    ax.legend(loc="upper right", fontsize=8)
    ax.set_title(f"whole run aligned: a={p['a']:.3f} s, method={p['method']}, confidence={p['confidence']}, "
                 f"xcorr lag {p['xcorr_lag_s']:.2f} s (z={p['xcorr_z']:.1f})")
    fig.suptitle(f"{run.stem}: sensor -> audio sync")
    fig.tight_layout()
    fig.savefig(OUT / f"{run.stem}.png", dpi=90)
    plt.close(fig)


def main(stems=None):
    OUT.mkdir(parents=True, exist_ok=True)
    result = json.loads(SYNC_JSON.read_text()) if SYNC_JSON.exists() and stems else {}
    for r in list_runs():
        if stems and r.run_id not in stems and r.stem not in stems:
            continue
        run = load_run(r)
        y, sr = load_audio(run)
        p = sync_run(run, y, sr)
        result[run.stem] = p
        plot(run, y, sr, p)
        del y
        print(f"{run.stem:18s} {p['method']:15s} {p['confidence']:6s} a={p['a']:8.3f} "
              f"xc={p['xcorr_lag_s']:.2f}(z{p['xcorr_z']:.1f}) first/last={p['xcorr_first_lag_s']:.2f}/"
              f"{p['xcorr_last_lag_s']:.2f} coarse={p['coarse_lag_s']:.1f}({p['coarse_support']}) "
              f"res={p['residuals_ms']} | {p['notes']}")
    SYNC_JSON.parent.mkdir(parents=True, exist_ok=True)
    SYNC_JSON.write_text(json.dumps(result, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:] or None)
