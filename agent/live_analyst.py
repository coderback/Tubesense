"""Live Argon analyst session for the stage: hypothesis -> fixed-harness dev score -> pre-declared verdict -> explanation.

gemini-4-argon (thinking medium) continues the recorded search (outputs_posthoc/attempts.jsonl and
analyst_session.jsonl, 24 Oct): it gets the earlier dev results, proposes an untried allow-listed tracker
setting with a falsifiable hypothesis, the unchanged agent.harness scores it on the 23 Oct dev split
(leave-one-run-out), the harness's accept() rule decides, and Argon then says whether its hypothesis held.
Tools: list_experiments, get_baseline, run_experiment only (no submit, no test: the one sealed 24 Oct test
is spent). Only metrics go to Gemini. Post-hoc, dev split only.
Isolation: the harness reads TUBESENSE_HARNESS_LOG at import, so this module imports it lazily with the
variable set to outputs_posthoc/attempts_live.jsonl (its own hash chain) and refuses to run if the harness
is already bound to another log in this process; the main chain's SHA-256 is checked before and after.
The harness also mirrors each line to Atlas `attempts`, whose count and head demo.atlas_show compares with
the main chain, so here that mirror is made to fail fast (the harness skips it, harness.py unchanged) and
the live lines are upserted into Atlas `attempts_live` instead.
Argon's prose is checked against agent.verify's banned-overclaim list (+ LIVE_BANNED); a flagged closing
summary is sent back once for a rewrite, and every argon_text event carries its flags.
Writes outputs_posthoc/live_sessions/<ts>.jsonl (the events below, as emitted) and <ts>.md.
Events (--jsonl, one per line): start, argon_text, tool_call, tool_result, verdict, chain, final, error.
For a web app: stream(max_exp, on_event) runs the CLI in its own process and relays the events;
list_sessions() lists earlier records for a CACHED replay (label it with the record's own ts).
Live attempts are labelled "live #N" everywhere (the main chain has its own attempt numbers) and live events carry
chain="live". One experiment per turn is enforced; every Gemini call has an HTTP timeout; a second start while a
session runs is refused without writing a record. Older records are re-checked when their .md is rebuilt.
Run: PYTHONPATH=. .venv/Scripts/python -m agent.live_analyst --jsonl [--max-experiments 2]
     PYTHONPATH=. .venv/Scripts/python -m agent.live_analyst --prepare   (score the baseline into the live chain once)
     PYTHONPATH=. .venv/Scripts/python -m agent.live_analyst --rewrite-md outputs_posthoc/live_sessions/<ts>.jsonl
"""
import argparse
import contextlib
import hashlib
import inspect
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POSTHOC = ROOT / "outputs_posthoc"
LIVE_LOG = POSTHOC / "attempts_live.jsonl"
MAIN_LOG = POSTHOC / "attempts.jsonl"
RECORDED = POSTHOC / "analyst_session.jsonl"
SESSIONS = POSTHOC / "live_sessions"
ENV = "TUBESENSE_HARNESS_LOG"
MODEL, THINKING = "gemini-4-argon", "medium"
HTTP_TIMEOUT_MS = 180_000  # per Gemini request; a hung call becomes an `error` event instead of a blocked session
LABEL = "LIVE, post-hoc, 23 Oct dev split only (leave-one-run-out); not a test result"
KEYS = ("GAMMA", "YAW_SD_FLOOR", "P_SPEED", "stop_gate")

