"""Web app stream worker: atlas.stream_replay.replay with JSON lines on stdout.

What: REPLAYs a recorded ride's derived per-second values into Atlas `ingest_live` (the Stream Processing
demo) and prints one JSON object per line: `{"type": "row", ...row}` for each inserted row, then
`{"type": "done", "ride_id": ...}` or `{"type": "error", "message": ...}`. Library prints go to stderr.
--dry-run runs the tracker and emits the rows without any Atlas write.
Why: heavy work stays out of the server process; the job manager relays these lines as SSE.
Run (repo root): PYTHONPATH=. .venv/Scripts/python.exe -B -m webapp.api.stream_worker --run R5 --track "EB LST->BTH" --speed 4 [--dry-run]
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True
OUT = sys.stdout
sys.stdout = sys.stderr

import argparse
import json
import math


def emit(obj: dict) -> None:
    def plain(o):
        if isinstance(o, dict):
            return {k: plain(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [plain(v) for v in o]
        if isinstance(o, float) and not math.isfinite(o):
            return None
        return o
    OUT.write(json.dumps(plain(obj), allow_nan=False, default=str) + "\n")
    OUT.flush()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run", required=True)
    ap.add_argument("--track", required=True)
    ap.add_argument("--speed", type=float, default=4.0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    emit({"type": "status", "phase": "starting", "message": "Loading the ride and the tracker"})
    try:
        from atlas import stream_replay as SR
        rid = SR.replay(a.run, a.track, a.speed, on_row=lambda r: emit({"type": "row", **r}), dry_run=a.dry_run)
    except SystemExit as e:
        emit({"type": "error", "message": str(e.code) if isinstance(e.code, str) else f"exit {e.code}"})
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        emit({"type": "error", "message": f"{type(e).__name__}: {e}"[:500]})
        sys.exit(1)
    emit({"type": "done", "ride_id": rid, "dry_run": a.dry_run})


if __name__ == "__main__":
    main()
