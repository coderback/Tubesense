"""GET /api/method: the honest scoreboard, pre-registration facts and sealed-test numbers.

What: the scoreboard rows are fixed text copied from outputs/FINDINGS.md and the deck, each with its source path
(they change only when someone edits this file). `prereg` is read from git now (tag commit; whether the frozen code
is unchanged since the tag); `sealed` and `h5` are read from outputs_posthoc/attempts.jsonl, outputs/online/
forward_test.json and outputs_posthoc/h5_niosh.json. Cached 60 s (git calls are cheap but not free).
Why: NO-GO results belong on the screen in plain words; the numbers come from files, not from this module.
"""
from __future__ import annotations

import subprocess
import threading
import time

from .util import POSTHOC, ROOT, now_iso, read_json, read_jsonl

TAG = "prereg-24oct"
_cache: dict = {"t": 0.0, "v": None}
_lock = threading.Lock()

SCOREBOARD = [
    {"question": "Do gyro curve signatures repeat ride to ride?", "verdict": "GO",
     "evidence": "same-section r 0.76-0.99 on the distance grid, far above the different-section null (mean 0.20, max 0.67)",
     "source": "outputs/FINDINGS.md (a)", "label": "PRE-REGISTERED"},
    {"question": "Positioning on new rides with frozen maps (H2b)", "verdict": "GO",
     "evidence": "median 1.95 m vs 8.2 m timetable on LST<->BTH, 90 % coverage 0.96",
     "source": "outputs/online/forward_test.json", "label": "PRE-REGISTERED"},
    {"question": "Does it transfer to the Hammersmith & City (H1/H2a)?", "verdict": "NO-GO",
     "evidence": "one usable pair, r 0.72 vs null max 0.84; tracker 52 m vs timetable 47 m",
     "source": "outputs/FINDINGS.md (24 Oct)", "label": "PRE-REGISTERED"},
    {"question": "Rank section loudness from phone audio", "verdict": "NO-GO",
     "evidence": "louder on 10 of 11 trips, mean +0.54 dB; the rule needs every trip (R10 WB -0.13 dB)",
     "source": "outputs/gonogo/loudness.json", "label": "PRE-REGISTERED"},
    {"question": "Magnetometer as a position fingerprint", "verdict": "NO-GO",
     "evidence": "|B| NO-GO; no stable platform signature (within-platform sd 19 vs between-station 22 uT)",
     "source": "outputs/FINDINGS.md (b), H4", "label": "PRE-REGISTERED"},
    {"question": "Absolute level, NIOSH app (H5, indicative)", "verdict": "MEASURED",
     "evidence": "91.9 dB(A) EB, 87.3 dB(A) WB, one whole trip each; the app is validated only to about 85 dB(A)",
     "source": "outputs_posthoc/h5_niosh.json", "label": "PRE-REGISTERED"},
    {"question": "Tracker v2 (Argon-tuned) on the sealed 24 Oct test", "verdict": "MATCHED, NOT BEATEN",
     "evidence": "median 1.8 m vs v1 1.8 m; coverage 0.936 vs 0.951; stop gate engaged in 0 of 10 sections",
     "source": "outputs_posthoc/attempts.jsonl attempt 8", "label": "POST-HOC"},
    {"question": "Corrugation wavelength from the roar", "verdict": "OPEN",
     "evidence": "dominant roar peaks are fixed-frequency resonances (9 of 9 runs), so frequency / speed gives no wavelength",
     "source": "docs/ARCHITECTURE.md", "label": "POST-HOC"},
]


def _git(*args: str) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=15)
        return p.returncode, p.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return -1, ""


def _prereg() -> dict:
    rc, commit = _git("rev-list", "-n1", TAG)
    unchanged = None
    if rc == 0 and commit:
        rc2, _ = _git("diff", "--quiet", TAG, "--", "tubesense", "scripts")
        unchanged = True if rc2 == 0 else False if rc2 == 1 else None
    return {"tag": TAG, "tag_commit": commit or None, "frozen_unchanged": unchanged, "checked_at": now_iso()}


def _build() -> dict:
    att = read_jsonl(POSTHOC / "attempts.jsonl")
    test = next((a for a in reversed(att) if a.get("kind") == "test"), None)
    v1 = (read_json(ROOT / "outputs" / "online" / "forward_test.json", {}) or {}).get("overall", {})
    h5 = read_json(POSTHOC / "h5_niosh.json", {}) or {}
    sealed = None
    if test:
        m = test["metrics"]
        sealed = {"v2": {"median_error_m": m["median_error_m"], "coverage_mean": m["coverage_mean"], "n_runs": m["n_runs"],
                         "band_width_mean_m": m.get("band_width_mean_m"), "timetable_median_m": m.get("timetable_median_m"),
                         "attempt": test["attempt"], "ts": test["ts"]},
                  "v1": {"median_m": v1.get("tracker_median_of_runs_m"), "coverage": v1.get("band90_coverage_mean"),
                         "n_sections": v1.get("n_sections")}}
    return {"scoreboard": SCOREBOARD, "prereg": _prereg(), "sealed": sealed,
            "h5": [{"run_id": t["run_id"], "direction": t["direction"], "LAeq_dBA": t["LAeq_dBA"]} for t in h5.get("trips", [])]}


def method() -> dict:
    with _lock:
        if _cache["v"] is None or time.time() - _cache["t"] > 60:
            _cache["v"], _cache["t"] = _build(), time.time()
        return _cache["v"]