JOB = """This is a LIVE, post-hoc continuation of an earlier recorded session, watched by an audience. Dev split
only: the 23 Oct runs, leave-one-run-out. The single sealed 24 Oct test is already spent, so there is no
submission and nothing found here can be test-scored. The harness always compares against the fixed
baseline with its pre-declared rule; you cannot change the rule, and the rule, not you, decides accept/reject.
Tools: list_experiments, get_baseline, run_experiment. Budget: at most {max_exp} new experiment(s), ~1 min each.
1. Call get_baseline. Then, in 2-4 sentences, say what the earlier results suggest is worth testing next.
2. For each experiment pick an allowed configuration that is NOT in the earlier results (repeats are refused).
   `rationale` is a falsifiable hypothesis: the mechanism and the predicted direction of named metrics
   (median error, 90 % band width, mean or per-track coverage). `refuted_if` is the concrete harness outcome
   that would refute it (e.g. "coverage_ok is false" or "error CI upper bound >= 0").
3. After each result, before anything else, say in <= 3 sentences whether the hypothesis HELD or was REFUTED,
   citing the numbers, and what the harness verdict was.
4. Finish with a summary of <= 150 words: each hypothesis, held/refuted, the harness verdict, and what it
   means for the tracker. Errors are agreement with a yaw-landmark proxy on dev runs: never claim test-set or
   real-world accuracy. An honest negative result is a good result.
Context: `synthetic_stop` is the end-of-stop error after a 30 s stop spliced into each dev run (ideal / quiet
features / a replayed real platform dwell; the stop starts and ends instantly). It is NOT part of the
acceptance rule. It is simulated: on one exploratory Elizabeth line ride the gate caught 0 of 3 real platform
dwells, so never call the gate essential, validated or proven for real tunnel stops.
Name tracks only as the harness names them (e.g. "EB BTH->MLE"); the harness has no straight-versus-curved
labels, so say nothing about section type. Say which values of a setting were tried; with fewer than all
allowed values tried, do not call one value a "sweet spot" or an optimum.
Wording: no "proves", "proven" or "conclusively"; say what the dev numbers show. Your text is checked
against the project's banned-wording list."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(p: Path) -> str | None:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def load_harness():
    """Import agent.harness (and agent.analyst) bound to the live log; refuse if bound to another log."""
    prev = os.environ.get(ENV)
    if "agent.harness" not in sys.modules:
        os.environ[ENV] = str(LIVE_LOG)
    try:
        from . import analyst as A, harness as H
    finally:
        if prev is None:
            os.environ.pop(ENV, None)
        else:
            os.environ[ENV] = prev
    if H.LOG.resolve() != LIVE_LOG.resolve():
        raise RuntimeError(f"agent.harness is bound to {H.LOG.name} in this process; run "
                           "`python -m agent.live_analyst` as its own process (the main chain must stay untouched)")
    return A, H


@contextlib.contextmanager
def no_attempts_mirror():
    """While the harness runs here its Atlas mirror (db().attempts) fails fast and is skipped by the harness."""
    import atlas.db as adb
    real = adb.db

    def refuse(*_a, **_k):
        raise RuntimeError("live chain: mirrored to attempts_live by agent.live_analyst, never to attempts")

    adb.db = refuse
    try:
        yield
    finally:
        adb.db = real


def sync_mirror(rows: list[dict]) -> str:
    """Upsert the live chain into Atlas `attempts_live` (_id = line hash); idempotent, offline is fine."""
    try:
        from atlas.db import client
        from pymongo import ReplaceOne
        c = client(timeout_ms=4000)
        try:
            coll = c["tubesense"].attempts_live
            if rows:
                coll.bulk_write([ReplaceOne({"_id": r["hash"]}, {**r, "_id": r["hash"]}, upsert=True) for r in rows])
            n = coll.count_documents({"_id": {"$in": [r["hash"] for r in rows]}})
        finally:
            c.close()
        return f"{n}/{len(rows)} live lines in Atlas `attempts_live`"
    except BaseException as e:  # noqa: BLE001  (SystemExit from a missing URI included)
        if isinstance(e, KeyboardInterrupt):
            raise
        return f"Atlas not reachable ({type(e).__name__}); the local file is the record of truth"


class Busy(RuntimeError):
    """Another live session holds the lock; nothing is recorded for the refused one."""


@contextlib.contextmanager
def single_session():
    """OS file lock, released if the process dies: two live sessions must not append to one chain at once."""
    SESSIONS.mkdir(parents=True, exist_ok=True)
    fh = open(SESSIONS / ".lock", "a+")
    try:
        fh.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        raise Busy("another live analyst session is running")
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            fh.seek(0)
            if os.name == "nt":
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        fh.close()


def tag(chain: str, n) -> str:
    """Attempt numbers restart in the live chain, so they are always shown with their chain: 'live #3' vs 'recorded #3'."""
    return f"{chain} #{n}"


def stop_note(base: dict, ss: dict) -> str | None:
    if ss and base and ss != base:
        return ("not scored by the rule: synthetic-stop end error " +
                ", ".join(f"{k} {base.get(k)} -> {x} m" for k, x in ss.items()) + " (baseline -> this)")


def history() -> dict:
    """Earlier dev results the session continues from: recorded session (main chain) + earlier live sessions."""
    def rows(lines: list[dict], source: str, chain: str) -> list[dict]:
        out = []
        for r in lines:
            if r["kind"] != "dev":
                continue
            m, vs = r["metrics"], r.get("vs_baseline")
            out.append({"source": source, "attempt": tag(chain, r["attempt"]), "ts": r["ts"], "config": r["config"],
                        "config_hash": r["config_hash"], "hypothesis": r.get("rationale", ""),
                        "median_error_m": m["median_error_m"], "coverage_mean": m["coverage_mean"],
                        "band_width_mean_m": m["band_width_mean_m"],
                        "track_coverage": {t: d.get("v2_band90_coverage_independent")
                                           for t, d in (m.get("per_track") or {}).items()},
                        "vs_baseline": vs})
        return out
    main = jsonl(MAIN_LOG)
    rec = rows(main, "recorded session attempts.jsonl", "recorded")
    seen = {r["config_hash"] for r in rec}  # the live chain re-scores the baseline once; list it only once
    report = next((e.get("text") for e in reversed(jsonl(RECORDED)) if e.get("kind") == "report"), None)
    sub = next((r for r in main if r["kind"] == "submit"), None)
    return {"recorded": rec, "live": [r for r in rows(jsonl(LIVE_LOG), "earlier live session", "live") if r["config_hash"] not in seen],
            "recorded_report": report, "recorded_submitted": sub and sub["config"],
            "test_spent": any(r["kind"] == "test" for r in main)}


def reason(vs: dict, m: dict, H) -> str:
    """The harness verdict in words, from accept()'s own fields (the rule decides, this only describes it)."""
    lo, mean, hi = vs["error_delta_ci_m"]
    bad = {t: d for t, d in vs["track_coverage_drop"].items() if d > H.TRACK_COV_DROP}
    mean_ok = m["coverage_mean"] >= H.MIN_COVERAGE
    cov = "coverage ok" if vs["coverage_ok"] else (
        "coverage FAILS (" + "; ".join(x for x in (
            "" if mean_ok else f"mean {m['coverage_mean']} < min {H.MIN_COVERAGE}",
            f"per-track drop > {H.TRACK_COV_DROP} on " + ", ".join(f"{t} {d:+.3f}" for t, d in bad.items()) if bad else "",
            f"mean {m['coverage_mean']} >= {H.MIN_COVERAGE} is ok" if mean_ok else "") if x) + ")")
    err = f"error delta mean {mean:+.2f} m, 95% CI [{lo:+.2f}, {hi:+.2f}]" + (" entirely below 0" if hi < 0 else "")
    gain = hi < 0 or (vs["width_change"] <= -H.WIDTH_GAIN and hi <= H.ERR_SLACK_M)
    why = "gain shown" if gain else (f"no gain (needs error CI below 0, or band >= {H.WIDTH_GAIN:.0%} narrower "
                                     f"with CI upper <= {H.ERR_SLACK_M} m)")
    return (f"{'ACCEPTED' if vs['accepted'] else 'REJECTED'} by the pre-declared rule: {cov}; {err}; "
            f"band {vs['width_change'] * 100:+.1f}%; {why}")


