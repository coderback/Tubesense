"""Atlas Stream Processing for the live demo: create, start, stop and tear down, only when a human asks.

What: one stream workspace (tubesense-sp, AWS Dublin, tier SP2, maxTierSize SP2), one connection to our M10
cluster (tsCluster), and one processor (live_windows) that reads inserts on tubesense.ingest_live
(atlas/stream_replay.py writes them), groups them per ride into 10 s tumbling windows (event time from our
ts field) and $merges the windows into tubesense.live_view (atlas/live_view.py reads them). Late documents
go to the dead letter queue tubesense.sp_dlq. Bodies: atlas/stream/{workspace,connection,processor}.json;
the cluster name is filled into the connection body in memory from 'atlas clusters list'.
Why: Stream Processing is one of the six scored MongoDB features; it is billed while a processor runs.
How: Atlas CLI 'atlas api streams ...' (Admin API), bodies on stdin. Nothing changes unless a command is given:
  plan      (default) print every command and the cost; changes nothing
  create    workspace + connection + processor (free; idempotent: skips what exists; --replace re-creates
            a stopped processor from processor.json; --no-timefield drops $source.timeField so the change
            event time is used, a fallback if the first run sends everything to the DLQ)
  start     needs --yes: billing starts, SP2 about $0.059/h (pricing page, verify; per second while started);
            starts fresh (resumeFromCheckpoint false), so start BEFORE the replay
  stop      stop the processor (billing stops)
  status    workspace/processor state + counts in ingest_live, live_view, sp_dlq (free reads)
  teardown  needs --yes: stop, delete processor, delete workspace (its connection goes with it)

Run:  PYTHONPATH=. .venv/Scripts/python.exe -m atlas.stream_setup            (plan)
      PYTHONPATH=. .venv/Scripts/python.exe -m atlas.stream_setup create
      PYTHONPATH=. .venv/Scripts/python.exe -m atlas.stream_setup start --yes
      PYTHONPATH=. .venv/Scripts/python.exe -m atlas.stream_replay R5 --track "EB LST->BTH" --speed 4
      PYTHONPATH=. .venv/Scripts/python.exe -m atlas.stream_setup status
      PYTHONPATH=. .venv/Scripts/python.exe -m atlas.stream_setup stop      (or teardown --yes)
The connection uses the built-in role readWriteAnyDatabase (a role scoped to tubesense would need a custom
database role in Atlas, not created here); the cluster holds only this project's data.
Never prints secrets: CLI output is parsed and only names, states and error messages are shown.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ATLAS = Path("C:/Program Files (x86)/MongoDB Atlas CLI/atlas.exe")
GROUP = "6adc1900a599673c186621f5"
HERE = Path(__file__).resolve().parent / "stream"
WS, CONN, PROC = "tubesense-sp", "tsCluster", "live_windows"
COST = ("SP2 about $0.059/h (AWS eu-west-1; from the Atlas pricing page, verify), billed per second only while "
        "the processor is started; workspaces and connections are not billed")


class CliError(RuntimeError):
    pass


def body(name: str) -> dict:
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def _error_text(out: str, err: str, code: int) -> str:
    """Short message for a failed CLI call. With a logged-in CLI the Atlas error body (JSON) is on stdout and
    stderr is a version warning plus a generic line, so prefer the body, then non-warning stderr lines."""
    try:
        d = json.loads(out)
        if isinstance(d, dict) and (d.get("detail") or d.get("errorCode")):
            return f"{d.get('error', '')} {d.get('errorCode', '')}: {d.get('detail', '')}".strip()[:300]
    except (json.JSONDecodeError, TypeError):
        pass
    lines = [l.strip() for l in err.splitlines() if l.strip() and not l.strip().lower().startswith("warning:")]
    return (lines[0] if lines else f"exit {code}")[:300]


def cli(*args: str, stdin: dict | None = None, timeout: float = 90) -> dict | list | None:
    """Run the Atlas CLI; return parsed JSON (None if empty). Raises CliError with a short message."""
    if not ATLAS.exists():
        raise CliError(f"Atlas CLI not found at {ATLAS}")
    try:
        r = subprocess.run([str(ATLAS), *args], input=json.dumps(stdin) if stdin is not None else None,
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise CliError(f"Atlas CLI timed out after {timeout:g} s") from None
    out = (r.stdout or "").strip()
    if r.returncode != 0:
        err = (r.stderr or "").strip() or out
        if "session expired" in err.lower() or "not logged in" in err.lower():
            raise CliError("Atlas CLI session expired: run 'atlas auth login' and retry")
        raise CliError(_error_text(out, err, r.returncode))
    try:
        return json.loads(out) if out else None
    except json.JSONDecodeError:
        return None


def streams(op: str, *flags: str, stdin: dict | None = None, timeout: float = 90):
    return cli("api", "streams", op, "--groupId", GROUP, *flags, stdin=stdin, timeout=timeout)


def cluster_name() -> str:
    d = cli("clusters", "list", "--projectId", GROUP, "-o", "json") or {}
    names = [c.get("name") for c in d.get("results", []) if c.get("name")]
    if len(names) != 1:
        raise CliError(f"expected one cluster in the project, found {len(names)}: pass --cluster NAME")
    return names[0]


def processor_body(no_timefield: bool) -> dict:
    b = body("processor.json")
    if no_timefield:
        b["pipeline"][0]["$source"].pop("timeField", None)
    return b


def exists(op: str, *flags: str, timeout: float = 90) -> dict | None:
    try:
        return streams(op, *flags, timeout=timeout) or {}
    except CliError as e:
        if any(s in str(e).lower() for s in ("not found", "404", "does not exist", "not_found")):
            return None
        raise


def proc_state(timeout: float = 90) -> str | None:
    p = exists("getStreamProcessor", "--tenantName", WS, "--processorName", PROC, timeout=timeout)
    return None if p is None else p.get("state", "?")


def counts() -> str:
    try:
        from atlas.db import db
        d = db()
        return " | ".join(f"{c}: {d[c].estimated_document_count()}" for c in ("ingest_live", "live_view", "sp_dlq"))
    except Exception as e:
        return f"Atlas counts unavailable ({type(e).__name__})"


def plan(cluster: str) -> None:
    a = f'"{ATLAS}" api streams'
    g = f"--groupId {GROUP}"
    print("Atlas Stream Processing plan (nothing below has been run):")
    print(f"  cost: workspace and connection free; processor {COST}")
    print(f"  cluster for the connection: {cluster}")
    print(f"  create:   {a} createStreamWorkspace {g} < atlas/stream/workspace.json   "
          "(tier SP2, maxTierSize SP2, AWS DUBLIN_IRL)")
    print(f"            {a} createStreamConnection {g} --tenantName {WS} < atlas/stream/connection.json "
          f"(clusterName={cluster}, filled in memory)")
    print(f"            {a} createStreamProcessor {g} --tenantName {WS} < atlas/stream/processor.json")
    print(f"  start:    {a} startStreamProcessorWith {g} --tenantName {WS} --processorName {PROC} "
          "< {\"resumeFromCheckpoint\": false, \"tier\": \"SP2\"}   (needs --yes; billing starts)")
    print(f"  status:   {a} getStreamWorkspace {g} --tenantName {WS}; getStreamProcessor ... --processorName {PROC}")
    print(f"  stop:     {a} stopStreamProcessor {g} --tenantName {WS} --processorName {PROC}")
    print(f"  teardown: stop; {a} deleteStreamProcessor {g} --tenantName {WS} --processorName {PROC}; "
          f"{a} deleteStreamWorkspace {g} --tenantName {WS}   (needs --yes)")
    print("  order for the demo: create -> start --yes -> stream_replay -> status -> stop (or teardown --yes)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", default="plan", choices=("plan", "create", "start", "stop", "status", "teardown"))
    ap.add_argument("--yes", action="store_true", help="confirm start (billing) or teardown (deletes)")
    ap.add_argument("--cluster", default=None, help="cluster name (default: from 'atlas clusters list')")
    ap.add_argument("--replace", action="store_true", help="create: delete and re-create a stopped processor")
    ap.add_argument("--no-timefield", action="store_true", help="create: use the change event time, not ts")
    a = ap.parse_args()
    try:
        if a.cmd == "plan":
            try:
                cl = a.cluster or cluster_name()
            except CliError as e:
                cl = "<CLUSTER>"
                print(f"(cluster name not looked up: {e})")
            plan(cl)
            return
        if a.cmd == "create":
            if exists("getStreamWorkspace", "--tenantName", WS) is None:
                streams("createStreamWorkspace", stdin=body("workspace.json"))
                print(f"created workspace {WS} (SP2, maxTierSize SP2, AWS DUBLIN_IRL)")
            else:
                print(f"workspace {WS} exists")
            if exists("getStreamConnection", "--tenantName", WS, "--connectionName", CONN) is None:
                conn = {**body("connection.json"), "clusterName": a.cluster or cluster_name()}
                streams("createStreamConnection", "--tenantName", WS, stdin=conn)
                print(f"created connection {CONN} -> cluster {conn['clusterName']}")
            else:
                print(f"connection {CONN} exists")
            st = proc_state()
            if st is not None and a.replace:
                if st == "STARTED":
                    raise CliError("processor is STARTED: stop it before --replace")
                streams("deleteStreamProcessor", "--tenantName", WS, "--processorName", PROC)
                print(f"deleted processor {PROC} (was {st}) for --replace")
                st = None
            if st is None:
                streams("createStreamProcessor", "--tenantName", WS, stdin=processor_body(a.no_timefield))
                print(f"created processor {PROC}" + (" (no timeField: change event time)" if a.no_timefield else ""))
            else:
                print(f"processor {PROC} exists ({st}); --replace to re-create it from processor.json")
            print(f"nothing is billed until: python -m atlas.stream_setup start --yes  ({COST})")
        elif a.cmd == "start":
            if not a.yes:
                sys.exit(f"start bills {COST}. Re-run with --yes to start.")
            print(f"requesting start: {COST}")
            try:
                streams("startStreamProcessorWith", "--tenantName", WS, "--processorName", PROC,
                        stdin={"resumeFromCheckpoint": False, "tier": "SP2"})
            except CliError as e:
                raise CliError(f"{e} | the start call failed or timed out: the processor may be running and billing; "
                               "run 'status', and 'stop' if it is STARTED") from None
            print("start accepted: billing has started")
            for _ in range(20):
                st = proc_state()
                if st in ("STARTED", "FAILED"):
                    break
                time.sleep(3)
            print(f"processor {PROC}: {st}. Remember: python -m atlas.stream_setup stop")
        elif a.cmd == "stop":
            streams("stopStreamProcessor", "--tenantName", WS, "--processorName", PROC)
            print(f"stop requested; processor {PROC}: {proc_state()}")
        elif a.cmd == "status":
            print(counts())                                     # free reads, even when the CLI is logged out
            ws = exists("getStreamWorkspace", "--tenantName", WS)
            if ws is None:
                print(f"workspace {WS}: not created")
            else:
                cfg = ws.get("streamConfig") or {}
                reg = ws.get("dataProcessRegion") or {}
                print(f"workspace {WS}: {reg.get('cloudProvider')} {reg.get('region')}, tier {cfg.get('tier')}, "
                      f"maxTierSize {cfg.get('maxTierSize')}")
                p = exists("getStreamProcessor", "--tenantName", WS, "--processorName", PROC)
                print(f"processor {PROC}: " + ("not created" if p is None else
                      f"{p.get('state')}" + (f" (tier {p['tier']})" if p.get("tier") else "")
                      + (f" error: {p['errorMsg']}" if p.get("errorMsg") else "")))
        elif a.cmd == "teardown":
            if not a.yes:
                sys.exit("teardown deletes the processor and the workspace. Re-run with --yes.")
            st = proc_state()
            if st == "STARTED":
                streams("stopStreamProcessor", "--tenantName", WS, "--processorName", PROC)
                print(f"stopped {PROC}")
            if st is not None:
                streams("deleteStreamProcessor", "--tenantName", WS, "--processorName", PROC)
                print(f"deleted processor {PROC}")
            if exists("getStreamWorkspace", "--tenantName", WS) is not None:
                streams("deleteStreamWorkspace", "--tenantName", WS)
                print(f"deleted workspace {WS} (and its connection)")
            print("teardown done; collections ingest_live / live_view / sp_dlq are left as they are")
    except CliError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
