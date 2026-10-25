"""Ask Argon (live): one question -> 2-5 checkable claims, verified deterministically, with a fix loop.

What: Atlas hybrid search ($rankFusion: Atlas Search + Automated Embedding; local BM25 fallback, labelled)
picks document chunks; their full text and the relevant section statistics go to gemini-4-argon (thinking
medium only), which returns claims in the investigator's schema plus a one-line answer. agent.verify checks
every claim; rejects go back with the verifier's reasons for up to MAX_ROUNDS fix rounds, and what still
fails is dropped with its reasons. A separate Argon review then checks the verified claims against their
full sources for what the verifier cannot (attribution, hedges, a verdict's reason, a NO-GO read as a
ranking); its findings get one revision round, re-verified, and anything changed gets a final review that
can only withhold. The answer line is Argon's restatement, gated in code (check_answer), never a verified
fact; when the gate rejects it, a verified claim's text is shown instead.
Why: a named user (Elly Baker AM's office) asks in plain words; only claims that pass the verifier are
shown, and every answer is saved (outputs_posthoc/asks/, Atlas `asks`) so it can be replayed and re-checked.
Honesty limits:
- Every claim shown passed the verifier; the review is a model judgement and is labelled as such.
- A failed or unparseable revision never shows a flagged claim as reviewed: the claims verified before it,
  minus everything the review flagged, are shown and the record is flagged `incomplete`; so is a run cut
  short by a Gemini error after round 0 or by TIME_BUDGET_S.
- The answer gate is lexical. It catches banned phrasing (negation-aware for ANSWER_BANNED), phone-data
  louder/quieter rankings (our pre-registered loudness check was NO-GO), numbers/refs/dates not in the
  cited claims, dBFS(A) written as dB, and unscoped NIOSH figures. It cannot tell which cited claim a
  number belongs to, synonyms beyond its patterns, or numbers spelled out below ten; the review and the
  evidence shown beside the answer cover those.
- Cached answers (--cached, --file, replay()) are labelled CACHED with their original ts and re-verified and
  re-gated now; they are never presented as live. Post-hoc (after prereg-24oct).
- Only the question, public document text and derived statistics leave this machine (Gemini; the question
  also goes to Atlas for hybrid search, Voyage embedding in a US region).
Run: PYTHONPATH=. .venv/Scripts/python -m agent.ask "question" [--cached] [--jsonl] [--no-review]
     PYTHONPATH=. .venv/Scripts/python -m agent.ask --list
     PYTHONPATH=. .venv/Scripts/python -m agent.ask --file <saved file name> [--jsonl]     (replay one saved file)
     PYTHONPATH=. .venv/Scripts/python -m agent.ask --selftest                           (offline: gate + loop cases)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from collections import Counter
from functools import lru_cache
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from pydantic import BaseModel, Field, ValidationError

from atlas.db import load_env

from . import pack as P
from . import verify as V
from .investigator import MAX_ROUNDS, SECTIONS, Claim, corpus_block

MODEL = "gemini-4-argon"
THINKING = "medium"         # owner requirement: Argon runs at thinking_level medium only
OUTDIR = Path(os.environ.get("TUBESENSE_POSTHOC_OUT", V.ROOT / "outputs_posthoc"))
ASKS = OUTDIR / "asks"
MIRROR = OUTDIR == V.ROOT / "outputs_posthoc"   # no Atlas writes from test runs
K_HITS = 8
ANSWER_MAX_CHARS = 400
REQUEST_TIMEOUT_MS = 90_000
BACKOFF_S = (2, 5)          # waits before retry 1 and 2 on a transient Gemini error
TIME_BUDGET_S = 300         # no new Gemini call after this; the partial record is saved as incomplete
NEAR_JACCARD = 0.5          # --cached: minimum token overlap to serve a different saved question

STATIONS = {"VIC": r"victoria(?! line)", "GPK": r"green park", "OXC": r"oxford circus", "TCR": r"tottenham court",
            "HOL": r"holborn", "CYL": r"chancery lane", "STP": r"st\.? paul", "BNK": r"\bbank\b",
            "LST": r"liverpool st", "BTH": r"bethnal green", "MLE": r"mile end", "ALE": r"aldgate east",
            "WCL": r"whitechapel"}
NOISE = r"loud|nois|\bdb\b|decibel|level|sound|corrugat|grind|roar|niosh|hse"
POSITION = r"position|locat|gps|accura|metre|meter|where (is|was) the train|track(ing|er)\b"
STOP = set("a an and are as at be been being but by can could did do does for from had has have how i if in "
           "into is it its me my of on or our over should so than that the their them then there these this "
           "those to was we were what when where which while who why will with would you your".split())


class AskDraft(BaseModel):
    answer: str = Field(description="ONE sentence directly answering the question, restating only what the claims "
                                    "listed in answer_claim_ids say; no new facts or numbers")
    answer_claim_ids: list[str] = Field(description="ids of the claims the answer restates")
    claims: list[Claim] = Field(description="2-5 claims")


class Finding(BaseModel):
    claim_id: str = Field(description="the claim id, or 'answer' for the answer line")
    problem: str = Field(description="what would mislead a careful reader, citing the source text or statistic")
    fix: str = Field(description="how to correct it, or 'drop'")


class Review(BaseModel):
    findings: list[Finding]


SYSTEM = f"""You are the Ask agent of Tubesense. You answer one question from Elly Baker AM's office
(London Assembly, Labour transport spokesperson) about London Underground noise, using ONLY the public
documents retrieved below and our measured statistics. A deterministic verifier checks every claim;
rejected claims come back to you with the reasons, and claims that still fail are dropped. The user
sees only verified claims.

Return 2-5 claims and a one-sentence answer:
- answer: ONE sentence (at most 40 words) that directly answers the question by restating your claims;
  list the claims it restates in answer_claim_ids. Every number in the answer must appear in the text
  of one of those claims, with the same sign and the unit that claim gives (dBFS(A) is not dB). Every
  document reference (FOI-1231-2425) and full date in the answer must appear in a claim you list. Add
  nothing the claims do not say.
- If the question asks for something the documents and statistics cannot support (to prove something,
  or a measurement that does not exist here), do not try: write claims in section "limits" that say
  what the evidence can and cannot show, plus the closest facts that are supported, and make the
  answer say what cannot be shown and why.
- Claim ids A1, A2, ...; section is one of {", ".join(SECTIONS)}.
Claim rules (the verifier enforces them):
- type "sourced": cite >= 1 retrieved chunk with a VERBATIM quote (copy exact characters; >= 4 words).
  Page markers like [p.5] are not part of quotes. Quote only from chunks whose full text is below.
- type "measured": cite stat_keys from SECTION STATS. Our phone-audio levels (level.*, contrast.*) are
  uncalibrated relative dBFS(A): never compare them with TfL's dB values or call them absolute. The
  niosh.* stats are indicative dB(A) from a phone sound level meter app, one whole trip per direction
  including platform and dwell time; the app is validated only up to about 85 dB(A), so they may read
  low. Always call them indicative, and never present them as like-for-like with TfL's per-section
  in-cab figures.
  Positioning errors (positioning.*, v2.*) are agreement with a yaw curve-edge reference, not ground
  truth (there is none in the tunnel): say "against a curve-edge reference"; coverage is the fraction
  of the 90 % band that contains the reference.
- type "derived": give a formula whose inputs are stat keys or literal numbers each quoted verbatim.
- type "inference": your reasoning, clearly hedged, with no new numbers.
- Every number in a claim's text must appear in its cited quotes, its stats, or its formula. Document
  references (FOI-1231-2425, 2022/1791) and dates are fine.
- Primary sources outrank secondary. The 92.8 dB figure is a secondary press report (Brixton Buzz, via
  JRPass); never present it as TfL's own data. TfL's in-cab figures are cab levels, not saloon levels.
- Never use the words "prove", "proves", "proven" or "conclusively", or the phrases "noise (has) returned",
  "noise has come back", "corrugation has come back", not even negated ("does not prove" is rejected too):
  write "cannot show". Never say a section "was never ground": say the documents we hold do not list
  grinding for it. Never say corrugation was
  measured, detected or confirmed: nothing in this project measures corrugation, so the evidence cannot
  show whether it has returned or re-formed. Never say passengers' hearing is harmed; HSE action values
  apply to workers. Never say grinding failed, or that a section is the loudest on the Tube (a press
  report's ranking stays attributed to the press report).
Framing (an independent review checks this after the verifier):
- Attribute each statement to the document and speaker it comes from: a Mayor's written answer is the
  Mayor's answer, an Assembly Member's account at Mayor's Question Time is her account. A claim citing
  two documents must say which statement comes from which.
- Keep the source's hedges and scope: "usually", "planned", "reported", "around"; a whole stretch is not
  a section; one direction is not both; a plan is not a completed job.
- For our statistics, state a verdict's reason as its detail gives it (rule, justification, reversed
  trips), and describe counts and means over the right set of trips. A verdict's reason is the
  condition that triggers it in the rule: for a NO-GO it is a trip that goes the other way
  (reversed_trips); the other unmet GO conditions (mean below 1 dB, level variants disagreeing) may be
  listed only as also unmet, never as reasons for the NO-GO.