class Live:
    """The three tools Argon gets, emitting events; scoring goes through agent.analyst.Session._score."""

    def __init__(self, A, H, max_exp: int, hist: dict):
        self.A, self.H, self.hist = A, H, hist
        self.base = A.Session(max_exp, path=Path(os.devnull), echo=False)  # only _score is used, it never logs
        self.experiments, self.pending = [], []

    def list_experiments(self) -> dict:
        """Settings you may change, their allowed values, the baseline, the acceptance rule and every configuration already run."""
        r = self.H.list_experiments()
        r["already_run"] = {x["config_hash"]: x["config"] for x in self.hist["recorded"] + self.hist["live"]}
        r["label"] = LABEL
        return r

    def get_baseline(self) -> dict:
        """Dev-split metrics of the baseline configuration (median error m, coverage, band width, per run, per track)."""
        with no_attempts_mirror():
            r = self.H.baseline()
        rec = next((x for x in self.hist["recorded"] if x["vs_baseline"] is None), None)
        if rec:
            r = {**r, "same_as_recorded_baseline": all(r[k] == rec[k] for k in
                                                       ("median_error_m", "coverage_mean", "band_width_mean_m"))}
        return r

    def run_experiment(self, GAMMA: float, YAW_SD_FLOOR: float, P_SPEED: float, stop_gate: bool,
                       rationale: str, refuted_if: str) -> dict:
        """Score one untried configuration on the 23 Oct dev split (~1 min); the harness rule decides accept/reject.

        Args:
            GAMMA: likelihood tempering, one of the allowed values.
            YAW_SD_FLOOR: deg/s added in quadrature to the map's sd, one of the allowed values.
            P_SPEED: per-step speed-level change probability, one of the allowed values.
            stop_gate: whether the stop gate is on.
            rationale: the falsifiable hypothesis: mechanism and predicted direction of named metrics.
            refuted_if: the concrete harness outcome that would refute the hypothesis.
        """
        cfg = {"GAMMA": GAMMA, "YAW_SD_FLOOR": YAW_SD_FLOOR, "P_SPEED": P_SPEED, "stop_gate": stop_gate}
        try:
            h = self.H._chash(self.H._check(cfg))
        except ValueError as e:
            return {"error": str(e)}
        seen = next((x for x in self.hist["recorded"] + self.hist["live"] if x["config_hash"] == h), None)
        if seen:
            return {"error": f"already run ({seen['source']}, {seen['attempt']}, {seen['ts']}); pick an "
                             "untried configuration. Its result is attached.", "earlier": seen}
        t = time.time()
        with no_attempts_mirror():
            r = self.base._score(cfg, f"{rationale} | refuted if: {refuted_if}",
                                 when_spent="stop experimenting and write the final summary")
        r["seconds"] = round(time.time() - t, 1)
        if "error" in r:
            return r
        r["label"], r["chain"], r["attempt_label"] = LABEL, "live", tag("live", r["attempt"])
        vs = r.get("vs_baseline")
        if vs is not None:
            m = r["metrics"]
            v = {"type": "verdict", "chain": "live", "attempt": r["attempt"], "label": r["attempt_label"], "accepted": bool(vs["accepted"]),
                 "reason": reason(vs, m, self.H), "config": r["config"], "seconds": r["seconds"],
                 "changes": {k: [self.H.BASELINE[k], x] for k, x in r["config"].items() if x != self.H.BASELINE[k]},
                 "metrics": {k: m.get(k) for k in ("median_error_m", "coverage_mean", "band_width_mean_m",
                                                   "timetable_median_m", "per_track")} | {"vs_baseline": vs}}
            with no_attempts_mirror():
                b = self.H.baseline().get("synthetic_stop") or {}
            if note := stop_note(b, m.get("synthetic_stop") or {}):
                v["note"] = note
            self.experiments.append({**v, "hypothesis": rationale, "refuted_if": refuted_if})
            self.hist["live"].append({"source": "this live session", "attempt": r["attempt_label"], "ts": now(),
                                      "config_hash": r["config_hash"], "config": r["config"]})
            self.pending.append(v)  # emitted right after this call's tool_result
        r["instruction"] = "Now say whether your hypothesis HELD or was REFUTED, citing these numbers."
        return r


