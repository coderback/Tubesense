"""Argon investigator: "Was this section ground for noise, and is it still loud?", as checkable claims.

gemini-4-argon reads the whole public corpus (44 chunks of TfL / London Assembly documents, long
context), the Atlas hybrid-search ($rankFusion) top hits for each sub-question, and our measured
section statistics, and returns structured claims (sourced / measured / derived / inference), each
with verbatim quotes, stat keys or a formula. agent.verify checks every claim; rejected claims go
back to Argon with the reasons (up to MAX_ROUNDS), and anything still failing is dropped and listed.
Only public document text and derived statistics are sent to Gemini (no audio, no raw sensor data).
With --review FILE, Argon instead revises its previous verified draft (outputs_posthoc/investigation.json)
to resolve the findings of an independent review against the full sources (framing the verifier cannot
check: a quote out of context, a commitment overstated, a scope overgeneralised); the revision goes
through the same verifier loop, and the record keeps the review file and the draft it revised.
Writes outputs_posthoc/investigation.json and upserts the claims into Atlas `claims`.
Run: PYTHONPATH=. .venv/Scripts/python -m agent.investigator [--thinking medium] [--review FILE]
"""
import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Literal, Optional

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from atlas.db import load_env

from . import verify as V

MODEL = "gemini-4-argon"
OUT = Path(os.environ.get("TUBESENSE_POSTHOC_OUT", V.ROOT / "outputs_posthoc")) / "investigation.json"
MAX_ROUNDS = 2
SECTIONS = ("tfl_data", "grinding", "our_measurements", "limits", "context")

QUESTIONS = [
    "Was the Central line between Liverpool Street and Bethnal Green ground to reduce noise, and when?",
    "Was the Central line between Bethnal Green and Mile End ground to reduce noise, and when?",
    "What noise levels has TfL itself reported for Liverpool Street to Bethnal Green and Bethnal Green to Mile End?",
    "How often does rail grinding need repeating, and how far ahead is it planned?",
    "Has TfL refused or limited data requests about Tube noise from London Assembly members?",
    "Is deep Tube track reconditioning planned between Liverpool Street and Bethnal Green?",
]


class Citation(BaseModel):
    chunk_id: str = Field(description="the corpus _id, e.g. FOI-1231-2425#000")
    quote: str = Field(description="an exact, verbatim substring of that chunk's text, at least 4 words")


class FormulaInput(BaseModel):
    name: str
    stat: Optional[str] = Field(None, description="a section-stats key, or null")
    value: Optional[float] = Field(None, description="a literal number taken from the quote, or null")
    chunk_id: Optional[str] = None
    quote: Optional[str] = Field(None, description="verbatim quote containing the literal value")


class Formula(BaseModel):
    expression: str = Field(description="arithmetic using the input names, + - * / ** log10 sqrt min max abs round")
    inputs: list[FormulaInput]
    result: float


class Claim(BaseModel):
    id: str
    section: Literal[SECTIONS]
    type: Literal["sourced", "measured", "derived", "inference"]
    text: str = Field(description="one plain-English sentence; every number in it must be supported")
    citations: list[Citation] = []
    stat_keys: list[str] = []
    formula: Optional[Formula] = None


class Ask(BaseModel):
    request: str = Field(description="a specific information request Elly Baker AM could put to TfL (EIR/FOI or a Mayor's Question)")
    why: str = Field(description="the gap in the public record this closes, citing claim ids")


class Investigation(BaseModel):
    claims: list[Claim]
    asks: list[Ask]
    headline_claim_ids: list[str] = Field(description="the 3-5 claims that best answer the question, in order")