- A NO-GO or WEAK verdict is not a finding in the tested direction. Never write, from our phone data
  (level.*, contrast.*, loudness.*, niosh.*), that one section or direction is louder, quieter or noisier
  than another, or that it "ranks" above another, not even "in 10 of 11 trips". Write instead that the
  pre-registered loudness check was NO-GO (mean +0.54 dB, inside the recorder's compression), so our
  phone data do not rank the sections. A comparison of TfL's own figures is allowed only attributed to
  TfL ("TfL's in-cab figures in FOI-1231-2425 ...").
- When a list names a section without a direction field (the MQ 2022/1791 grinding list gives Central
  entries such as 'Bethnal Green to Liverpool Street' and 'Mile End to Bethnal Green': every Central
  entry of the December 2021-May 2022 list is written in westbound order, and no entry has a direction
  field), quote the name as given and say the list does not state a direction. Never infer that the
  other direction (Liverpool Street to Bethnal Green) was excluded, omitted or not ground.
- NIOSH readings are whole-trip readings for Liverpool Street to Mile End (including the Bethnal Green
  dwell), not for one section: say so wherever they appear, the answer line included.
- TfL's 2018 rail roughness plots (FOI-1730-1819) cover eastbound Bethnal Green to Mile End only
  (published 22 October 2018; the text we hold is figure captions). A bare year ("2018", "January 2018")
  counts as a number and must appear in a cited quote; a full date such as 22 October 2018 is allowed. For any other section say that no roughness data
  is held for that section, never that none exists at all.
- The answer line keeps the attribution of each claim it restates ("according to ...", "Elly Baker AM
  recounted ..."): never state as fact something a claim attributes to a speaker's account.
- Refer to TfL staff below Head level by role ("a TfL case officer"), not by name.
Prefer precise, short claims that answer the question over background."""

REVIEW_SYSTEM = """You are an independent reviewer for Tubesense. Every claim below already passed a
deterministic verifier: each quote is verbatim in its source and every number traces to a quote, a
statistic or a recomputed formula. Check only what that verifier cannot, against the full source text
and statistics given:
1. attribution: each statement is attributed to the document and speaker it actually comes from (a
   Mayor's written answer is not "TfL states"; an Assembly Member's account is hers); a claim citing two
   documents must not attribute both statements to one of them;
2. hedges and scope: "usually", "planned", "reported", "around" kept; a whole stretch not called a
   section; one direction not generalised to both; a plan not presented as done;
3. our statistics: a verdict's reason matches its detail (rule, justification, reversed_trips); a mean
   or count describes the right set (a mean over 11 trips is not "across 10 of 11 trips"); relative
   dBFS(A) never compared with dB; indicative readings stay indicative; positioning errors are against a
   yaw curve-edge reference, never "ground truth" or "true position";
   a NO-GO verdict's reason is the condition that triggers NO-GO in the rule (a reversed trip); other
   unmet GO conditions may be listed only as also unmet; NIOSH readings are whole-trip readings for
   Liverpool Street to Mile End and must be scoped so wherever they appear; TfL staff below Head level
   are referred to by role, not by name;
   a NO-GO or WEAK verdict is not a finding in the tested direction: flag any claim or answer that says,
   from our phone data (level.*, contrast.*, loudness.*, niosh.*), that one section or direction is louder,
   quieter or noisier than another or ranks above it (including "louder in 10 of 11 trips" offered as a
   result); the honest statement is that the pre-registered loudness check was NO-GO (mean +0.54 dB,
   inside the recorder's compression), so our phone data do not rank the sections;
4. lists without a direction: the MQ 2022/1791 grinding list names sections ('Bethnal Green to Liverpool
   Street', 'Mile End to Bethnal Green', ...; every Central entry of the December 2021-May 2022 list in
   westbound order) with no direction field. Flag any claim that turns a listed name into a direction or
   infers that the other direction (Liverpool Street to Bethnal Green) was excluded, omitted or not
   ground; the fix is to quote the name as given and say the list does not state a direction;
5. the answer line: restates the claims it lists faithfully, adds nothing, keeps the attribution of every
   restated statement ("according to ...", "recounted ...": an account is not presented as fact), and
   attributes each figure to the claim that actually gives it (TfL's in-cab figure is not our phone figure).
Report only errors that would mislead a careful reader (an Assembly Member's researcher), not style or
missing background. Give claim_id "answer" for the answer line. Return an empty list if there are none."""
REVIEW_NOTE = ("Argon review of framing (a model judgement, not deterministic): findings go back to the drafting "
               "agent for one revision, which the deterministic verifier checks again; a flagged claim returned "
               "unchanged is withheld")


# --------------------------------------------------------------------------- helpers
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def norm_q(q: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", q.lower().replace("'", ""))).strip()


def slug(q: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", norm_q(q)))[:48].strip("-") or "question"


def corpus_hash(chunks: dict) -> str:
    return hashlib.sha256("".join(c["hash"] for c in chunks.values()).encode()).hexdigest()[:16]


# --------------------------------------------------------------------------- retrieval
def _tokens(s: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 1 and w not in STOP]


def local_rank(q: str, chunks: dict, k: int = K_HITS) -> list[dict]:
    """BM25 (k1 1.5, b 0.75) over title + text: the offline stand-in for Atlas hybrid search."""
    docs = {cid: _tokens(c["title"] + " " + c["text"]) for cid, c in chunks.items()}
    avg = sum(map(len, docs.values())) / len(docs)
    df = Counter(w for toks in docs.values() for w in set(toks))
    qt, n, out = set(_tokens(q)), len(docs), []
    for cid, toks in docs.items():
        tf = Counter(toks)
        s = sum(math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5)) * tf[w] * 2.5 / (tf[w] + 1.5 * (0.25 + 0.75 * len(toks) / avg))
                for w in qt if w in tf)
        if s > 0:
            out.append((s, cid))
    return [{"chunk_id": cid, "score": round(s, 3)} for s, cid in sorted(out, reverse=True)[:k]]


def atlas_rank(q: str, k: int = K_HITS) -> list[dict]:
    from atlas.db import client
    client(timeout_ms=4000).admin.command("ping")   # fail fast (search.db() waits 10 s)
    from atlas.search import hybrid_search
    return [{"chunk_id": h["_id"], "score": round(h["score"], 5),
             "ranks": {d["inputPipelineName"]: d.get("rank") for d in h.get("scoreDetails", {}).get("details", [])}}
            for h in hybrid_search(q, k=k, with_text=False)]


def relevant_stats(q: str, stats: dict) -> list[str]:
    ql = q.lower()
    codes = {c for c, pat in STATIONS.items() if re.search(pat, ql) or re.search(rf"\b{c.lower()}\b", ql)}
    noise, pos = re.search(NOISE, ql), re.search(POSITION, ql)
    keys = []
    for k in stats:
        sec = re.match(r"(level|duration)\.\w+\.(\w+)-(\w+)$", k)
        if sec:
            ends = {sec.group(2), sec.group(3)}
            hit = ends <= codes if len(codes) > 1 else bool(ends & codes)
            if hit or (noise and not codes and sec.group(1) == "level"):
                keys.append(k)
        elif k.startswith(("contrast.", "loudness.", "niosh.")):
            if noise or codes & {"LST", "BTH", "MLE"}:
                keys.append(k)
        elif k.startswith(("positioning.", "v2.")) and pos:
            keys.append(k)
    return keys or list(stats)


def retrieve(q: str, chunks: dict, stats: dict, rank: Optional[Callable[[str], list[dict]]] = None) -> dict:
    t = time.time()
    try:
        hits, source, reason = (rank or atlas_rank)(q), "atlas hybrid ($rankFusion)", None
        missing = [h["chunk_id"] for h in hits if h["chunk_id"] not in chunks]
        hits = [h for h in hits if h["chunk_id"] in chunks]
        if missing:
            reason = f"Atlas returned chunks missing from the local corpus (skipped): {missing}"
        if not hits:
            hits, source, reason = local_rank(q, chunks), "local fallback", "Atlas hybrid search returned no hits"
    except (Exception, SystemExit) as e:  # noqa: BLE001  (offline / no URI)
        hits, source, reason = local_rank(q, chunks), "local fallback", f"Atlas unreachable: {type(e).__name__}"
    for h in hits:
        c = chunks[h["chunk_id"]]
        h.update({f: c.get(f) for f in ("title", "publisher", "doc_date", "page")})
    return {"source": source, "reason": reason, "hits": hits, "stats": relevant_stats(q, stats),
            "seconds": round(time.time() - t, 2)}


def build_prompt(q: str, ret: dict, chunks: dict, stats: dict) -> str:
    docs = {}
    for c in chunks.values():
        docs.setdefault(c["doc_id"], f"{c['doc_id']} | {c['title']} | {c['publisher']} | {c['doc_date']} | {c['reliability']}")
    sub = {h["chunk_id"]: chunks[h["chunk_id"]] for h in ret["hits"]}
    ranks = [{k: h[k] for k in ("chunk_id", "score", "ranks") if k in h} for h in ret["hits"]]
    return (f"QUESTION: {q}\n\n"
            f"RETRIEVAL ({ret['source']}, best first):\n{json.dumps(ranks)}\n\n"
            f"SECTION STATS (our measurements; cite by key):\n"
            f"{json.dumps({k: stats[k] for k in ret['stats']}, indent=1, default=float)}\n\n"
            f"CORPUS CATALOGUE (every document we hold; quote ONLY from the retrieved chunks below):\n"
            + "\n".join(docs.values()) +
            f"\n\nRETRIEVED CHUNKS ({len(sub)}, full text):\n{corpus_block(sub)}\n\nReturn the AskDraft JSON.")


# --------------------------------------------------------------------------- checks
_DATE = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3})[a-z]*\.?\s+(\d{4})\b")
_DB = re.compile(r"(?<![\w.])([-+−]?\d+(?:[.,]\d+)?)\s*(dB[A-Za-z]*|decibels?)", re.I)
_GARBLE = re.compile(r"\w*[a-z]{3}[A-Z][a-z]\w*")
_NUMWORD = re.compile(r"\b(?:ten|eleven|twelve|(?:thir|four|fif|six|seven|eigh|nine)teen|(?:twen|thir|for|fif|six|seven|eigh|nine)ty"
                      r"(?:[- ](?:one|two|three|four|five|six|seven|eight|nine))?|hundred|thousand)\b", re.I)
NIOSH_SCOPE = re.compile(r"mile end|\bMLE\b", re.I)
# Clause boundaries for the negation-aware checks: a hedge counts only inside the clause of the match.
_CLAUSE = re.compile(r"[;:!?]|\.(?!\d)|\b(?:while|whereas|but|although|though|however|yet|so|therefore|thus|hence|"
                     r"because|since)\b|,\s*and\b", re.I)
_HEDGE = (r"\b(?:cannot|can\s?not|no\s+evidence|not|never|whether|if|nor|neither|unable|unclear|without|"
          r"no\s+(?:data|measurement))\b|n['’]t\b")
_REPORTED = r"\b(?:reported|according\s+to|press|described|claimed|called)\b"
_RIDERS = r"(?:passengers?|riders?|travell?ers?|commuters?|people|the\s+public|users?|customers?)"
# Assertions the verifier's BANNED list does not cover; applied to the answer line only. Each entry is
# (pattern, why, hedge): when `hedge` is set, a match is allowed if that hedge occurs earlier in its clause
# (at most 80 characters back), so "cannot show whether corrugation has returned" passes and "corrugation
# has definitely returned" does not. hedge None = rejected even negated.
ANSWER_BANNED = [
    (r"\b(?:noise|corrugation|roar|ripples?)\s+(?:(?:has|have|had|is|are)\s+)?(?:\w+ly\s+)?"
     r"(?:returned|come\s+back|came\s+back|re-?formed|re-?appeared|recurred|back)\b",
     "no before/after measurement exists, so a return of noise or corrugation cannot be shown", _HEDGE),
    (r"\b(?:was|were|has|have|had)\s+never\s+(?:been\s+)?ground\b",
     "the documents we hold do not show that a section was never ground; say what they do and do not show", None),
    (rf"\b{_RIDERS}['’]?\s+(?:hearing|ears?)\s+(?:(?:is|was|are|were|has\s+been|have\s+been|being|will\s+be|gets?)\s+)?"
     r"(?:\w+ly\s+)?(?:harmed|damaged|hurt|injured|impaired|at\s+risk)\b|"
     rf"\b(?:harm|damag|hurt|injur|impair)\w*\s+(?:the\s+)?{_RIDERS}['’]?\s+(?:hearing|ears?)\b",
     "no passenger hearing-harm claim: HSE values apply to workers, and nothing here measures hearing", _HEDGE),
    (r"\b(?:caus|lead|result)\w*\s+(?:in\s+|to\s+)?(?:permanent\s+)?hearing\s+(?:damage|loss|harm)\b|"
     rf"\bhearing\s+(?:damage|loss|harm)\b.{{0,60}}\b{_RIDERS}\b",
     "no hearing-damage claim for riders: HSE values apply to workers, and nothing here measures hearing", _HEDGE),
    (r"\b(?:loudest|noisiest)\b(?:\s+[\w-]+){0,4}?\s+(?:on|of|in|across)\s+(?:the\s+)?(?:whole\s+|entire\s+)?"
     r"(?:tube|underground|london\s+underground|network)\b",
     "no network-wide superlative: nothing we hold ranks every section of the Tube (a press ranking must be "
     "attributed as reported)", rf"{_HEDGE}|{_REPORTED}"),
    (r"\bgrinding\s+(?:(?:has|had|have)\s+)?(?:\w+ly\s+)?(?:failed|did\s+not\s+work|didn['’]t\s+work|has\s+not\s+worked|"
     r"(?:was|is)\s+ineffective|(?:was|is)\s+not\s+working)\b|\bfail(?:ure|ed)\s+of\s+(?:the\s+)?(?:rail\s+)?grinding\b",
     "no before/after measurement exists, so whether grinding failed cannot be shown", _HEDGE),
]
# Rankings from our phone data: the pre-registered loudness check was NO-GO, so they are rejected unless the
# clause hedges them ("do not rank ... as louder") or attributes them to TfL / a press report only.
_RANK = re.compile(r"\b(?:louder|quieter|noisier|loudest|quietest|noisiest|less\s+noisy|more\s+noisy)\b|"
                   r"\b(?:higher|lower)\s+(?:(?:noise|sound)\s+)?(?:levels?|readings?)\s+than\b|\boutranks?\b|"
                   r"\branks?\s+(?:above|below|higher|lower|first)\b", re.I)
_RANK_HEDGE = re.compile(r"\b(?:do|does|did|can|could)\s*(?:not|n['’]t)\s+(?:rank|show|establish|tell)\b|"
                         r"\bcannot\s+(?:rank|show|establish|tell)\b|\bno\s+(?:ranking|evidence)\b|\bwhether\b|\bnor\b", re.I)
_PHONE_STATS = ("level.", "contrast.", "loudness.", "niosh.")
_PHONE_WORDS = re.compile(r"\b(?:our|we|us|phone|dBFS|recordings?|recorder|Voice\s+Memos|within-trip|placement-ok|"
                          r"Tubesense|NIOSH|app|trips?)\b", re.I)
_TFL_WORDS = re.compile(r"\b(?:TfL|FOI-\d+-\d+|in-cab|cab|Brixton\s+Buzz|JRPass)\b", re.I)


def _clause(text: str, start: int, end: int) -> tuple[str, str]:
    """(the clause text before the match, the whole clause) around text[start:end], split at _CLAUSE."""
    cuts = [m.end() for m in _CLAUSE.finditer(text, 0, start)]
    a = cuts[-1] if cuts else 0
    nxt = _CLAUSE.search(text, end)
    return text[a:start], text[a:nxt.start() if nxt else len(text)]


def banned_in_answer(text: str) -> list[str]:
    """ANSWER_BANNED with negation: a hedged match (cannot show whether ...) passes, a plain assertion does not."""
    out = []
    for pat, why, hedge in ANSWER_BANNED:
        for m in re.finditer(pat, text, re.I | re.S):
            before = _clause(text, m.start(), m.end())[0][-80:]
            if hedge is None or not re.search(hedge, before, re.I):
                out.append(f"banned: {why}")
                break
    return out


def phone_ranking(text: str, cited: list[dict]) -> list[str]:
    """A louder/quieter ranking in the answer that rests on our phone data. Phone context = a cited claim built
    on a phone statistic, or phone wording in the clause; a clause that names TfL (or the press report) and no
    phone wording is TfL's comparison and passes; a hedge before the ranking in its clause passes."""
    phone_cited = any(k.startswith(_PHONE_STATS) for c in cited for k in _claim_stats(c))
    for m in _RANK.finditer(text):
        before, clause = _clause(text, m.start(), m.end())
        if _RANK_HEDGE.search(before):
            continue
        phone_here = bool(_PHONE_WORDS.search(clause))
        if _TFL_WORDS.search(clause) and not phone_here:
            continue
        if phone_here or phone_cited:
            return [f"'{m.group()}': our pre-registered loudness check was NO-GO (one trip reversed; mean +0.54 dB, "
                    "inside the recorder's compression), so our phone data do not rank sections or directions; say "
                    "so (\"our phone data do not rank the sections\"), or attribute a comparison to TfL's own figures"]
    return []


@lru_cache(maxsize=1)
def _corpus_text() -> str:
    return " ".join(c["text"] for c in V.load_chunks().values())


def _canon_ref(ref: str) -> str:
    m = _DATE.fullmatch(ref.strip())
    return f"{int(m[1])} {m[2].lower()} {m[3]}" if m else re.sub(r"\s+", "", ref).upper()


def refs(text: str) -> set[str]:
    """Document references, question numbers and full dates in `text`, canonicalised for comparison."""
    return {_canon_ref(m.group()) for m in V._ID_PAT.finditer(text or "")}


def _allowed_numbers(text: str) -> list[float]:
    """Numbers a claim states: those V.numbers finds, plus the day and year of each full date in it (so the
    answer may say 'December 2022' when the claim says '9 December 2022')."""
    nums = [float(t) for t in V.numbers(text)]
    for m in _DATE.finditer(text or ""):
        nums += [float(m[1]), float(m[3])]
    return nums


def _same(tok: str, v: float) -> bool:   # sign-sensitive, unlike V._matches
    return abs(float(tok) - v) <= 0.5 * 10 ** -V._decimals(tok) + 1e-9


def _units(text: str) -> list[tuple[str, str]]:
    return [(m[1].replace("−", "-").replace(",", ""), m[2]) for m in _DB.finditer(text or "")]


def _niosh_values() -> list[float]:
    return [float(v["value"]) for k, v in V.load_stats().items() if k.startswith("niosh.") and isinstance(v.get("value"), (int, float))]


def _claim_stats(c: dict) -> set[str]:
    inputs = (c.get("formula") or {}).get("inputs") or []
    inputs = list(inputs.values()) if isinstance(inputs, dict) else inputs
    return set(c.get("stat_keys") or []) | {i["stat"] for i in inputs if isinstance(i, dict) and i.get("stat")}


def check_answer(text: str, ids: list[str], verified: dict[str, dict]) -> list[str]:
    """The answer may only restate verified claims: cited ids verified, BANNED / ANSWER_BANNED clean, no
    phone-data ranking, and every number (with its sign), reference and full date taken from those claims,
    with the dB unit they give."""
    reasons = []
    if not text.strip():
        reasons.append("empty answer")
    if "\n" in text.strip() or len(text) > ANSWER_MAX_CHARS:
        reasons.append(f"answer must be one line of at most {ANSWER_MAX_CHARS} characters")
    if not ids:
        reasons.append("answer_claim_ids is empty")
    bad_ids = [i for i in ids if i not in verified]
    if bad_ids:
        reasons.append(f"answer cites claims that did not pass the verifier: {bad_ids}")
    for pat, why in V.BANNED:   # the verifier's list: rejected even negated
        if re.search(pat, text, re.I | re.S):
            reasons.append(f"banned: {why}")
    reasons += banned_in_answer(text)
    cited = [verified[i] for i in ids if i in verified]
    reasons += phone_ranking(text, cited)
    ctext = " ".join(c["text"] for c in cited)
    known = ctext + " " + _corpus_text()   # CamelCase names that occur in the sources (MoniRail) are not garbling
    runs = [t for t in _GARBLE.findall(text) if t not in known]
    if runs:
        reasons.append(f"garbled text: words run together (a missing space): {runs[:3]}")
    if _NUMWORD.search(text):
        reasons.append("write numbers above nine as figures (spelled out they cannot be checked against the claims)")
    allowed = [a for c in cited for a in _allowed_numbers(c["text"])]
    extra = [t for t in V.numbers(text) if not any(_same(t, a) for a in allowed)]
    if extra:
        reasons.append(f"answer numbers (with sign) not in the text of the claims it cites: {extra}")
    have = refs(ctext)
    loose = sorted(r for r in refs(text) if r not in have)
    if loose:
        reasons.append(f"answer document references or dates not in the text of the claims it cites: {loose}")
    claim_units = _units(ctext)
    for tok, unit in _units(text):
        seen = [u for t, u in claim_units if _same(tok, float(t))]
        if seen and all(("FS" in u.upper()) != ("FS" in unit.upper()) for u in seen):
            reasons.append(f"{tok} {unit}: the claim it cites gives this figure in {seen[0]}, a different unit")
    nv = _niosh_values()
    if cited and (re.search(r"niosh", text, re.I) or any(k.startswith("niosh.") for c in cited for k in _claim_stats(c))
                  or any('.' in t and _same(t, v) for t in V.numbers(text) for v in nv)) and not NIOSH_SCOPE.search(text):
        reasons.append("NIOSH readings are whole trips from Liverpool Street to Mile End; the answer must say so")
    return reasons


def dedupe_ids(claims: list[dict]) -> list[dict]:
    seen = Counter()
    for c in claims:
        seen[c["id"]] += 1
        if seen[c["id"]] > 1:
            c["id"] = f"{c['id']}_{seen[c['id']]}"
    return claims


# --------------------------------------------------------------------------- storage
def _load(p: Path) -> Optional[dict]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _rel(p: Path) -> str:
    return p.relative_to(V.ROOT).as_posix() if p.is_relative_to(V.ROOT) else str(p)


def _path(file: str) -> Path:
    return Path(file) if Path(file).is_absolute() else V.ROOT / file


def list_cached() -> list[dict]:
    """Saved answers, newest first; when a question was asked more than once only its newest answer is listed."""
    out = []
    for p in ASKS.glob("*.json"):
        r = _load(p)
        if r and "question" in r and "verified_claims" in r:
            out.append({"question": r["question"], "ts": r["ts"], "file": _rel(p),
                        "n_verified": len(r["verified_claims"]), "n_dropped": len(r["dropped_claims"])})
    newest = {}
    for r in sorted(out, key=lambda r: (r["ts"], r["file"]), reverse=True):
        newest.setdefault(norm_q(r["question"]), r)
    return list(newest.values())


def save(res: dict) -> None:
    ASKS.mkdir(parents=True, exist_ok=True)
    stamp = res["ts"].replace("-", "").replace(":", "").replace("+0000", "Z")
    p = ASKS / f"{stamp}_{slug(res['question'])}.json"
    res["file"] = _rel(p)
    if not MIRROR:
        res["atlas"] = {"ok": False, "reason": "test run (TUBESENSE_POSTHOC_OUT set): Atlas not written"}
    else:
        try:
            from atlas.db import client
            doc = {"_id": p.stem, **{k: v for k, v in res.items() if k != "atlas"}}
            client(timeout_ms=4000)["tubesense"]["asks"].replace_one({"_id": p.stem}, doc, upsert=True)
            res["atlas"] = {"ok": True, "collection": "asks", "_id": p.stem}
        except (Exception, SystemExit) as e:  # noqa: BLE001
            res["atlas"] = {"ok": False, "reason": f"Atlas skipped: {type(e).__name__}"}
    p.write_text(json.dumps(res, indent=1, default=float), encoding="utf-8")


# --------------------------------------------------------------------------- ask
class AnswerFix(BaseModel):
    answer: str = Field(description="ONE sentence (at most 40 words) restating only the listed claims")
    answer_claim_ids: list[str] = Field(description="ids of the claims the answer restates")


ANSWER_SYSTEM = f"""You rewrite the one-sentence answer line of a Tubesense answer. Every claim given passed a
deterministic verifier; the previous answer line was rejected for the reasons given. Write ONE sentence (at
most 40 words) that directly answers the question and restates only the claims you list in
answer_claim_ids. Every number must appear in the text of a claim you list, with the same sign and the unit
that claim gives (dBFS(A) is not dB); every document reference and full date must appear there too. Keep the
attribution of each restated claim ("according to ...", "recounted ..."): never state as fact something a
claim attributes to a speaker's account. NIOSH readings are whole-trip readings for Liverpool Street to Mile
End: say so. Never use the words "prove",
"proves", "proven" or "conclusively", or the phrases "noise (has) returned", "noise has come back"; never say a
section "was never ground"; never say corrugation was measured, has returned or re-formed (say the evidence
cannot show whether it has), that passengers' hearing is harmed, that grinding failed, or that a section is the
loudest on the Tube. A NO-GO or WEAK verdict is not a finding in the tested direction: never write from our
phone data that one section or direction is louder or quieter than another; write that the pre-registered
loudness check was NO-GO (mean +0.54 dB, inside the recorder's compression), so our phone data do not rank the
sections. A comparison of TfL's own figures must be attributed to TfL. When a list names a section without a
direction (MQ 2022/1791), quote the name as given and say the list does not state a direction; never say the
other direction was excluded. Sections of the evidence: {", ".join(SECTIONS)}. Add nothing the claims do not say."""


NO_CLAIM = "No claim survived verification, so there is no answer to show."
NO_CLAIM_REVISION = "No claim can be shown (the revision did not complete)."


def _finalise_answer(draft: dict, verified: dict[str, dict], reasons: list[str], empty: str = NO_CLAIM) -> dict:
    ids = draft.get("answer_claim_ids") or []
    if not reasons:
        return {"text": draft["answer"].strip(), "claim_ids": ids, "source": "argon, checked against verified claims",
                "rejected_line": None}
    if not verified:
        return {"text": empty, "claim_ids": [], "source": "none",
                "rejected_line": {"text": draft.get("answer", ""), "reasons": reasons}}
    first = next((i for i in ids if i in verified), next(iter(verified)))
    return {"text": verified[first]["text"], "claim_ids": [first], "source": "verified claim text (fallback)",
            "rejected_line": {"text": draft.get("answer", ""), "reasons": reasons}}


def _timeout(e: Exception) -> bool:
    return isinstance(e, TimeoutError) or "timeout" in type(e).__name__.lower() or "timed out" in str(e).lower()


def _transient(e: Exception) -> bool:
    code = getattr(e, "code", None)
    return (isinstance(code, int) and code in (408, 429, 500, 502, 503, 504)) or isinstance(e, (TimeoutError, ConnectionError)) \
        or "timeout" in type(e).__name__.lower() or "timed out" in str(e).lower()


def _call(fn: Callable[[], object], deadline: float) -> tuple[str, dict]:
    """Run one Gemini call, retrying transient errors (429/5xx) after BACKOFF_S while the deadline allows; a
    timeout (REQUEST_TIMEOUT_MS spent) is retried once, and only if the retry could still finish in time."""
    tries = 0
    for wait in (*BACKOFF_S, None):
        try:
            r = fn()
            break
        except Exception as e:  # noqa: BLE001
            late = _timeout(e) and (tries >= 1 or time.time() + (wait or 0) + REQUEST_TIMEOUT_MS / 1000 > deadline)
            if wait is None or not _transient(e) or time.time() + wait > deadline or late:
                raise
            tries += 1
            time.sleep(wait)
    u = getattr(r, "usage_metadata", None)
    return r.text or "", {"prompt": getattr(u, "prompt_token_count", None), "output": getattr(u, "candidates_token_count", None),
                          "retries": tries}


def _send(chat, msg: str, deadline: float = float("inf")) -> tuple[str, dict]:
    return _call(lambda: chat.send_message(msg), deadline)


def _fix_msg(bad: list[dict], ans: list[str]) -> str:
    return ("The verifier rejected the items below. Fix each claim (exact quotes, supported numbers, allowed "
            "wording) or drop it. A bare year such as 2018 counts as a number and must appear in a quote; a "
            "full date such as 9 December 2022 is allowed. Keep every claim that passed exactly as it is, with its id; "
            "make the answer restate only claims that pass. Return the FULL corrected AskDraft JSON.\n"
            + json.dumps({"rejected_claims": bad, "answer_problems": ans}, indent=1))


def _review_msg(findings: list[dict], answer: str, ids: list[str]) -> str:
    return ("An independent review checked your verified claims against the full source texts and our statistics "
            "and found the framing problems below (things the verifier cannot check). Fix every claim a finding "
            "names (rewrite it so it is accurate, or drop it); a named claim returned unchanged will be withheld. "
            "Keep every other claim exactly as it is, with its id. Quotes stay verbatim and numbers supported. "
            f"The answer line currently in force is: {json.dumps(answer)} (restating {ids}); change it only if a "
            "finding names it or a claim it restates. Return the FULL corrected AskDraft JSON.\n"
            + json.dumps(findings, indent=1))


def _answer_msg(question: str, claims: list[dict], bad: str, ans: list[str]) -> str:
    keep = [{"id": c["id"], "type": c["type"], "text": c["text"]} for c in claims]
    return (f"QUESTION: {question}\n\nVERIFIED CLAIMS (these stay exactly as they are):\n{json.dumps(keep, indent=1)}\n\n"
            f"REJECTED ANSWER LINE: {json.dumps(bad)}\nWHY IT WAS REJECTED: {json.dumps(ans)}\n\n"
            "Return the AnswerFix JSON.")


def review_prompt(question: str, answer: str, ids: list[str], claims: list[dict], chunks: dict, stats: dict) -> str:
    cids, keys = [], []
    for c in claims:
        cids += [x["chunk_id"] for x in c.get("citations") or []]
        keys += c.get("stat_keys") or []
        for i in (c.get("formula") or {}).get("inputs") or []:
            cids += [i["chunk_id"]] if i.get("chunk_id") else []
            keys += [i["stat"]] if i.get("stat") else []
    src = {cid: chunks[cid] for cid in dict.fromkeys(cids) if cid in chunks}
    cited = {k: stats[k] for k in dict.fromkeys(keys) if k in stats}
    return (f"QUESTION: {question}\n\nANSWER LINE (restates {ids}): {answer}\n\n"
            f"CLAIMS (all passed the deterministic verifier):\n{json.dumps(claims, indent=1)}\n\n"
            f"CITED STATS (full entries):\n{json.dumps(cited, indent=1, default=float)}\n\n"
            f"CITED SOURCES (full chunk text):\n{corpus_block(src)}\n\nReturn the Review JSON.")


def _live(question: str, thinking: str, review: bool, emit: Callable[[dict], None], client=None,
          rank: Optional[Callable[[str], list[dict]]] = None) -> dict:
    """One live ask. `client` / `rank` replace the Gemini client and Atlas ranking (the offline selftest only)."""
    from google.genai import types
    t0, ts = time.time(), now_iso()
    deadline = t0 + TIME_BUDGET_S
    chunks, stats = V.load_chunks(), V.load_stats()
    emit({"type": "start", "question": question, "model": MODEL, "thinking": thinking, "ts": ts, "cached": False,
          "review": review})
    ret = retrieve(question, chunks, stats, rank)
    emit({"type": "retrieval", "source": ret["source"], "reason": ret["reason"], "seconds": ret["seconds"],
          "hits": ret["hits"], "stats": ret["stats"]})
    cl = client
    if cl is None:
        from google import genai
        load_env()
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set (.env)")
        cl = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS))

    def cfg(system: str, schema) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json",
                                           response_schema=schema,
                                           thinking_config=types.ThinkingConfig(thinking_level=thinking))
    chat = cl.chats.create(model=MODEL, config=cfg(SYSTEM, AskDraft))
    rounds, rejected = [], {}

    def ok_claims(rd: dict) -> dict[str, dict]:
        return {c["id"]: c for c, v in zip(rd["claims"], rd["verdicts"]) if v["ok"]}

    def step(trigger: str, msg: str = "", pin: Optional[list[dict]] = None, bad_answer: str = "",
             why: list[str] = ()) -> tuple[list[dict], list[str]]:
        """One Argon round. `pin` = claims carried over unchanged (answer-only fix: only the answer line is asked for)."""
        rnd, t = len(rounds), time.time()
        if pin is None:
            text, usage = _send(chat, msg, deadline)
            parse = AskDraft
        else:
            text, usage = _call(lambda: cl.models.generate_content(
                model=MODEL, contents=_answer_msg(question, pin, bad_answer, list(why)), config=cfg(ANSWER_SYSTEM, AnswerFix)), deadline)
            parse = AnswerFix
        try:
            draft = parse.model_validate_json(text).model_dump()
            if pin is not None:
                draft["claims"] = json.loads(json.dumps(pin))
        except ValidationError as e:
            draft = {"answer": "", "answer_claim_ids": [], "claims": pin or [], "parse_error": str(e)[:500]}
        claims, sec = dedupe_ids(draft["claims"]), round(time.time() - t, 1)
        emit({"type": "draft", "round": rnd, "trigger": trigger, "claims": claims, "answer": draft["answer"],
              "answer_claim_ids": draft["answer_claim_ids"], "seconds": sec})
        verdicts = V.verify_all(P.for_verifier(claims))
        verified = {c["id"]: c for c, v in zip(claims, verdicts) if v["ok"]}
        ans = check_answer(draft["answer"], draft["answer_claim_ids"], verified)
        if draft.get("parse_error"):
            ans = [f"reply did not match the schema: {draft['parse_error']}"]
        emit({"type": "verdicts", "round": rnd, "results": verdicts, "answer_check": {"ok": not ans, "reasons": ans}})
        rounds.append({"round": rnd, "trigger": trigger, "claims": claims, "verdicts": verdicts, "seconds": sec,
                       "usage": usage, "answer": draft["answer"], "answer_claim_ids": draft["answer_claim_ids"],
                       "answer_check": {"ok": not ans, "reasons": ans}, "parse_error": draft.get("parse_error")})
        bad = [{"id": c["id"], "text": c["text"], "reasons": v["reasons"]} for c, v in zip(claims, verdicts) if not v["ok"]]
        rejected.update({c["id"]: {"claim": c, "reasons": v["reasons"], "round": rnd}
                         for c, v in zip(claims, verdicts) if not v["ok"]})
        return bad, ans

    def fix(bad: list[dict], ans: list[str]) -> tuple[list[dict], list[str]]:
        last = rounds[-1]
        if not bad and last["claims"]:
            return step("answer-fix", pin=last["claims"], bad_answer=last["answer"], why=ans)
        return step("verifier", _fix_msg(bad, ans))

    def review_of(rd: dict, claims: dict[str, dict]) -> dict:
        t = time.time()
        try:
            rchat = cl.chats.create(model=MODEL, config=cfg(REVIEW_SYSTEM, Review))
            text, usage = _send(rchat, review_prompt(question, rd["answer"], rd["answer_claim_ids"], list(claims.values()),
                                                    chunks, stats), deadline)
            return {"ok": True, "findings": Review.model_validate_json(text).model_dump()["findings"],
                    "seconds": round(time.time() - t, 1), "usage": usage, "reviewed_round": rd["round"]}
        except Exception as e:  # noqa: BLE001  (a failed review is recorded, never hidden)
            return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300], "findings": [],
                    "seconds": round(time.time() - t, 1), "reviewed_round": rd["round"]}

    bad, ans = step("initial", build_prompt(question, ret, chunks, stats))   # a failure here is a plain error: nothing to save
    rev = rev2 = seen = None
    held, ans_unresolved, incomplete = {}, [], None
    n_pre, resolved = 0, False

    def hold_flagged(why: str) -> None:
        """Until the revision is known, everything the review flagged stays withheld (a failed revision never shows it as reviewed)."""
        for f in rev["findings"]:
            if f["claim_id"] == "answer":
                ans_unresolved.append(f"review finding not resolved ({why}): {f['problem']}")
            else:
                held[f["claim_id"]] = {**f, "problem": f"{why}: {f['problem']}"}

    def resolve_review(rd: dict) -> None:
        """Replace the provisional holds by what round `rd` shows: flagged text left unchanged stays withheld."""
        held.clear()
        ans_unresolved.clear()
        before = {c["id"]: c["text"] for c in seen["claims"]}
        for f in rev["findings"]:
            if f["claim_id"] == "answer":
                if rd["answer"] == seen["answer"]:
                    ans_unresolved.append(f"review finding not resolved: {f['problem']}")
                continue
            for c in rd["claims"]:   # unchanged text, under the same or a new id
                if f["claim_id"] in before and c["text"] == before[f["claim_id"]]:
                    held[c["id"]] = f

    def usable() -> Optional[dict]:
        """The latest round whose reply parsed: a round that did not match the schema has no claims of its own."""
        return next((rd for rd in reversed(rounds) if not rd.get("parse_error")), None)

    def judge_revision() -> None:
        """The revision counts only if a round from it parsed; otherwise the provisional holds stay (flagged = withheld)."""
        b = usable()
        if b is not None and b["round"] >= n_pre:
            resolve_review(b)
    try:
        while (bad or ans) and len(rounds) <= MAX_ROUNDS:
            if time.time() > deadline:
                incomplete = f"time budget of {TIME_BUDGET_S} s used up before the fix rounds finished"
                break
            bad, ans = fix(bad, ans)
        base = usable()
        if review and base and ok_claims(base) and not incomplete:
            if time.time() > deadline:
                incomplete = f"time budget of {TIME_BUDGET_S} s used up before the review"
            else:
                seen = base
                rev = review_of(seen, ok_claims(seen))
                emit({"type": "review", "model": MODEL, "note": REVIEW_NOTE, **rev})
        if rev and rev["findings"]:
            n_pre = len(rounds)
            hold_flagged("revision did not complete")
            bad, ans = step("review", _review_msg(rev["findings"], seen["answer"], seen["answer_claim_ids"]))
            if bad or ans:
                bad, ans = fix(bad, ans)
            judge_revision()
            resolved = True
            last = usable()
            reviewed = {i: c["text"] for i, c in ok_claims(seen).items()}
            fresh = [i for i, c in ok_claims(last).items() if i not in held and reviewed.get(i) != c["text"]]
            if last["round"] >= n_pre and (fresh or last["answer"] != seen["answer"]) and ok_claims(last)                     and time.time() < deadline:
                rev2 = review_of(last, ok_claims(last))   # what the revision added or changed gets reviewed too
                emit({"type": "final_review", "model": MODEL, "note": REVIEW_NOTE + " (final check of the revision; no further revision)",
                      "final_check": True, **rev2})
                for f in rev2["findings"]:
                    if f["claim_id"] == "answer":
                        ans_unresolved.append(f"final review finding: {f['problem']}")
                    elif f["claim_id"] in ok_claims(last):
                        held.setdefault(f["claim_id"], {**f, "problem": f"final review, no further revision: {f['problem']}"})
    except Exception as e:  # noqa: BLE001  (keep what was verified so far; flag it)
        incomplete = f"{type(e).__name__}: {e}"[:300]
        if rev and rev["findings"] and not resolved and n_pre > 0:
            judge_revision()   # a parsed revision round exists (only its fix failed): judge it as usual
    final = usable() or rounds[-1]
    revision_failed = bool(rev and rev["findings"] and n_pre > 0 and final["round"] < n_pre)
    if final is not rounds[-1]:
        skipped = [rd["round"] for rd in rounds if rd["round"] > final["round"]]
        why = (f"round(s) {skipped} did not return a reply matching the schema and no later round completed; "
               f"showing the claims verified in round {final['round']}"
               + (" minus every claim the review flagged (the revision did not complete)" if revision_failed else ""))
        incomplete = f"{incomplete}; {why}" if incomplete else why
    elif usable() is None:
        why = "no round returned a reply matching the schema"
        incomplete = f"{incomplete}; {why}" if incomplete else why
    final_ok = ok_claims(final)
    verified = {i: c for i, c in final_ok.items() if i not in held}
    final_ids = {c["id"] for c in final["claims"]}
    dropped = [{"claim": c, "reasons": v["reasons"], "stage": f"failed the verifier in final round {final['round']}"}
               for c, v in zip(final["claims"], final["verdicts"]) if not v["ok"]]
    dropped += [{"claim": final_ok[i], "reasons": [f"review finding not resolved: {f['problem']}"],
                 "stage": "withheld after review"} for i, f in held.items() if i in final_ok]
    dropped += [{"claim": x["claim"], "reasons": x["reasons"], "stage": f"rejected in round {x['round']}, withdrawn by Argon"}
                for i, x in rejected.items() if i not in final_ids]
    listed = {d["claim"]["id"] for d in dropped}
    for rd in reversed(rounds):   # verified in another round, absent from the final one (latest round first)
        if rd is final:
            continue
        for i, c in ok_claims(rd).items():
            if i in final_ids or i in listed:
                continue
            why = [f"review: {f['problem']}" for f in (rev or {}).get("findings", [])
                   if f["claim_id"] == i and rd["round"] == rev["reviewed_round"]]
            stage = ("flagged by review, withdrawn by Argon" if why else "omitted by Argon in the review revision"
                     if rev and rd["round"] == rev["reviewed_round"] else f"verified in round {rd['round']}, omitted by Argon later")
            dropped.append({"claim": c, "reasons": why or ["verified, then left out of a later round without a recorded reason"],
                            "stage": stage})
            listed.add(i)
    reasons = final["answer_check"]["reasons"] + ans_unresolved
    reasons += [f"answer restates a withheld claim: {i}" for i in final["answer_claim_ids"] if i in held]
    answer = _finalise_answer({"answer": final["answer"], "answer_claim_ids": final["answer_claim_ids"]},
                              verified, reasons, NO_CLAIM_REVISION if revision_failed else NO_CLAIM)
    reviewed_ids = {i: c["text"] for i, c in ok_claims(seen).items()} if seen else {}
    final_check = bool(rev2 and rev2["ok"])

    def status(i: str) -> str:
        if not rev:
            return "not reviewed (review skipped)" if not review else "not reviewed (no review ran)"
        if not rev["ok"]:
            return "not reviewed (review call failed)"
        if reviewed_ids.get(i) == verified[i]["text"]:
            return "reviewed"
        return "reviewed in the final check of the revision" if final_check else "added or changed after review, not reviewed"
    changed = bool(seen) and final["answer"] != seen["answer"]
    a_status = ("verified claim text (not Argon's line)" if not answer["source"].startswith("argon")
                else "not reviewed" if not rev or not rev["ok"] else "reviewed" if not changed
                else "reviewed in the final check of the revision" if final_check else "changed after review, not reviewed")
    answer["review"] = a_status
    return {"question": question, "norm_question": norm_q(question), "ts": ts, "model": MODEL, "thinking": thinking,
            "elapsed_s": round(time.time() - t0, 1), "cached": False, "kind": "post-hoc live ask",
            "incomplete": incomplete, "answer": answer, "verified_claims": list(verified.values()),
            "review_status": {i: status(i) for i in verified}, "dropped_claims": dropped,
            "rounds": rounds, "review": rev, "final_review": rev2, "retrieval": ret, "verifier_sha": V.VERIFIER_SHA,
            "corpus_hash": corpus_hash(chunks), "stats_keys": len(stats)}


