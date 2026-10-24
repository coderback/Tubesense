"""Demo beat 1:15-1:50, the verification loop: an honest terminal REPLAY of the recorded Argon analyst session.

Nothing here calls Gemini. The recorded conversation (outputs_posthoc/analyst_session.jsonl) is replayed with
its real timestamps, compressed; what is checked is recomputed now, on screen:
- the hash chain of the harness log (agent.harness.verify_log) and the harness SHA-256 against the file on disk,
  against every log line and against the file at git tag v2-posthoc;
- every ACCEPTED / REJECTED verdict, by re-running the pre-declared rule (agent.harness.accept) on the logged
  metrics; the decisive reason is printed next to it;
- the Atlas mirror of the log (collection `attempts`), when reachable (--no-atlas to skip);
- the sealed 24 Oct test: scored or not, from the log (budget 1).
The failed first session (analyst_session_failed1.*) is reported, not hidden.

Run from the repo root:
    PYTHONPATH=. .venv/Scripts/python -m demo.replay_analyst              # ~30 s, colour
    PYTHONPATH=. .venv/Scripts/python -m demo.replay_analyst --step       # Enter advances each event
    PYTHONPATH=. .venv/Scripts/python -m demo.replay_analyst --speed 1    # real time (~6 min)
    PYTHONPATH=. .venv/Scripts/python -m demo.replay_analyst --summary    # compact table (README / caption)
    add --no-color for plain text, --no-atlas to skip the Atlas check
The script-path form (.venv/Scripts/python demo/replay_analyst.py) also works without PYTHONPATH.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root, so `python demo/replay_analyst.py` works
from agent import harness as H  # noqa: E402

ROOT = H.ROOT
OUT = H.LOG.parent
SESSION = OUT / "analyst_session.jsonl"
FAILED = OUT / "analyst_session_failed1.jsonl"
V1_FORWARD = ROOT / "outputs" / "online" / "forward_test.json"
TAG = "v2-posthoc"
TARGET_S = 30.0
SHORT = {"GAMMA": "GAMMA", "YAW_SD_FLOOR": "YAW_SD_FLOOR", "P_SPEED": "P_SPEED", "stop_gate": "stop_gate"}


# --------------------------------------------------------------------------- output
class Ink:
    """ANSI escape codes, or empty strings with --no-color / NO_COLOR."""
    CODES = {"b": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33", "cyan": "36", "mag": "35"}

    def __init__(self, on: bool):
        self.on = on
        if on and os.name == "nt":  # enable VT processing on the classic Windows console
            try:
                import ctypes
                k = ctypes.windll.kernel32
                h = k.GetStdHandle(-11)
                mode = ctypes.c_uint32()
                if k.GetConsoleMode(h, ctypes.byref(mode)):
                    k.SetConsoleMode(h, mode.value | 0x0004)
            except Exception:  # noqa: BLE001
                pass

    def __call__(self, text: str, *styles: str) -> str:
        if not self.on or not styles:
            return text
        return "\033[" + ";".join(self.CODES[s] for s in styles) + "m" + text + "\033[0m"


def jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def rel(p: Path) -> str:
    try:
        return p.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(p)


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


def hm(s: str, sec: bool = True) -> str:
    return ts(s).strftime("%H:%M:%S" if sec else "%H:%M")


def git(*args: str) -> bytes | None:
    try:
        p = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, timeout=15)
        return p.stdout if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def fmt(v) -> str:
    return ("T" if v else "F") if isinstance(v, bool) else f"{v:g}"


def cfg_diff(cfg: dict) -> str:
    d = [f"{SHORT[k]} {fmt(H.BASELINE[k])}->{fmt(v)}" for k, v in cfg.items() if v != H.BASELINE[k]]
    return ", ".join(d) or "baseline (no change)"


def ci(c: list[float]) -> str:
    """[lo, mean, hi] of the paired per-run error delta, labelled."""
    return f"mean {c[1]:+.2f} m, 95% CI [{c[0]:+.2f}, {c[2]:+.2f}] m"


# --------------------------------------------------------------------------- checks (all recomputed now)
def integrity(att: list[dict]) -> dict:
    chain = H.verify_log()
    disk = hashlib.sha256((ROOT / "agent" / "harness.py").read_bytes()).hexdigest()
    logged = sorted({r["harness_sha"] for r in att})
    tag_commit = (git("rev-list", "-n1", TAG) or b"").decode().strip() or None
    tag_listed = (git("tag", "-l", TAG) or b"").decode().strip() == TAG
    tag_src = git("show", f"{TAG}:agent/harness.py")
    tag_sha = hashlib.sha256(tag_src).hexdigest() if tag_src else None
    tag_final = git("show", f"{TAG}:outputs_posthoc/final_config.json")
    final = json.loads(H.FINAL.read_text(encoding="utf-8")) if H.FINAL.exists() else None
    sub = [r for r in att if r["kind"] == "submit"]
    return {"chain": chain, "disk": disk, "logged": logged, "module": H.HARNESS_SHA,
            "sha_ok": logged == [disk] and H.HARNESS_SHA == disk,
            "tag": tag_listed, "tag_commit": tag_commit, "tag_sha_ok": tag_sha == disk,
            "final": final, "final_at_tag_ok": bool(tag_final and final and json.loads(tag_final) == final),
            "final_hash_ok": bool(final and sub and final["log_hash"] == sub[-1]["hash"])}


def atlas_mirror(att: list[dict]) -> str:
    try:
        from atlas.db import client
        c = client(timeout_ms=4000)
        try:
            ids = [r["hash"] for r in att]
            n = c["tubesense"].attempts.count_documents({"_id": {"$in": ids}})
        finally:
            c.close()
        return f"{n}/{len(ids)} log lines found in Atlas `attempts` by hash"
    except BaseException as e:  # noqa: BLE001  (SystemExit from a missing URI included; offline is fine)
        if isinstance(e, KeyboardInterrupt):
            raise
        return f"Atlas not reachable now ({type(e).__name__}); the local file is the record of truth"


def verdict(vs: dict, m: dict) -> tuple[bool, str]:
    """Decisive reason under the pre-declared rule, from the logged comparison."""
    err, wid, drops = vs["error_delta_ci_m"], vs["width_change"], vs["track_coverage_drop"]
    narrow = wid <= -H.WIDTH_GAIN
    if not vs["coverage_ok"]:
        bad = [f"{t} coverage fell {d:.3f} (limit {H.TRACK_COV_DROP:g})" for t, d in drops.items()
               if d > H.TRACK_COV_DROP]
        if m["coverage_mean"] < H.MIN_COVERAGE:
            bad.insert(0, f"mean coverage {m['coverage_mean']:.3f} < {H.MIN_COVERAGE:g}")
        s = "; ".join(bad)
        if err[2] < 0:  # the gain test alone would have passed: calibration is what rejected it
            s += f" - error CI upper {err[2]:+.2f} m < 0, the error gain alone would have passed"
        elif narrow and err[2] <= H.ERR_SLACK_M:
            s += f" - band {-wid:.1%} narrower, the band gain alone would have passed"
        return False, s
    worst = max(drops.items(), key=lambda kv: kv[1]) if drops else None
    held = (f"coverage held (worst drop {worst[0]} {max(worst[1], 0):.3f} <= {H.TRACK_COV_DROP:g})"
            if worst else "coverage held")
    if vs["accepted"]:
        if err[2] < 0:
            return True, f"error CI upper {err[2]:+.2f} m < 0; {held}"
        return True, f"band {-wid:.1%} narrower and error CI upper {err[2]:+.2f} <= {H.ERR_SLACK_M:g} m; {held}"
    if narrow:
        return False, f"no gain: band {-wid:.1%} narrower but error CI upper {err[2]:+.2f} m > {H.ERR_SLACK_M:g} m"
    return False, f"no gain: error CI upper {err[2]:+.2f} m not < 0; band {wid:+.1%}, not <= -{H.WIDTH_GAIN:.0%}"


def recheck(base: dict, cand: dict, vs: dict) -> bool:
    """Re-apply harness.accept() to the logged metrics: does it reproduce the logged verdict?"""
    r = H.accept(base, cand)
    return r["accepted"] == vs["accepted"] and r["error_delta_ci_m"] == vs["error_delta_ci_m"]


def failed_session(att: list[dict]) -> str | None:
    ev = jsonl(FAILED)
    if not ev:
        return None
    t0, t1 = ev[0]["ts"], ev[-1]["ts"]
    window = [r for r in att if t0 <= r["ts"] <= t1]
    n_exp = sum(r["kind"] == "dev" and r["rationale"] != "baseline" for r in window)
    n_sub = sum(r["kind"] == "submit" for r in window)
    b = [r["attempt"] for r in window if r["kind"] == "dev" and r["rationale"] == "baseline"]
    rep = next((e.get("text") or "" for e in ev if e["kind"] == "report"), "")
    claims_sub = "submitted the **baseline" in rep or "submitted the baseline" in rep
    err = re.search(r"TypeError: [^`\n]+", rep)
    s = (f"session 1 ({hm(t0, False)}-{hm(t1, False)} UTC) FAILED on a bug in our tool wrapper"
         + (f" ({err.group(0)})" if err else "") + ": "
         + (f"only the baseline (attempt {b[0]}) scored, " if b and not n_exp else "")
         + f"{n_exp} experiments, {n_sub} submissions logged")
    if claims_sub and n_sub == 0:
        s += "; its report says it submitted the baseline, the harness log shows no submission"
    return s + ". Kept: outputs_posthoc/analyst_session_failed1.md"


def sealed_test(att: list[dict]) -> list[str] | None:
    """None if unscored; else lines comparing the single v2 test score with the pre-registered v1 forward test."""
    t = [r for r in att if r["kind"] == "test"]
    if not t:
        return None
    m = t[0]["metrics"]
    rows = [f"v2-posthoc sealed test ({hm(t[0]['ts'])} UTC, attempt {t[0]['attempt']}): median {m['median_error_m']} m, "
            f"coverage {m['coverage_mean']}, band {m['band_width_mean_m']} m, timetable {m['timetable_median_m']} m, "
            f"n={m['n_runs']}"]
    v1 = json.loads(V1_FORWARD.read_text(encoding="utf-8"))["overall"] if V1_FORWARD.exists() else {}
    if v1.get("n_sections"):
        rows.append(f"v1 pre-registered (frozen maps, outputs/online/forward_test.json): median "
                    f"{v1['tracker_median_of_runs_m']} m, coverage {v1['band90_coverage_mean']}, timetable "
                    f"{v1['timetable_median_of_runs_m']} m, n={v1['n_sections']}  (v1 metres chord-scaled, v2 WTT)")
    else:
        rows.append("v1 pre-registered forward test (outputs/online/forward_test.json): not yet run")
    rows.append("pre-registered H2b GO: median <= 5 m and coverage >= 0.80 on LST<->BTH (outputs/PLAN_24Oct.md)")
    return rows


# --------------------------------------------------------------------------- replay
class Replay:
    def __init__(self, a: argparse.Namespace):
        self.a, self.ink = a, Ink(not a.no_color and not os.environ.get("NO_COLOR"))
        self.w = max(80, min(110, shutil.get_terminal_size((100, 30)).columns))
        self.att, self.ses = jsonl(H.LOG), jsonl(SESSION)
        if not self.att or not self.ses:
            raise SystemExit(f"missing log: {H.LOG} or {SESSION}")
        self.by_attempt = {r["attempt"]: r for r in self.att}
        self.base = next(r for r in self.att if r["kind"] == "dev" and r["config"] == H._check({}))

    def p(self, text: str = ""):
        print(text, flush=True)

    def kv(self, key: str, text: str, *styles: str):
        """Key column + wrapped value."""
        lines = textwrap.wrap(text, self.w - 13) or [""]
        self.p(self.ink(f"  {key:<10} ", "dim") + self.ink(lines[0], *styles))
        for x in lines[1:]:
            self.p(" " * 13 + self.ink(x, *styles))

    def wait(self, dt: float, label: str):
        if self.a.step:
            try:
                input(self.ink(f"  .. {label}  [Enter]", "dim"))
            except EOFError:
                self.p()
            return
        s = dt / self.a.speed
        if s <= 0:
            return
        if not sys.stdout.isatty():
            self.p(self.ink(f"  .. {label}", "dim"))
            time.sleep(s)
            return
        t_end, spin = time.time() + s, "|/-\\"
        i = 0
        while time.time() < t_end:
            sys.stdout.write("\r" + self.ink(f"  {spin[i % 4]} {label}", "dim"))
            sys.stdout.flush()
            time.sleep(min(0.12, max(0.0, t_end - time.time())))
            i += 1
        sys.stdout.write("\r" + self.ink(f"  . {label}", "dim") + "\n")

    def rule(self, title: str = "", style: str = "cyan"):
        self.p(self.ink((f"-- {title} " if title else "") + "-" * (self.w - len(title) - (4 if title else 0)), style))

    # ---- sections
    def header(self):
        ink, start, rep = self.ink, self.ses[0], self.ses[-1]
        self.speed_note = (f"time compressed {self.a.speed:.1f}x" if not self.a.step else "step mode")
        self.p(ink("=" * self.w, "mag"))
        self.p(ink(f"REPLAY of a recorded {start['model']} session (recorded {start['ts'][:10]} "
                   f"{hm(start['ts'])}-{hm(rep['ts'])} UTC, not live)", "b", "mag"))
        self.p(ink(f"Nothing below calls Gemini. Marked 'now' = recomputed on this machine at this moment. "
                   f"{self.speed_note}.", "mag"))
        self.p(ink("=" * self.w, "mag"))
        g = integrity(self.att)
        self.g = g
        ok, bad = (lambda t: ink(t, "green", "b")), (lambda t: ink(t, "red", "b"))
        ch = g["chain"]
        self.kv("log", f"{rel(H.LOG)}: {ch['lines']} lines, every line hash-linked to the previous")
        self.p(" " * 13 + "now: verify_log() -> " + (ok("hash chain INTACT") if not ch["broken"]
                                                     else bad(f"hash chain BROKEN at attempts {ch['broken']}")))
        self.kv("harness", f"agent/harness.py SHA-256 {g['disk']}")
        self.p(" " * 13 + "now: file on disk = SHA in every log line: "
               + (ok("MATCH") if g["sha_ok"] else bad(f"MISMATCH (logged {', '.join(x[:12] for x in g['logged'])})")))
        if g["tag_commit"]:
            self.p(" " * 13 + f"now: = harness.py at tag {TAG} ({g['tag_commit'][:7]}): "
                   + (ok("MATCH") if g["tag_sha_ok"] else bad("MISMATCH")))
        else:
            self.p(" " * 13 + ink(f"now: tag {TAG} not found here (git tags not fetched?); tag check skipped",
                                  "yellow"))
        if not self.a.no_atlas:
            self.kv("atlas", "now: " + atlas_mirror(self.att))
        f = failed_session(self.att)
        if f:
            self.kv("earlier", f, "yellow")
        self.p()
        allowed = "  ".join(f"{k} {{{', '.join(fmt(v) for v in vals)}}}" for k, vals in H.ALLOWED.items())
        self.kv("allowed", allowed + "  (nothing else; out-of-list values are refused)")
        self.kv("rule", f"ACCEPT only if mean 90% coverage >= {H.MIN_COVERAGE:g} and no track's coverage drops > "
                        f"{H.TRACK_COV_DROP:g} below baseline, AND (95% CI of the paired per-run error delta "
                        f"entirely < 0, or band >= "
                        f"{H.WIDTH_GAIN:.0%} narrower with CI upper <= {H.ERR_SLACK_M:g} m). Fixed in the harness "
                        f"before the session; Argon cannot edit it.")
        bm = self.base["metrics"]
        trk = bm.get("per_track", {}).get("EB BTH->MLE", {}).get("v2_band90_coverage_independent")
        cfg = ", ".join(f"{k} {fmt(v)}" for k, v in self.base["config"].items())
        self.kv("baseline", f"{cfg}: median error {bm['median_error_m']} m (median of per-run medians), "
                            f"coverage {bm['coverage_mean']}" + (f" (EB BTH->MLE {trk})" if trk else "")
                + f", band {bm['band_width_mean_m']} m   [attempt {self.base['attempt']}, {hm(self.base['ts'])} UTC]")
        self.kv("data", f"23 Oct dev split only, leave-one-run-out, n={bm['n_runs']} runs; error = agreement with a "
                        f"yaw curve-edge proxy (not GPS truth); 24 Oct test "
                + ("scored once (below)" if any(r["kind"] == "test" for r in self.att) else "sealed"))
        self.p()

    def event(self, e: dict, n: int):
        ink = self.ink
        name = e.get("name", e["kind"])
        if e["kind"] == "start":
            self.rule(f"{hm(e['ts'])} session start: {e['model']}, thinking {e['thinking']}, "
                      f"budget {e['max_experiments']} experiments")
            return
        if name in ("list_experiments", "get_baseline"):
            self.p(ink(f"  {hm(e['ts'])}  Argon called {name}()", "cyan"))
            return
        if name == "run_experiment":
            res = e["result"]
            self.rule(f"{hm(e['ts'])} experiment {n}")
            self.kv("hypothesis", self.trim(e["rationale"]), "b")
            self.kv("change", cfg_diff(e["args"]))
            if "error" in res:
                self.kv("harness", f"REFUSED: {res['error']}", "red", "b")
                return
            line = self.by_attempt[res["attempt"]]
            m, vs = line["metrics"], line["vs_baseline"]
            self.kv("metrics", f"median error {m['median_error_m']} m, coverage {m['coverage_mean']}, band "
                               f"{m['band_width_mean_m']} m ({vs['width_change']:+.1%})")
            self.kv("vs base", f"paired per-run error delta: {ci(vs['error_delta_ci_m'])}  (the rule uses this, "
                               f"not the median)")
            acc, why = verdict(vs, m)
            again = recheck(self.base["metrics"], m, vs)
            tag = ink("ACCEPTED", "green", "b") if acc else ink("REJECTED", "red", "b")
            col = "green" if acc else "red"
            wl = textwrap.wrap(why, self.w - 13 - 9) or [""]  # 9 = len("REJECTED ")
            self.p(ink(f"  {'harness':<10} ", "dim") + tag + " " + ink(wl[0], col))
            for x in wl[1:]:
                self.p(" " * 22 + ink(x, col))
            self.p(" " * 13 + ink(f"attempt {line['attempt']}, line hash {line['hash'][:12]}; now: accept() on the "
                                  f"logged metrics -> ", "dim")
                   + (ink("same verdict", "green") if again else ink("DIFFERENT VERDICT", "red", "b")))
            return
        if name == "submit_final":
            res = e["result"]
            self.rule(f"{hm(e['ts'])} submit_final (one allowed)", "yellow")
            self.kv("config", cfg_diff(e["args"]) + (f"  [config {res['config_hash']}]" if "config_hash" in res else ""), "b")
            self.kv("why", self.trim(e["rationale"], 300))
            g = self.g
            if g["tag_commit"]:
                self.kv("git tag", f"{TAG} -> {g['tag_commit']}" + ("" if g["tag"] else " (tag not listed)"))
                okf = g["final_hash_ok"] and g["final_at_tag_ok"]
                self.kv("now", ("final_config.json = the submit line in the log = the file at the tag"
                                if okf else "final_config.json does NOT match the log/tag"), "green" if okf else "red")
            else:
                self.kv("git tag", f"{TAG} not found here (git tags not fetched?)", "yellow")
                okf = g["final_hash_ok"]
                self.kv("now", ("final_config.json = the submit line in the log" if okf
                                else "final_config.json does NOT match the log"), "green" if okf else "red")
            return
        if e["kind"] == "report":
            self.report_event = e

    def trim(self, s: str, n: int | None = None) -> str:
        n = n or self.a.trim
        s = " ".join(s.split())
        return s if len(s) <= n else s[: n - 3].rsplit(" ", 1)[0] + "..."

    def sealed(self):
        self.rule("sealed 24 Oct test", "yellow")
        rows = sealed_test(self.att)
        if rows is None:
            self.p(self.ink("  sealed 24 Oct test: not yet scored (budget 1, unused)", "yellow", "b"))
        else:
            for r in rows:
                self.kv("", r)

    def report(self):
        e = getattr(self, "report_event", None)
        if not e:
            return
        ink = self.ink
        u = e.get("usage") or {}
        self.rule(f"{hm(e['ts'])} Argon's own report")
        for x in textwrap.wrap(f"Written by {self.ses[0]['model']}, verbatim from analyst_session.jsonl (markdown "
                               f"markers removed); session {e.get('seconds')} s, {u.get('prompt')} prompt / "
                               f"{u.get('output')} output tokens. Its words, not ours:", self.w - 2):
            self.p(ink("  " + x, "dim"))
        for para in (e.get("text") or "(no text)").split("\n"):
            head = para.startswith("#")
            t = re.sub(r"[`*]|^#+\s*", "", para).strip()
            if not t:
                self.p(ink("  |", "cyan"))
                continue
            ind = "     " if re.match(r"\d+\.", t) else "  | "
            for i, x in enumerate(textwrap.wrap(t, self.w - 8)):
                self.p(ink("  | ", "cyan") + (ind[4:] if i == 0 or ind == "  | " else "   ")
                       + ink(x, "b" if head or (len(t) < 40 and not t.endswith(".")) else "cyan"))

    def caveat(self):
        dev = [r for r in self.att if r["kind"] == "dev" and r["vs_baseline"]]
        acc = [r for r in dev if r["vs_baseline"]["accepted"]]
        sub = next((r for r in self.att if r["kind"] == "submit"), None)
        a = next((r for r in acc if sub and r["config_hash"] == sub["config_hash"]), acc[0] if acc else None)
        tested = any(r["kind"] == "test" for r in self.att)
        if a:
            c = a["vs_baseline"]["error_delta_ci_m"]
            s = (f"Caveat: a small gain - error CI upper bound {c[2]:+.2f} m (only just below 0), {len(acc)} of "
                 f"{len(dev)} configurations accepted, {a['metrics']['n_runs']} dev runs from one morning, errors vs a "
                 f"yaw curve-edge proxy; " + ("see the single sealed test above." if tested
                                              else "the single sealed 24 Oct test has not been scored yet."))
        else:
            s = f"Caveat: 0 of {len(dev)} configurations accepted; the baseline stands."
        self.p()
        for x in textwrap.wrap(s, self.w - 2):
            self.p(self.ink("  " + x, "yellow", "b"))
        self.p(self.ink("=" * self.w, "mag"))

    def run(self):
        self.header()
        prev, n = None, 0
        for e in self.ses:
            if prev is not None:
                dt = (ts(e["ts"]) - ts(prev)).total_seconds()
                if dt > 0:
                    self.wait(dt, f"+{dt:.0f} s in the recording (Argon reasoning"
                                  + (f" + harness scoring {self.base['metrics']['n_runs']} held-out runs)"
                                     if e.get("name") == "run_experiment" else ")"))
            if e.get("name") == "run_experiment":
                n += 1
            self.event(e, n)
            prev = e["ts"]
        self.sealed()
        self.report()
        self.caveat()


# --------------------------------------------------------------------------- summary
def summary(a: argparse.Namespace):
    att, ses = jsonl(H.LOG), jsonl(SESSION)
    g = integrity(att)
    base = next(r for r in att if r["kind"] == "dev" and r["config"] == H._check({}))
    rows = [("#", "UTC", "change vs baseline", "median err m", "cov", "band m", "err delta m: mean [95% CI]",
             "harness", "decisive reason")]
    for r in att:
        if r["kind"] == "dev":
            m, vs = r["metrics"], r["vs_baseline"]
            if vs is None:
                rows.append((str(r["attempt"]), hm(r["ts"]), "baseline", f"{m['median_error_m']}", f"{m['coverage_mean']}",
                             f"{m['band_width_mean_m']}", "-", "-", "reference"))
                continue
            acc, why = verdict(vs, m)
            if not recheck(base["metrics"], m, vs):
                why += " (RECHECK DIFFERS)"
            c = vs["error_delta_ci_m"]
            rows.append((str(r["attempt"]), hm(r["ts"]), cfg_diff(r["config"]), f"{m['median_error_m']}",
                         f"{m['coverage_mean']}", f"{m['band_width_mean_m']}",
                         f"{c[1]:+.2f} [{c[0]:+.2f}, {c[2]:+.2f}]",
                         "ACCEPTED" if acc else "REJECTED", why))
        elif r["kind"] == "submit":
            rows.append((str(r["attempt"]), hm(r["ts"]), "submit_final: " + cfg_diff(r["config"]), "", "", "", "", "",
                         f"tag {TAG} " + (g["tag_commit"][:7] if g["tag_commit"] else "(not found here)")))
        elif r["kind"] == "test":
            m = r["metrics"]
            rows.append((str(r["attempt"]), hm(r["ts"]), "SEALED 24 Oct test", f"{m['median_error_m']}",
                         f"{m['coverage_mean']}", f"{m['band_width_mean_m']}", "", "", f"n={m['n_runs']}"))
    w = [max(len(x[i]) for x in rows) for i in range(len(rows[0]))]
    line = lambda x: "| " + " | ".join(c.ljust(w[i]) for i, c in enumerate(x)) + " |"
    start = ses[0] if ses else {"model": "gemini-4-argon", "ts": att[0]["ts"]}
    print(f"REPLAY of a recorded {start['model']} analyst session ({start['ts'][:10]}, not live); "
          f"23 Oct dev split, n={base['metrics']['n_runs']} runs; errors = agreement with a yaw curve-edge proxy.")
    print()
    print(line(rows[0]))
    print("|" + "|".join("-" * (x + 2) for x in w) + "|")
    for x in rows[1:]:
        print(line(x))
    print()
    print("median err = median of per-run medians; err delta = paired per-run change vs baseline (mean, 95% bootstrap "
          "CI). The rule uses the CI, not the median.")
    ch = g["chain"]
    tag = ("not found here (git tags not fetched?)" if not g["tag_commit"]
           else "match" if g["tag_sha_ok"] else "DOES NOT MATCH")
    print(f"Hash chain: {ch['lines']} lines, " + ("intact" if not ch["broken"] else f"BROKEN at {ch['broken']}")
          + f"; harness SHA-256 {g['disk'][:16]}...: disk vs every log line "
          + ("match" if g["sha_ok"] else "DOES NOT MATCH") + f"; vs tag {TAG}: {tag}.")
    st = sealed_test(att)
    print("Sealed 24 Oct test: not yet scored (budget 1, unused)." if st is None else "Sealed 24 Oct test (scored once): "
          + "; ".join(st))
    f = failed_session(att)
    if f:
        print(f[0].upper() + f[1:])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--speed", type=float, default=None, help=f"time compression (default: fit ~{TARGET_S:.0f} s)")
    ap.add_argument("--step", action="store_true", help="press Enter to advance each event")
    ap.add_argument("--summary", action="store_true", help="compact table only")
    ap.add_argument("--no-color", action="store_true")
    ap.add_argument("--no-atlas", action="store_true", help="skip the Atlas mirror check")
    ap.add_argument("--trim", type=int, default=230, help="max characters of each hypothesis")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass
    if a.summary:
        summary(a)
        return
    ses = jsonl(SESSION)
    span = (ts(ses[-1]["ts"]) - ts(ses[0]["ts"])).total_seconds() if ses else 0.0
    a.speed = a.speed or max(1.0, span / TARGET_S)
    Replay(a).run()


if __name__ == "__main__":
    main()