def prompt(hist: dict, max_exp: int) -> str:
    return (f"EARLIER DEV RESULTS (do not repeat these configurations):\n{json.dumps(hist['recorded'] + hist['live'], indent=1)}\n\n"
            f"CONFIGURATION SUBMITTED BY THE RECORDED SESSION (its test score is sealed away from you): "
            f"{json.dumps(hist['recorded_submitted'])}\n\n"
            f"RECORDED SESSION'S REPORT (24 Oct):\n{hist['recorded_report']}\n\n"
            f"Begin: call get_baseline, then run at most {max_exp} new experiment(s) as instructed.")


def run(max_exp: int = 2, emit=print) -> dict:
    """One live session; emit(event) is called for every event in order. Returns the final event.
    The record file is created only once the single-session lock is held, so a refused start leaves nothing behind."""
    t0, stamp = time.time(), datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    SESSIONS.mkdir(parents=True, exist_ok=True)
    rec, events = None, []

    def out(ev: dict):
        ev = {**ev, "elapsed_s": round(time.time() - t0, 1)}
        events.append(ev)
        if rec is not None:
            with open(rec, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(ev, default=float) + "\n")
        emit(ev)

    main_sha = sha256(MAIN_LOG)
    try:
        with single_session():
            new = SESSIONS / f"{stamp}-{os.getpid()}.jsonl"
            new.touch(exist_ok=False)
            rec = new
            A, H = load_harness()
            from google import genai
            from google.genai import types
            from atlas.db import load_env
            load_env()
            hist = history()
            out({"type": "start", "ts": now(), "model": MODEL, "thinking": THINKING, "max_experiments": max_exp,
                 "label": LABEL, "harness_sha": H.HARNESS_SHA, "log": LIVE_LOG.relative_to(ROOT).as_posix(),
                 "record": rec.relative_to(ROOT).as_posix(), "main_chain_sha256": main_sha,
                 "continues": {"recorded_dev_configs": len(hist["recorded"]), "live_dev_configs": len(hist["live"]),
                               "test_spent": hist["test_spent"]}})
            live = Live(A, H, max_exp, hist)
            tools = {"list_experiments": live.list_experiments, "get_baseline": live.get_baseline,
                     "run_experiment": live.run_experiment}
            system = A.SYSTEM.split("\n\nYour job:")[0] + "\n\n" + JOB.format(max_exp=max_exp)
            client = genai.Client(api_key=os.environ["GEMINI_API_KEY"],  # keep a reference: the chat needs it open
                                  http_options=types.HttpOptions(timeout=HTTP_TIMEOUT_MS))
            chat = client.chats.create(
                model=MODEL, config=types.GenerateContentConfig(
                    system_instruction=system, tools=list(tools.values()),
                    thinking_config=types.ThinkingConfig(thinking_level=THINKING),
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)))
            msg, usage, nudged, summary_nudged, rewritten = prompt(hist, max_exp), [0, 0], False, False, False
            summary, summary_flags = "", []  # the closing text only: a reflection after an experiment is never the summary
            for _ in range(max_exp + 10):
                r = send(chat, msg)
                um = r.usage_metadata
                usage = [usage[0] + (um.prompt_token_count or 0), usage[1] + (um.candidates_token_count or 0)]
                parts = (r.candidates[0].content.parts if r.candidates and r.candidates[0].content else None) or []
                text = "\n".join(p.text for p in parts if p.text and not p.thought).strip()
                fl = wording_flags(text) if text else []
                if text:
                    out({"type": "argon_text", "text": text, "flags": fl})
                calls = [p.function_call for p in parts if p.function_call]
                if not calls:
                    if not (live.experiments or nudged):
                        msg, nudged = "Run your experiment(s) now with run_experiment, then write the summary.", True
                        continue
                    if text:
                        summary, summary_flags = text, fl
                    elif not (summary or summary_nudged):
                        msg, summary_nudged = "Your last turn had no text. Write the final summary now (<= 150 words).", True
                        continue
                    if text and fl and not rewritten:  # one rewrite of the closing summary if the wording check flags it
                        msg, rewritten = ("The project's wording check flagged your summary: " + "; ".join(fl) +
                                          ". Rewrite the full summary without that wording, same numbers."), True
                        continue
                    break
                replies, ran = [], 0
                for fc in calls:
                    args = dict(fc.args or {})
                    out({"type": "tool_call", "name": fc.name, "args": args})
                    fn = tools.get(fc.name)
                    if fn is None:
                        res = {"error": f"unknown tool {fc.name}; tools: {sorted(tools)}"}
                    elif fc.name == "run_experiment" and ran:
                        res = {"error": "one experiment per turn: state whether the previous hypothesis HELD or was "
                                        "REFUTED first, then call run_experiment again in your next turn"}
                    else:
                        try:
                            inspect.signature(fn).bind(**args)
                        except TypeError as e:
                            res = {"error": f"bad arguments: {e}"}
                        else:
                            ran += fc.name == "run_experiment"
                            res = fn(**args)
                    out({"type": "tool_result", "chain": "live", "name": fc.name, "result": res})
                    while live.pending:
                        out(live.pending.pop(0))
                    replies.append(types.Part.from_function_response(name=fc.name, response=json.loads(
                        json.dumps(res, default=float))))
                msg = replies
            chain = H.verify_log()
            rows = jsonl(LIVE_LOG)
            mirror = sync_mirror(rows)
            out({"type": "chain", "ok": not chain["broken"], "lines": chain["lines"], "broken": chain["broken"],
                 "file": LIVE_LOG.relative_to(ROOT).as_posix(), "head": rows[-1]["hash"] if rows else None,
                 "main_chain_unchanged": sha256(MAIN_LOG) == main_sha, "atlas": mirror})
            fin = {"type": "final", "summary": summary, "summary_missing": not summary, "flags": summary_flags,
                   "rewritten": rewritten, "label": LABEL, "chain": "live",
                   "experiments": [{k: e[k] for k in ("attempt", "label", "config", "accepted", "reason", "hypothesis",
                                                      "refuted_if", "seconds")} for e in live.experiments],
                   "tokens": {"prompt": usage[0], "output": usage[1]},
                   "files": {"jsonl": rec.relative_to(ROOT).as_posix(),
                             "md": rec.with_suffix(".md").relative_to(ROOT).as_posix()}}
            out(fin)
    except Exception as e:  # noqa: BLE001  (the web app shows the error event; the record keeps it)
        fin = {"type": "error", "message": f"{type(e).__name__}: {e}"}
        out(fin)
    if rec is not None:
        write_md(events, rec.with_suffix(".md"))
    return fin