def _codes(q: str) -> set[str]:
    ql = q.lower()
    return {c for c, pat in STATIONS.items() if re.search(pat, ql) or re.search(rf"\b{c.lower()}\b", ql)}


def _content(q: str) -> set[str]:
    """Question words once station names and filler are removed, so shared station names alone never make a match."""
    ql = q.lower()
    for pat in STATIONS.values():
        ql = re.sub(pat, " ", ql)
    return {w for w in _tokens(ql) if w not in {"central", "line", "between", "tfl", "street", "green", "end"}}


def _nearest(question: str, items: list[dict]) -> tuple[Optional[dict], float]:
    """Closest saved question: the same stations mentioned and the remaining words overlap (Jaccard) >= NEAR_JACCARD."""
    qt, best = _content(question), (None, 0.0)
    for c in items:
        ct = _content(c["question"])
        j = len(qt & ct) / len(qt | ct) if qt | ct else 0.0
        if j >= NEAR_JACCARD and _codes(c["question"]) == _codes(question) and j > best[1]:
            best = (c, j)
    return best


def _cached(question: str, emit: Callable[[dict], None]) -> dict:
    nq = norm_q(question)
    items = list_cached()
    match = next((c for c in items if norm_q(c["question"]) == nq), None)
    exact, score = match is not None, 1.0
    if match is None:
        match, score = _nearest(question, items)
    if match is None:
        raise LookupError(f"no saved answer close enough to this question in {ASKS}")
    return _replay(match["file"], emit, question, exact, score)


