"""REPLAY a recorded ride into tubesense.ingest_live, one document per recorded second, for Stream Processing.

What: the post-hoc v2 tracker (outputs_posthoc/final_config.json, exactly as demo/ride_v2.py applies it) is
run now, causally, over a recorded ride; each recorded second becomes one derived document
  {ride_id, t, ts, s_m, frac, map_frac, band_lo, band_hi, confident, level_rel_dbA, stopped, arrived,
   section, replay: true}
inserted at the replay speed (one insert every 1/speed s of wall clock). The Atlas stream processor
(atlas/stream/processor.json) reads these inserts from the change stream and writes 10 s (replay wall-clock; ~10 x speed recorded s) tumbling windows
into tubesense.live_view; atlas/live_view.py reads those windows back. Nothing here computes windows.
Fields: s_m is metres along the section; frac = s_m / section length (distance fraction along the strip);
map_frac is the tracker's own map-grid fraction (non-linear in metres, kept for reference); stopped = the stop
gate is engaged; arrived = the tracker has reached the end of its map (the train may still be moving).
Why: the live demo needs a ride flowing into Atlas; this is a REPLAY of a recorded ride, labelled as such
(replay: true on every document), not a phone streaming from a tunnel.
Privacy: only derived per-second values leave the machine (position, band, relative phone level in
dBFS(A), flags). No audio, no raw sensor arrays, and no run id: ride_id is a fresh random 'trip-xxxxxxxx'.
ts is the UTC wall clock at insert (event time for the processor's windows); t is s since departure.
Timing: the tracker starts at the back-dated departure onset (as the demo); rows for the seconds before the
departure was detected are held and inserted together when it is detected, as a live system would.

Run:  PYTHONPATH=. .venv/Scripts/python.exe -m atlas.stream_replay R5 --track "EB LST->BTH" --speed 4
      PYTHONPATH=. .venv/Scripts/python.exe -m atlas.stream_replay R5 --track "EB LST->BTH" --speed 20 --cleanup
      (--dry-run: no Atlas writes; --cleanup: delete this ride_id's documents from ingest_live afterwards)
API:  replay(run, track, speed=4.0, on_row=None, ride_id=None) -> ride_id. on_row gets a JSON-safe copy of
      each row (ts as an ISO 8601 string) plus "inserted": true only if that row was written to Atlas (false
      for a dry run, or once an insert has failed: the replay then continues without Atlas, rows not written). Note: applying final_config sets tubesense.online module settings
      for the whole process (as the demo does).
"""
from __future__ import annotations

import argparse
import json
import secrets
import threading
import time
from datetime import datetime, timezone

import numpy as np

COLL = "ingest_live"


def _setup(run_id: str, track: str):
    """Load the ride, map and v2 tracker exactly as demo/ride_v2.py does (imported, not copied)."""
    from demo import ride_v2 as RV
    O, V2, D1 = RV.O, RV.V2, RV.D1
    cfg = json.load(open(RV.FINAL))
    prm = RV.AE.apply(cfg["config"])                         # GAMMA / YAW_SD_FLOOR / P_SPEED, stop gate on
    run = RV.load_run(run_id)
    tpl_c, tpl, shown, _ = RV.build_maps(track, run.run_id)  # leave-one-out map; v2 uses the WTT-scaled one
    feat = V2.gate_features(run, O.stream_features(run))     # causal features
    arm = max(feat.wall.iloc[0], shown.sec["depart_wall"] - RV.ARM_S) if shown is not None else feat.wall.iloc[0]
    dep = O.departure_after(feat, arm)
    if dep is None:
        raise SystemExit("no departure detected after the detector was armed")
    onset = dep["wall_onset"]
    arrive = shown.sec["arrive_wall"] if shown is not None else onset + tpl.duration_s
    w_end = min(feat.wall.iloc[-1], onset + 1.3 * tpl.duration_s + 10, arrive + RV.MAX_POST)
    steps = feat[(feat.wall >= onset) & (feat.wall <= w_end)].reset_index(drop=True)
    return dict(RV=RV, O=O, D1=D1, cfg=cfg, run=run, tpl=tpl, steps=steps, dep=dep, onset=onset, arrive=arrive,
                tracker=V2.GatedTracker(tpl, "depart", prm), noise=D1.live_noise(run),
                paint_band=D1.PAINT_BAND_M * (tpl.length_m / tpl_c.length_m))


def _level(noise, wall: float):
    """The demo's rule: the latest 1-s level bin centred at or before `wall`, if less than 1 s old."""
    if noise is None:
        return None
    k = int(np.searchsorted(noise[0], wall)) - 1
    if k >= 0 and wall - noise[0][k] < 1.0 and np.isfinite(noise[1][k]):
        return round(float(noise[1][k]), 2)
    return None


def _json_safe(row: dict) -> dict:
    return {k: (v.isoformat().replace("+00:00", "Z") if isinstance(v, datetime) else v)
            for k, v in row.items() if k != "_id"}


