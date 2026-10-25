"""Recorded Argon analyst session: propose tracker settings, score them with the fixed harness, explain.

gemini-4-argon gets four tools that wrap agent.harness (list_experiments, get_baseline, run_experiment,
submit_final) and nothing else: no file access, no code execution, no test data. It sees metrics
only (no audio, no raw sensor data). The harness decides acceptance with its pre-declared rule and
logs every call to outputs_posthoc/attempts.jsonl; this script records the conversation itself
(prompts, tool calls, tool results, Argon's text) to outputs_posthoc/analyst_session.jsonl and a
readable outputs_posthoc/analyst_session.md, for the demo to replay.
Post-hoc, declared after prereg-24oct; 23 Oct dev runs only.
Run: PYTHONPATH=. .venv/Scripts/python -m agent.analyst [--max-experiments 10] [--thinking medium]
"""

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from google import genai
from google.genai import types

from atlas.db import load_env

from . import harness as H

MODEL = "gemini-4-argon"
OUT = H.LOG.parent
SESSION = OUT / "analyst_session.jsonl"

SYSTEM = """You are the analyst agent of Tubesense, which locates a London Underground train between
stations without GPS from a phone's gyroscope. The online tracker is a grid Bayes filter (HMM) over
(speed level x position bin): each 0.25 s step it predicts with a speed-level random walk (P_SPEED),
then multiplies by a Gaussian likelihood of the measured yaw rate against a map of yaw rate vs position
built from earlier rides; the map's run-to-run sd gets YAW_SD_FLOOR (deg/s) added in quadrature, and
the likelihood is raised to the power GAMMA (tempering, because 4 Hz samples of a ~1 Hz signal are
not independent). An optional stop gate freezes the prediction while the train stands in a tunnel.

What is already known (23 Oct rides, leave-one-run-out, errors against an independent curve-edge
truth): the tracker is metre-level near curves and timetable-level between them; EB BTH->MLE is
over-confident (90 % band coverage ~0.82); earlier work found that tempering moves the error little
but the band width a lot, and that no single GAMMA calibrated both sections.

Your job: use the tools to find out whether any allowed configuration is genuinely better than the
baseline on the 23 Oct dev split, under the harness's pre-declared acceptance rule (which you cannot
change). Work like a careful scientist: state a hypothesis before each experiment (in the rationale),
run at most {max_exp} experiments, do not repeat configurations, and do not cherry-pick: an honest
"no change is accepted" is a valid result. Then call submit_final exactly once with the configuration
to be scored ONCE on the sealed 24 Oct test (the baseline if nothing was accepted). Finally write a
short report (<= 250 words): what you tried and why, what the numbers say (cite them), what you
submitted and why, and ONE structural hypothesis outside the allowed settings worth testing next.
Never claim accuracy beyond the measured numbers; errors are agreement with a yaw-landmark proxy."""

KICKOFF = ("Start by calling list_experiments and get_baseline, then plan and run your experiments, "
           "submit, and write the report.")