SYSTEM = """You are the investigator agent of Tubesense, preparing a cited evidence pack for Elly Baker AM
(London Assembly, Labour transport spokesperson), who has asked TfL for section-level Tube noise data
and been turned down. Question: were the Central line sections Liverpool Street-Bethnal Green (LST-BTH)
and Bethnal Green-Mile End (BTH-MLE) ground for noise, and are they still loud?

Write claims a deterministic verifier will check. Rules:
- type "sourced": cite >= 1 chunk with a VERBATIM quote (copy exact characters; >= 4 words). Page markers
  like [p.5] are not part of quotes.
- type "measured": cite stat_keys from SECTION STATS. Our phone-audio levels (level.*, contrast.*) are
  uncalibrated relative dBFS(A): never compare them with TfL's dB values or call them absolute. The
  niosh.* stats are indicative dB(A) from a phone sound level meter app, one whole trip per direction
  including platform and dwell time; the app is validated only up to about 85 dB(A), so they may read
  low. Always call them indicative, and never present them as like-for-like with TfL's per-section
  in-cab figures.
- type "derived": give a formula whose inputs are stat keys or literal numbers each quoted verbatim.
- type "inference": your reasoning, clearly hedged, with no new numbers.
- Every number in a claim's text must appear in its cited quotes, its stats, or its formula. Document
  references (FOI-1231-2425, 2022/1791) and dates are fine.
- Primary sources outrank secondary. The 92.8 dB figure is a secondary press report (Brixton Buzz, via
  JRPass); never present it as TfL's own data. TfL's in-cab figures are cab levels, not saloon levels.
- Never say the evidence "proves", that "noise returned", that corrugation was measured, or that
  passengers' hearing is harmed. HSE action values apply to workers.
- Put what CANNOT be concluded in section "limits" (e.g. which direction was ground, whether it was
  ground again since, where 92.8 dB comes from). Ask for those gaps in "asks".
Prefer 10-16 precise claims over many vague ones."""


def corpus_block(chunks: dict) -> str:
    out = []
    for cid, c in chunks.items():
        meta = f"[{cid}] {c['title']} | {c['publisher']} | {c['doc_date']} | {c['reliability']}" + \
               (f" | NOTE: {c['note']}" if c.get("note") else "")
        out.append(meta + "\n" + c["text"])
    return "\n\n".join(out)


def retrieval() -> dict:
    """Atlas hybrid search ($rankFusion over Atlas Search + autoEmbed) top hits per sub-question."""
    try:
        from atlas.search import hybrid_search
        return {q: [h["_id"] for h in hybrid_search(q, k=5, with_text=False)] for q in QUESTIONS}
    except Exception as e:  # noqa: BLE001  (offline: the full corpus is in the prompt anyway)
        return {"error": f"{type(e).__name__}: {e}"}


