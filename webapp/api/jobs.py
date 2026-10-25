"""JobManager: subprocess jobs whose JSON-lines output the server relays as server-sent events.

What: runs a ride replay, the live analyst, a stream replay, a live sensor capture (live.capture) or an Argon ask as a separate Python process
(`python -B -m <module> ...`, cwd = repo root, PYTHONPATH = repo root). Each stdout line that is a JSON object
becomes an event (kept in memory with a `seq`); any other stdout line and every stderr line becomes a
`{"type": "log"}` event. When the process exits a `job_end` event is appended.
Why separate processes: the trackers' settings are module globals (v1 and v2 cannot share a process), memory
returns to the OS when a job exits, and cancel is a kill (webapp/DESIGN.md sections 5.1 and 6.3).
JSON lines that carry their kind under "event" (agent.ride_report) are relayed with type = event (a "type" they carry
becomes "type_name").
Rules: one heavy job (ride, analyst, stream, live, and the Argon-at-work jobs: roughness, recheck, recheck_canary, ride_report) at a time and one ask at a time (409 `busy`, or `replace`);
a running or held ride, stream or live job nobody follows for ORPHAN_S is cancelled; finished jobs are kept 30 min (max 20).
"""
from __future__ import annotations

import bisect
import json
import os
import subprocess
import sys
import threading
import time
import uuid

from .util import ROOT, ApiError, now_iso