LIVE_BANNED = [
    (r"\bproving\b", "say what the dev numbers show, not that they prove"),
    (r"\b(essential|indispensable|validated)\b.{0,60}\b(real|unplanned|tunnel) stops?\b|"
     r"\b(real|unplanned|tunnel) stops?\b.{0,60}\b(essential|indispensable|validated)\b",
     "the stop gate is scored on synthetic stops only (0 of 3 real dwells caught on the one exploratory ride)"),
    (r"\bsweet[- ]spots?\b|\boptimum\b|\boptimal\b",
     "only some allowed values were tried; say which, do not name a sweet spot or optimum"),
    (r"\bstraight[- ](section|segment|track|stretch)s?\b|\bcurved[- ](section|segment|track|stretch)s?\b",
     "the harness has no straight/curved labels; name tracks as the harness does"),
]


def wording_flags(text: str) -> list[str]:
    """agent.verify's banned-overclaim patterns (pure module, no harness import) plus LIVE_BANNED, on Argon's prose."""
    from .verify import BANNED
    return [why for pat, why in BANNED + LIVE_BANNED if re.search(pat, text, re.I | re.S)]


def send(chat, msg, tries: int = 3):
    """One chat turn; retries a server error, turns an HTTP timeout into a TimeoutError the session reports cleanly."""
    import httpx
    from google.genai import errors
    for i in range(tries):
        try:
            return chat.send_message(msg)
        except errors.ServerError:
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))
        except httpx.TimeoutException as e:
            raise TimeoutError(f"Gemini call exceeded the {HTTP_TIMEOUT_MS / 1000:g} s HTTP timeout "
                               f"({type(e).__name__})") from e