def replay(file: str, emit: Optional[Callable[[dict], None]] = None) -> dict:
    """Serve one saved answer by file name (any file in asks/, superseded ones included) as a CACHED replay:
    labelled with its original ts, the verifier re-run on its claims now, the answer line re-gated now, no
    Gemini and no Atlas. Raises LookupError for a name that is not a saved answer. This, not the raw file, is
    what a UI must show: the raw file says cached false."""
    p = ASKS / Path(file).name
    res = _load(p) if p.is_file() else None
    if not res or "verified_claims" not in res:
        raise LookupError(f"no saved answer named {Path(file).name} in {ASKS}")
    return _replay(_rel(p), emit or (lambda e: None), res["question"], True, 1.0)


def _replay(file: str, emit: Callable[[dict], None], question: str, exact: bool, score: float) -> dict:
    t0 = time.time()
    res = _load(_path(file))
    newer = next((c for c in list_cached() if norm_q(c["question"]) == norm_q(res["question"])), None)
    superseded = newer["file"] if newer and newer["file"] != file and (newer["ts"], newer["file"]) > (res["ts"], file) else None
    label = (f"CACHED answer from {res['ts']} (not a live Argon call)" if exact else
             f"CACHED answer to a different saved question: \"{res['question']}\" ({res['ts']}); not a live Argon call "
             f"and not an answer to what was asked")
    if superseded:
        label += f"; a newer saved answer to this question exists ({superseded})"
    emit({"type": "start", "question": res["question"], "model": res["model"], "thinking": res["thinking"],
          "ts": res["ts"], "cached": True, "label": label, "review": bool(res.get("review")), "exact_match": exact,
          "asked_question": question})
    r = res["retrieval"]
    emit({"type": "retrieval", "source": r["source"], "reason": r.get("reason"), "seconds": r.get("seconds"),
          "hits": r["hits"], "stats": r["stats"], "cached": True})
    rev, rev2 = res.get("review"), res.get("final_review")
    for rd in res["rounds"]:
        emit({"type": "draft", "round": rd["round"], "trigger": rd.get("trigger"), "claims": rd["claims"],
              "answer": rd.get("answer"), "answer_claim_ids": rd.get("answer_claim_ids"), "seconds": rd["seconds"],
              "cached": True})
        emit({"type": "verdicts", "round": rd["round"], "results": rd["verdicts"],
              "answer_check": rd.get("answer_check"), "cached": True})
        if rev and rev.get("reviewed_round") == rd["round"]:
            emit({"type": "review", "model": res["model"], "note": REVIEW_NOTE, **rev, "cached": True})
    if rev2:
        emit({"type": "final_review", "model": res["model"], "note": REVIEW_NOTE + " (final check of the revision; no further revision)",
              "final_check": True, **rev2, "cached": True})
    checks = V.verify_all(P.for_verifier(res["verified_claims"]))
    still = {c["id"]: c for c, v in zip(res["verified_claims"], checks) if v["ok"]}
    newly = [{"claim": c, "reasons": v["reasons"], "stage": "failed re-verification now"}
             for c, v in zip(res["verified_claims"], checks) if not v["ok"]]
    a = res["answer"]
    a_reasons = check_answer(a["text"], a["claim_ids"], still) if a["source"].startswith("argon") else \
        ([] if all(i in still for i in a["claim_ids"]) else ["the claim shown as the answer failed re-verification"])
    if a_reasons:
        a = _finalise_answer({"answer": a["text"], "answer_claim_ids": a["claim_ids"]}, still, a_reasons)
        a["review"] = "the saved answer line fails the current answer gate; shown verified claim text instead"
    res.update({"cached": True, "label": label, "served_at": now_iso(), "replay_s": round(time.time() - t0, 3),
                "kind": "post-hoc cached replay (originally a live ask)", "original_atlas": res.pop("atlas", None),
                "exact_match": exact, "asked_question": question, "match_score": round(score, 2),
                "file": file, "superseded_by": superseded, "answer": a, "verified_claims": list(still.values()),
                "dropped_claims": res["dropped_claims"] + newly,
                "review_status": {i: v for i, v in (res.get("review_status") or {}).items() if i in still},
                "reverified": {"ts": now_iso(), "verifier_sha": V.VERIFIER_SHA,
                               "same_verifier": res.get("verifier_sha") == V.VERIFIER_SHA,
                               "same_corpus": res.get("corpus_hash") == corpus_hash(V.load_chunks()),
                               "all_ok": not newly and not a_reasons, "results": checks}})
    return res


