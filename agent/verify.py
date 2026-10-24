"""Deterministic verifier for the investigator's claims: nothing reaches the evidence pack unchecked.

A claim is {id, section, text, type, citations: [{chunk_id, quote}], stat_keys: [..], formula}.
Checks, in order (the first failure rejects the claim, with the reason sent back to the agent):
1. banned phrasing (BANNED): overclaims we know are false or unsupported (e.g. 92.8 dB as "TfL's own
   data", "corrugation measured", certified levels, passenger hearing damage);
2. by type:
   sourced   >= 1 citation; each chunk exists in the corpus and its quote (>= 4 words) is an exact
             substring of the chunk after normalisation (NFKC, quotes/dashes, soft hyphens, page
             markers, whitespace, case);
   measured  >= 1 stat key, each present in outputs_posthoc/section_stats.json;
   derived   a formula {expression, inputs: {name: {stat: key} | {value, chunk_id, quote}}, result}
             recomputed with a whitelisted arithmetic evaluator; every literal input must appear in
             its own verified quote; the stated result must match (1 % or 0.05);
   inference no numbers of its own (only numbers supported as below), shown labelled as inference;
3. every number in the claim text must be supported: it appears in one of the claim's verified quotes,
   or equals a cited stat (value, n, or a detail field) at the claim's precision, or is a derived
   input/result. Document references (FOI-1231-2425, 2022/1791, s123225, dates) are not numbers.
Pure functions; no network. VERIFIER_SHA pins this file for the approval record.
Run the seeded canary: PYTHONPATH=. .venv/Scripts/python -m agent.verify --canary
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import operator
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHUNKS = ROOT / "data" / "corpus" / "chunks.jsonl"
STATS = ROOT / "outputs_posthoc" / "section_stats.json"
VERIFIER_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
TYPES = ("sourced", "measured", "derived", "inference")

BANNED = [
    (r"92\.[89]\s*dB.{0,80}\b(tfl'?s own|tfl data shows|tfl measured|according to tfl)\b|"
     r"\b(tfl'?s own|tfl measured|according to tfl)\b.{0,80}92\.[89]",
     "92.8/92.9 dB is a secondary press figure (JRPass via Brixton Buzz), not TfL's own published data"),
    (r"\bcorrugation (was |is )?(measured|detected|confirmed|proven)\b|\bmeasured (the )?corrugation\b",
     "corrugation is an open hypothesis here; nothing in this project measures it"),
    (r"\b(certified|calibrated) (sound|noise) (level|meter|measurement)", "our levels are uncalibrated, relative dBFS(A)"),
    (r"\bhearing (damage|loss)\b.{0,60}\bpassengers?\b|\bpassengers?\b.{0,60}\bhearing (damage|loss)\b",
     "no passenger hearing-damage claim: HSE values apply to workers, and we measure relative levels"),
    (r"\b(proves?|proven|conclusively)\b", "say what the evidence shows, not that it proves"),
    (r"\bnoise (has )?returned\b", "no before/after measurement exists, so 'noise returned' cannot be shown"),
]

# Conventional constants a claim may state without a citation, only when the claim names what they are
# (so "the section reached 85 dB" still needs a source). Value -> (context regex, meaning).
CONSTANTS = {80.0: (r"\bHSE\b|action value", "HSE lower exposure action value, dB(A) (SI 2005/1643 reg 4)"),
             85.0: (r"\bHSE\b|action value", "HSE upper exposure action value, dB(A)"),
             87.0: (r"\bHSE\b|exposure limit", "HSE exposure limit value, dB(A)"),
             8.0: (r"8-hour|8 hour|\bdaily\b", "the HSE daily exposure is an 8-hour equivalent"),
             90.0: (r"\bband\b|credible|coverage", "the tracker's 90 % band")}

_ID_PAT = re.compile(r"(FOI-\d+-\d+|\b(?:MQ|MQT|Q)\s*\d{4}/\d+|\b\d{4}/\d{3,5}\b|\bs\d{5,6}\b|\bR\d+(?:\.\d)?\b|"
                     r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}\b|"
                     r"\b\d{4}-\d{2}-\d{2}\b|\bTTN\s*\d+/\d+|\bWTT\s*\d+|\bRP-R\d+|#\d+)", re.I)
_NUM = re.compile(r"(?<![\w.])[-+−]?\d+(?:[.,]\d+)?(?![\w])")


# --------------------------------------------------------------------------- corpus / stats
def load_chunks() -> dict[str, dict]:
    return {c["_id"]: c for c in map(json.loads, CHUNKS.read_text(encoding="utf-8").splitlines()) if c}


def load_stats() -> dict[str, dict]:
    return json.loads(STATS.read_text(encoding="utf-8")) if STATS.exists() else {}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"\[p\.\d+\]", " ", s)
    s = s.replace("­", "").replace("|", " ")   # table cells extract as 'a | b'
    for a, b in (("‘", "'"), ("’", "'"), ("“", '"'), ("”", '"'), ("–", "-"), ("—", "-"), ("−", "-")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip().lower()


def numbers(text: str) -> list[str]:
    return [m.group().replace("−", "-").replace(",", "") for m in _NUM.finditer(_ID_PAT.sub(" ", text or ""))]


def _decimals(tok: str) -> int:
    return len(tok.split(".")[1]) if "." in tok else 0


def _matches(tok: str, value: float) -> bool:
    x = float(tok)
    tol = 0.5 * 10 ** -_decimals(tok) + 1e-9
    return abs(x - value) <= tol or abs(abs(x) - abs(value)) <= tol


# --------------------------------------------------------------------------- formula
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {"log10": math.log10, "sqrt": math.sqrt, "min": min, "max": max, "abs": abs, "round": round}


def safe_eval(expr: str, env: dict[str, float]) -> float:
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.Name) and n.id in env:
            return env[n.id]
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS:
            return _FUNCS[n.func.id](*[ev(a) for a in n.args])
        raise ValueError(f"unsupported expression element: {ast.dump(n)[:60]}")
    return float(ev(ast.parse(expr, mode="eval")))


# --------------------------------------------------------------------------- checks
def _check_quote(c: dict, chunks: dict) -> str | None:
    ch = chunks.get(c.get("chunk_id", ""))
    if ch is None:
        return f"unknown chunk_id {c.get('chunk_id')!r}"
    q = norm(c.get("quote", ""))
    if len(q.split()) < 4:
        return f"quote too short to verify in {c['chunk_id']} (need >= 4 words)"
    if q not in norm(ch["text"]):
        return f"quote not found verbatim in {c['chunk_id']}: {c.get('quote', '')[:80]!r}"
    return None


def verify_claim(cl: dict, chunks: dict, stats: dict) -> dict:
    """Returns {id, ok, reasons, supported_numbers}."""
    text, typ = cl.get("text", ""), cl.get("type")
    reasons = []
    for pat, why in BANNED:
        if re.search(pat, text, re.I | re.S):
            reasons.append(f"banned: {why}")
    if typ not in TYPES:
        reasons.append(f"type must be one of {TYPES}")
    cits = cl.get("citations") or []
    quote_nums, stat_vals, derived_vals = [], [], []
    for c in cits:
        err = _check_quote(c, chunks)
        if err:
            reasons.append(err)
        else:
            quote_nums += numbers(c["quote"])
    for k in cl.get("stat_keys") or []:
        if k not in stats:
            reasons.append(f"unknown stat key {k!r}")
            continue
        s = stats[k]
        for v in [s["value"], s.get("n"), *[x for x in (s.get("detail") or {}).values() if isinstance(x, (int, float))]]:
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                stat_vals.append(float(v))
    if typ == "sourced" and not cits:
        reasons.append("a sourced claim needs at least one citation")
    if typ == "measured" and not cl.get("stat_keys"):
        reasons.append("a measured claim needs at least one stat key")
    if typ == "derived":
        f = cl.get("formula") or {}
        try:
            env = {}
            for name, inp in (f.get("inputs") or {}).items():
                if "stat" in inp:
                    if inp["stat"] not in stats:
                        raise ValueError(f"unknown stat {inp['stat']!r}")
                    env[name] = float(stats[inp["stat"]]["value"])
                else:
                    err = _check_quote(inp, chunks)
                    if err:
                        raise ValueError(f"input {name}: {err}")
                    if not any(_matches(t, float(inp["value"])) for t in numbers(inp["quote"])):
                        raise ValueError(f"input {name}={inp['value']} does not appear in its quote")
                    env[name] = float(inp["value"])
            got = safe_eval(f["expression"], env)
            want = float(f["result"])
            if not (abs(got - want) <= max(0.05, 0.01 * abs(got))):
                raise ValueError(f"formula gives {got:.4g}, claim states {want}")
            consts = [float(n.value) for n in ast.walk(ast.parse(f["expression"], mode="eval"))
                      if isinstance(n, ast.Constant) and isinstance(n.value, (int, float))]
            derived_vals += [got, want, *env.values(), *consts]
        except (KeyError, ValueError, TypeError, ZeroDivisionError, SyntaxError) as e:
            reasons.append(f"derived: {e}")
    consts = [v for v, (ctx, _) in CONSTANTS.items() if re.search(ctx, text, re.I)]
    unsupported = []
    for tok in numbers(text):
        if any(_matches(tok, float(q)) for q in quote_nums) or \
                any(_matches(tok, v) for v in stat_vals + derived_vals + consts):
            continue
        unsupported.append(tok)
    if unsupported:
        reasons.append(f"numbers not supported by the cited quotes/stats/formula: {unsupported}")
    return {"id": cl.get("id"), "ok": not reasons, "reasons": reasons}


def verify_all(claims: list[dict]) -> list[dict]:
    chunks, stats = load_chunks(), load_stats()
    return [verify_claim(c, chunks, stats) for c in claims]


# --------------------------------------------------------------------------- demo canary
CANARY = {"id": "CANARY", "section": "tfl_data", "type": "sourced", "seeded": True,
          "text": "According to TfL's own data, Liverpool Street to Bethnal Green averages 92.8 dB.",
          "citations": [{"chunk_id": "BrixtonBuzz-2026-01-27#000", "quote": "Liverpool Street to Bethnal Green"}]}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--canary", action="store_true", help="verify the seeded false claim (it must be rejected)")
    ap.add_argument("claims", nargs="?", help="JSON file with a list of claims")
    a = ap.parse_args()
    items = [CANARY] if a.canary else json.load(open(a.claims, encoding="utf-8"))
    for r in verify_all(items):
        print(json.dumps(r, indent=1))