def replay(run: str, track: str, speed: float = 4.0, on_row=None, ride_id: str | None = None, *,
           dry_run: bool = False, stop_event: threading.Event | None = None) -> str:
    """Replay `run`'s `track` section into ingest_live; returns the random ride_id."""
    ride_id = ride_id or "trip-" + secrets.token_hex(4)
    S = _setup(run, track)
    O, D1, tpl, steps, tracker = S["O"], S["D1"], S["tpl"], S["steps"], S["tracker"]
    onset, detect, arrive = S["onset"], S["dep"]["wall_detect"], S["arrive"]
    coll = None
    if not dry_run:
        from atlas.db import db
        coll = db()[COLL]
    print(f"REPLAY of recorded ride -> {ride_id} | {track} | {len(steps) * O.STEP:.0f} s of ride at x{speed:g} | "
          f"tracker v2, final_config {S['cfg'].get('config_hash', '?')} | departure detected "
          f"{detect - onset:.1f} s after onset" + (" | DRY RUN (no Atlas writes)" if dry_run else ""))

    held, pending, arrived_wall, n_ins, n_fail, n_rows, e = 0, [], None, 0, 0, 0, None
    wall = steps.wall.to_numpy()
    i, k, t0 = 0, 0, time.perf_counter()
    while True:
        k += 1
        w = onset + k                                        # end of recorded second k
        if w > wall[-1] + 1e-9 or (stop_event is not None and stop_event.is_set()):
            break
        while i < len(steps) and wall[i] <= w:              # every 0.25 s tracker step up to this moment
            b = steps.iloc[i]
            e = tracker.step(b.yaw, bool(b.valid), b.gyr > O.HANDLED, {"vib": b.vib, "body": b.body, "h": b.h})
            ok = e["lock"] >= D1.LOCKED and (tpl.metres(e["q95"]) - tpl.metres(e["q05"])) < S["paint_band"]
            held = held + 1 if ok else 0
            if e["map"] >= 0.995 and arrived_wall is None:
                arrived_wall = b.wall
            i += 1
        if e is None:
            continue
        pending.append({"ride_id": ride_id, "t": k, "s_m": round(float(tpl.metres(e["map"])), 1),
                        "frac": round(float(tpl.metres(e["map"])) / tpl.length_m, 4),
                        "map_frac": round(float(e["map"]), 4),
                        "band_lo": round(float(tpl.metres(e["q05"])), 1),
                        "band_hi": round(float(tpl.metres(e["q95"])), 1),
                        "confident": bool(held >= D1.HOLD), "level_rel_dbA": _level(S["noise"], w),
                        "stopped": bool(e.get("gate")), "arrived": arrived_wall is not None,
                        "section": track, "replay": True})
        if w >= detect:                                      # departure known: release held rows
            n_rows += len(pending)
            for row in pending:
                row["ts"] = datetime.now(timezone.utc)       # event time for the processor = wall clock now
                written = False
                if coll is not None:
                    try:
                        coll.insert_one(dict(row))           # insert_one adds _id to its argument: keep row clean
                        n_ins += 1
                        written = True
                    except Exception as ex:                  # keep the replay (and the web app) going
                        n_fail += 1
                        print(f"WARNING: Atlas insert failed ({type(ex).__name__}); continuing without Atlas")
                        coll = None
                if on_row is not None:
                    on_row({**_json_safe(row), "inserted": written})
            pending = []
        if arrived_wall is not None and w >= max(arrived_wall, arrive) + S["RV"].POST_S:
            break
        delay = t0 + k / speed - time.perf_counter()        # one recorded second per 1/speed s
        if delay > 0:
            time.sleep(delay)
    print(f"done: {n_rows} recorded s replayed in {time.perf_counter() - t0:.1f} s; {n_ins} documents inserted "
          f"into {COLL}" + ("" if dry_run else f", {n_rows - n_ins} not written" + (f" ({n_fail} insert failed)"
          if n_fail else "")) + f"; tracker "
          + ("arrived" if arrived_wall is not None else "did not reach the end of the map") + f"; ride_id {ride_id}")
    return ride_id


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", help="recorded run id, e.g. R5")
    ap.add_argument("--track", required=True, help='e.g. "EB LST->BTH"')
    ap.add_argument("--speed", type=float, default=4.0, help="replay speed factor (default 4)")
    ap.add_argument("--ride-id", default=None, help="default: a fresh random trip-xxxxxxxx")
    ap.add_argument("--dry-run", action="store_true", help="run the tracker and print rows; no Atlas writes")
    ap.add_argument("--cleanup", action="store_true", help="delete this ride_id's documents from ingest_live after")
    ap.add_argument("--print-every", type=int, default=10, help="print every n-th row (0: none)")
    a = ap.parse_args()

    def show(row):
        if a.print_every and row["t"] % a.print_every == 0:
            print(f"  t {row['t']:4d} s  s {row['s_m']:7.1f} m  [{row['band_lo']:.0f}, {row['band_hi']:.0f}]  "
                  f"conf {int(row['confident'])}  stop {int(row['stopped'])}  arr {int(row['arrived'])}  "
                  f"ins {int(row['inserted'])}  level {row['level_rel_dbA']}")

    rid = replay(a.run, a.track, a.speed, on_row=show, ride_id=a.ride_id, dry_run=a.dry_run)
    if a.cleanup and not a.dry_run:
        from atlas.db import db
        n = db()[COLL].delete_many({"ride_id": rid}).deleted_count
        print(f"cleanup: deleted {n} documents with ride_id {rid} from {COLL}")


if __name__ == "__main__":
    main()