class Session:
    def __init__(self, max_exp: int, path: Path = SESSION, echo: bool = True):
        self.max_exp, self.n_exp, self.events, self.path, self.echo = max_exp, 0, [], path, echo

    def log(self, kind: str, **kw):
        ev = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": kind, **kw}
        self.events.append(ev)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev, default=float) + "\n")
        if self.echo:
            print(f"[{ev['ts']}] {kind}: {json.dumps(kw, default=float)[:300]}", flush=True)

    def _score(self, cfg: dict, rationale: str, when_spent: str = "submit_final now") -> dict:
        """Budget check, one harness dev run (cached runs are free), per-run detail trimmed; errors as {"error"}."""
        if self.n_exp >= self.max_exp:
            return {"error": f"experiment budget of {self.max_exp} used; {when_spent}"}
        try:
            r = H.run_experiment(cfg, rationale=rationale)
            if not r.get("cached"):
                self.n_exp += 1
            return {k: v for k, v in r.items() if k != "metrics"} | {"metrics": {k: v for k, v in r["metrics"].items()
                                                                                if k != "per_run"}}
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}

    # ---- tools exposed to Argon (typed signatures: the SDK builds the function declarations from them)
    def list_experiments(self) -> dict:
        """Settings you may change, their allowed values, the baseline and the acceptance rule."""
        r = H.list_experiments()
        self.log("tool", name="list_experiments", result=r)
        return r

    def get_baseline(self) -> dict:
        """Dev-split metrics of the baseline configuration (median error m, coverage, band width, per run, per track)."""
        r = H.baseline()
        self.log("tool", name="get_baseline", result=r)
        return r

    def run_experiment(self, GAMMA: float, YAW_SD_FLOOR: float, P_SPEED: float, stop_gate: bool,
                       rationale: str) -> dict:
        """Score one configuration on the 23 Oct dev split (~40 s) and compare it with the baseline.

        Args:
            GAMMA: likelihood tempering, one of the allowed values.
            YAW_SD_FLOOR: deg/s added in quadrature to the map's sd, one of the allowed values.
            P_SPEED: per-step speed-level change probability, one of the allowed values.
            stop_gate: whether the stop gate is on.
            rationale: the hypothesis this experiment tests and what result would support it.
        """
        cfg = {"GAMMA": GAMMA, "YAW_SD_FLOOR": YAW_SD_FLOOR, "P_SPEED": P_SPEED, "stop_gate": stop_gate}
        r = self._score(cfg, rationale)
        self.log("tool", name="run_experiment", args=cfg, rationale=rationale, result=r)
        return r

    def submit_final(self, GAMMA: float, YAW_SD_FLOOR: float, P_SPEED: float, stop_gate: bool,
                     rationale: str) -> dict:
        """Declare the single configuration to score once on the sealed 24 Oct test. Callable once.

        Args:
            GAMMA: likelihood tempering.
            YAW_SD_FLOOR: deg/s added in quadrature to the map's sd.
            P_SPEED: per-step speed-level change probability.
            stop_gate: whether the stop gate is on.
            rationale: why this configuration, citing the dev numbers.
        """
        cfg = {"GAMMA": GAMMA, "YAW_SD_FLOOR": YAW_SD_FLOOR, "P_SPEED": P_SPEED, "stop_gate": stop_gate}
        try:
            r = H.submit_final(cfg, rationale)
        except (ValueError, RuntimeError) as e:
            r = {"error": str(e)}
        self.log("tool", name="submit_final", args=cfg, rationale=rationale, result=r)
        return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-experiments", type=int, default=10)
    ap.add_argument("--thinking", default="medium", choices=("low", "medium", "high"))
    a = ap.parse_args()
    load_env()
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    s = Session(a.max_experiments)
    s.log("start", model=MODEL, thinking=a.thinking, max_experiments=a.max_experiments, harness_sha=H.HARNESS_SHA,
          system=SYSTEM.format(max_exp=a.max_experiments), user=KICKOFF)
    chat = client.chats.create(model=MODEL, config=types.GenerateContentConfig(
        system_instruction=SYSTEM.format(max_exp=a.max_experiments),
        tools=[s.list_experiments, s.get_baseline, s.run_experiment, s.submit_final],
        thinking_config=types.ThinkingConfig(thinking_level=a.thinking),
        automatic_function_calling=types.AutomaticFunctionCallingConfig(maximum_remote_calls=a.max_experiments + 8)))
    t0 = time.time()
    r = chat.send_message(KICKOFF)
    if not any(e["kind"] == "tool" and e["name"] == "submit_final" and "error" not in e["result"] for e in s.events):
        r = chat.send_message("You have not submitted yet. Call submit_final once, then write the report.")
    s.log("report", text=r.text, seconds=round(time.time() - t0, 1),
          usage={"prompt": r.usage_metadata.prompt_token_count, "output": r.usage_metadata.candidates_token_count})
    write_md(s.events)


def write_md(events: list[dict]):
    lines = ["# Argon analyst session (post-hoc, 23 Oct dev split only)", ""]
    for e in events:
        if e["kind"] == "start":
            lines += [f"- Model `{e['model']}`, thinking `{e['thinking']}`, experiment budget {e['max_experiments']},"
                      f" harness SHA-256 `{e['harness_sha'][:16]}…`", ""]
        elif e["kind"] == "tool" and e["name"] in ("run_experiment", "submit_final"):
            res = e["result"]
            lines.append(f"**{e['name']}** `{json.dumps(e['args'])}` ({e['ts']})")
            lines.append(f"> {e['rationale']}")
            if "error" in res:
                lines.append(f"- error: {res['error']}")
            elif e["name"] == "run_experiment":
                m, vs = res["metrics"], res.get("vs_baseline")
                lines.append(f"- median error {m['median_error_m']} m, coverage {m['coverage_mean']}, band "
                             f"{m['band_width_mean_m']} m" + (f"; vs baseline {json.dumps(vs)}" if vs else " (baseline)"))
            lines.append("")
        elif e["kind"] == "report":
            lines += ["## Argon's report", "", e["text"] or "(no text)", "", f"_{e['seconds']} s, tokens {e['usage']}_"]
    (OUT / "analyst_session.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