def ask(question: str, *, thinking: str = THINKING, on_event: Optional[Callable[[dict], None]] = None,
        cached: bool = False, review: bool = True, file: Optional[str] = None) -> dict:
    """Answer `question` with verified claims only. Events go to on_event in order; returns the result dict.
    review=False skips the Argon framing review (faster; the deterministic verifier loop always runs).
    Argon runs at thinking medium only (owner requirement); any other level is an error."""
    emit = on_event or (lambda e: None)
    try:
        if thinking != THINKING:
            raise ValueError(f"thinking must be {THINKING!r} (owner requirement), not {thinking!r}")
        if file:
            res = replay(file, emit)
        elif cached:
            res = _cached(question, emit)
        else:
            res = _live(question, thinking, review, emit)
            save(res)
    except Exception as e:  # noqa: BLE001  (surface every failure as an event, never a fake answer)
        msg = f"{type(e).__name__}: {e}"[:500]
        emit({"type": "error", "message": msg})
        return {"question": question, "cached": cached, "error": msg}
    emit({"type": "final", "cached": bool(res.get("cached")), "incomplete": res.get("incomplete"), "result": res})
    return res


# --------------------------------------------------------------------------- selftest (offline)
_ST_TFL = {"id": "A1", "type": "sourced", "section": "tfl_data", "citations": [], "stat_keys": [],
           "text": "In FOI-1231-2425, a TfL case officer reported 87 dB LAeq eastbound on Liverpool Street to Bethnal "
                   "Green, measured in the cab on 24 June 2024."}
