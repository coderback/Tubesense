"""Atlas endpoints: three-way search with a CACHED fallback, and the Stream Processing windows.

What: GET /api/atlas/search runs demo.atlas_show.gather_live(q, k) (read-only on the cluster) in a worker thread
with an overall deadline; on any failure (including SystemExit from a missing URI) it serves the stored snapshot
(outputs_posthoc/atlas_snapshot.json) and says so (`mode: cached`, `fallback_reason`). It never writes the snapshot.
GET /api/stream/status calls atlas.live_view.latest(ride_id, 30) (imported lazily, 10 s deadline) and returns its
dict (`windows` only while live; older windows under `history`, never shown as live; `processor_state`, `age_s`);
if the module is missing or fails the answer is `available: false` with the reason and no windows.
POST /api/stream/start launches webapp.api.stream_worker (atlas.stream_replay.replay) as a heavy job.
Why: the Atlas screen must work offline and must never invent stream windows.
"""
from __future__ import annotations

import threading

from . import egress
from .util import ApiError, exc_text, has_module, now_iso, read_json

DEADLINE_S = 25.0
STATUS_DEADLINE_S = 10.0


def _run_with_deadline(fn, seconds: float):
    """fn() in a daemon thread; returns (value, None) or (None, exception). A timeout is a TimeoutError."""
    box: dict = {}

    def target():
        try:
            box["v"] = fn()
        except BaseException as e:  # noqa: BLE001  (SystemExit from atlas.db when MONGODB_URI is missing)
            box["e"] = e
    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        return None, TimeoutError(f"no answer within {seconds:.0f} s")
    return box.get("v"), box.get("e")


def _chain(AS, snap: dict) -> dict:
    local = AS._local_chain()
    recs = snap.get("records") or {}
    last = recs.get("attempts_last") or []
    head = last[0]["hash"] if last else None
    local["atlas_head_matches"] = bool(local.get("lines")) and head == local.get("head_hash") and \
        recs.get("attempts_count") == local.get("lines")
    return local


def search(q: str | None, k: int, mode: str) -> dict:
    from demo import atlas_show as AS
    q = (q or AS.DEFAULT_Q).strip()
    if not 3 <= len(q) <= 200:
        raise ApiError(400, "bad_request", "q must be 3 to 200 characters")
    if not 1 <= k <= 5:
        raise ApiError(400, "bad_request", "k must be 1 to 5")
    if mode not in ("live", "cached"):
        raise ApiError(400, "bad_request", "mode must be live or cached")
    snap, reason = None, None
    if mode == "live":
        snap, err = _run_with_deadline(lambda: AS.gather_live(q, k), DEADLINE_S)
        if err is not None or snap is None:
            snap, reason = None, type(err).__name__ if err else "no result"
        else:
            egress.add("MongoDB Atlas", "your search text (read-only queries)", "/api/atlas/search")
    live = snap is not None
    if not live:
        snap = read_json(AS.SNAPSHOT)
        if snap is None:
            raise ApiError(503, "unavailable", "Atlas is not reachable and there is no cached snapshot"
                           + (f" ({reason})" if reason else ""))
        for m in snap["search"]["modes"].values():
            m["hits"] = m["hits"][:k]
    return {"mode": "live" if live else "cached", "taken": snap["taken"],
            "fallback_reason": None if live or mode == "cached" else reason,
            "question_mismatch": not live and snap["search"]["question"] != q,
            "cluster": snap["cluster"], "search": snap["search"], "records": snap["records"],
            "local_chain": _chain(AS, snap)}


# --------------------------------------------------------------------------- Stream Processing
def stream_status(ride_id: str | None) -> dict:
    base = {"available": False, "reason": None, "processor": None, "processor_state": None, "age_s": None, "windows": [],
            "history": [], "checked_at": now_iso()}
    if not has_module("atlas.live_view"):
        return {**base, "reason": "atlas.live_view not installed"}

    def call():
        from atlas import live_view as LV
        return LV.latest(ride_id, 30)
    out, err = _run_with_deadline(call, STATUS_DEADLINE_S)
    if err is not None or not isinstance(out, dict):
        return {**base, "reason": exc_text(err or ValueError("no result"))}
    egress.add_throttled("MongoDB Atlas", "stream window read (read-only)", "/api/stream/status")
    return {**out, "checked_at": now_iso()}


def stream_start(jobs, body: dict):
    from .rides import find_ride
    run, track = body.get("run"), body.get("track")
    if not isinstance(run, str) or not isinstance(track, str):
        raise ApiError(400, "bad_request", "run and track are required strings")
    ride = find_ride(run, track)
    if ride is None or not ride["available"]:
        raise ApiError(400, "bad_request", f"{run} {track} is not an available catalogue ride")
    speed = body.get("speed", 4)
    if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not 1 <= speed <= 8:
        raise ApiError(400, "bad_request", "speed must be 1 to 8")
    dry = body.get("dry_run", False)
    if not isinstance(dry, bool):
        raise ApiError(400, "bad_request", "dry_run must be true or false")
    if not has_module("atlas.stream_replay"):
        raise ApiError(503, "unavailable", "atlas.stream_replay not installed yet")
    args = ["--run", run, "--track", track, "--speed", str(speed)] + (["--dry-run"] if dry else [])
    job = jobs.start("stream", "webapp.api.stream_worker", args,
                     {"run": run, "track": track, "speed": speed, "dry_run": dry},
                     f"stream {run} {track} into Atlas at {speed:g}x" + (" (dry run)" if dry else ""),
                     replace=bool(body.get("replace", False)))
    if not dry:
        egress.add("MongoDB Atlas `ingest_live`", "per-second derived values (t, position, relative level, section)",
                   "/api/stream/start")
    return job
