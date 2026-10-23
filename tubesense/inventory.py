"""File inventory: what each phyphox export and audio file actually contains.

One row per run (an .xlsx paired with its .m4a). Per sensor: sample count, duration,
median rate, dt jitter, gaps and non-monotonic timestamps. Pauses come from the
event log because phyphox experiment time does not advance while paused.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from .ingest import SENSORS, RAW, Run, list_runs, load_audio, load_run

GAP_FACTOR = 3.0      # dt > GAP_FACTOR * median dt counts as a gap
CLIP_LEVEL = 0.999    # |sample| at or above this counts as clipped


def sheet_names(run: Run) -> list[str]:
    import openpyxl

    wb = openpyxl.load_workbook(run.xlsx, read_only=True)
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()


def sensor_stats(t: np.ndarray) -> dict:
    """Rate, jitter, gaps and ordering of one sensor's timestamps."""
    d = np.diff(t)
    med = float(np.median(d))
    gaps = d > GAP_FACTOR * med
    return {
        "n": int(len(t)),
        "t0": float(t[0]),
        "t1": float(t[-1]),
        "dur_s": float(t[-1] - t[0]),
        "rate_hz": 1.0 / med,
        "dt_p1_ms": float(np.percentile(d, 1) * 1e3),
        "dt_p99_ms": float(np.percentile(d, 99) * 1e3),
        "dt_max_ms": float(d.max() * 1e3),
        "n_gaps": int(gaps.sum()),
        "gap_total_s": float(d[gaps].sum()),
        "n_nonmono": int((d <= 0).sum()),
    }


def pauses(run: Run) -> list[dict]:
    """Pauses from the event log: experiment time of the pause, wall-clock length."""
    ev = run.events.reset_index(drop=True)
    out = []
    for i in range(len(ev) - 1):
        if ev.event[i] == "PAUSE" and ev.event[i + 1] == "START":
            out.append({
                "t_exp": float(ev["experiment time"][i]),
                "wall": float(ev["system time"][i]),
                "dur_s": float(ev["system time"][i + 1] - ev["system time"][i]),
            })
    return out


def audio_stats(run: Run) -> dict:
    """Container/codec facts plus clipping and peak level (decoded in memory only)."""
    import av

    with av.open(str(run.audio_path)) as c:
        s = c.streams.audio[0]
        info = {
            "audio_codec": s.codec_context.name,
            "audio_sr": int(s.rate),
            "audio_channels": int(s.channels),
            "audio_sample_fmt": s.format.name,
        }
    x, sr = load_audio(run)
    a = np.abs(x)
    peak = float(a.max())
    info.update({
        "audio_dur_s": len(x) / sr,
        "audio_peak_dbfs": 20 * np.log10(max(peak, 1e-12)),
        "audio_rms_dbfs": 10 * np.log10(max(float(np.mean(x.astype(np.float64) ** 2)), 1e-24)),
        "audio_clip_frac": float((a >= CLIP_LEVEL).mean()),
        # share of samples within 0.1 dB of the file's own peak: a pile-up hints at a limiter
        "audio_near_peak_frac": float((a >= peak * 10 ** (-0.1 / 20)).mean()),
    })
    return info


def _fmt_wall(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone(dt.timedelta(hours=1))).strftime("%H:%M:%S.%f")[:-3]


def inventory_run(run: Run, audio: bool = True) -> dict:
    """One inventory row for a run (flat dict, CSV-friendly)."""
    run = load_run(run)
    sheets = sheet_names(run)
    row = {
        "stem": run.stem, "run_id": run.run_id, "direction": run.direction, "route": run.route,
        "sheets": ";".join(sheets),
        "sensors_present": ";".join(k for s, k in SENSORS.items() if s in sheets),
        "extra_sheets": ";".join(s for s in sheets if s not in SENSORS and not s.startswith("Metadata")),
    }
    for key, df in run.sensors.items():
        for k, v in sensor_stats(df.t.to_numpy()).items():
            row[f"{key}_{k}"] = v
    if "mag" in run.sensors:
        m = run.sensors["mag"]
        b = np.sqrt(m.x ** 2 + m.y ** 2 + m.z ** 2)
        row.update({"mag_B_med_uT": float(b.median()), "mag_B_p5_uT": float(b.quantile(.05)),
                    "mag_B_p95_uT": float(b.quantile(.95))})

    ev = run.events
    ps = pauses(run)
    row.update({
        "events": " ".join(f"{e}@{t:.2f}" for e, t in zip(ev.event, ev["experiment time"])),
        "wall_start": _fmt_wall(float(ev["system time"].iloc[0])),
        "wall_end": _fmt_wall(float(ev["system time"].iloc[-1])),
        "wall_span_s": float(ev["system time"].iloc[-1] - ev["system time"].iloc[0]),
        "n_pauses": len(ps),
        "pause_total_s": sum(p["dur_s"] for p in ps),
        "pauses": "; ".join(f"t={p['t_exp']:.2f}s ({_fmt_wall(p['wall'])}) for {p['dur_s']:.2f}s" for p in ps),
    })
    row["sensor_dur_s"] = max(row.get(f"{k}_dur_s", 0) for k in ("acc", "gyr", "mag"))
    if audio and run.audio_path.exists():
        row.update(audio_stats(run))
        row["audio_minus_wall_s"] = row["audio_dur_s"] - row["wall_span_s"]
    row["audio_file"] = run.audio_path.name if run.audio_path.exists() else ""
    return row