def old_cov(m: re.Match) -> str:
    """Reword a pre-fix 'coverage FAILS (mean X vs min Y; dropped ...)', which read as if the mean had failed."""
    mean, lo, drop, where = m.groups()
    parts = ([] if float(mean) >= float(lo) else [f"mean {mean} < min {lo}"]) + (
        [f"per-track drop > {drop} on {where}"] if drop else []) + ([f"mean {mean} >= {lo} is ok"] if float(mean) >= float(lo) else [])
    return "coverage FAILS (" + "; ".join(parts) + ")"


def annotate(events: list[dict]) -> tuple[list[dict], bool]:
    """Fill in what older records lack, without touching the jsonl: the wording flags re-run with today's list and the
    synthetic-stop note of each verdict (from its tool results). Returns the events and whether the record predates the check."""
    base = next((e["result"].get("synthetic_stop") for e in events if e["type"] == "tool_result"
                 and e["name"] == "get_baseline" and "error" not in e["result"]), None) or {}
    stops = {e["result"]["attempt"]: (e["result"].get("metrics") or {}).get("synthetic_stop") for e in events
             if e["type"] == "tool_result" and e["name"] == "run_experiment" and "attempt" in e["result"]}
    out, unchecked = [], False
    for e in events:
        e = dict(e)
        if e["type"] == "argon_text":
            unchecked |= "flags" not in e
            e["rechecked"] = [f for f in wording_flags(e["text"]) if f not in (e.get("flags") or [])]
            e["flags"] = (e.get("flags") or []) + e["rechecked"]
        elif e["type"] == "verdict":
            e.setdefault("label", tag("live", e["attempt"]))
            e["reason"] = re.sub(r"coverage FAILS \(mean ([\d.]+) vs min ([\d.]+)(?:; dropped > ([\d.]+) on (.*?))?\)", old_cov, e["reason"])
            if not e.get("note") and (note := stop_note(base, stops.get(e["attempt"]) or {})):
                e["note"] = note
        out.append(e)
    return out, unchecked