def as_verifier(cl: dict) -> dict:
    f = cl.get("formula")
    if f:
        f = {**f, "inputs": {i["name"]: ({"stat": i["stat"]} if i.get("stat") else
                                         {k: i[k] for k in ("value", "chunk_id", "quote")}) for i in f["inputs"]}}
    return {**cl, "formula": f}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--thinking", default="medium", choices=("low", "medium", "high"))
    ap.add_argument("--review", default=None, help="JSON list of review findings: revise the previous draft")
    a = ap.parse_args()
    load_env()
    chunks, stats = V.load_chunks(), V.load_stats()
    hits = retrieval()
    prompt = (f"HYBRID SEARCH TOP HITS (Atlas $rankFusion, start here):\n{json.dumps(hits, indent=1)}\n\n"
              f"SECTION STATS (our measurements; cite by key):\n{json.dumps(stats, indent=1, default=float)}\n\n"
              f"CORPUS ({len(chunks)} chunks):\n{corpus_block(chunks)}\n\n")
    revision = None
    if a.review:
        prev = json.loads(OUT.read_text(encoding="utf-8"))
        findings = json.loads(Path(a.review).read_text(encoding="utf-8"))
        revision = {"of": prev["ts"], "review_file": Path(a.review).name, "n_findings": len(findings)}
        prompt += ("PREVIOUS DRAFT (it passed the verifier):\n" + json.dumps(prev["rounds"][-1]["investigation"], indent=1) +
                   "\n\nINDEPENDENT REVIEW FINDINGS (each checked against the full source text; resolve every one):\n" +
                   json.dumps(findings, indent=1) +
                   "\n\nRevise the draft so that every finding is resolved: rewrite, split, merge or drop claims and "
                   "asks as needed, and keep what was already right. Change only the claims and asks the findings "
                   "name (ASK-n is the n-th ask); copy every other claim and ask exactly as it is, with its id. "
                   "Quotes stay verbatim and every number stays supported. Return the FULL revised Investigation JSON.")
    else:
        prompt += "Return the Investigation JSON."
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    cfg = types.GenerateContentConfig(system_instruction=SYSTEM, response_mime_type="application/json",
                                      response_schema=Investigation,
                                      thinking_config=types.ThinkingConfig(thinking_level=a.thinking))
    chat = client.chats.create(model=MODEL, config=cfg)
    rounds, t0 = [], time.time()
    msg = prompt
    for rnd in range(MAX_ROUNDS + 1):
        t = time.time()
        r = chat.send_message(msg)
        inv = Investigation.model_validate_json(r.text).model_dump()
        checks = V.verify_all([as_verifier(c) for c in inv["claims"]])
        rounds.append({"round": rnd, "seconds": round(time.time() - t, 1), "investigation": inv, "verification": checks,
                       "usage": {"prompt": r.usage_metadata.prompt_token_count,
                                 "output": r.usage_metadata.candidates_token_count}})
        bad = [c for c in checks if not c["ok"]]
        print(f"round {rnd}: {len(inv['claims'])} claims, {len(bad)} rejected ({rounds[-1]['seconds']} s)", flush=True)
        for b in bad:
            print(f"  x {b['id']}: {'; '.join(b['reasons'])[:200]}")
        if not bad or rnd == MAX_ROUNDS:
            break
        msg = ("The verifier rejected these claims. Fix each one (exact quotes, supported numbers, allowed "
               "wording) or drop it, and return the FULL corrected Investigation JSON:\n" +
               json.dumps(bad, indent=1))
    final = rounds[-1]
    ok_ids = {c["id"] for c in final["verification"] if c["ok"]}
    verified = [c for c in final["investigation"]["claims"] if c["id"] in ok_ids]
    dropped = [{"claim": c, "reasons": next(v["reasons"] for v in final["verification"] if v["id"] == c["id"])}
               for c in final["investigation"]["claims"] if c["id"] not in ok_ids]
    res = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "model": MODEL, "thinking": a.thinking,
           "verifier_sha": V.VERIFIER_SHA, "corpus_chunks": len(chunks),
           "corpus_hash": hashlib.sha256("".join(c["hash"] for c in chunks.values()).encode()).hexdigest()[:16],
           "stats_keys": len(stats), "retrieval": hits, "rounds": rounds, "verified_claims": verified,
           "dropped_claims": dropped, "asks": final["investigation"]["asks"],
           "headline_claim_ids": [i for i in final["investigation"]["headline_claim_ids"] if i in ok_ids],
           "seconds_total": round(time.time() - t0, 1), "revision": revision}
    OUT.write_text(json.dumps(res, indent=1, default=float), encoding="utf-8")
    try:
        from atlas.db import db
        from pymongo import ReplaceOne
        run = res["ts"]
        db().claims.bulk_write([ReplaceOne({"_id": f"{run}:{c['id']}"}, {"_id": f"{run}:{c['id']}", "run": run,
                                           "verified": c in verified, **c}, upsert=True)
                                for c in final["investigation"]["claims"]])
    except Exception as e:  # noqa: BLE001
        print(f"(atlas skipped: {type(e).__name__})")
    print(f"verified {len(verified)}, dropped {len(dropped)}, asks {len(res['asks'])}, {res['seconds_total']} s -> {OUT}")


if __name__ == "__main__":
    main()