_ST_PHONE = {"id": "A2", "type": "measured", "section": "our_measurements", "citations": [],
             "stat_keys": ["loudness.verdict", "contrast.LST-BTH_minus_BTH-MLE"],
             "text": "The pre-registered loudness check comparing Liverpool Street to Bethnal Green with Bethnal Green to "
                     "Mile End was NO-GO: one of 11 trips went the other way (R10_WB_MLE-LST, -0.13 dB) and the mean "
                     "within-trip difference was +0.54 dB."}
GATE_CASES = [   # (answer, cited claim ids, want pass, note)
    ("TfL measured 87 dB LAeq and corrugation has definitely returned.", ["A1"], False, "corrugation returned"),
    ("TfL measured 87 dB LAeq in the cab, but the documents cannot show whether corrugation has returned.", ["A1"], True,
     "hedged: corrugation"),
    ("TfL measured 87 dB LAeq in the cab, so passengers' hearing is harmed.", ["A1"], False, "passengers' hearing harmed"),
    ("TfL measured 87 dB LAeq in the cab, which cannot show whether passengers' hearing is harmed.", ["A1"], True,
     "hedged: hearing harmed"),
    ("TfL measured 87 dB LAeq, a level that causes hearing damage to riders.", ["A1"], False, "hearing damage to riders"),
    ("TfL measured 87 dB LAeq in the cab, but the documents cannot show whether it causes hearing damage to riders.",
     ["A1"], True, "hedged: hearing damage"),
    ("TfL measured 87 dB LAeq, the loudest section on the whole Tube.", ["A1"], False, "superlative"),
    ("TfL measured 87 dB LAeq in the cab, but no document we hold shows whether it is the loudest section on the whole Tube.",
     ["A1"], True, "hedged: superlative"),
    ("TfL measured 87 dB LAeq, so grinding failed.", ["A1"], False, "grinding failed"),
    ("TfL measured 87 dB LAeq in the cab, which cannot show whether grinding failed.", ["A1"], True, "hedged: grinding"),
    ("TfL measured 87 dB LAeq in the cab, which does not prove that grinding failed.", ["A1"], False,
     "verifier BANNED stays strict even negated"),
    ("Liverpool Street to Bethnal Green is louder than Bethnal Green to Mile End, by +0.54 dB on average.", ["A2"], False,
     "phone ranking (cited phone stat)"),
    ("Our phone recordings found Liverpool Street to Bethnal Green louder than Bethnal Green to Mile End, although the "
     "check was NO-GO.", ["A2"], False, "phone ranking with NO-GO tacked on"),
    ("In our recordings Liverpool Street to Bethnal Green was louder than Bethnal Green to Mile End.", ["A1"], False,
     "phone ranking (phone wording, TfL claim cited)"),
    ("TfL measured 87 dB LAeq, and our phone data also found Liverpool Street to Bethnal Green louder.", ["A1", "A2"], False,
     "phone ranking after a TfL clause"),
    ("The pre-registered loudness check was NO-GO (mean +0.54 dB, inside the recorder's compression), so our phone data "
     "do not rank the sections.", ["A2"], True, "NO-GO stated honestly"),
    ("Our phone data do not rank Liverpool Street to Bethnal Green as louder than Bethnal Green to Mile End: the "
     "pre-registered check was NO-GO with a mean of +0.54 dB.", ["A2"], True, "hedged ranking"),
    ("According to TfL's FOI-1231-2425, Liverpool Street to Bethnal Green was louder in the cab at 87 dB LAeq; our "
     "pre-registered phone check was NO-GO (mean +0.54 dB), so our phone data do not rank the sections.", ["A1", "A2"], True,
     "TfL comparison attributed to TfL"),
]