def write_md(events: list[dict], path: Path):
    events, unchecked = annotate(events)
    st = next((e for e in events if e["type"] == "start"), {})
    flagged = any(e.get("flags") for e in events if e["type"] == "argon_text")
    lines = ["# Live Argon analyst session (post-hoc, 23 Oct dev split only)", ""]
    if unchecked or flagged:
        lines += [("> **Pre-wording-check test session**: Argon's text was not checked when it was written. " if unchecked
                   else "> **Flagged text in this record.** ") +
                  "The flags below come from re-running today's wording check. Do not present flagged text as written.", ""]
    lines += [f"- {st.get('ts', '?')}: model `{st.get('model', MODEL)}`, thinking `{st.get('thinking', THINKING)}`, "
              f"experiment budget {st.get('max_experiments', '?')}, harness SHA-256 `{str(st.get('harness_sha'))[:16]}…`",
              f"- Own hash chain `{st.get('log', LIVE_LOG.name)}` (attempts here are numbered \"live #N\", separately from "
              f"the recorded session's); continues from "
              f"{(st.get('continues') or {}).get('recorded_dev_configs', '?')} recorded and "
              f"{(st.get('continues') or {}).get('live_dev_configs', '?')} earlier live dev configurations.",
              f"- {LABEL}. Errors are agreement with a yaw-landmark proxy.", ""]
    for e in events:
        t = e["type"]
        if t == "argon_text":
            lines += [f"**Argon** ({e['elapsed_s']} s):", "", e["text"], ""]
            if e.get("flags"):
                how = "wording check" + (" (re-run now, not applied when written)" if e["rechecked"] else "")
                lines += [f"_{how} flagged: {'; '.join(e['flags'])}_", ""]
        elif t == "tool_call" and e["name"] == "run_experiment":
            a = e["args"]
            lines += [f"**run_experiment** `{json.dumps({k: a.get(k) for k in KEYS})}`",
                      f"> Hypothesis: {a.get('rationale')}  ", f"> Refuted if: {a.get('refuted_if')}", ""]
        elif t == "tool_result" and "error" in e["result"]:
            lines += [f"- {e['name']} refused: {e['result']['error']}", ""]
        elif t == "verdict":
            m = e["metrics"]
            ch = ", ".join(f"{k} {a} -> {b}" for k, (a, b) in e["changes"].items()) or "baseline"
            lines += [f"- Harness, {e['label']} ({ch}; {e['seconds']} s): median error "
                      f"{m['median_error_m']} m, coverage {m['coverage_mean']}, band {m['band_width_mean_m']} m.",
                      f"- **{e['reason']}**"] + ([f"- {e['note']}"] if e.get("note") else []) + [""]
        elif t == "chain":
            lines += ["## Record", "", f"- `{e['file']}`: {e['lines']} lines, hash chain "
                      f"{'intact' if e['ok'] else 'BROKEN at ' + str(e['broken'])}; head `{str(e['head'])[:12]}`",
                      f"- main chain outputs_posthoc/attempts.jsonl unchanged: {e['main_chain_unchanged']}",
                      f"- {e['atlas']}", ""]
        elif t == "final":
            lines += ([] if not e.get("summary_missing") else ["_No closing summary was written._"]) + [
                f"_tokens {e['tokens']}, {e['elapsed_s']} s total_"]
        elif t == "error":
            lines += ["## Error", "", e["message"]]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def prepare() -> dict:
    """Score (or read) the baseline into the live chain, so the stage session does not pay for it."""
    with single_session():
        _, H = load_harness()
        t = time.time()
        with no_attempts_mirror():
            m = H.baseline()
        return {"baseline": {k: m[k] for k in ("median_error_m", "coverage_mean", "band_width_mean_m")},
                "seconds": round(time.time() - t, 1), "chain": H.verify_log(), "atlas": sync_mirror(jsonl(LIVE_LOG))}