HEAVY = ("ride", "analyst", "stream", "live", "roughness", "recheck", "recheck_canary", "ride_report")   # one at a time (RAM)
LIVE_STATES = ("starting", "holding", "running")
MAX_EVENTS = 20_000
MAX_LOGS = 500
KEEP_S = 30 * 60
KEEP_N = 20
ORPHAN_S = 60.0
KEEP_TYPES = ("init", "end", "error", "job_end", "meta", "departure", "final")


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the job process and its children. On Windows the venv's python.exe is a launcher that starts the
    real interpreter as a child, so terminate() alone would leave the worker running."""
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=10)
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        proc.kill()
    except OSError:
        pass


class Job:
    def __init__(self, kind: str, params: dict, label: str, argv: list[str]):
        self.id = "j" + uuid.uuid4().hex[:8]
        self.kind, self.params, self.label, self.argv = kind, params, label, argv
        self.created = now_iso()
        self.t0 = time.monotonic()
        self.status = "starting"
        self.ended: str | None = None
        self.exit_code: int | None = None
        self.error: str | None = None
        self.events: list[dict] = []
        self.seqs: list[int] = []
        self.seq = 0
        self.n_logs = 0
        self.cond = threading.Condition()
        self.proc: subprocess.Popen | None = None
        self.cancelled = False
        self.finished = False
        self.subscribers = 0
        self.idle_since = time.monotonic()      # last time the subscriber count was 0 (or job creation)
        self.ended_mono: float | None = None

    # ---- events
    def emit(self, ev: dict) -> None:
        with self.cond:
            t = ev.get("type")
            if t == "log":
                if self.n_logs >= MAX_LOGS:
                    return
                self.n_logs += 1
            self.seq += 1
            ev = {**ev, "seq": self.seq}
            self.events.append(ev)
            self.seqs.append(self.seq)
            if t == "error":
                self.error = str(ev.get("message", ""))[:500]
            if self.status == "starting":
                if t == "holding":
                    self.status = "holding"
                elif t not in ("status", "log", "warning", "init", "meta"):
                    self.status = "running"
            if len(self.events) > MAX_EVENTS:
                self._compact()
            self.cond.notify_all()

    def _compact(self) -> None:
        """Drop the oldest 10 % of the events that are not structural (init, end, error ...)."""
        drop = MAX_EVENTS // 10
        keep, dropped = [], 0
        for e in self.events:
            if dropped < drop and e.get("type") not in KEEP_TYPES:
                dropped += 1
                continue
            keep.append(e)
        self.events, self.seqs = keep, [e["seq"] for e in keep]

    def wait_events(self, after: int, timeout: float) -> tuple[list[dict], bool]:
        """Events with seq > after (waits up to `timeout` s for new ones); the flag says the job has ended
        and everything up to and including job_end is in the list or was already delivered."""
        with self.cond:
            end = time.monotonic() + timeout
            while True:
                i = bisect.bisect_right(self.seqs, after)
                if i < len(self.events) or self.finished:
                    return self.events[i:], self.finished
                left = end - time.monotonic()
                if left <= 0:
                    return [], False
                self.cond.wait(left)

    # ---- view
    def to_dict(self) -> dict:
        with self.cond:
            return {"id": self.id, "kind": self.kind, "status": self.status, "params": self.params,
                    "label": self.label, "created": self.created, "started": self.created, "ended": self.ended,
                    "n_events": len(self.events), "events_url": f"/api/jobs/{self.id}/events",
                    "exit_code": self.exit_code, "error": self.error, "subscribers": self.subscribers}


class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self.lock = threading.RLock()
        self.stopping = False
        threading.Thread(target=self._watch, daemon=True, name="job-watch").start()

    # ---- lookup
    def get(self, jid: str) -> Job:
        with self.lock:
            j = self.jobs.get(jid)
        if j is None:
            raise ApiError(404, "not_found", f"no job {jid}")
        return j

    def list(self) -> list[dict]:
        with self.lock:
            return [j.to_dict() for j in sorted(self.jobs.values(), key=lambda j: j.t0, reverse=True)]

    def live(self, kinds: tuple[str, ...]) -> Job | None:
        with self.lock:
            return next((j for j in self.jobs.values() if j.kind in kinds and j.status in LIVE_STATES), None)

    def heavy_running(self) -> dict | None:
        j = self.live(HEAVY)
        return j.to_dict() if j else None

    # ---- start / control / cancel
    def start(self, kind: str, module: str, args: list[str], params: dict, label: str, *,
              control: bool = False, replace: bool = False) -> Job:
        argv = [sys.executable, "-B", "-m", module, *args]
        with self.lock:
            self._prune()
            clash = self.live(HEAVY) if kind in HEAVY else self.live(("ask",)) if kind == "ask" else None
            if clash is not None:
                if not replace:
                    what = "ask" if kind == "ask" else "heavy job"
                    raise ApiError(409, "busy", f"A {what} is running ({clash.label}). Stop it first or pass replace.",
                                   running=clash.to_dict())
                self._cancel(clash, wait=6.0)
            job = Job(kind, params, label, argv)
            env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1",
                   "PYTHONDONTWRITEBYTECODE": "1"}
            try:
                job.proc = subprocess.Popen(argv, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            stdin=subprocess.PIPE if control else subprocess.DEVNULL,
                                            text=True, encoding="utf-8", errors="replace", bufsize=1)
            except OSError as e:
                raise ApiError(500, "internal", f"could not start the job process: {type(e).__name__}") from e
            self.jobs[job.id] = job
        outs = [threading.Thread(target=self._pump, args=(job, job.proc.stdout, "stdout"), daemon=True),
                threading.Thread(target=self._pump, args=(job, job.proc.stderr, "stderr"), daemon=True)]
        for t in outs:
            t.start()
        threading.Thread(target=self._wait, args=(job, outs), daemon=True).start()
        return job

    def _pump(self, job: Job, pipe, stream: str) -> None:
        try:
            for line in pipe:
                line = line.rstrip("\r\n")
                if not line.strip():
                    continue
                if stream == "stdout":
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        ev = None
                    if isinstance(ev, dict) and isinstance(ev.get("event"), str):
                        # agent.ride_report names its events "event"; its error event also has "type" = the exception name
                        ev = {**ev, "type": ev["event"], **({"type_name": ev["type"]} if "type" in ev else {})}
                    if isinstance(ev, dict) and isinstance(ev.get("type"), str):
                        job.emit(ev)
                        continue
                job.emit({"type": "log", "stream": stream, "text": line[:1000]})
        except (OSError, ValueError):
            pass

    def _wait(self, job: Job, pumps: list[threading.Thread]) -> None:
        code = job.proc.wait()
        for t in pumps:
            t.join(timeout=3)
        with job.cond:
            has_error = any(e.get("type") == "error" for e in job.events)
        if code != 0 and not job.cancelled and not has_error:
            job.emit({"type": "error", "message": f"process exited with code {code}"})
        status = "cancelled" if job.cancelled else "done" if code == 0 else "error"
        secs = round(time.monotonic() - job.t0, 1)
        with job.cond:
            job.status, job.exit_code = status, code
            job.ended, job.ended_mono = now_iso(), time.monotonic()
        job.emit({"type": "job_end", "status": status, "exit_code": code, "seconds": secs})
        with job.cond:
            job.finished = True
            job.cond.notify_all()

    def _write(self, job: Job, line: str) -> None:
        try:
            job.proc.stdin.write(line + "\n")
            job.proc.stdin.flush()
        except (OSError, ValueError, AttributeError) as e:
            raise ApiError(409, "not_running", "the job process is not accepting commands") from e

    def go(self, jid: str) -> dict:
        job = self.get(jid)
        with job.cond:
            if job.status != "holding":
                raise ApiError(409, "not_holding", f"job {jid} is {job.status}, not holding")
            job.status = "running"
        self._write(job, "go")
        return job.to_dict()

    def control(self, jid: str, body: dict) -> dict:
        job = self.get(jid)
        if job.kind != "ride":
            raise ApiError(409, "not_running", "only ride jobs accept control")
        with job.cond:
            if job.status not in ("holding", "running"):
                raise ApiError(409, "not_running", f"job {jid} is {job.status}")
        if "speed" in body:
            sp = body["speed"]
            if isinstance(sp, bool) or sp not in (1, 4, 8):
                raise ApiError(400, "bad_request", "speed must be 1, 4 or 8")
            job.params["speed"] = int(sp)
            self._write(job, f"speed {int(sp)}")
        elif body.get("action") in ("pause", "resume"):
            self._write(job, body["action"])
        else:
            raise ApiError(400, "bad_request", 'send {"speed": 1|4|8} or {"action": "pause"|"resume"}')
        return job.to_dict()

    def cancel(self, jid: str) -> dict:
        job = self.get(jid)
        self._cancel(job, wait=6.0)
        return job.to_dict()

    def _cancel(self, job: Job, wait: float = 0.0) -> None:
        with job.cond:
            if job.status not in LIVE_STATES or job.proc is None:
                return
            job.cancelled = True
        _kill_tree(job.proc)
        end = time.monotonic() + wait
        while time.monotonic() < end and not job.finished:
            time.sleep(0.05)

    # ---- subscribers
    def subscribe(self, job: Job) -> None:
        with job.cond:
            job.subscribers += 1

    def unsubscribe(self, job: Job) -> None:
        with job.cond:
            job.subscribers = max(0, job.subscribers - 1)
            if job.subscribers == 0:
                job.idle_since = time.monotonic()

    # ---- housekeeping
    def _prune(self) -> None:
        now = time.monotonic()
        done = [j for j in self.jobs.values() if j.status not in LIVE_STATES and j.finished]
        for j in done:
            if j.ended_mono is not None and now - j.ended_mono > KEEP_S:
                self.jobs.pop(j.id, None)
        done = sorted((j for j in self.jobs.values() if j.status not in LIVE_STATES and j.finished),
                      key=lambda j: j.t0)
        for j in done[:max(0, len(done) - KEEP_N)]:
            self.jobs.pop(j.id, None)

    def _watch(self) -> None:
        while not self.stopping:
            time.sleep(5)
            now = time.monotonic()
            with self.lock:
                self._prune()
                jobs = list(self.jobs.values())
            for j in jobs:
                if j.kind not in ("ride", "stream", "live") or j.status not in LIVE_STATES or j.subscribers:
                    continue
                if now - j.idle_since > ORPHAN_S:        # a held ride too: the demo page keeps its stream open while holding
                    print(f"[jobs] cancelling orphan {j.id} ({j.label}, {j.status}): no subscriber for {ORPHAN_S:.0f} s", file=sys.stderr)
                    self._cancel(j)

    def shutdown(self) -> None:
        self.stopping = True
        with self.lock:
            jobs = list(self.jobs.values())
        for j in jobs:
            self._cancel(j)