class _StResp:
    def __init__(self, text: str):
        self.text, self.usage_metadata = text, None


class _StChat:
    def __init__(self, script: list):
        self.script = script

    def send_message(self, msg: str):
        r = self.script.pop(0)
        if isinstance(r, Exception):
            raise r
        return _StResp(r)


class _StClient:
    """Scripted stand-in for genai.Client: the drafting chat and answer fixes pop `main`, review chats pop `review`."""
    def __init__(self, main: list, review: list):
        from types import SimpleNamespace
        self.main, self.review = main, review
        self.chats = SimpleNamespace(create=lambda model, config: _StChat(
            self.review if config.system_instruction == REVIEW_SYSTEM else self.main))
        self.models = SimpleNamespace(generate_content=lambda **kw: _StChat(self.main).send_message(""))


def _st_offline(q: str) -> list[dict]:
    raise ConnectionError("selftest: offline")


def _st_loop() -> list[tuple[bool, str]]:
    a1 = {"id": "A1", "section": "our_measurements", "type": "measured", "citations": [], "formula": None,
          "stat_keys": ["loudness.verdict"], "text": "Our pre-registered loudness check gave a NO-GO verdict."}
    a2 = {"id": "A2", "section": "our_measurements", "type": "measured", "citations": [], "formula": None,
          "stat_keys": ["contrast.LST-BTH_minus_BTH-MLE"],
          "text": "Across 11 placement-ok trips the mean within-trip difference was 0.54 dB."}
    line = "Our pre-registered loudness check gave a NO-GO verdict, so our phone data do not rank the sections."
    d0 = json.dumps({"answer": line, "answer_claim_ids": ["A1"], "claims": [a1, a2]})
    d1 = json.dumps({"answer": line, "answer_claim_ids": ["A1"], "claims": [a1]})

    def flags(*ids: str) -> str:
        return json.dumps({"findings": [{"claim_id": i, "problem": "selftest finding", "fix": "drop"} for i in ids]})

    def run(main: list, review: list) -> dict:
        return _live("selftest: is Liverpool Street to Bethnal Green louder?", THINKING, True, lambda e: None,
                     client=_StClient(main, review), rank=_st_offline)

    def ids(r: dict) -> list[str]:
        return [c["id"] for c in r["verified_claims"]]
    out = []
    r = run([d0, "not json", RuntimeError("fix call failed")], [flags("A2")])
    out.append((bool(r["incomplete"]) and ids(r) == ["A1"] and r["answer"]["text"] == line
                and any(d["claim"]["id"] == "A2" and d["stage"] == "withheld after review" for d in r["dropped_claims"]),
                f"revision unparseable, then its fix raises: reviewed A1 shown, flagged A2 withheld, incomplete "
                f"({ids(r)}; {r['incomplete']!r:.100})"))
    r = run([d0, "not json", "still not json"], [flags("A2")])
    out.append((bool(r["incomplete"]) and ids(r) == ["A1"],
                f"revision and its fix both unparseable: A1 shown, A2 withheld, incomplete ({ids(r)})"))
    r = run([d0, "not json", RuntimeError("fix call failed")], [flags("A1", "A2")])
    out.append((bool(r["incomplete"]) and not r["verified_claims"] and r["answer"]["text"] == NO_CLAIM_REVISION,
                f"everything flagged and the revision fails: {r['answer']['text']!r}"))
    r = run([d0, d1], [flags("A2")])
    out.append((not r["incomplete"] and ids(r) == ["A1"] and r["answer"]["text"] == line,
                f"control: the revision parses and drops A2: complete ({ids(r)}, incomplete={r['incomplete']})"))
    return out


