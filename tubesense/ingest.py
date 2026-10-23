"""Load phyphox .xlsx exports and iPhone .m4a recordings.

Conventions used by every later stage:
- Sensor time `t` is phyphox "experiment time" in seconds. It does NOT advance while
  phyphox is paused, so a pause is invisible in `t`; use `wall` (Unix seconds,
  reconstructed from the START/PAUSE event log) when lining sensors up with audio.
- Audio is decoded on demand and never cached to disk (privacy).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
INTERIM = ROOT / "data" / "interim"

SENSORS = {
    "Accelerometer": "acc",
    "Gyroscope": "gyr",
    "Magnetometer": "mag",
    "Barometer": "baro",
}

RUN_RE = re.compile(r"^(?P<run>R[\d.]+)_(?P<dir>[A-Z]+)_(?P<route>.+)$")


@dataclass
class Run:
    run_id: str            # e.g. "R3"
    direction: str         # EB / WB / NB
    route: str             # e.g. "LST-MLE"
    stem: str              # file stem shared by .xlsx and .m4a
    sensors: dict[str, pd.DataFrame] = field(default_factory=dict)
    events: pd.DataFrame | None = None

    @property
    def xlsx(self) -> Path:
        return RAW / "phyphox" / f"{self.stem}.xlsx"

    @property
    def audio_path(self) -> Path:
        return RAW / "audio" / f"{self.stem}.m4a"

    def segments(self) -> pd.DataFrame:
        """Recording segments between START and PAUSE events.

        Columns: t0, t1 (experiment time), wall0 (Unix time of t0).
        """
        ev = self.events
        rows = []
        for i, r in ev.iterrows():
            if r.event != "START":
                continue
            nxt = ev.iloc[i + 1] if i + 1 < len(ev) else None
            t1 = nxt["experiment time"] if nxt is not None and nxt.event == "PAUSE" else np.inf
            rows.append({"t0": r["experiment time"], "t1": t1, "wall0": r["system time"]})
        return pd.DataFrame(rows)

    def to_wall(self, t: np.ndarray) -> np.ndarray:
        """Map experiment time -> Unix wall time using the event log."""
        t = np.asarray(t, dtype=float)
        seg = self.segments()
        out = np.full_like(t, np.nan)
        for _, s in seg.iterrows():
            m = (t >= s.t0) & (t <= s.t1)
            out[m] = s.wall0 + (t[m] - s.t0)
        return out


def list_runs() -> list[Run]:
    runs = []
    for p in sorted((RAW / "phyphox").glob("*.xlsx")):
        m = RUN_RE.match(p.stem)
        if not m:
            continue
        runs.append(Run(m["run"], m["dir"], m["route"], p.stem))
    runs.sort(key=lambda r: float(r.run_id[1:]))
    return runs


def _cache_path(stem: str, key: str) -> Path:
    return INTERIM / "sensors" / f"{stem}.{key}.parquet"


def load_run(run: Run | str, use_cache: bool = True) -> Run:
    """Load all sensor sheets + event log for a run (cached as parquet in data/interim)."""
    if isinstance(run, str):
        run = next(r for r in list_runs() if r.run_id == run or r.stem == run)
    keys = list(SENSORS.values()) + ["events"]
    if use_cache and all(_cache_path(run.stem, k).exists() for k in keys):
        for k in SENSORS.values():
            run.sensors[k] = pd.read_parquet(_cache_path(run.stem, k))
        run.events = pd.read_parquet(_cache_path(run.stem, "events"))
        return run

    sheets = pd.read_excel(run.xlsx, sheet_name=None, engine="openpyxl")
    for sheet, key in SENSORS.items():
        if sheet not in sheets:
            continue
        df = sheets[sheet]
        cols = ["t"] + (["p"] if key == "baro" else ["x", "y", "z"])
        df = df.iloc[:, : len(cols)]
        df.columns = cols
        run.sensors[key] = df.dropna().reset_index(drop=True)
    run.events = sheets["Metadata Time"].reset_index(drop=True)

    _cache_path(run.stem, "x").parent.mkdir(parents=True, exist_ok=True)
    for k, df in run.sensors.items():
        df.to_parquet(_cache_path(run.stem, k))
    run.events.to_parquet(_cache_path(run.stem, "events"))
    return run


def load_all() -> list[Run]:
    return [load_run(r) for r in list_runs()]


def load_audio(run: Run, sr: int | None = None) -> tuple[np.ndarray, int]:
    """Decode the run's .m4a to mono float32 in [-1, 1]. Not cached (privacy)."""
    import av

    with av.open(str(run.audio_path)) as c:
        stream = c.streams.audio[0]
        native = stream.rate
        resampler = av.AudioResampler(format="flt", layout="mono", rate=sr or native)
        chunks = []
        for frame in c.decode(stream):
            for f in resampler.resample(frame):
                chunks.append(f.to_ndarray().reshape(-1))
        for f in resampler.resample(None):
            chunks.append(f.to_ndarray().reshape(-1))
    return np.concatenate(chunks).astype(np.float32), sr or native
