"""Per-second A-weighted levels + third-octave bands for every run.

Writes data/interim/noise/<stem>.parquet, outputs/noise/<stem>_levels.csv,
outputs/noise/<stem>.png, outputs/noise/summary.csv|json and band_check.csv.
"""
import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from tubesense.ingest import ROOT, list_runs
from tubesense.noise import BAND_COLS, BAND_LABELS, SPEECH_THRESH, band_check, levels, processing, summary

OUT = ROOT / "outputs" / "noise"


def plot(df: pd.DataFrame, s: dict, path) -> None:
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(13, 7.5), sharex=True,
                                 gridspec_kw={"height_ratios": [1, 1.3]})
    t = df.t_audio + 0.5
    a1.plot(t, df.LAFmax_1s, color="0.6", lw=0.9, label="LAFmax, 1 s")
    a1.plot(t, df.LAeq_1s, color="C3", lw=1.4, label="LAeq, 1 s")
    sp = df.speech_flag
    a1.plot(t[sp], df.LAeq_1s[sp] + 2, "v", ms=4, color="C0", label=f"speech-like (mod > {SPEECH_THRESH})")
    lim = df.limited
    a1.plot(t[lim], np.full(lim.sum(), df.LAFmax_1s.max() + 1.5), "|", ms=6, color="k",
            label="peak at limiter ceiling")
    a1.set_ylabel("level (dBFS, A-wtd, uncalibrated)")
    a1.set_title(f"{df.stem.iloc[0]}: per-second loudness  |  overall LAeq {s['LAeq_overall_dbfs']:.1f} dBFS(A), "
                 f"p5-p95 {s['LAeq_1s_p5']:.1f} to {s['LAeq_1s_p95']:.1f}")
    a1.grid(alpha=0.3)
    a1.legend(loc="lower right", fontsize=8, ncol=4)
    B = df[BAND_COLS].to_numpy().T
    vmax = np.nanpercentile(B, 99.5)
    im = a2.imshow(B, aspect="auto", origin="lower", cmap="magma", vmin=vmax - 50, vmax=vmax,
                   extent=[df.t_audio.iloc[0], df.t_audio.iloc[-1] + 1, -0.5, len(BAND_COLS) - 0.5],
                   interpolation="nearest")
    ticks = range(0, len(BAND_LABELS), 3)
    a2.set_yticks(list(ticks), [BAND_LABELS[i] for i in ticks])
    a2.set_ylabel("third-octave band centre (Hz)")
    a2.set_xlabel("audio time (s from start of Voice Memos recording)")
    a2.set_title("third-octave band Leq per second (unweighted, dBFS)")
    fig.colorbar(im, ax=a2, pad=0.01, label="dBFS")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for run in list_runs():
        df = levels(run, use_cache=False)
        df.to_csv(OUT / f"{run.stem}_levels.csv", index=False, float_format="%.2f")
        s = summary(df) | processing(run)
        rows.append(s)
        plot(df, s, OUT / f"{run.stem}.png")
        print(f"{run.stem:18s} LAeq {s['LAeq_overall_dbfs']:6.1f}  p5 {s['LAeq_1s_p5']:6.1f}  "
              f"p95 {s['LAeq_1s_p95']:6.1f}  ceiling {s['ceiling_dbfs']:5.2f}  limited {s['limited_frac']:.2f} "
              f"(top10% {s['limited_frac_top10pct']:.2f}, crest deficit {s['crest_deficit_p50_top10pct_db']:.1f} dB)")
    summ = pd.DataFrame(rows)
    summ.to_csv(OUT / "summary.csv", index=False, float_format="%.3f")
    (OUT / "summary.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    band_check("R5").to_csv(OUT / "band_check.csv", index=False, float_format="%.2f")