def selftest() -> int:
    """Offline (no Gemini, no Atlas): answer-gate cases and the revision-failure paths of the live loop."""
    verified = {c["id"]: c for c in (_ST_TFL, _ST_PHONE)}
    pre = V.verify_all(P.for_verifier([_ST_PHONE]))[0]
    results = [(pre["ok"], f"fixture A2 passes the verifier {pre['reasons'][:1]}")]
    for text, cids, want, note in GATE_CASES:
        reasons = check_answer(text, cids, verified)
        results.append(((not reasons) == want, f"want={'pass' if want else 'fail'} got={'fail' if reasons else 'pass'} "
                                               f"[{note}] {text[:70]}" + (f"\n      {reasons[0][:150]}" if reasons else "")))
    results += _st_loop()
    bad = 0
    for ok, msg in results:
        bad += not ok
        print(f"{'ok ' if ok else 'BAD'} {msg}")
    print(f"selftest: {len(results) - bad}/{len(results)} as expected")
    return bad


# --------------------------------------------------------------------------- CLI
def _print_event(e: dict) -> None:
    t = e["type"]
    if t == "start":
        print(f"[{'CACHED ' + e['ts'] if e.get('cached') else 'LIVE'}] {e['question']}  ({e['model']}, thinking {e['thinking']})")
        if e.get("label") and not e.get("exact_match", True):
            print(f"  {e['label']}")
    elif t == "retrieval":
        print(f"retrieval: {e['source']}{' - ' + e['reason'] if e.get('reason') else ''}; "
              f"{[h['chunk_id'] for h in e['hits']]}; {len(e['stats'])} stats")
    elif t == "draft":
        print(f"round {e['round']} ({e.get('trigger')}): {len(e['claims'])} claims ({e['seconds']} s)")
    elif t == "review":
        print(f"review ({e['seconds']} s): " + (f"{len(e['findings'])} finding(s)" if e["ok"] else f"failed: {e['error']}"))
        for f in e["findings"]:
            print(f"  ? {f['claim_id']}: {f['problem'][:200]}")
    elif t == "final_review":
        print(f"final check of the revision ({e['seconds']} s): " + (f"{len(e['findings'])} finding(s)" if e["ok"] else f"failed: {e['error']}"))
        for f in e["findings"]:
            print(f"  ? {f['claim_id']}: {f['problem'][:200]}")
    elif t == "verdicts":
        for v in e["results"]:
            if not v["ok"]:
                print(f"  x {v['id']}: {'; '.join(v['reasons'])[:220]}")
        if e.get("answer_check") and not e["answer_check"]["ok"]:
            print(f"  x answer: {'; '.join(e['answer_check']['reasons'])[:220]}")
    elif t == "error":
        print(f"ERROR {e['message']}")
    elif t == "final":
        r = e["result"]
        if r.get("incomplete"):
            print(f"\nINCOMPLETE (partial record saved): {r['incomplete']}")
        note = "Argon's one-line restatement, gated in code; evidence = the claims below" if r["answer"]["source"].startswith("argon") \
            else "evidence = the claims below"
        print(f"\nANSWER ({r['answer']['source']}; {note}): {r['answer']['text']}")
        for c in r["verified_claims"]:
            print(f"  [{c['type']}, {c['id']}, {c['section']}; {r.get('review_status', {}).get(c['id'], 'review status not recorded')}] {c['text']}")
        for d in r["dropped_claims"]:
            print(f"  DROPPED {d['claim']['id']} ({d['stage']}): {'; '.join(d['reasons'])[:160]}")
        extra = f", replay {r['replay_s']} s, re-verified ok={r['reverified']['all_ok']}" if r.get("cached") else \
            f", atlas {r['atlas']}"
        print(f"verified {len(r['verified_claims'])}, dropped {len(r['dropped_claims'])}, {r['elapsed_s']} s{extra} -> {r['file']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="*")
    ap.add_argument("--cached", action="store_true", help="serve the most recent saved answer (no Gemini, no Atlas)")
    ap.add_argument("--jsonl", action="store_true", help="print one JSON event per line")
    ap.add_argument("--thinking", default=THINKING, choices=(THINKING,), help="Argon thinking level (medium only)")
    ap.add_argument("--list", action="store_true", help="list saved answers")
    ap.add_argument("--file", help="replay one saved answer by file name (CACHED, re-verified; no Gemini, no Atlas)")
    ap.add_argument("--no-review", action="store_true", help="skip the Argon framing review (verifier loop still runs)")
    ap.add_argument("--selftest", action="store_true", help="offline: answer-gate cases and the revision-failure paths")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(1 if selftest() else 0)
    if a.list:
        for c in list_cached():
            print(json.dumps(c))
        return
    if not a.question and not a.file:
        ap.error("a question (or --file) is required")
    out = (lambda e: print(json.dumps(e, default=float), flush=True)) if a.jsonl else _print_event
    res = ask(" ".join(a.question), thinking=a.thinking, on_event=out, cached=a.cached, review=not a.no_review, file=a.file)
    sys.exit(1 if "error" in res else 0)


if __name__ == "__main__":
    main()
