"""Run the whole GO/NO-GO chain in order and regenerate every output.

    .venv/Scripts/python scripts/run_gonogo.py           # rebuild all outputs
    .venv/Scripts/python scripts/run_gonogo.py --clean   # wipe outputs/ first (keeps FINDINGS.md)

Order matters: noise -> sync -> segment -> qc feed repeatability and loudness; repeatability
feeds the positioning ablation (which also fetches the public TfL Route/Sequence for track lengths).
The parquet sensor cache in data/interim/sensors is reused; audio is decoded in memory
only (never written to disk).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time

from tubesense.ingest import ROOT

STEPS = ["inventory", "noise", "sync", "segment", "qc", "repeatability", "mag_anomaly", "mag_compare", "ablation", "online_eval", "forward_test", "loudness"]
KEEP = {"FINDINGS.md", "PLAN_24Oct.md", "prereg"}  # prereg: maps frozen before the 24 Oct rides


def main(clean: bool = False) -> None:
    out = ROOT / "outputs"
    if clean and out.exists():
        for p in out.iterdir():
            if p.name in KEEP:
                continue
            shutil.rmtree(p) if p.is_dir() else p.unlink()
    out.mkdir(exist_ok=True)
    t_all = time.time()
    for step in STEPS:
        t0 = time.time()
        print(f"=== {step} ===", flush=True)
        subprocess.run([sys.executable, str(ROOT / "scripts" / f"{step}.py")], cwd=ROOT, check=True)
        print(f"=== {step} done in {time.time() - t0:.0f} s ===", flush=True)
    print(f"all steps done in {time.time() - t_all:.0f} s; outputs in {out}")


if __name__ == "__main__":
    main(clean="--clean" in sys.argv[1:])