def inventory(audio: bool = True) -> pd.DataFrame:
    """Inventory of every run in data/raw, plus any unpaired files."""
    rows = [inventory_run(r, audio) for r in list_runs()]
    stems = {r["stem"] for r in rows}
    for p in sorted((RAW / "audio").glob("*")):
        if p.stem not in stems:
            rows.append({"stem": p.stem, "audio_file": p.name, "note": "audio without phyphox export"})
    return pd.DataFrame(rows)


def to_markdown(df: pd.DataFrame) -> str:
    """Readable summary tables for outputs/inventory.md."""
    def tbl(cols: dict[str, str], fmt: dict[str, str] | None = None) -> str:
        fmt = fmt or {}
        head = "| " + " | ".join(cols.values()) + " |\n|" + "---|" * len(cols) + "\n"
        lines = []
        for _, r in df.iterrows():
            cells = []
            for c in cols:
                v = r.get(c, "")
                if isinstance(v, float) and np.isnan(v):
                    v = ""
                elif c in fmt and v != "":
                    v = format(v, fmt[c])
                cells.append(str(v))
            lines.append("| " + " | ".join(cells) + " |")
        return head + "\n".join(lines) + "\n"

    md = ["# Tubesense data inventory\n",
          "Generated by `scripts/inventory.py` from `data/raw/`. Sensor durations are phyphox "
          "*experiment time*, which stops during a pause; `wall span` is from the START/PAUSE log "
          f"and includes pauses. Gaps = dt > {GAP_FACTOR:g} x median dt. Audio is decoded in memory "
          "only, never written to disk.\n",
          "## Runs, durations and pauses\n",
          tbl({"stem": "run", "sensors_present": "sensors", "sensor_dur_s": "sensor s",
               "wall_span_s": "wall span s", "audio_dur_s": "audio s", "audio_minus_wall_s": "audio - wall s",
               "n_pauses": "pauses", "pauses": "pause detail", "wall_start": "start (BST)"},
              {"sensor_dur_s": ".1f", "wall_span_s": ".1f", "audio_dur_s": ".1f", "audio_minus_wall_s": ".1f"}),
          "\n## Sensor sample rates and timing quality\n",
          tbl({"stem": "run", "acc_n": "acc n", "acc_rate_hz": "acc Hz", "gyr_rate_hz": "gyr Hz",
               "mag_rate_hz": "mag Hz", "baro_rate_hz": "baro Hz", "acc_dt_p1_ms": "acc dt p1 ms",
               "acc_dt_p99_ms": "acc dt p99 ms", "acc_dt_max_ms": "acc dt max ms",
               "baro_dt_max_ms": "baro dt max ms", "acc_n_gaps": "acc gaps", "gyr_n_gaps": "gyr gaps",
               "mag_n_gaps": "mag gaps", "baro_n_gaps": "baro gaps", "acc_n_nonmono": "non-mono"},
              {"acc_rate_hz": ".2f", "gyr_rate_hz": ".2f", "mag_rate_hz": ".2f", "baro_rate_hz": ".3f",
               "acc_dt_p1_ms": ".1f", "acc_dt_p99_ms": ".1f", "acc_dt_max_ms": ".1f", "baro_dt_max_ms": ".0f"}),
          "\n## Magnetometer |B| (uT)\n",
          "Earth's field in London is ~49 uT. A calibrated (bias-corrected) magnetometer should sit "
          "near that outside heavy interference; raw iPhone output carries a large device bias.\n\n",
          tbl({"stem": "run", "mag_B_med_uT": "median", "mag_B_p5_uT": "p5", "mag_B_p95_uT": "p95"},
              {"mag_B_med_uT": ".0f", "mag_B_p5_uT": ".0f", "mag_B_p95_uT": ".0f"}),
          "\n## Audio files\n",
          tbl({"audio_file": "file", "audio_codec": "codec", "audio_sample_fmt": "fmt", "audio_sr": "Hz",
               "audio_channels": "ch", "audio_dur_s": "dur s", "audio_peak_dbfs": "peak dBFS",
               "audio_rms_dbfs": "rms dBFS", "audio_clip_frac": "clip frac", "audio_near_peak_frac": "near-peak frac"},
              {"audio_dur_s": ".1f", "audio_peak_dbfs": ".2f", "audio_rms_dbfs": ".1f",
               "audio_clip_frac": ".2e", "audio_near_peak_frac": ".2e"}),
          ]
    return "\n".join(md)