def kill_tree(p: subprocess.Popen):
    """Kill the live process and its harness child (a plain kill leaves agent._eval running for ~45 s on Windows)."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
    with contextlib.suppress(OSError):
        p.kill()


def stream(max_exp: int = 2, on_event=print, timeout_s: float = 1200) -> int:
    """For the web app: run the CLI in its own process (the harness binding stays out of the caller) and relay events.
    A watchdog kills the process tree at the deadline even if it prints nothing (a hung call); events after the
    deadline are dropped, and the `error` event is sent only if no `final`/`error` was relayed first."""
    p = subprocess.Popen([sys.executable, "-m", "agent.live_analyst", "--jsonl", "--max-experiments", str(max_exp)],
                         cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)}, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
    deadline, done, expired = time.time() + timeout_s, False, False

    def expire():
        nonlocal expired
        if not done:
            expired = True
            kill_tree(p)

    timer = threading.Timer(timeout_s, expire)
    timer.daemon = True
    timer.start()
    try:
        for line in p.stdout:
            if expired or time.time() > deadline:
                break
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            done |= ev.get("type") in ("final", "error")
            on_event(ev)
    finally:
        timer.cancel()
    if not done and (expired or time.time() > deadline):
        kill_tree(p)
        on_event({"type": "error", "message": f"live session exceeded {timeout_s:g} s"})
        p.wait()
        return 1
    return p.wait()


def list_sessions() -> list[dict]:
    """Earlier live sessions (for a CACHED replay labelled with its original timestamp)."""
    out = []
    for f in sorted(SESSIONS.glob("2*.jsonl"), reverse=True):
        ev = jsonl(f)
        st = next((e for e in ev if e["type"] == "start"), {})
        fin = next((e for e in ev if e["type"] in ("final", "error")), {})
        texts = [e for e in ev if e["type"] == "argon_text"]
        out.append({"file": f.relative_to(ROOT).as_posix(), "ts": st.get("ts"), "ok": fin.get("type") == "final",
                    "summary_flags": wording_flags(fin.get("summary") or ""),  # re-checked now (older records predate the check)
                    "unchecked": any("flags" not in e for e in texts),  # written before the wording check existed
                    "text_flags": sorted({w for e in texts for w in wording_flags(e["text"])}),
                    "experiments": [{k: v.get(k) for k in ("attempt", "config", "accepted")} | {"label": tag("live", v["attempt"])}
                                    for v in ev if v["type"] == "verdict"]})
    return out


def human(ev: dict):
    t = ev["type"]
    body = {k: v for k, v in ev.items() if k not in ("type", "elapsed_s", "chain")}
    if t == "argon_text":
        body = "\n" + ev["text"] + (f"\n[wording check flagged: {'; '.join(ev['flags'])}]" if ev.get("flags") else "")
    elif t == "tool_result" and ev["name"] == "get_baseline":
        body = {k: ev["result"].get(k) for k in ("median_error_m", "coverage_mean", "band_width_mean_m",
                                                  "same_as_recorded_baseline")}
    elif t == "tool_result":
        body = {k: v for k, v in ev["result"].items() if k not in ("metrics", "earlier", "attempt", "chain")}
    elif t == "verdict":
        body = {k: v for k, v in body.items() if k not in ("metrics", "attempt")}
    elif t == "final":
        body = {"experiments": [{**x, "attempt": x.get("label", x["attempt"])} for x in ev["experiments"]],
                "files": ev["files"], "tokens": ev["tokens"]}
    print(f"[{ev['elapsed_s']:6.1f} s] {t}: {body if isinstance(body, str) else json.dumps(body, default=float)}",
          flush=True)


def main():
    for f in (sys.stdout, sys.stderr):  # cp1252 on a Windows pipe cannot print Argon's >= or arrows
        f.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", action="store_true", help="one JSON event per line on stdout")
    ap.add_argument("--max-experiments", type=int, default=2)
    ap.add_argument("--prepare", action="store_true", help="score the baseline into the live chain once, no Gemini")
    ap.add_argument("--rewrite-md", metavar="JSONL", help="rebuild a session's .md from its .jsonl (re-runs the wording check)")
    a = ap.parse_args()
    if a.rewrite_md:
        src = Path(a.rewrite_md)
        write_md(jsonl(src), src.with_suffix(".md"))
        print(f"wrote {src.with_suffix('.md')}")
        return
    if a.prepare:
        print(json.dumps(prepare(), indent=1, default=float))
        return
    stdout = sys.stdout

    def emit(ev: dict):
        if a.jsonl:
            stdout.write(json.dumps(ev, default=float) + "\n")
            stdout.flush()
        else:
            with contextlib.redirect_stdout(stdout):
                human(ev)

    with contextlib.redirect_stdout(sys.stderr):  # stdout carries only events
        fin = run(max(0, a.max_experiments), emit)
    sys.exit(0 if fin["type"] == "final" else 1)


if __name__ == "__main__":
    main()
