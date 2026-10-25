"""Argon ride report: a plain-English account of one recorded ride, with every number bound to a stat field BY SCHEMA.

Post-hoc (added 25 Oct, after prereg-24oct). gemini-4-argon (thinking medium, structured output) reads ONLY the
ride's derived statistics (agent.ride_stats: QC flags, sync quality, sections, tracker results, relative noise
levels; no audio, no sensor arrays, no notes) and writes 4-8 one-sentence claims. Each claim declares, as data,
what its numbers are; the code then checks the declaration against the stored stats and against the sentence:

  claim = {id, text, type: measured|inference, qualifiers: [lower_bound|approximate|relative_level|
           excluded_from_averages|stress_test|single_ride], cites: [stat_key],
           facts: [{stat_key, field, value_as_written, role}],
           comparisons: [{a: {stat_key, field}, b: {stat_key, field}, relation: greater|less|equal|about_equal,
                          relation_word, a_text, b_text, difference_as_written}]}

Checks (all deterministic, no keyword search over the meaning of the whole sentence):
  1. numbers 1:1. Code extracts every number from the text (digits with sign and decimals, thousands commas,
     glued units "99s" "-31dBFS" "4.19x" "x4.19" "1.5-second", percentages, spelled-out numbers "two" "twice"
     "half" "a dozen", ordinals) after masking ids (R11, FOI-..., p90, L10). Every extracted number must be one
     declared fact or one comparison difference_as_written, written identically, and every declared number must be
     in the text: no unlisted numbers, no listed numbers missing.
  2. each fact: stat_key exists in this ride's stats, field is one of its numeric `fields`; value_as_written equals
     the stored value at the precision written (agent.verify's half-unit rule) with the SAME sign (stricter than
     verify._matches); the unit written beside the number is the field's unit (s only from seconds fields, % only
     from pct/fraction fields, dBFS only from a phone-level field, dB only from a dB difference, "N stops" only
     from a count field; a dB/dBFS/% field must carry its unit); the fact's role (words copied from the sentence
     naming the quantity) is adjacent to that number (no other number or other fact's role between them) and
     names this field: it holds one of the field's words, none of a same-unit sibling field's own words (so a
     tracker median cannot be called the timetable baseline, a p90 cannot be called a median), and a non-value
     field must use one of its distinctive words. For a section-scoped stat, the section named nearest to the
     number (clause boundaries cost extra distance) must be that stat's section.
  3. each comparison: code recomputes the relation from the two stored values and rejects a wrong one; the two
     sides must be the same unit (and, for tracker errors, the same statistic); a lower-bound field cannot be
     compared; difference_as_written must equal a - b (signed) or |a - b|. The sentence must say it in that
     direction: relation_word is a comparative of the stated relation ("louder" = greater, "quieter" = less), it
     is not negated, a_text names side a and is NOT governed by than/compared with/versus/against, b_text names
     side b and IS. Every comparative word in the text (louder, higher, more, shorter, above, than, ...) must
     belong to a declared comparison, so a direction cannot be stated outside the schema. Direction verbs (topped,
     surpassed, outstripped, trailed) count as comparatives. Superlatives (loudest, longest, ...) and change verbs
     (rose, fell, dropped, ...) are refused unless the word sits inside a fact's role and is that field's own word
     ("the loudest 10 % of seconds"): the stats hold no ranking or trend to check them against. Spelled fractions
     ("a third", "two-thirds") are numbers too.
  4. qualifiers: every field cited (fact, comparison side, or the value of a cited stat) lists the qualifiers it
     requires (ride_stats `fields.requires`: relative_level for any dB/dBFS field, lower_bound for a truncated
     section's recorded span, approximate for a section time with segmentation_confidence < 0.9,
     excluded_from_averages, stress_test); a missing one rejects the claim. The rendered sentence carries the
     qualifier badges, so the reader sees "lower bound" whatever the wording.
  5. wording rules kept from before: agent.verify.BANNED; RIDE_RULES (no corrugation, HSE, hearing, dB(A), TfL dB
     figures, other lines, loud/quiet verdicts, the rider); no dates; a 'measured' claim does not conclude; a
     claim about the tracker references a positioning stat; a trust/usable statement states the sync
     uncertainty as a fact and references the truncated section / tracker caveats of this ride; no "on time",
     "as scheduled" or "normal" beside a lower bound.
  6. second review (25 Oct): units no field has (ms, km, hours, glued letters) are refused and s/m values must carry
     their unit; "no pauses" is the number 0, "tenfold" a ratio, vague quantities and Roman numerals are refused; a
     negator in the clause before a number or comparative (and "it is false that") rejects the claim; the role-number
     gap holds at most 4 words, no punctuation or brackets and no sibling field's words; instants take no duration
     words; difference fields written "X minus Y" / "between X and Y" must keep the stored operand order; ids with
     digits other than this ride's own (and p50-style labels outside their field's role) are refused; lower-bound span
     times need lower_bound and the missing end's word (arrival / departure) is refused; each greater/less comparison
     is bound to one comparative occurrence (side a right before it in its segment, side b first after its
     connector); "between X and Y" names a section and a lone station nearer the number than the section rejects the
     fact; measured claims state no cause; usability words are refused on not-evaluated / excluded stats and on
     generalisations beyond the ride.
What remains for the reviewer: statements with no number (e.g. "no tunnel stops") are only tied to the stats
they cite, and the comparative vocabulary and role words are lists: the .md shows each number's field, label
and role so a person confirms them. Failing claims go back to Argon once with the reasons; still failing =
dropped and listed. The report is a DRAFT until a person reads it: --approve NAME refuses if the claims were
edited after generation (claims_sha), the stats changed (stats_sha), it is already approved, or a claim no
longer verifies; it writes nothing but the approval record.
Writes outputs_posthoc/ride_reports/<run>.{json,md} (and outputs_posthoc/ride_stats/<run>.json); upserts Atlas
`ride_reports` unless --no-atlas.
Run: PYTHONPATH=. .venv/Scripts/python -m agent.ride_report R14 [--jsonl] [--no-atlas]
     PYTHONPATH=. .venv/Scripts/python -m agent.ride_report R14 --approve "Tobi"
     PYTHONPATH=. .venv/Scripts/python -m agent.ride_report R11 --canary   (seeds one false claim to test the retry)
     PYTHONPATH=. .venv/Scripts/python -m agent.ride_report --selftest      (offline: the checker on known good/bad claims)
--jsonl prints one event per line: start | stats | draft | verdicts | final | error.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from . import ride_stats as RS
from . import verify as V

MODEL, THINKING = "gemini-4-argon", "medium"
OUTDIR = V.ROOT / "outputs_posthoc" / "ride_reports"
CHECKS_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
QUALIFIERS = ("lower_bound", "approximate", "relative_level", "excluded_from_averages", "stress_test", "single_ride")
BADGE = {"lower_bound": "lower bound", "approximate": "approximate", "relative_level": "relative level, uncalibrated",
         "excluded_from_averages": "excluded from averages", "stress_test": "stress test on a QC-excluded section",
         "single_ride": "single ride"}

# --------------------------------------------------------------------------- wording rules (kept from v1)
_MON = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
_DATE = re.compile(rf"\b\d{{1,2}}\s*{_MON}(?:\s+\d{{4}})?\b|\b{_MON}\s+\d{{1,2}}\b|\b{_MON}\s+\d{{4}}\b|"
                   r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b")  # case-sensitive: "may" stays a verb
_LINES = r"(?:Central|Hammersmith|Elizabeth|Victoria|District|Northern|Jubilee|Piccadilly|Circle|Metropolitan|Bakerloo)(?:\s+(?:&\s+City\s+)?line)?"
RIDE_RULES = [
    (r"\bcorrugat", "a ride report does not discuss corrugation: nothing in this project measures it"),
    (r"\bHSE\b|action value|exposure limit|hearing",
     "phone levels are uncalibrated relative dBFS(A): no HSE / exposure / hearing comparison"),
    (r"\bdB\s*\(A\)|\bdBA\b|\bdecibels? \(A\)", "write dBFS(A) or 'relative': dB(A) implies a calibrated level"),
    (r"\bTfL'?s?\b.{0,60}\b(?:dB|decibels?)\b|\b(?:dB|decibels?)\b.{0,60}\bTfL\b",
     "phone levels cannot be compared with TfL's dB figures"),
    (rf"\b(?:louder|quieter|noisier|higher|lower)\b.{{0,40}}\b{_LINES}\b|\b{_LINES}\b.{{0,40}}\b(?:louder|quieter|noisier)\b",
     "no comparison with another line: the stats hold none"),
    (r"\bvery (?:loud|quiet|noisy)\b|\bdeafening\b|\bquiet ride\b|\b(?:loud|noisy) ride\b|\bpainful",
     "uncalibrated relative levels carry no loud/quiet verdict: state the relative number"),
    (r"\bTobi\b|\bhis\b|\bhers?\b|\bhome\b|\bcommut\w*|\bmorning\b|\bevening\b|\bafternoon\b|\bthe rider\b",
     "describe the ride, not the person (no name, pronoun, time of day or journey purpose)"),
    (r"\bacross (?:the )?(?:Tube|Underground|network|London|lines)\b|\bnetwork\b|"
     r"\b(?:all|every|any|other) (?:sections?|lines?|trains?|rides?|trips?)\b|\bin general\b|\bgeneral(?:ly|i[sz]\w*)\b|"
     r"\balways\b|\beverywhere\b",
     "a ride report speaks for this one ride: no generalisation to other sections, rides, lines or the network"),
    (r"\b(?:it (?:is|was) (?:not|false|untrue|incorrect|wrong)|not (?:the case|true|correct)|false|untrue|incorrect(?:ly)?|"
     r"wrong(?:ly)?|mistaken(?:ly)?|contrary to|disprov\w*|refut\w*)\b(?![- ](?:start|pause|restart))",
     "no negated or 'false that' statements: state what the stats show, positively"),
    (r"\b(?:dozens|hundreds|thousands|millions|scores of|tens of|handful|several|few|many|numerous|countless|(?<!at )most|"
     r"majority|minority|nearly all|almost all|virtually all|the bulk|lots of|plenty|split second|fraction of an?|"
     r"momentar\w*|repeated\w*|multiple|many times)\b",
     "a vague quantity (most, several, dozens, ...) cannot be checked: write the number and declare it"),
    # third review (25 Oct): strength, certainty, bounds and generalisation words the stats cannot carry
    (r"\b(?:dramatic\w*|significant\w*|substantial\w*|vastly|hugely|markedly|far|much|exactly|confirmed|precise\w*|"
     r"definitive\w*|certain(?:ly)?|clearly|consistently|on average|typically|usually|every (?:run|ride|trip|time)|"
     r"another (?:run|ride|trip)|other (?:runs?|rides?|trips?)|(?:first|second|third|fourth|fifth|sixth|seventh|eighth|"
     r"ninth|tenth|next|previous|earlier|later) (?:run|ride|trip)|yesterday|today|tomorrow|monday|tuesday|wednesday|"
     r"thursday|friday|saturday|sunday|weekends?|weekdays?)\b",
     "no strength, certainty or generalisation words (significantly, exactly, consistently, on average, another run, "
     "a weekday): the stats are one ride's numbers"),
    (r"\b(?:rails?|tracks?|wear\w*|worn|grind\w*|rough\w*|harm\w*|health\w*|safe(?:ty|ly)?|unsafe|danger\w*|"
     r"regulat\w*|rules?|legal\w*|real[- ]world|absolute\w*|ears?|ear-\w+|noisy|hotspots?|problem\w*|passengers?|"
     r"workplace|breach\w*|staff|workers?)\b",
     "the stats are uncalibrated phone levels and tracker scores: no track condition, grinding, health, safety, "
     "regulation or real-world loudness reading"),
]
_GLUED_CODE = re.compile(r"[a-z](?:LST|BTH|MLE|ALE|WCL|VIC|GPK|OXC)\b")  # "eastboundLST-MLE"
CAUSAL = (r"\b(?:caus(?:e|ed|es|ing)|due to|because of|owing to|result(?:ed|s|ing)? in|as a result|led to|leads? to|"
          r"leading to|thanks to|driven by|responsible for|creat(?:e|ed|es|ing)|trigger\w*|gave rise|giving rise|"
          r"brought about|induc\w*|therefore|hence|thus|consequently)\b")
CONCLUDES = r"\b(?:indicat\w*|suggest\w*|likely|unlikely|probably|implie[sd]|implying|means|meaning|consistent with)\b"
RECOMMEND = (r"\b(?:trust\w*|reliab\w+|suitable|usable|safe to|fit for use|can be (?:used|relied)|robust\w*|dependab\w*|"
             r"valid\w*|(?:is|are|was|were|be) sound|soundly|good enough|fine to|ok to|okay to|accurate\w*|precise\w*|"
             r"confident\w*|with confidence)\b")
POSITIONING = r"\b(?:positioning|tracker|yaw)\b"
LOWER_BOUND_BAN = (r"\bon[- ]time\b|\bas scheduled\b|\bschedul\w*|\bnormal\b|\btypical\b|\bas planned\b|\bas usual\b|\bpunctual|"
                   r"\bcomplete(?:ly|d)?\b|\bin full\b|\bfull\b(?![- ]scale)|\bwhole\b(?![- ]recording)|\bentire(?:ly)?\b|"
                   r"\bend[- ]to[- ]end\b|\bat most\b|\bup to\b|\bno more than\b")
# in the segment (; . ", whereas ...") that holds a lower-bound number: words that make the span a whole trip
LOWER_BOUND_SEG_BAN = (r"\bcover(?:ed|s|ing)?\b|\bdone\b|\bjourneys?\b|\btrips?\b|\bstation[- ]to[- ]station\b|"
                   r"\bdoor[- ]to[- ]door\b|\bquick\w*|\bbrief\w*|\bshort\w*|\bfinish\w*|\bcross(?:ed|ing)\b|\btravers\w*")
# negation: a negator anywhere in the clause before a fact's number or a comparative word rejects it, except these phrases
_NEGATOR = re.compile(r"\b(?:not|no|never|nor|neither|none|nothing|without|cannot|untrue)\b|"
                      r"\bfalse\b(?![- ](?:start|pause|restart))|n't\b", re.I)
_NEG_OK = re.compile(r"\bnot (?:evaluated|observed|recorded|scored|captured|available)\b|"
                     r"\bno (?:tracker|leave-one-run-out|LOO) maps?\b|\bno timetable comparison\b|"
                     r"\b(?:no|without) tap[- ]markers?(?: alignment| found| matched)?\b|\bno one\b", re.I)
_CLAUSE = re.compile(r"[;:,()]|\.(?:\s|$)|—|–|\b(?:and|but|whereas|while|although|though|yet)\b", re.I)
# comparison binding: a comparison and its two sides must sit in one segment (split at ; . and ", and/while/but ...")
_SEG = re.compile(r";|\.(?:\s|$)|,\s*(?:and|while|whereas|but|though|although|yet|so)\b", re.I)
_PREP = {"inside", "within", "at", "from", "to", "after", "before", "between", "until", "through", "over", "of", "in", "on",
         "by", "than", "among"}
DURATION_WORDS = {"for","lasted", "lasting", "duration", "long", "interval", "period", "took", "taking", "spanning", "spanned"}

# --------------------------------------------------------------------------- number extraction
_UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
          "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
          "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
          "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_WORDS = {**{k: (v, 0) for k, v in _UNITS.items()}, "hundred": (100, 0), "thousand": (1000, 0), "dozen": (12, 0),
          "twice": (2, 0), "thrice": (3, 0), "once": (1, 0), "single": (1, 0), "double": (2, 0), "triple": (3, 0),
          "pair": (2, 0), "couple": (2, 0), "decile": (10, 0), "half": (0.5, 1), "quarter": (0.25, 2),
          "trio": (3, 0), "quartet": (4, 0), "quintet": (5, 0)}
_TENS = "twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety"
_NUMWORD = re.compile(rf"\b(?:(?:{_TENS})[- ](?:one|two|three|four|five|six|seven|eight|nine)|{'|'.join(_WORDS)})\b", re.I)
_DENOM = {"third": 3, "quarter": 4, "fifth": 5, "sixth": 6, "eighth": 8, "tenth": 10}
_FRAC = re.compile(r"\b(a|an|one|two|three|four|five|seven|nine)[- ](thirds?|quarters?|fifths?|sixths?|eighths?|tenths?)\b", re.I)
# not numbers: method names and set phrases ("leave-one-out", "one end of the section", "no one")
_IDIOM = re.compile(r"leave-one-(?:run-)?out|\bone another\b|\bno one\b|\bone-off\b|\b(?:one|either) end\b|\bone side\b", re.I)
_A_UNIT = re.compile(r"\b(an?)\s+(?:seconds?|minutes?|hours?|decibels?|metres?|meters?|percent|dB)\b", re.I)
_RAW = re.compile(r"[-+−]?\d+(?:,\d{3})*(?:\.\d+)?")
_UNIT_PATS = [("pct", r"(?:%|percent\b|per\s+cent\b|percentage points?\b|pp\b)"), ("dbfs", r"dBFS"),
              ("db", r"(?:dB\b|decibels?\b)"), ("hz", r"Hz\b"), ("ut", r"[uµμ]T\b"), ("deg", r"(?:°|deg(?:rees?)?\b)"),
              ("min", r"(?:mins?\b|minutes?\b)"), ("m", r"(?:m\b|metres?\b|meters?\b)"),
              ("s", r"(?:s\b|secs?\b|seconds?\b)"), ("ratio", r"(?:x\b|×|fold\b)"), ("times", r"times\b"), ("z", r"z\b")]
# units a number may be followed by that no stat field has: read as "unknown" and refused (143.0 ms, 1.7 km, 15.2 hours)
_BAD_UNIT = re.compile(r"(?:km|cm|mm|nm|ms|msecs?|µs|us|ns|hrs?|hours?|h|days?|weeks?|months?|years?|k|kHz|MHz|GHz|kph|mph|"
                       r"km/h|m/s|ft|feet|foot|yards?|miles?|inch(?:es)?|kg|mT|nT|tesla|gauss|dBA|dBC|dBZ|SPL|hPa|kPa|Pa|"
                       r"mbar|bar|milli\w+|micro\w+|kilo\w+|centi\w+)(?![A-Za-z])", re.I)
_FOLD = re.compile(r"\b([a-z]+)-?fold\b", re.I)
_ROMAN = re.compile(r"\b(?=[IVXL]+\b)(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})\b")
_NO_OK = re.compile(r"no (?:tracker|leave-one-run-out|LOO) maps?\b|no timetable comparison\b", re.I)
_COUNT_NOUN = re.compile(r"(?:[A-Za-z-]+\s+){0,2}?(?:stops?|flags?|sections?|pauses?|placements?|runs?|trips?|recordings?|"
                         r"files?|epochs?|samples?|resets?|combinations?|combos?|maps?|points?|jumps?)\b", re.I)
# unit written -> field units it may stand for (None = no unit written)
WRITTEN_OK = {"s": {"s"}, "min": {"min"}, "m": {"m"}, "pct": {"pct", "fraction", "confidence"}, "dbfs": {"dBFS"},
              "db": {"dB"}, "hz": {"Hz"}, "deg": {"deg"}, "ut": {"uT"}, "ratio": {"ratio"}, "times": {"ratio", "count"},
              "count": {"count"}, "ordinal": set(), "z": {"z"}, "unknown": set()}
NEEDS_UNIT = {"dB", "dBFS", "pct", "s", "m", "min", "uT", "Hz", "deg"}


def _unit_after(text: str, e: int, integral: bool) -> str | None:
    if re.match(r"(?:st|nd|rd|th)\b", text[e:]):
        return "ordinal"
    after = re.sub(r"^[  ]?-?[  ]?", "", text[e:e + 40])
    for code, pat in _UNIT_PATS:
        um = re.match(pat, after, re.I if code not in ("s", "m", "z") else 0)
        if um:
            rest = after[um.end():]
            if code == "dbfs" and rest.startswith("(A)"):
                rest = rest[3:]
            # a compound or another quantity: 1.7 m/s, 1.7 m per run, 0.67 dB SPL, dB HL, dB(A)
            if re.match(r"\s*(?:/|per\b|SPL\b|HL\b|\(A\))", rest, re.I):
                return "unknown"
            return code
    if integral and _COUNT_NOUN.match(after):
        return "count"
    if _BAD_UNIT.match(after) or (e < len(text) and text[e].isalpha()):  # a unit no field has, or letters glued on
        return "unknown"
    return None


def numbers_in(text: str) -> list[dict]:
    """Every number in the text: {written, value (signed), dec, sign, start, end, unit, kind}."""
    text = text or ""
    blank = lambda m: " " * len(m.group())  # noqa: E731
    masked = _IDIOM.sub(blank, V._ID_PAT.sub(blank, text))
    out = []
    for m in _RAW.finditer(masked):
        s, g = m.start(), m.group()
        sign = ""
        if g[0] in "+-−":
            prev = masked[s - 1] if s else " "
            if prev.isalnum() or prev in "._":  # 9.01-10.51 or LST-1: a dash, not a sign
                s, g = s + 1, g[1:]
            else:
                sign = "+" if g[0] == "+" else "-"
        num = g.lstrip("+-−")
        ns = m.end() - len(num)
        unit = None
        if not sign and ns and (masked[ns - 1].isalpha() or masked[ns - 1] in "_"):
            if masked[ns - 1] in "xX×" and (ns < 2 or not masked[ns - 2].isalpha()):
                unit = "ratio"  # x4.19
            else:
                continue  # an identifier: p90, L10, v1, H2b
        if not sign and re.search(r"\b(?:minus|negative)\s+$", masked[max(0, s - 10):s], re.I):
            sign = "-"
        elif not sign and re.search(r"\b(?:plus|positive)\s+$", masked[max(0, s - 10):s], re.I):
            sign = "+"
        clean = num.replace(",", "")
        val = float(clean) * (-1 if sign == "-" else 1)
        dec = len(clean.split(".")[1]) if "." in clean else 0
        unit = unit or _unit_after(masked, m.end(), dec == 0)
        out.append({"written": ("-" if sign == "-" else sign) + clean, "value": val, "dec": dec, "sign": sign,
                    "start": s, "end": m.end(), "unit": unit, "kind": "digits"})
    for m in _FRAC.finditer(masked):  # "a third", "two-thirds", "three quarters": one number, not two
        n, d = m.group(1).lower(), m.group(2).lower().rstrip("s")
        val = (1 if n in ("a", "an", "one") else _UNITS[n]) / _DENOM[d]
        out.append({"written": f"{n}-{d}", "value": val, "dec": 2, "sign": "", "start": m.start(), "end": m.end(),
                    "unit": _unit_after(masked, m.end(), False), "kind": "word"})
        masked = masked[:m.start()] + " " * (m.end() - m.start()) + masked[m.end():]
    for m in _FOLD.finditer(masked):  # "tenfold", "two-fold": a ratio; "manyfold" has no value and never verifies
        w = m.group(1).lower()
        val = _WORDS[w][0] if w in _WORDS else float("nan")
        out.append({"written": m.group().lower(), "value": float(val), "dec": 0, "sign": "", "start": m.start(),
                    "end": m.end(), "unit": "ratio", "kind": "word"})
        masked = masked[:m.start()] + " " * (m.end() - m.start()) + masked[m.end():]
    for m in _A_UNIT.finditer(masked):  # "a minute", "an hour", "a decibel": the number 1 with that unit
        out.append({"written": " ".join(m.group().lower().split()), "value": 1.0, "dec": 0, "sign": "",
                    "start": m.start(), "end": m.end(1), "unit": _unit_after(masked, m.end(1), True), "kind": "word"})
    for m in re.finditer(r"\bno\s+", masked, re.I):  # "no pauses", "no tunnel stops": the count 0
        if not _NO_OK.match(masked, m.start()) and _COUNT_NOUN.match(masked, m.end()):
            out.append({"written": "no", "value": 0.0, "dec": 0, "sign": "", "start": m.start(), "end": m.start() + 2,
                        "unit": "count", "kind": "word"})
    for m in _NUMWORD.finditer(masked):
        w = m.group().lower().replace(" ", "-")
        if "-" in w:
            a, b = w.split("-")
            val, dec = _UNITS[a] + _UNITS[b], 0
        else:
            val, dec = _WORDS[w]
        out.append({"written": w, "value": float(val), "dec": dec, "sign": "", "start": m.start(), "end": m.end(),
                    "unit": _unit_after(masked, m.end(), True), "kind": "word"})
    out.sort(key=lambda t: t["start"])
    for x, y in zip(out, out[1:]):  # "9.01 to 10.51 s", "9.01-10.51 s": the range shares the unit written last
        if x["unit"] is None and y["unit"] and re.fullmatch(r"\s*(?:-|–|to|and)\s*", masked[x["end"]:y["start"]]):
            x["unit"] = y["unit"]
    return out


def norm_written(s: str) -> str | None:
    """A declared value_as_written -> the same normal form numbers_in() gives (None if it is not one number)."""
    if str(s).strip().lower() == "no":
        return "no"
    t = numbers_in(str(s))
    return t[0]["written"] if len(t) == 1 and t[0]["value"] == t[0]["value"] else None  # NaN ("manyfold") is no number


# --------------------------------------------------------------------------- stats access
def field_of(stats: dict, key: str, field: str) -> tuple[float | None, dict | None, str | None]:
    """(stored value, field meta, error)."""
    st = stats.get(key)
    if st is None:
        return None, None, f"unknown stat_key {key!r}"
    meta = (st.get("fields") or {}).get(field)
    if meta is None:
        return None, None, (f"{key} has no numeric field {field!r} (numeric fields: {', '.join(st.get('fields') or {}) or 'none'})")
    v = st["value"] if field == "value" else st.get("n") if field == "n" else st["detail"].get(field)
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return None, None, f"{key}.{field} is not a number"
    return float(v), meta, None


def _match(tok: str, word: str) -> bool:
    return tok == word or (len(word) >= 4 and tok.startswith(word))


_NEUTRAL = {"the", "a", "an", "of", "its", "was", "is", "were", "on", "in", "this", "that", "by", "s", "m", "db",
            "dbfs", "relative", "section"}


def _role_tokens(role: str, codes: set[str]) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9|±%]+", role.lower()) if t not in _NEUTRAL and t.upper() not in codes]


def _distinct(stats: dict, key: str, field: str) -> tuple[set[str], set[str]]:
    """(words only this field has among its same-unit siblings in the stat, words its siblings have and it lacks)."""
    fields = stats[key]["fields"]
    fam = fields[field]["family"]
    own = set(fields[field]["words"])
    sib = set().union(*[set(m["words"]) for f, m in fields.items() if f != field and m["family"] == fam])
    return own - sib, sib - own


# --------------------------------------------------------------------------- sections named in the text
STATION_NAMES = {"LST": "Liverpool Street", "BTH": "Bethnal Green", "MLE": "Mile End", "ALE": "Aldgate East",
                 "WCL": "Whitechapel", "VIC": "Victoria", "GPK": "Green Park", "OXC": "Oxford Circus"}
_STRONG = re.compile(r";|\bwhereas\b|\bwhile\b|\bbut\b|\bhowever\b|\balthough\b|\bthough\b|[()]", re.I)


def _mentions(text: str, codes: set[str]) -> list[tuple[int, int, frozenset]]:
    names = {c: rf"(?:{c}|{re.escape(STATION_NAMES[c])})" if c in STATION_NAMES else c for c in codes}
    st = "|".join(f"(?P<{c}>{p})" for c, p in names.items())
    out = []
    st_b = st.replace('(?P<', '(?P<b_')
    for pat in (rf"(?:{st})\s*(?:-|–|—|->|→|\bto\b)\s*(?:{st_b})", rf"\bbetween\s+(?:{st})\s+and\s+(?:{st_b})"):
        for m in re.finditer(pat, text):
            a = next(c for c in codes if m.group(c))
            b = next(c for c in codes if m.group("b_" + c))
            out.append((m.start(), m.end(), frozenset((a, b))))
    return sorted(out)


def _single_stations(text: str, codes: set[str], mentions) -> list[tuple[int, int, frozenset]]:
    """Station codes / names that are not part of a parsed section mention ("at Bethnal Green", "BTH alone")."""
    names = "|".join([re.escape(c) for c in codes] + [re.escape(STATION_NAMES[c]) for c in codes if c in STATION_NAMES])
    return [(m.start(), m.end(), frozenset()) for m in re.finditer(rf"\b(?:{names})\b", text)
            if not any(ms <= m.start() and m.end() <= me for ms, me, _ in mentions)] if names else []


def _distance(text: str, start: int, end: int, ms: int, me: int) -> float:
    a, b = (me, start) if me <= start else (end, ms)
    gap = text[a:b] if a <= b else ""
    return len(gap.split()) + 25 * len(_STRONG.findall(gap))


def _governing_section(text: str, start: int, end: int, mentions) -> frozenset | None:
    """The section named nearest the number; crossing ; whereas while but ( ) costs 25 words."""
    best, bd = None, 1e9
    for ms, me, sec in mentions:
        d = _distance(text, start, end, ms, me)
        if d < bd:
            best, bd = sec, d
    return best


# --------------------------------------------------------------------------- comparisons
CMP_GREATER = (r"louder|noisier|higher|greater|larger|bigger|more|longer|slower|above|exceed(?:s|ed|ing)?|"
               r"surpass(?:es|ed|ing)?|outstrip(?:s|ped|ping)?|topped|tops|outranked|outranks|led|leads?|leading|ahead|"
               r"outpac\w*|eclips\w*|overt(?:ook|akes?|aking|aken)|stronger|heavier")
CMP_LESS = r"quieter|lower|smaller|less|fewer|shorter|faster|below|trail(?:s|ed|ing)?|behind|weaker|softer|lighter"
# equality wording: needs a declared equal / about_equal comparison (the code recomputes it)
CMP_EQUAL = (r"similar|comparable|equal|identical|matched|match(?:es|ing)?|equivalent|indistinguishable|alike|"
             r"(?:the )?same as|on par(?: with)?|in line with|close to|level with|as (?!well\b|soon\b|far\b|such\b)\w+ as")
CMP_BAD = r"better|worse|outperform\w*|beat\w*|underperform\w*"
_CMP_WORD = re.compile(rf"\b(?:{CMP_GREATER}|{CMP_LESS}|{CMP_EQUAL}|{CMP_BAD}|than)\b", re.I)
_CMP_MASK = re.compile(r"\b(?:lower|upper) bound\b|\brather than\b|\bother than\b|\bat least\b|\bat most\b|"
                       r"\bmore or less\b|\btap[- ]markers? matched\b", re.I)
_CONNECT = {"than", "compared", "versus", "vs", "against", "relative", "above", "below", "exceed", "exceeds",
            "exceeded", "exceeding", "beyond", "surpass", "surpasses", "surpassed", "surpassing", "outstrip", "outstrips",
            "outstripped", "outstripping", "topped", "tops", "outranked", "outranks", "trail", "trails", "trailed",
            "trailing", "led", "lead", "leads", "leading", "ahead", "behind", "outpaced", "outpaces", "outpacing",
            "eclipsed", "eclipses", "eclipsing", "overtook", "overtakes", "overtaking", "overtaken"}
# a ranking or a change of direction that no declared comparison can carry: rejected unless the word sits inside a
# fact's role, is one of that field's own words, the role names no section, and the word is followed by a number or
# the field's own words ("the loudest 10 % of seconds", "maximum tracker error")
SUPERLATIVE = re.compile(r"\b(?:loudest|quietest|noisiest|highest|lowest|longest|shortest|largest|smallest|biggest|"
                         r"fastest|slowest|greatest|strongest|weakest|best|worst|maximum|minimum|peak(?:ed|s)?|top|"
                         r"rank\w*|first|last|foremost)\b", re.I)
CHANGE = re.compile(r"\b(?:rose|risen|rises|rising|fell|fallen|falls|falling|increas\w*|decreas\w*|dropp?(?:ed|s|ing)?|"
                    r"climb\w*|jump\w*|spik\w*|dipp?(?:ed|s|ing)?|plung\w*|soar\w*|grew|grown|shr[ai]nk\w*|doubl\w*|"
                    r"halv\w*|improv\w*|worsen\w*|deteriorat\w*|(?:went|go|goes|going|gone) (?:up|down)|ramp\w*|"
                    r"escalat\w*|recover\w*)\b", re.I)
_FILLER = {"the", "a", "an", "its", "their", "on", "in", "at", "with", "to", "that", "of", "those", "this", "for"}


def _neg_text(text: str, toks=()) -> str:
    """The text with allowed negation phrases ('not evaluated', 'no tracker map') and zero counts ('no pauses') blanked."""
    t = _NEG_OK.sub(lambda m: " " * len(m.group()), text)
    for k in toks:
        if k.get("written") == "no":
            t = t[:k["start"]] + "  " + t[k["end"]:]
    return t


def _negated(neg_text: str, pos: int) -> str | None:
    """The negator in the clause before `pos` (clause = back to , ; : ( ) . — and / but / while / whereas ...)."""
    start = 0
    for m in _CLAUSE.finditer(neg_text, 0, pos):
        start = m.end()
    m = None
    for m in _NEGATOR.finditer(neg_text, start, pos):
        pass
    return m.group() if m else None


def _segment(text: str, pos: int) -> tuple[int, int]:
    s, e = 0, len(text)
    for m in _SEG.finditer(text):
        if m.end() <= pos:
            s = m.end()
        elif m.start() >= pos:
            e = m.start()
            break
    return s, e


def _occ(text: str, phrase: str) -> list[tuple[int, int]]:
    if not phrase.strip():
        return []
    return [m.span() for m in re.finditer(rf"(?<!\w){re.escape(phrase.strip())}(?!\w)", text, re.I)]


def _governed(text: str, start: int) -> bool:
    """True when the words just before `start` are a comparison connector (than / compared with / versus / against /
    relative to / above / below / exceeds), with at most 3 filler words between and no punctuation."""
    before = text[:start]
    toks = re.findall(r"[A-Za-z']+|[^\sA-Za-z']", before)
    skipped = 0
    for t in reversed(toks):
        tl = t.lower()
        if tl in _CONNECT:
            return True
        if tl in _FILLER and skipped < 3:
            skipped += 1
            continue
        return False
    return False


def _names_side(span: str, side: dict, other: dict, stats: dict, codes: set[str]) -> str | None:
    """None when the span names this side (its section, or its field words) and not the other side."""
    ss, os_ = side["meta"].get("section"), other["meta"].get("section")
    secs = {sec for _, _, sec in _mentions(span, codes)}
    if ss and os_ and ss != os_:
        if frozenset(ss.split("-")) not in secs:
            return f"{span!r} must name {ss}"
        if frozenset(os_.split("-")) in secs:
            return f"{span!r} also names {os_}, the other side"
        return None
    toks = _role_tokens(span, codes)
    if not any(_match(t, w) for t in toks for w in side["meta"]["words"]) and \
            not (ss and frozenset(ss.split("-")) in secs and side["field"] == "value"):
        return f"{span!r} does not name {side['key']}.{side['field']} ({side['meta']['label']}; words: {', '.join(side['meta']['words'])})"
    o_only = set(other["meta"]["words"]) - set(side["meta"]["words"])
    bad = [t for t in toks if any(_match(t, w) for w in o_only) and not any(_match(t, w) for w in side["meta"]["words"])]
    if bad:
        return f"{span!r} uses {bad[0]!r}, a word of the other side ({other['meta']['label']})"
    return None


ABOUT_EQUAL_DB = 0.25  # dB: "similar" levels; the LST-BTH vs BTH-MLE effect under study is ~0.5 dB, so 5 % (~1 dB) is too loose


def _relation_of(va: float, vb: float, unit: str) -> set[str]:
    """greater / less / equal from the stored values, plus about_equal within 0.25 dB (levels) or 5 % (anything else).
    Levels within 0.25 dB are ONLY about_equal: on an uncalibrated phone a 0.1 dB gap is not a direction."""
    rel = {"greater"} if va > vb else {"less"} if va < vb else {"equal", "about_equal"}
    if unit in ("dB", "dBFS"):
        return (rel & {"equal"}) | {"about_equal"} if abs(va - vb) <= ABOUT_EQUAL_DB + 1e-9 else rel
    tol = 0.05 * max(abs(va), abs(vb))
    if abs(va - vb) <= tol + 1e-9:
        rel.add("about_equal")
    return rel


# --------------------------------------------------------------------------- the schema Argon fills
class Fact(BaseModel):
    stat_key: str = Field(description="a key of the ride stats, e.g. 'qc.pause.1'")
    field: str = Field(description="'value', 'n', or a numeric detail field name listed in that stat's `fields`")
    value_as_written: str = Field(description="the number exactly as written in the sentence, with its sign, e.g. '-24.7', '15.2', '34.2'")
    role: str = Field(description="words copied exactly from the sentence, right next to the number, that say what the "
                                  "number is; must contain one of the field's `words`, e.g. 'paused for', 'at', 'tracker median error'")


class Side(BaseModel):
    stat_key: str
    field: str


class Comparison(BaseModel):
    a: Side = Field(description="the side the sentence makes the subject (e.g. the section that is 'louder')")
    b: Side = Field(description="the side after 'than' / 'compared with'")
    relation: Literal["greater", "less", "equal", "about_equal"] = Field(description="a relative to b")
    relation_word: str = Field(description="the comparative word as written: louder, quieter, higher, lower, longer, shorter, ...")
    a_text: str = Field(description="words copied from the sentence naming side a (e.g. 'LST-BTH', 'moving audio')")
    b_text: str = Field(description="words copied from the sentence naming side b, right after than / compared with")
    difference_as_written: str = Field(description="the difference as written in the sentence (a - b or |a - b|), or '' if none")


class Claim(BaseModel):
    id: str = Field(description="C1, C2, ...")
    text: str = Field(description="one plain-English sentence")
    type: Literal["measured", "inference"]
    qualifiers: list[Literal["lower_bound", "approximate", "relative_level", "excluded_from_averages", "stress_test",
                             "single_ride"]]
    facts: list[Fact] = Field(description="one entry per number in the sentence (except a comparison's difference)")
    comparisons: list[Comparison] = Field(description="one entry per comparative statement (louder, higher, shorter, than, ...)")
    cites: list[str] = Field(description="stat keys the sentence relies on without quoting a number from them")


class Report(BaseModel):
    claims: list[Claim]


# --------------------------------------------------------------------------- the checker
def _ride_codes(stats: dict) -> set[str]:
    codes = set(stats["ride.route"]["detail"]["stations"].split()) if "ride.route" in stats else set()
    for k in stats:
        m = re.match(r"(?:section|positioning)\.([A-Z]{3})-([A-Z]{3})\.", k)
        if m:
            codes |= set(m.groups())
    return codes


# difference fields: (stat-key regex, field) -> (minuend test, subtrahend test, wording of the stored order).
# A test gets the span on one side of "minus" / " - " / "between ... and" and returns True when it names that operand.
def _sec_test(which: str):
    def t(span: str, st: dict, codes: set[str]) -> bool:
        sec = st["detail"].get(which)
        return bool(sec) and frozenset(sec.split("-")) in {m[2] for m in _mentions(span, codes)}
    return t


def _word_test(pat: str, neg: str | None = None):
    def t(span: str, st: dict, codes: set[str]) -> bool:
        return bool(re.search(pat, span, re.I)) and not (neg and re.search(neg, span, re.I))
    return t


DIFF_FIELDS = [
    (r"trip\.contrast_db", "value", _sec_test("minuend_section"), _sec_test("subtrahend_section"),
     "minuend_section minus subtrahend_section"),
    (r"noise\.moving_minus_dwell_db", "value", _word_test(r"\b(?:moving|motion|running)\b"),
     _word_test(r"\b(?:dwell\w*|platform\w*|stationary|standing|stopped)\b"), "moving level minus dwell level"),
    (r"section\..*\.level", "minus_section_mean_db", _word_test(r"\b(?:level|section|this|ride|run)\b", r"\b(?:mean|average)\b"),
     _word_test(r"\b(?:mean|average)\b"), "this section's level minus the all-runs section mean"),
    (r"section\..*\.level", "level_minus_trip_mean_db", _word_test(r"\b(?:level|section)\b", r"\btrip\b"),
     _word_test(r"\btrip\b"), "this section's level minus the trip mean"),
    (r"noise\.level_overall", "p95_minus_p5_db", _word_test(r"\b(?:p95|loudest)\b"), _word_test(r"\b(?:p5|quietest)\b"),
     "p95 minus p5"),
]
_DIFF_OP = re.compile(r"\bminus\b(?!\s*[-+−]?\d)|(?<=[A-Za-z)])\s+[-–−]\s+(?=[A-Za-z(])|\bbetween\b", re.I)
_UNIT_RX = r"(?:%|percent|per cent|dBFS(?:\(A\))?|dB|m\b|s\b|secs?\b|seconds?\b|metres?\b|x\b|uT\b|Hz\b|deg\b)"


def _diff_order(f: dict, tok: dict, text: str, stats: dict, codes: set[str]) -> list[str]:
    """For a difference field, wording that orders its operands ('X minus Y', 'X - Y', 'between X and Y') must put
    them in the stored order; 'minus' wording that names neither operand is refused."""
    key, field = f.get("stat_key", ""), f.get("field", "")
    spec = next((d for d in DIFF_FIELDS if re.fullmatch(d[0], key) and d[1] == field), None)
    if not spec:
        return []
    _, _, t_min, t_sub, order = spec
    st = stats[key]
    s, e = _segment(text, tok["start"])
    R = []
    for m in _DIFF_OP.finditer(text, s, e):
        if m.group().lower() == "between":
            mm = re.match(r"between\s+(.+?)\s+and\s+(.+?)(?=[,;:()]|\s+(?:was|were|is|of|at|in|by)\b|[-+−]?\d|$)",
                          text[m.start():e], re.I)
            if not mm:
                continue
            x, y = mm.group(1), mm.group(2)
        else:
            x = re.split(r"[,;:(]", text[s:m.start()])[-1]
            x = " ".join(x.split()[-6:])
            y = " ".join(re.split(r"[,;:)]|[-+−]?\d", text[m.end():e])[0].split()[:6])
        named = t_min(x, st, codes) or t_sub(x, st, codes) or t_min(y, st, codes) or t_sub(y, st, codes)
        if not named:
            if m.group().lower() != "between":
                R.append(f"{tok['written']} is {key}.{field}: name both sides around {m.group().strip()!r} in the "
                         f"stored order ({order}), or drop the 'minus' wording")
            continue
        if not (t_min(x, st, codes) and not t_sub(x, st, codes) and t_sub(y, st, codes) and not t_min(y, st, codes)):
            R.append(f"{tok['written']} is {key}.{field} = {order}; the sentence orders it {x.strip()!r} "
                     f"{m.group().strip()} {y.strip()!r}: write the operands in the stored order (or declare a comparison)")
    return R


def _check_fact(f: dict, tok: dict, text: str, stats: dict, codes: set[str], role_occ_others, toks, mentions,
                n_sections: int, neg_text: str) -> list[str]:
    key, field = f.get("stat_key", ""), f.get("field", "")
    val, meta, err = field_of(stats, key, field)
    if err:
        return [err]
    w, unit, R = tok["unit"], meta["unit"], []
    name = f"{key}.{field}"
    # unit written vs the field's unit
    if w is None:
        if unit in NEEDS_UNIT:
            shown = {"pct": "%", "fraction": "%"}.get(unit, unit)
            R.append(f"{tok['written']} is {name} ({meta['label']}, unit {unit}): write it with its unit ({shown})")
    elif w == "unknown":
        R.append(f"{tok['written']} is followed by {text[tok['end']:tok['end'] + 10].strip()!r}, not a unit of {name} "
                 f"({unit}): write the field's own unit")
    elif unit not in WRITTEN_OK[w]:
        R.append(f"{tok['written']} is written as {w} but {name} is {unit} ({meta['label']})")
    # value at the written precision, sign exact
    x, dec = tok["value"], tok["dec"]
    if x != x:
        return R + [f"{tok['written']!r} has no definite value: write the number"]
    if w == "pct" and unit in ("fraction", "confidence"):
        x, dec = x / 100, dec + 2
    if val < 0 and tok["sign"] != "-":
        R.append(f"{tok['written']}: {name} = {val:g} is negative; write the minus sign")
    elif val >= 0 and tok["sign"] == "-":
        R.append(f"{tok['written']}: {name} = {val:g} is not negative")
    elif abs(x - val) > 0.5 * 10 ** -dec + 1e-9:
        R.append(f"{tok['written']} is not {name} = {val:g} at the precision written")
    elif x == 0 and val != 0:
        R.append(f"{tok['written']}: {name} = {val:g} is not zero")
    elif unit in ("fraction", "confidence") and w != "pct" and dec == 0 and val not in (0.0, 1.0):
        R.append(f"{tok['written']}: {name} = {val:g} is a 0-1 value; write its decimals")
    neg = _negated(neg_text, tok["start"])
    if neg:
        R.append(f"{tok['written']} ({name}) is negated by {neg!r} in its clause: state the fact positively")
    # role: names this field, sits next to this number
    role = (f.get("role") or "").strip()
    occ = _occ(text, role)
    if not role or not occ:
        R.append(f"role {role!r} for {tok['written']} must be words copied exactly from the sentence")
        return R
    rt = _role_tokens(role, codes)
    own_distinct, sib_distinct = _distinct(stats, key, field)
    words = meta["words"]
    sec_ok = meta.get("section") and field == "value" and any(
        sec == frozenset(meta["section"].split("-")) for _, _, sec in _mentions(role, codes))
    if not any(_match(t, x) for t in rt for x in words) and not sec_ok:
        R.append(f"role {role!r} does not name {name} ({meta['label']}); use one of: {', '.join(words)}")

    def sib(ts):
        return sorted({t for t in ts if any(_match(t, x) for x in sib_distinct) and not any(_match(t, x) for x in words)})
    bad = sib(rt)
    if bad:
        R.append(f"role {role!r} uses {bad[0]!r}, which names a different field of {key} than {field} ({meta['label']})")
    if field != "value" and own_distinct and not any(_match(t, x) for t in rt for x in own_distinct):
        R.append(f"role {role!r} must say which quantity {tok['written']} is ({meta['label']}): use one of "
                 f"{', '.join(sorted(own_distinct))}")
    dur = set(re.findall(r"[a-z]+", role.lower())) & DURATION_WORDS
    if meta.get("instant") and dur:
        R.append(f"{tok['written']} is {name}, a point in time ({meta['label']}): no duration words "
                 f"({', '.join(sorted(dur))}) in its role")
    near, why = False, "they are more than 4 words apart, or another number or another fact's role is between them"
    other_spans = list(role_occ_others)
    for rs, re_ in occ:
        a, b = (re_, tok["start"]) if re_ <= tok["start"] else (tok["end"], rs)
        if a > b:  # the role contains the number's position: not a label
            continue
        gap = text[a:b]
        core = gap.rstrip(" (") if re_ <= tok["start"] else re.sub(r"^\s*" + _UNIT_RX + r"?", "", gap, count=1)
        if re.search(r"[;:,()]|\.(?:\s|$)|\bnot\b|n't\b", core):
            why = f"the words between them ({gap.strip()!r}) hold punctuation, brackets or 'not'"
            continue
        gtoks = [t for t in _role_tokens(core, codes) if t not in _FILLER]
        if len(gtoks) > 4:
            continue
        if any(t is not tok and t["start"] >= a and t["end"] <= b for t in toks):
            continue
        if any(os_ >= a and oe <= b for os_, oe in other_spans):
            continue
        bad = sib(gtoks)
        if bad:
            why = f"the words between them name a different field ({bad[0]!r})"
            continue
        if meta.get("instant") and set(gtoks) & DURATION_WORDS:
            why = f"the words between them are duration words ({', '.join(sorted(set(gtoks) & DURATION_WORDS))})"
            continue
        if re_ > tok["start"]:  # role after the number: the words just before the number must not name a sibling
            lim = max([0] + [t["end"] for t in toks if t["end"] <= tok["start"]] +
                      [m.end() for m in re.finditer(r"[;:,()]|\.(?:\s|$)", text[:tok["start"]])])
            pre = text[lim:tok["start"]]
            for os_, oe in other_spans:
                if os_ >= lim and oe <= tok["start"]:
                    pre = pre[:os_ - lim] + " " * (oe - os_) + pre[oe - lim:]
            pre_t = [t for t in _role_tokens(pre, codes) if t not in _FILLER | _PREP | {"was", "were", "is", "are", "be", "been"}]
            bad = sib(pre_t[-1:])  # the word right before the number ("uncertainty 3.95 s offset" is refused)
            if bad:
                why = f"the words just before the number name a different field ({bad[0]!r})"
                continue
        near = True
        break
    if not near:
        R.append(f"role {role!r} is not next to {tok['written']} ({why}): the role must be the words that name this number")
    # section: the section named nearest the number must be the stat's
    if meta.get("section"):
        want = frozenset(meta["section"].split("-"))
        got = _governing_section(text, tok["start"], tok["end"], mentions)
        if got is None and n_sections > 1:
            R.append(f"{tok['written']} belongs to {meta['section']}: name the section in the sentence")
        elif got is not None and got != want:
            R.append(f"{tok['written']} is {name} of {meta['section']}, but the section named nearest to it is "
                     f"{'-'.join(sorted(got))}")
        elif got is not None:
            dg = min(_distance(text, tok["start"], tok["end"], ms, me) for ms, me, sec in mentions if sec == got)
            loose = [text[ms:me] for ms, me, _ in _single_stations(text, codes, mentions)
                     if _distance(text, tok["start"], tok["end"], ms, me) < dg]
            if loose:
                R.append(f"{tok['written']} is {name} of {meta['section']}, but {loose[0]!r} is named nearer to it than "
                         "the section: name the section as X-Y next to the number")
    R += _diff_order(f, tok, text, stats, codes)
    return R


def _check_diff(c: dict, tok: dict, stats: dict) -> list[str]:
    va, ma, ea = field_of(stats, c["a"]["stat_key"], c["a"]["field"])
    vb, mb, eb = field_of(stats, c["b"]["stat_key"], c["b"]["field"])
    if ea or eb:
        return [ea or eb]
    unit = "dB" if ma["unit"] in ("dB", "dBFS") else ma["unit"]
    w, R = tok["unit"], []
    if w is None and unit in NEEDS_UNIT | {"fraction"}:
        R.append(f"difference {tok['written']}: write it with its unit")
    elif w is not None and unit not in WRITTEN_OK[w] and not (w == "pct" and unit == "fraction"):
        R.append(f"difference {tok['written']} is written as {w} but the difference of these fields is {unit}")
    d = va - vb
    x, dec = tok["value"], tok["dec"]
    if w == "pct" and unit == "fraction":
        x, dec = x / 100, dec + 2
    tol = 0.5 * 10 ** -dec + 1e-9
    if tok["sign"] == "-":
        ok = abs(x - d) <= tol
    elif tok["sign"] == "+":
        ok = d > 0 and abs(x - d) <= tol
    else:
        ok = abs(x - abs(d)) <= tol
    if not ok:
        R.append(f"difference {tok['written']} is not a - b = {d:+.4g} (or its magnitude) at the precision written")
    return R


def _check_comparison(c: dict, text: str, masked_cmp: str, stats: dict, codes: set[str],
                      neg_text: str) -> tuple[list[str], dict]:
    R, info = [], {}
    sides = {}
    for s in ("a", "b"):
        sd = c.get(s) or {}
        v, meta, err = field_of(stats, sd.get("stat_key", ""), sd.get("field", ""))
        if err:
            R.append(f"comparison side {s}: {err}")
        sides[s] = {"key": sd.get("stat_key"), "field": sd.get("field"), "v": v, "meta": meta}
    if R:
        return R, info
    A, B = sides["a"], sides["b"]
    info = {"a": f"{A['key']}.{A['field']}", "b": f"{B['key']}.{B['field']}", "va": A["v"], "vb": B["v"],
            "stated": c.get("relation")}
    if A["meta"]["unit"] != B["meta"]["unit"] or (A["key"] == B["key"] and A["field"] == B["field"]):
        R.append(f"comparison must set two different quantities of the same unit side by side "
                 f"({A['meta']['unit']} vs {B['meta']['unit']})")
    if A["meta"]["unit"] == "m":
        cls = lambda s: "median" if s["field"] in ("value", "timetable_baseline_median_m") else s["field"]  # noqa: E731
        if cls(A) != cls(B):
            R.append("compare like with like: a tracker median only with a median (not a p90 or maximum)")
    for s in (A, B):
        if "lower_bound" in s["meta"]["requires"]:
            R.append(f"{s['key']} is a lower bound (one end of the section not observed): it cannot be compared")
    real = _relation_of(A["v"], B["v"], A["meta"]["unit"])
    info["recomputed"] = sorted(real)
    if c.get("relation") not in real:
        R.append(f"wrong relation: {A['key']}.{A['field']} = {A['v']:g} vs {B['key']}.{B['field']} = {B['v']:g} "
                 f"is {' / '.join(sorted(real))} (a relative to b), not {c.get('relation')}")
    word = re.sub(r"\s+", " ", (c.get("relation_word") or "").strip().lower())
    pol = "greater" if re.fullmatch(CMP_GREATER, word) else "less" if re.fullmatch(CMP_LESS, word) else \
        "equal" if re.fullmatch(CMP_EQUAL, word) else None
    wocc = []
    if pol is None:
        R.append(f"relation_word {word!r} must be one comparative word as written (louder, quieter, higher, lower, "
                 "longer, shorter, more, fewer, above, below, similar, ...)")
    else:
        if pol == "equal" and c.get("relation") not in ("equal", "about_equal") or \
                pol != "equal" and pol != c.get("relation"):
            R.append(f"relation_word {word!r} says {pol} but the comparison declares {c.get('relation')}")
        if word in ("faster", "slower") and A["meta"]["unit"] != "s":
            R.append("faster/slower only compares durations")
        wocc = _occ(masked_cmp, word)
        if not wocc:
            R.append(f"relation_word {word!r} is not in the sentence")
        else:
            negs = [_negated(neg_text, s) for s, _ in wocc]
            if all(negs):
                R.append(f"{word!r} is negated ({negs[0]!r} in its clause): state the comparison positively")
            wocc = [o for o, n in zip(wocc, negs) if not n]
            if word.startswith("exceed") and wocc and all(re.match(r"\s+by\b", masked_cmp[e:]) for _, e in wocc):
                R.append("write the comparison actively ('A exceeded B'), not 'exceeded by'")
    for s, sd in (("a", A), ("b", B)):
        txt = c.get(f"{s}_text") or ""
        if not _occ(text, txt):
            R.append(f"{s}_text {txt!r} must be copied exactly from the sentence")
            continue
        bad = _names_side(txt, sd, B if s == "a" else A, stats, codes)
        if bad:
            R.append(f"{s}_text: {bad}")
    if c.get("relation") in ("greater", "less") and not R:
        if not any(_bound(text, ws, we, c["a_text"], c["b_text"]) for ws, we in wocc):
            R.append(f"the sentence must say it in the declared direction around {word!r}: side a ({c['a_text']!r}) "
                     f"right before it in the same clause and side b ({c['b_text']!r}) right after its than / to / "
                     f"versus (or 'compared with <b>, <a> was {word}')")
    return R, info


def _bound(text: str, ws: int, we: int, a_txt: str, b_txt: str) -> bool:
    """One comparative occurrence [ws, we) carries the comparison a > b (or a < b) as written: within its segment,
    the last side named before the word is a (not governed by than / compared with) and either the word is followed
    by a connector (than, to, versus; or is itself a direction verb: topped, trailed) whose first side is b, or the
    word ends the comparison and b precedes a after 'compared with'."""
    s, e = _segment(text, ws)
    ao = [o for o in _occ(text, a_txt) if s <= o[0] and o[1] <= e]
    bo = [o for o in _occ(text, b_txt) if s <= o[0] and o[1] <= e]
    ao_ = [o for o in ao if not any(b0 <= o[0] and o[1] <= b1 and (b0, b1) != o for b0, b1 in bo)]
    bo_ = [o for o in bo if not any(a0 <= o[0] and o[1] <= a1 and (a0, a1) != o for a0, a1 in ao)]
    sides = sorted([(o, "a") for o in ao_] + [(o, "b") for o in bo_])
    before = [x for x in sides if x[0][1] <= ws]
    after = [x for x in sides if x[0][0] >= we]
    if not before or before[-1][1] != "a" or _governed(text, before[-1][0][0]):
        return False
    word = text[ws:we].lower()
    toks = re.findall(r"[A-Za-z']+|[^\sA-Za-z']", text[we:e])
    nxt = [t.lower() for t in toks[:4] if t.lower() not in _FILLER and t.strip() and t not in "(),"]
    connected = word in _CONNECT or (nxt and nxt[0] in _CONNECT | {"to", "with", "of"})
    if connected:
        return bool(after) and after[0][1] == "b" and (word in _CONNECT or _governed(text, after[0][0][0]))
    a_pos = before[-1][0][0]
    return any(lab == "b" and o[1] <= a_pos and _governed(text, o[0]) for o, lab in before)


def check(cl: dict, stats: dict) -> dict:
    """Schema binding of one claim against this ride's stats. Returns {id, ok, reasons, trace, comparisons}."""
    text, typ = cl.get("text", "") or "", cl.get("type")
    quals = set(cl.get("qualifiers") or [])
    facts = list(cl.get("facts") or [])
    cmps = list(cl.get("comparisons") or [])
    cites = list(cl.get("cites") or [])
    R: list[str] = []
    codes = _ride_codes(stats)
    n_sections = (stats.get("ride.route") or {}).get("detail", {}).get("n_sections", 1)
    for pat, why in V.BANNED:
        if re.search(pat, text, re.I | re.S):
            R.append(f"banned: {why}")
    if typ not in ("measured", "inference"):
        R.append("type must be measured or inference")
    bad_q = quals - set(QUALIFIERS)
    if bad_q:
        R.append(f"unknown qualifiers {sorted(bad_q)}")
    for k in cites:
        if k not in stats:
            R.append(f"cites: unknown stat key {k!r}")
    # ---- 1:1 numbers
    toks = numbers_in(text)
    neg_text = _neg_text(text, toks)
    mentions = _mentions(text, codes)
    items = []  # (normalised written, kind, payload)
    for f in facts:
        w = norm_written(f.get("value_as_written", ""))
        if w is None:
            R.append(f"value_as_written {f.get('value_as_written')!r} ({f.get('stat_key')}.{f.get('field')}) must be one number")
            continue
        items.append((w, "fact", f))
    for c in cmps:
        d = str(c.get("difference_as_written") or "").strip()
        if d:
            w = norm_written(d)
            if w is None:
                R.append(f"difference_as_written {d!r} must be one number")
                continue
            items.append((w, "diff", c))
    by_tok, by_item = {}, {}
    for t in toks:
        by_tok.setdefault(t["written"], []).append(t)
    for it in items:
        by_item.setdefault(it[0], []).append(it)
    for w in sorted(set(by_tok) | set(by_item)):
        nt, ni = len(by_tok.get(w, [])), len(by_item.get(w, []))
        if nt > ni:
            R.append(f"the number {w!r} appears {nt}x in the text but is declared {ni}x: every number in the sentence "
                     "must be listed in facts (or as a comparison's difference_as_written)")
        elif ni > nt:
            R.append(f"{w!r} is declared {ni}x but appears {nt}x in the text (copy value_as_written exactly as written, "
                     "with its sign)")
    trace = []
    roles_all = [(f.get("role") or "").strip() for f in facts]
    for w, ts in by_tok.items():
        its = by_item.get(w, [])
        if len(ts) != len(its) or not ts:
            continue
        def eval_pair(it, t):
            if it[1] == "diff":
                return _check_diff(it[2], t, stats)
            f = it[2]
            others = [o for r in roles_all if r and r.lower() != (f.get("role") or "").strip().lower() for o in _occ(text, r)]
            return _check_fact(f, t, text, stats, codes, others, toks, mentions, n_sections, neg_text)
        best = None
        perms = itertools.permutations(range(len(its))) if len(its) <= 6 else [tuple(range(len(its)))]
        for p in perms:
            res = [eval_pair(its[j], ts[i]) for i, j in enumerate(p)]
            score = sum(bool(r) for r in res)
            if best is None or score < best[0]:
                best = (score, p, res)
            if score == 0:
                break
        _, p, res = best
        for i, j in enumerate(p):
            it, t = its[j], ts[i]
            R += res[i]
            if it[1] == "fact":
                f = it[2]
                val, meta, _ = field_of(stats, f.get("stat_key", ""), f.get("field", ""))
                trace.append({"written": t["written"], "pos": t["start"], "kind": "fact", "stat_key": f.get("stat_key"),
                              "field": f.get("field"), "stored": val, "unit": meta and meta["unit"],
                              "label": meta and meta["label"], "role": f.get("role"), "ok": not res[i]})
            else:
                c = it[2]
                trace.append({"written": t["written"], "pos": t["start"], "kind": "difference",
                              "a": f"{c['a']['stat_key']}.{c['a']['field']}", "b": f"{c['b']['stat_key']}.{c['b']['field']}",
                              "ok": not res[i]})
    trace.sort(key=lambda x: x["pos"])
    # ---- comparisons and comparative words
    masked_cmp = _CMP_MASK.sub(lambda m: " " * len(m.group()), text)
    cmp_info = []
    for c in cmps:
        r, info = _check_comparison(c, text, masked_cmp, stats, codes, neg_text)
        R += [f"comparison {c.get('relation_word')!r}: {x}" for x in r]
        cmp_info.append({**info, "relation_word": c.get("relation_word"), "ok": not r})
    words = [m.group().lower() for m in _CMP_WORD.finditer(masked_cmp)]
    declared = [str(c.get("relation_word") or "").strip().lower() for c in cmps]
    for wd in sorted(set(words)):
        if wd == "than":
            n_dir = sum(1 for c in cmps if c.get("relation") in ("greater", "less"))
            if words.count("than") > n_dir:
                R.append("'than' states a comparison: declare it in comparisons (one per 'than')")
        elif re.fullmatch(CMP_BAD, wd):
            R.append(f"{wd!r} is a judgement, not a measured direction: write higher/lower/larger/smaller and declare the comparison")
        elif words.count(wd) > declared.count(wd):
            R.append(f"the comparative {wd!r} appears {words.count(wd)}x but {declared.count(wd)} comparison(s) declare it: "
                     "every comparative statement must be a declared comparison (the code recomputes its direction)")
    # superlatives and change verbs: allowed only as a fact's own naming words (inside its role)
    role_spans = []
    for f in facts:
        _, meta, _ = field_of(stats, f.get("stat_key", ""), f.get("field", ""))
        if meta:
            r = (f.get("role") or "").strip()
            clean = not _mentions(r, codes) and not _single_stations(r, codes, [])  # the role names no section
            role_spans += [(s, e, meta["words"], clean) for s, e in _occ(text, r)]

    def own_word(m, wd: str) -> bool:
        """The word is a field's own naming word inside a section-free role, right before a number or the field's
        other words ('the loudest 10 %', 'maximum tracker error'), or right after a number ('10 % loudest')."""
        for s, e, words_, clean in role_spans:
            if not (s <= m.start() and m.end() <= e and clean and any(_match(wd, w) or _match(w, wd) for w in words_)):
                continue
            nxt = re.match(r"\s*([-+−]?\d|[A-Za-z]+)", text[m.end():])
            nw = nxt.group(1).lower() if nxt else ""
            if re.search(r"\d\s*(?:%|percent|per cent)?\s*$", text[:m.start()]) or (nw and (
                    nw[0].isdigit() or nw[0] in "-+−" or any(_match(nw, w) for w in words_ + ["second", "seconds"]))):
                return True
        return False
    for pat, why in ((SUPERLATIVE, "ranks quantities; the stats hold no ranking: compare two declared quantities "
                                   "instead (A was louder than B)"),
                     (CHANGE, "states a direction of change; write it as a declared comparison (A was higher than B)")):
        for m in pat.finditer(masked_cmp):
            wd = m.group().lower()
            if not own_word(m, wd):
                R.append(f"{wd!r} {why}")
    # ---- identifiers with digits: only this ride's id (or a split-run partner it cites), and p90 / L10 / p50-style
    # tokens only inside the role of a fact whose field they name
    refs = {f.get("stat_key") for f in facts} | {(c.get(s) or {}).get("stat_key") for c in cmps for s in "ab"} | set(cites)
    refs.discard(None)
    run_id = str((stats.get("ride.id") or {}).get("value") or "").upper()
    partners = {str(st["value"]).upper() for k, st in stats.items() if re.fullmatch(r"qc\.\w+_with", k) and k in refs}
    id_spans = []
    for m in V._ID_PAT.finditer(text):
        id_spans.append(m.span())
        if re.fullmatch(r"R\d+(?:\.\d)?", m.group(), re.I):
            if m.group().upper() not in {run_id} | partners:
                R.append(f"{m.group()!r} is another ride: this report is about {run_id or 'one ride'} only (a split-run "
                         "partner may be named only when its qc.*_with stat is cited)")
        else:
            R.append(f"{m.group()!r} is an identifier with a number that no stat of this ride holds: drop it")
    for m in re.finditer(r"(?<![\w.])[A-Za-z]+\d[\w]*", text):
        tk = m.group()
        if any(s <= m.start() < e for s, e in id_spans) or re.fullmatch(r"[xX]\d+", tk):
            continue
        if not any(s <= m.start() and m.end() <= e and tk.lower() in [w.lower() for w in words_]
                   for s, e, words_, _ in role_spans):
            R.append(f"{tk!r} must be a naming word of the number it labels (inside that fact's role, and one of that "
                     "field's words, e.g. 'p90' for p90_m): otherwise it names a statistic the sentence does not give")
    for m in _ROMAN.finditer(text):
        if m.group() and _unit_after(text, m.end(), True) is not None:
            R.append(f"{m.group()!r} is a number in Roman numerals: write digits and declare it")
    # ---- qualifiers required by what is cited
    need = {}
    for f in facts:
        _, meta, err = field_of(stats, f.get("stat_key", ""), f.get("field", ""))
        if meta:
            for q in meta["requires"]:
                need.setdefault(q, f"{f['stat_key']}.{f['field']}")
    for c in cmps:
        for s in ("a", "b"):
            sd = c.get(s) or {}
            _, meta, err = field_of(stats, sd.get("stat_key", ""), sd.get("field", ""))
            if meta:
                for q in meta["requires"]:
                    need.setdefault(q, f"{sd['stat_key']}.{sd['field']}")
    for k in cites:
        meta = ((stats.get(k) or {}).get("fields") or {}).get("value")
        if meta:
            for q in meta["requires"]:
                need.setdefault(q, f"{k} (cited)")
    for q, src in need.items():
        if q not in quals:
            R.append(f"qualifier {q!r} is required by {src}: add it to qualifiers (it is shown as a badge on the sentence)")
    # ---- wording rules
    for pat, msg in RIDE_RULES:
        if re.search(pat, text, re.I | re.S):
            R.append(f"ride rule: {msg}")
    if _DATE.search(text):
        R.append("ride rule: a ride claim carries no dates (the stats contain none)")
    if _GLUED_CODE.search(text):
        R.append(f"ride rule: {_GLUED_CODE.search(text).group()!r}: a word is glued to a station code; put a space before it")
    if typ == "measured":
        if not refs:
            R.append("a measured claim must declare at least one fact, comparison or cited stat")
        if re.search(CONCLUDES, text, re.I):
            R.append("ride rule: a 'measured' claim restates stats; put a conclusion ('indicates', 'suggests', "
                     "'likely', 'means') in an inference")
        if re.search(CAUSAL, text, re.I):
            R.append(f"ride rule: {re.search(CAUSAL, text, re.I).group()!r} states a cause; the stats hold no causes "
                     "(a flagged overlap is not 'caused by' the pause): restate it or make it a hedged inference")
    if re.search(POSITIONING, text, re.I) and not any(k.startswith("positioning.") for k in refs):
        R.append("ride rule: a statement about positioning or the tracker must reference a positioning.* stat")
    lower = [k for k in stats if k.endswith(".recorded_span_s") or k.startswith("qc.section_truncated")]
    if any(k in refs for k in lower) and re.search(LOWER_BOUND_BAN, text, re.I):
        R.append(f"ride rule: a truncated section's recorded span is a lower bound: no on-time / scheduled / normal / "
                 f"complete / full / at most / up to wording in that sentence (found "
                 f"{re.search(LOWER_BOUND_BAN, text, re.I).group()!r})")
    for k in (k for k in lower if k in refs):
        d = stats[k]["detail"]
        miss = d.get("missing_end") or ((stats.get(f"section.{d.get('section')}.recorded_span_s") or {}).get("detail")
                                        or {}).get("missing_end")
        if not miss:
            continue
        for m in re.finditer(rf"\b{'arriv' if miss == 'arrival' else 'depart'}\w*", text, re.I):
            if re.match(r"\s+(?:was\s+|is\s+)?(?:not\s+(?:observed|recorded|captured|seen)|unobserved|missing)\b",
                        text[m.end():], re.I) or re.search(r"\b(?:no|without|missing|unobserved)\s+$", text[:m.start()], re.I):
                continue
            R.append(f"ride rule: {m.group()!r}: the {miss} of {k.split('.')[1]} was not observed (the span ends at the "
                     f"recording's edge); write '({miss} not observed)', never a time of {miss}")
            break
    for m in re.finditer(RECOMMEND, text, re.I):  # positive usability wording on a stat that says otherwise
        if _negated(neg_text, m.start()):
            continue
        s, e = _segment(text, m.start())
        named = {sec for _, _, sec in _mentions(text[s:e], codes)}
        for k in sorted(refs):
            st = stats.get(k) or {}
            if not (st.get("value") == "not evaluated" or (st.get("detail") or {}).get("excluded_from_averages") is True):
                continue
            sm = re.match(r"(?:section|positioning)\.([A-Z]{3})-([A-Z]{3})\.", k)
            if sm is None or not named or frozenset(sm.groups()) in named:
                R.append(f"ride rule: {m.group()!r} calls a result usable, but {k} is "
                         f"{'not evaluated' if st.get('value') == 'not evaluated' else 'excluded from averages'}: "
                         "say what is excluded or not evaluated instead")
                break
    if re.search(RECOMMEND, text, re.I):
        if not any(f.get("stat_key") == "sync.offset_s" and f.get("field") == "uncertainty_s" for f in facts):
            R.append("ride rule: a trust / usable statement must state the sync uncertainty as a fact "
                     "(sync.offset_s, field uncertainty_s)")
        if lower and not any(k in refs for k in lower):
            R.append("ride rule: this ride has a truncated section: a trust / usable statement must reference it "
                     f"({', '.join(lower)}) with the lower_bound qualifier")
        if re.search(POSITIONING, text, re.I):
            cav = [k for k, s in stats.items() if k.startswith("positioning.") and isinstance(s.get("detail"), dict) and (
                "stress" in k or (s["detail"].get("band90_coverage") or 1) < 0.9 or s["detail"].get("departure_missed_online"))]
            miss = [k for k in cav if k not in refs]
            if miss:
                R.append("ride rule: a usable / trustworthy statement about the tracker must reference this ride's "
                         f"caveated tracker results (stress test, 90 % band coverage below 0.9, departure missed): {miss}")
    return {"id": cl.get("id"), "ok": not R, "reasons": R, "trace": trace, "comparisons": cmp_info}


# --------------------------------------------------------------------------- prompt
GLOSSARY = """QC flag meanings (outputs/qc.json, derived from the sensors and the phyphox event log):
pause: phyphox paused (e.g. a battery alert): no motion/pressure data for that time, audio kept running.
false_start: phyphox recorded a second or so, paused, then restarted. section_overlaps_pause: a pause fell
inside that section. section_truncated: the recording started/ended while the train was moving, so one end
of the section is missing (QC excludes it from tracker scoring). handheld / handled_mid_ride / walking_mid_file:
the phone was in a hand, touched, or carried. reorientation: the phone was turned to a new position.
floor_like_placement: phone level and vibration-isolated, consistent with lying on the floor or a soft surface
(excluded from noise averages). atypical_coupling: the phone sat on a soft or rattling support (noise OK,
vibration features normalised). slow_running: a section much slower than its median on other runs.
unplanned_stop: the train stood still between stations. magnetometer_reset: the iOS calibration jumped.
split_run / continuation: one journey recorded in two files. Times t0_s/t1_s are seconds of phyphox
experiment time (which does not advance during a pause).
Sync: method and tap_markers_matched in sync.offset_s say how the audio and sensor clocks were aligned;
uncertainty_s is how far the offset could be off. Tracker errors are against an independent reference made from
the ride's own gyro curve edges (not surveyed ground truth; there is none in the tunnel), in straight-line chord
metres; 'not evaluated' means no score exists for that section.
Sections: section.X.duration_s is a departure-to-arrival time (duration_status complete) and may be compared with
timetable_expected_s. section.X.recorded_span_s is only the part of a section that was recorded (duration_status
lower_bound): never a section time, never compared. segmentation 'low_confidence' (segmentation_confidence below
0.9) means the section boundaries are uncertain: that section's times are approximate.
Differences: trip.contrast_db = minuend_section level minus subtrahend_section level; noise.moving_minus_dwell_db =
moving_level minus dwell_level; minus_section_mean_db = this level minus section_mean_all_runs. To say which is
louder, compare the two level fields with a comparison (the code recomputes it)."""

EXAMPLES = [  # checked by --selftest (C1 on R14's stats, C2-C3 on R11's: they must pass), shown to Argon as the format
    {"id": "C1", "type": "measured", "text": "Phyphox logged 1 pause, lasting 15.2 s from 47.2 s.",
     "qualifiers": [], "cites": [], "comparisons": [],
     "facts": [{"stat_key": "ride.phyphox_pauses", "field": "value", "value_as_written": "1", "role": "pause"},
               {"stat_key": "qc.pause.1", "field": "value", "value_as_written": "15.2", "role": "lasting"},
               {"stat_key": "qc.pause.1", "field": "t0_s", "value_as_written": "47.2", "role": "from"}]},
    {"id": "C2", "type": "measured",
     "text": "LST-BTH (-18.78 dBFS) was louder than BTH-MLE (-19.45 dBFS), a 0.67 dB difference in relative level.",
     "qualifiers": ["relative_level"], "cites": [],
     "facts": [{"stat_key": "section.LST-BTH.level", "field": "value", "value_as_written": "-18.78", "role": "LST-BTH"},
               {"stat_key": "section.BTH-MLE.level", "field": "value", "value_as_written": "-19.45", "role": "BTH-MLE"}],
     "comparisons": [{"a": {"stat_key": "section.LST-BTH.level", "field": "value"},
                      "b": {"stat_key": "section.BTH-MLE.level", "field": "value"}, "relation": "greater",
                      "relation_word": "louder", "a_text": "LST-BTH", "b_text": "BTH-MLE", "difference_as_written": "0.67"}]},
    {"id": "C3", "type": "measured", "text": "LST-BTH took 143.0 s whereas only 118.8 s of BTH-MLE was recorded (arrival not observed).",
     "qualifiers": ["lower_bound", "approximate"], "cites": [], "comparisons": [],
     "facts": [{"stat_key": "section.LST-BTH.duration_s", "field": "value", "value_as_written": "143.0", "role": "took"},
               {"stat_key": "section.BTH-MLE.recorded_span_s", "field": "value", "value_as_written": "118.8", "role": "only"}]},
]

SYSTEM = f"""You write the ride report of Tubesense: a short, plain-English account of ONE recorded London
Underground ride for a non-specialist reader (a London Assembly researcher or a hackathon judge), based ONLY on
the ride's derived statistics. A deterministic checker verifies every claim against the stats AND against your
declaration of what each number is; a person reads the report before it is used.

Write 4-8 claims, one sentence each, in this order where the stats allow: which ride and route; how messy the
recording was (the QC flags that matter, with times and durations); how well audio and sensors are synchronised;
the sections and their timing; how well the tracker positioned the train (or that it was not evaluated, and why);
the relative noise picture; what a user should exclude or trust.

Each claim is {{id, text, type, qualifiers, facts, comparisons, cites}}:
- facts: ONE entry for EVERY number in the text (digits, percentages, or number words), in any order:
  {{stat_key, field, value_as_written, role}}. field is "value", "n" or a detail field listed in that stat's
  "fields". value_as_written is the number exactly as in the text, with its sign ("-24.7", "15.2", "34.2").
  role is 1-4 words COPIED EXACTLY from the sentence, directly next to that number, that say what it is, and it
  must contain one of that field's "words" (e.g. "paused for" 15.2 s, "at" 47.2 s, "tracker median error" 1.7 m,
  "timetable baseline" 17.6 m, "LST-BTH" for a section level written right after the section name). No other
  number may stand between a number and its role. A number not in facts, or a fact whose number is not in the
  text, rejects the claim.
- The number must equal the stored field at the precision you write (47.15 may be written 47.2 or 47; 0.276 as
  27.6 %), with the stored sign (a dBFS level is negative: "-24.7 dBFS"). Write the field's unit right after the
  number: s for s fields, m for m, % for pct/fraction, dBFS for phone levels (unit dBFS), dB for dB differences;
  count fields as "N stops" etc. No arithmetic of your own, except a comparison's difference.
- comparisons: ONE entry for EVERY comparative statement (louder, quieter, higher, lower, longer, shorter, more,
  fewer, above, below, faster, slower, similar, and every "than"). {{a, b, relation, relation_word, a_text, b_text,
  difference_as_written}}: a and b are {{stat_key, field}} of two quantities of the same unit (e.g. two section
  level values, moving_level vs dwell_level, a section duration vs its timetable_expected_s, a tracker median vs
  timetable_baseline_median_m). Write the sentence as "<a_text> was <relation_word> than <b_text>" (or "compared
  with <b_text>, <a_text> was <relation_word>"); a_text/b_text are copied from the sentence and name the side
  (its section, or its field words). relation is a relative to b. The code recomputes it from the stored values.
  difference_as_written is the difference as written ("0.67") or "".
  Two levels within 0.25 dB of each other are about_equal ("similar"), never louder/quieter.
  Never compare a lower_bound field. Do not write "better" or "worse". No superlatives (loudest, longest,
  highest, ...) and no change verbs (rose, fell, dropped, increased, ...): the stats hold no ranking or trend, so
  state a direction only as a declared two-sided comparison. (A field's own words are fine inside its role, e.g.
  "the loudest 10 % of seconds" for limiter_pct_loudest_10pct.)
- qualifiers: every field you cite lists "requires" in its metadata; put each required qualifier in the claim's
  qualifiers (relative_level for any dB/dBFS number, lower_bound for a truncated section's recorded span,
  approximate for a low-confidence section time, excluded_from_averages, stress_test). They are shown as badges.
  You may add single_ride.
- cites: stat keys the sentence relies on without quoting a number (e.g. positioning.X.status).
- type "measured": a fact from the stats; no "indicates", "suggests", "likely", "means". type "inference": your
  hedged reading; it follows the same number rules.

Rules:
- Write numbers as digits (not "two", "twice", "half", "once", "tenfold", "XV"); no ordinals ("2nd"); no dates.
  "no pauses" / "no tunnel stops" is the number 0: declare it as a fact with value_as_written "no". No vague
  quantities (most, majority, several, few, many, dozens, hundreds): write the number.
- Units: every s, m, %, dB, dBFS value carries its unit (s, m, %, dB, dBFS); no other units (ms, km, cm, h, hours).
- The role sits next to its number: at most 4 words between them, no comma, colon or bracket between them except a
  "(" right before the number ("LST-BTH (-18.78 dBFS)"), and no other quantity's words between them. A point in time
  (fields marked instant: t0_s, t1_s, departure/arrival/span times, a flag's event time) takes "at" / "from" / "to",
  never "for", "lasted" or "duration".
- No negation: no not / no / never / without / false / "it is false that" in the clause before a number or a
  comparative. Allowed: "not evaluated", "not observed", "no tracker map", "without tap markers". State facts
  positively.
- Direction, ranking and equality words follow the comparison rules: led, ahead, behind, outpaced, eclipsed,
  stronger, weaker count as comparatives; top, ranked, first, last are superlatives; improved, worsened, went up /
  down are change verbs; similar, the same as, in line with, close to, as loud as, matches need a declared
  about_equal comparison (the code checks the two values are within tolerance).
- Differences (trip.contrast_db, noise.moving_minus_dwell_db, minus_section_mean_db, level_minus_trip_mean_db): if
  you write "X minus Y" or "between X and Y", X must be the stored minuend ("LST-BTH minus BTH-MLE", "moving minus
  dwell"); otherwise write no "minus".
- Name no other ride (only this ride's own id), no WTT / # numbers; "p90", "L10", "p50" only inside the role of the
  field they name.
- A truncated section: "only 118.8 s of X was recorded (arrival not observed)", never "took", never a time of the
  missing end ("until arrival at"), and no "on time", "scheduled", "normal", "complete", "in full", "whole",
  "at most" or "up to" in that sentence; its span times need the lower_bound qualifier too.
- A measured claim states no cause (caused, due to, led to, because of): the stats hold none.
- Usability words (trusted, usable, reliable, robust, valid, accurate, good enough, ...) never on a section that is
  "not evaluated" or excluded_from_averages; no generalisation beyond this ride (across the Tube, the network, all
  sections, always).
- Any sentence about the tracker or positioning references a positioning.* stat (fact or cites). Any statement
  that something can be trusted / used must state the sync uncertainty as a fact (sync.offset_s, uncertainty_s),
  reference the ride's truncated section if there is one, and reference stress-test / low-coverage tracker results.
- Phone levels are uncalibrated dBFS(A): say "relative"; never compare with TfL's dB figures, HSE values, or any
  absolute dB(A); never mention corrugation or hearing; no other line; no "very loud".
- Never say "proves", "accurate to", "ground truth". Describe the ride, not the person (no name, pronoun, time of day).

FORMAT EXAMPLES (the format only: these numbers belong to another ride; use this ride's stats):
{json.dumps(EXAMPLES, indent=1)}

{GLOSSARY}"""


CANARY = {"id": "CANARY", "type": "measured", "seeded": True, "qualifiers": [], "comparisons": [], "cites": [],
          "facts": [{"stat_key": "noise.level_overall", "field": "value", "value_as_written": "92.8", "role": "reached"}],
          "text": "This ride reached 92.8 dB(A), which proves the section breaks the HSE limit."}


# --------------------------------------------------------------------------- selftest (offline)
def F(key, field, written, role):
    return {"stat_key": key, "field": field, "value_as_written": written, "role": role}


def C(a, b, rel, word, at, bt, diff=""):
    return {"a": {"stat_key": a[0], "field": a[1]}, "b": {"stat_key": b[0], "field": b[1]}, "relation": rel,
            "relation_word": word, "a_text": at, "b_text": bt, "difference_as_written": diff}


def T(run, text, facts=(), cmps=(), quals=(), cites=(), typ="measured", want=True, note=""):
    return {"run": run, "want": want, "note": note,
            "claim": {"id": "T", "type": typ, "text": text, "facts": list(facts), "comparisons": list(cmps),
                      "qualifiers": list(quals), "cites": list(cites)}}


P1 = F("qc.pause.1", "value", "15.2", "paused for")
P1T = F("qc.pause.1", "t0_s", "47.2", "at")
LVL_A = ("section.LST-BTH.level", "value")
LVL_B = ("section.BTH-MLE.level", "value")
TRK = ("positioning.LST-BTH.loo_median_m", "value")
DUR = ("section.LST-BTH.duration_s", "value")
SPAN = ("section.BTH-MLE.recorded_span_s",)
REL = ["relative_level"]
SELFTEST = [
    # --- 1:1 numbers, glued units, spelled-out numbers
    T("R14", "The phone paused for 15.2 s at 47.2 s.", [P1, P1T], note="baseline good claim"),
    T("R14", "The phone paused for 15.2s at 47.2s.", [P1, P1T], note="glued units are read"),
    T("R14", "The phone paused for 99s at 47.2 s.", [P1T], want=False, note="glued unlisted number"),
    T("R14", "The phone paused for 15.2 s at 47.2 s, 3 times.", [P1, P1T], want=False, note="unlisted number"),
    T("R14", "The phone paused for 15.2 s at 47.2 s.", [P1, P1T, F("qc.flag_counts", "value", "5", "flags")],
      want=False, note="listed number missing from text"),
    T("R14", "Phyphox paused once, for 15.2 s.", [F("qc.pause.1", "value", "15.2", "for")], want=False,
      note="spelled-out 'once' not declared"),
    T("R14", "Phyphox paused once, for 15.2 s.", [F("ride.phyphox_pauses", "value", "once", "paused"),
                                                   F("qc.pause.1", "value", "15.2", "for")], note="spelled-out declared"),
    T("R14", "Phyphox paused twice, for 15.2 s.", [F("ride.phyphox_pauses", "value", "twice", "paused"),
                                                    F("qc.pause.1", "value", "15.2", "for")], want=False,
      note="spelled-out wrong value"),
    T("R14", "Phyphox paused for fifteen seconds.", [], want=False, note="spelled-out number unlisted"),
    T("R14", "The pause lasted twenty-one s.", [F("qc.pause.1", "value", "twenty-one", "pause lasted")], want=False,
      note="compound number word"),
    T("R14", "The pause lasted 15.2 s and the 2nd section was slower.", [F("qc.pause.1", "value", "15.2", "pause lasted")],
      want=False, note="ordinal"),
    T("R11", "The rattle ratio was 4.19x the median.", [F("qc.atypical_coupling.1", "rattle_vs_median", "4.19", "rattle ratio")],
      note="glued ratio"),
    T("R11", "The rattle ratio was x4.19.", [F("qc.atypical_coupling.1", "rattle_vs_median", "4.19", "rattle ratio")],
      note="prefixed ratio"),
    T("R11", "The rattle ratio was 9x the median.", [F("qc.atypical_coupling.1", "rattle_vs_median", "9", "rattle ratio")],
      want=False, note="ratio value wrong"),
    T("R11", "The handling interval ran from 9.01 to 10.51 s.",
      [F("qc.handled_mid_ride.1", "t0_s", "9.01", "from"), F("qc.handled_mid_ride.1", "t1_s", "10.51", "to")],
      note="range shares its unit"),
    T("R14", "The phone paused 47.2 s into the ride.", [F("qc.pause.1", "t0_s", "47.2", "paused")], want=False,
      note="time put on the duration's wording"),
    T("R14", "The phone paused for 47.2 s at 15.2 s.", [F("qc.pause.1", "t0_s", "47.2", "at"), F("qc.pause.1", "value", "15.2", "paused for")],
      want=False, note="duration and time swapped"),
    # --- value, sign, unit
    T("R14", "The relative level was -24.7dBFS(A).", [F("noise.level_overall", "value", "-24.7", "relative level")], quals=REL,
      note="glued dBFS"),
    T("R14", "The relative level was -24.7 dBFS(A).", [F("noise.level_overall", "value", "-24.7", "relative level")],
      want=False, note="missing relative_level qualifier"),
    T("R14", "The overall relative level was 24.7 dBFS(A).", [F("noise.level_overall", "value", "24.7", "overall relative level")],
      quals=REL, want=False, note="sign dropped"),
    T("R14", "The overall relative level was +24.7 dBFS(A).", [F("noise.level_overall", "value", "+24.7", "overall relative level")],
      quals=REL, want=False, note="sign reversed"),
    T("R14", "The overall level was -24.7 dB(A).", [F("noise.level_overall", "value", "-24.7", "overall level")], quals=REL,
      want=False, note="dB(A) and dB written for a dBFS field"),
    T("R14", "The overall relative level was -33dBFS(A).", [F("noise.level_overall", "value", "-33", "overall relative level")],
      quals=REL, want=False, note="wrong value, glued"),
    T("R14", "The pause lasted 15.2 m.", [F("qc.pause.1", "value", "15.2", "pause lasted")], want=False, note="metres on a seconds field"),
    T("R14", "The pause lasted 15.2 %.", [F("qc.pause.1", "value", "15.2", "pause lasted")], want=False, note="% on a seconds field"),
    T("R14", "The limiter engaged in 27.6 % of seconds.", [F("noise.limiter_fraction", "value", "27.6", "limiter engaged")],
      note="% from a fraction field"),
    T("R14", "The limiter engaged in 27.6 of seconds.", [F("noise.limiter_fraction", "limiter_pct", "27.6", "limiter engaged")],
      want=False, note="pct field written without %"),
    T("R14", "The limiter engaged in 68.8% of seconds overall.",
      [F("noise.limiter_fraction", "limiter_pct_loudest_10pct", "68.8", "limiter engaged")], want=False,
      note="loudest-subset share put on the overall share"),
    T("R14", "The limiter engaged in 68.8% of the loudest 10% of seconds and 27.6% of all seconds.",
      [F("noise.limiter_fraction", "limiter_pct_loudest_10pct", "68.8", "loudest"),
       F("noise.limiter_fraction", "loudest_subset_pct", "10", "loudest"),
       F("noise.limiter_fraction", "limiter_pct", "27.6", "all seconds")], note="three shares bound right"),
    T("R11", "The LST-BTH section had 1 tunnel stop.", [F("section.LST-BTH.duration_s", "tunnel_stops", "1", "tunnel stop")],
      want=False, note="count wrong"),
    T("R11", "The LST-BTH section had 0 tunnel stops.", [F("section.LST-BTH.duration_s", "tunnel_stops", "0", "tunnel stops")],
      note="count right"),
    T("R11", "The LST-BTH section had 0 tunnel stops.", [F("section.LST-BTH.duration_s", "segmentation_confidence", "0", "tunnel stops")],
      want=False, note="confidence read as a count"),
    # --- number on the wrong quantity (tracker)
    T("R4", "The tracker median error on MLE-BTH was 33.8 m against a timetable baseline median of 11.3 m.",
      [F("positioning.MLE-BTH.stress_median_m", "timetable_baseline_median_m", "33.8", "timetable baseline median"),
       F("positioning.MLE-BTH.stress_median_m", "value", "11.3", "tracker median error")], quals=["stress_test"], want=False,
      note="tracker and baseline swapped"),
    T("R4", "The tracker median error on MLE-BTH was 11.3 m against a timetable baseline median of 33.8 m.",
      [F("positioning.MLE-BTH.stress_median_m", "value", "11.3", "tracker median error"),
       F("positioning.MLE-BTH.stress_median_m", "timetable_baseline_median_m", "33.8", "timetable baseline median")],
      quals=["stress_test"], note="tracker and baseline right"),
    T("R4", "The tracker median error on MLE-BTH was 11.3 m against a timetable baseline median of 33.8 m.",
      [F("positioning.MLE-BTH.stress_median_m", "value", "11.3", "tracker median error"),
       F("positioning.MLE-BTH.stress_median_m", "timetable_baseline_median_m", "33.8", "timetable baseline median")],
      want=False, note="stress-test qualifier missing"),
    T("R4", "The tracker median error on MLE-BTH was 126.1 m.",
      [F("positioning.MLE-BTH.stress_median_m", "p90_m", "126.1", "tracker median error")], quals=["stress_test"], want=False,
      note="p90 called the median"),
    T("R4", "The tracker p90 error on MLE-BTH was 126.1 m.",
      [F("positioning.MLE-BTH.stress_median_m", "p90_m", "126.1", "p90 error")], quals=["stress_test"], note="p90 named"),
    T("R4", "The tracker median error on BTH-LST was 11.3 m.",
      [F("positioning.MLE-BTH.stress_median_m", "value", "11.3", "tracker median error")], quals=["stress_test"], want=False,
      note="number from the other section"),
    T("R11", "On LST-BTH, the tracker median error was 1.7 m versus a timetable baseline of 17.6 m, while BTH-MLE was not evaluated.",
      [F("positioning.LST-BTH.loo_median_m", "value", "1.7", "tracker median error"),
       F("positioning.LST-BTH.loo_median_m", "timetable_baseline_median_m", "17.6", "timetable baseline")],
      cites=["positioning.BTH-MLE.status"], note="section named once, before the numbers"),
    T("R11", "BTH-MLE took 143.0 s.", [F("section.LST-BTH.duration_s", "value", "143.0", "took")], want=False,
      note="section time from the other section"),
    # --- comparisons: recomputed, any word order
    T("R11", "LST-BTH (-18.78 dBFS) was louder than BTH-MLE (-19.45 dBFS).",
      [F(*LVL_A, "-18.78", "LST-BTH"), F(*LVL_B, "-19.45", "BTH-MLE")],
      [C(LVL_A, LVL_B, "greater", "louder", "LST-BTH", "BTH-MLE")], quals=REL, note="comparison right"),
    T("R11", "BTH-MLE (-19.45 dBFS) was louder than LST-BTH (-18.78 dBFS).",
      [F(*LVL_A, "-18.78", "LST-BTH"), F(*LVL_B, "-19.45", "BTH-MLE")],
      [C(LVL_B, LVL_A, "greater", "louder", "BTH-MLE", "LST-BTH")], quals=REL, want=False, note="reversed, declared honestly"),
    T("R11", "BTH-MLE (-19.45 dBFS) was louder than LST-BTH (-18.78 dBFS).",
      [F(*LVL_A, "-18.78", "LST-BTH"), F(*LVL_B, "-19.45", "BTH-MLE")],
      [C(LVL_A, LVL_B, "greater", "louder", "LST-BTH", "BTH-MLE")], quals=REL, want=False,
      note="reversed text, declaration flipped to pass"),
    T("R11", "Compared with LST-BTH (-18.78 dBFS), BTH-MLE (-19.45 dBFS) was quieter.",
      [F(*LVL_A, "-18.78", "LST-BTH"), F(*LVL_B, "-19.45", "BTH-MLE")],
      [C(LVL_B, LVL_A, "less", "quieter", "BTH-MLE", "LST-BTH")], quals=REL, note="other word order, right"),
    T("R11", "Compared with LST-BTH (-18.78 dBFS), BTH-MLE (-19.45 dBFS) was louder.",
      [F(*LVL_A, "-18.78", "LST-BTH"), F(*LVL_B, "-19.45", "BTH-MLE")],
      [C(LVL_A, LVL_B, "greater", "louder", "LST-BTH", "BTH-MLE")], quals=REL, want=False,
      note="other word order, reversed, declaration flipped"),
    T("R11", "BTH-MLE was louder than LST-BTH on this trip.", [], [], quals=REL, cites=["trip.contrast_db"], want=False,
      note="comparative without a declared comparison"),
    T("R11", "BTH-MLE was not louder than LST-BTH.", [], [C(LVL_B, LVL_A, "less", "louder", "BTH-MLE", "LST-BTH")], quals=REL,
      want=False, note="negated comparative"),
    T("R11", "LST-BTH was 0.67 dB louder than BTH-MLE.", [], [C(LVL_A, LVL_B, "greater", "louder", "LST-BTH", "BTH-MLE", "0.67")],
      quals=REL, note="difference right"),
    T("R11", "BTH-MLE was 0.67 dB louder than LST-BTH.", [], [C(LVL_B, LVL_A, "greater", "louder", "BTH-MLE", "LST-BTH", "0.67")],
      quals=REL, want=False, note="difference with reversed direction"),
    T("R11", "LST-BTH was 0.9 dB louder than BTH-MLE.", [], [C(LVL_A, LVL_B, "greater", "louder", "LST-BTH", "BTH-MLE", "0.9")],
      quals=REL, want=False, note="difference wrong"),
    T("R11", "The relative contrast (LST-BTH minus BTH-MLE) was -0.67 dB.", [F("trip.contrast_db", "value", "-0.67", "relative contrast")],
      quals=REL, want=False, note="difference sign reversed"),
    T("R11", "Moving audio (-19.1 dBFS) was louder than platform dwells (-25.0 dBFS).",
      [F("noise.moving_minus_dwell_db", "moving_level", "-19.1", "Moving audio"),
       F("noise.moving_minus_dwell_db", "dwell_level", "-25.0", "platform dwells")],
      [C(("noise.moving_minus_dwell_db", "moving_level"), ("noise.moving_minus_dwell_db", "dwell_level"), "greater", "louder",
         "Moving audio", "platform dwells")], quals=REL, note="moving vs dwell right"),
    T("R11", "Platform dwells (-25.0 dBFS) were louder than moving audio (-19.1 dBFS).",
      [F("noise.moving_minus_dwell_db", "moving_level", "-19.1", "moving audio"),
       F("noise.moving_minus_dwell_db", "dwell_level", "-25.0", "Platform dwells")],
      [C(("noise.moving_minus_dwell_db", "dwell_level"), ("noise.moving_minus_dwell_db", "moving_level"), "greater", "louder",
         "Platform dwells", "moving audio")], quals=REL, want=False, note="moving vs dwell reversed"),
    T("R11", "LST-BTH took 143.0 s, shorter than the timetable's 159.8 s.",
      [F("section.LST-BTH.duration_s", "value", "143.0", "took"),
       F("section.LST-BTH.duration_s", "timetable_expected_s", "159.8", "timetable's")],
      [C(("section.LST-BTH.duration_s", "value"), ("section.LST-BTH.duration_s", "timetable_expected_s"), "less", "shorter",
         "LST-BTH", "timetable's")], note="duration vs timetable right"),
    T("R11", "LST-BTH took 143.0 s, longer than the timetable's 159.8 s.",
      [F("section.LST-BTH.duration_s", "value", "143.0", "took"),
       F("section.LST-BTH.duration_s", "timetable_expected_s", "159.8", "timetable's")],
      [C(("section.LST-BTH.duration_s", "value"), ("section.LST-BTH.duration_s", "timetable_expected_s"), "greater", "longer",
         "LST-BTH", "timetable's")], want=False, note="duration vs timetable reversed"),
    T("R11", "LST-BTH (-18.78 dBFS) was similar to BTH-MLE (-19.45 dBFS).",
      [F(*LVL_A, "-18.78", "LST-BTH"), F(*LVL_B, "-19.45", "BTH-MLE")],
      [C(LVL_A, LVL_B, "about_equal", "similar", "LST-BTH", "BTH-MLE")], quals=REL, want=False,
      note="0.67 dB apart called similar"),
    T("R14", "WCL-ALE (-24.54 dBFS) was similar to ALE-LST (-24.65 dBFS).",
      [F("section.WCL-ALE.level", "value", "-24.54", "WCL-ALE"), F("section.ALE-LST.level", "value", "-24.65", "ALE-LST")],
      [C(("section.WCL-ALE.level", "value"), ("section.ALE-LST.level", "value"), "about_equal", "similar", "WCL-ALE", "ALE-LST")],
      quals=REL, note="0.11 dB apart called similar"),
    T("R14", "WCL-ALE (-24.54 dBFS) was louder than ALE-LST (-24.65 dBFS) by 0.11 dB.",
      [F("section.WCL-ALE.level", "value", "-24.54", "WCL-ALE"), F("section.ALE-LST.level", "value", "-24.65", "ALE-LST")],
      [C(("section.WCL-ALE.level", "value"), ("section.ALE-LST.level", "value"), "greater", "louder", "WCL-ALE", "ALE-LST", "0.11")],
      quals=REL, want=False, note="0.11 dB gap given a direction (within the 0.25 dB band)"),
    T("R11", "BTH-MLE was exceeded by LST-BTH.", [], [C(LVL_A, LVL_B, "greater", "exceeded", "LST-BTH", "BTH-MLE")], quals=REL,
      want=False, note="passive comparison"),
    T("R11", "LST-BTH was louder than BTH-MLE, which was louder than the trip mean.", [],
      [C(LVL_A, LVL_B, "greater", "louder", "LST-BTH", "BTH-MLE")], quals=REL, want=False,
      note="second comparison not declared"),
    T("R11", "BTH-MLE was the loudest section at -19.45 dBFS.", [F(*LVL_B, "-19.45", "BTH-MLE")], quals=REL, want=False,
      note="false superlative (a ranking outside the schema)"),
    T("R11", "LST-BTH was the loudest section at -18.78 dBFS.", [F(*LVL_A, "-18.78", "LST-BTH")], quals=REL, want=False,
      note="true superlative still refused (no ranking stat)"),
    T("R11", "BTH-MLE (-19.45 dBFS) topped LST-BTH (-18.78 dBFS).", [F(*LVL_A, "-18.78", "LST-BTH"), F(*LVL_B, "-19.45", "BTH-MLE")],
      quals=REL, want=False, note="direction verb, undeclared"),
    T("R11", "BTH-MLE (-19.45 dBFS) topped LST-BTH (-18.78 dBFS).", [F(*LVL_A, "-18.78", "LST-BTH"), F(*LVL_B, "-19.45", "BTH-MLE")],
      [C(LVL_B, LVL_A, "greater", "topped", "BTH-MLE", "LST-BTH")], quals=REL, want=False, note="direction verb, reversed"),
    T("R11", "BTH-MLE (-19.45 dBFS) trailed LST-BTH (-18.78 dBFS).", [F(*LVL_A, "-18.78", "LST-BTH"), F(*LVL_B, "-19.45", "BTH-MLE")],
      [C(LVL_B, LVL_A, "less", "trailed", "BTH-MLE", "LST-BTH")], quals=REL, note="direction verb, right"),
    T("R11", "The relative level rose by 0.67 dB from BTH-MLE to LST-BTH.", [F("trip.contrast_db", "value", "0.67", "relative level rose by")],
      quals=REL, want=False, note="change verb carries a direction outside the schema"),
    T("R14", "The pause of 15.2 s was a third of the limiter share.", [F("qc.pause.1", "value", "15.2", "pause of")], want=False,
      note="spelled-out fraction unlisted"),
    T("R14", "The overall relative level was -24.7.", [F("noise.level_overall", "value", "-24.7", "overall relative level")],
      quals=REL, want=False, note="level without its unit"),
    T("R14", "The phone paused for 15.2 s at 47.2 s.", [F("qc.pause.1", "value", "15.2", "pause length"), P1T], want=False,
      note="role not copied from the sentence"),
    T("R5", "The tracker median error on BTH-MLE (2.4 m) was lower than its p90 error (49.9 m).",
      [F("positioning.BTH-MLE.loo_median_m", "value", "2.4", "tracker median error"),
       F("positioning.BTH-MLE.loo_median_m", "p90_m", "49.9", "p90 error")],
      [C(("positioning.BTH-MLE.loo_median_m", "value"), ("positioning.BTH-MLE.loo_median_m", "p90_m"), "less", "lower",
         "tracker median error", "p90 error")], want=False, note="median compared with a p90"),
    # --- truncated sections, approximate times, exclusion
    T("R11", "BTH-MLE took 118.8 s.", [F("section.BTH-MLE.recorded_span_s", "value", "118.8", "took")],
      quals=["lower_bound", "approximate"], want=False, note="truncated stated as complete"),
    T("R11", "Only 118.8 s of BTH-MLE was recorded.", [F("section.BTH-MLE.recorded_span_s", "value", "118.8", "Only")],
      want=False, note="truncated without the lower_bound qualifier"),
    T("R11", "Only 118.8 s of BTH-MLE was recorded.", [F("section.BTH-MLE.recorded_span_s", "value", "118.8", "Only")],
      quals=["lower_bound", "approximate"], note="truncated stated as a recorded part"),
    T("R11", "Only 118.8 s of BTH-MLE was recorded, as scheduled.", [F("section.BTH-MLE.recorded_span_s", "value", "118.8", "Only")],
      quals=["lower_bound", "approximate"], want=False, note="truncated with schedule wording"),
    T("R11", "Only 118.8 s of BTH-MLE was recorded, shorter than LST-BTH's 143.0 s.",
      [F("section.BTH-MLE.recorded_span_s", "value", "118.8", "Only"), F("section.LST-BTH.duration_s", "value", "143.0", "LST-BTH's")],
      [C(("section.BTH-MLE.recorded_span_s", "value"), ("section.LST-BTH.duration_s", "value"), "less", "shorter",
         "BTH-MLE", "LST-BTH's")], quals=["lower_bound", "approximate"], want=False, note="lower bound compared"),
    T("R11", "LST-BTH took 143.0 s whereas only 118.8 s of BTH-MLE was recorded.",
      [F("section.LST-BTH.duration_s", "value", "143.0", "took"), F("section.BTH-MLE.recorded_span_s", "value", "118.8", "only")],
      quals=["lower_bound", "approximate"], note="full and truncated section in one sentence"),
    T("R4", "The MLE-BTH section took 97.5 s.", [F("section.MLE-BTH.duration_s", "value", "97.5", "took")], want=False,
      note="low-confidence time without approximate"),
    T("R4", "The MLE-BTH section took about 97.5 s.", [F("section.MLE-BTH.duration_s", "value", "97.5", "took")],
      quals=["approximate"], note="low-confidence time with approximate"),
    T("R4", "The MLE-BTH section level was -19.86 dBFS.", [F("section.MLE-BTH.level", "value", "-19.86", "level")], quals=REL,
      want=False, note="excluded level without excluded_from_averages"),
    T("R4", "The MLE-BTH section level was -19.86 dBFS.", [F("section.MLE-BTH.level", "value", "-19.86", "level")],
      quals=REL + ["excluded_from_averages"], note="excluded level qualified"),
    # --- wording rules kept
    T("R4", "The recording reached 92.8 dB(A), which proves the HSE limit is broken.",
      [F("noise.level_overall", "value", "92.8", "reached")], quals=REL, want=False, note="banned + wrong value"),
    T("R14", "At -24.7 dBFS this ride is louder than the Central line.", [F("noise.level_overall", "value", "-24.7", "ride")],
      quals=REL, typ="inference", want=False, note="other line"),
    T("R5", "Ride R5 was recorded by Tobi on his way home.", [], cites=["ride.route"], want=False, note="the rider"),
    T("R14", "The pause happened on 12 Oct 2025 for 15.2 s.", [F("qc.pause.1", "value", "15.2", "for")], want=False, note="date"),
    T("R4", "The 7.7 dB relative step indicates the alignment is fine.",
      [F("noise.moving_minus_dwell_db", "value", "7.7", "relative step")], quals=REL, want=False, note="measured concludes"),
    T("R4", "The 6 dB step suggests sync is fine.", [], cites=["noise.moving_minus_dwell_db"], quals=REL, typ="inference",
      want=False, note="inference with an unlisted number"),
    T("R4", "The yaw-rate signature remains usable for train positioning.", [], cites=["qc.pause.1"], typ="inference",
      want=False, note="positioning without a positioning stat"),
    T("R11", "Users can trust the relative section levels.", [], cites=[LVL_A[0], LVL_B[0]], quals=REL, typ="inference",
      want=False, note="trust without sync uncertainty"),
    T("R11", "The relative section levels are usable with the 5.0 s sync uncertainty in mind, though only 118.8 s of BTH-MLE was recorded.",
      [F("sync.offset_s", "uncertainty_s", "5.0", "sync uncertainty"),
       F("section.BTH-MLE.recorded_span_s", "value", "118.8", "only")], cites=[LVL_A[0], LVL_B[0]],
      quals=REL + ["lower_bound", "approximate"], typ="inference", note="trust statement with its caveats"),
    # --- review of 25 Oct: every bypass found in the second review (want=False), plus the honest wording (want=True)
    # units no field has
    T("R11", "The tracker median error on LST-BTH was 1.7 km.", [F(*TRK, "1.7", "tracker median error")], want=False, note="km"),
    T("R11", "The tracker median error on LST-BTH was 1.7 cm.", [F(*TRK, "1.7", "tracker median error")], want=False, note="cm"),
    T("R11", "LST-BTH took 143.0 ms.", [F(*DUR, "143.0", "took")], want=False, note="ms"),
    T("R14", "The phone paused for 15.2 hours.", [P1], want=False, note="hours"),
    T("R14", "The phone paused for 15.2 h.", [P1], want=False, note="h"),
    T("R14", "The phone paused for 15.2k s.", [P1], want=False, note="glued k"),
    T("R14", "The phone paused for 15.2.", [P1], want=False, note="seconds field without its unit"),
    T("R14", "The phone paused for 15.2 seconds.", [P1], note="seconds spelled out"),
    # lower bounds: span times, complete / full / at most, the missing end
    T("R11", "BTH-MLE ran from 218.76 s until arrival at 337.51 s.",
      [F(*SPAN, "span_start_t_s", "218.76", "from"), F(*SPAN, "span_end_t_s", "337.51", "until arrival at")],
      quals=["lower_bound", "approximate"], want=False, note="span end called the arrival"),
    T("R11", "BTH-MLE was recorded from 218.76 s to 337.51 s (arrival not observed).",
      [F(*SPAN, "span_start_t_s", "218.76", "from"), F(*SPAN, "span_end_t_s", "337.51", "to")],
      quals=["approximate"], want=False, note="span times without lower_bound"),
    T("R11", "BTH-MLE was recorded from 218.76 s to 337.51 s (arrival not observed).",
      [F(*SPAN, "span_start_t_s", "218.76", "from"), F(*SPAN, "span_end_t_s", "337.51", "to")],
      quals=["lower_bound", "approximate"], note="span times, honest"),
    T("R11", "The complete BTH-MLE section was recorded: 118.8 s.", [F(*SPAN, "value", "118.8", "recorded")],
      quals=["lower_bound", "approximate"], want=False, note="lower bound called complete"),
    T("R11", "BTH-MLE took 118.8 s in full, as recorded.", [F(*SPAN, "value", "118.8", "as recorded")],
      quals=["lower_bound", "approximate"], want=False, note="lower bound 'in full'"),
    T("R11", "BTH-MLE was recorded for at most 118.8 s.", [F(*SPAN, "value", "118.8", "recorded for at most")],
      quals=["lower_bound", "approximate"], want=False, note="'at most' on a lower bound"),
    # negation, sentence-level and on facts
    T("R11", "LST-BTH was not at all louder than BTH-MLE.", [], [C(LVL_A, LVL_B, "greater", "louder", "LST-BTH", "BTH-MLE")],
      quals=REL, want=False, note="negated two words before the comparative"),
    T("R11", "It is false that LST-BTH was louder than BTH-MLE.", [], [C(LVL_A, LVL_B, "greater", "louder", "LST-BTH", "BTH-MLE")],
      quals=REL, want=False, note="sentence-level negation"),
    T("R14", "The phone did not pause for 15.2 s at 47.2 s.", [F("qc.pause.1", "value", "15.2", "pause for"), P1T], want=False,
      note="negated fact"),
    T("R14", "Phyphox logged no pauses.", [], cites=["ride.phyphox_pauses"], want=False, note="'no pauses' is the count 0, undeclared"),
    T("R14", "Phyphox logged no pauses.", [F("ride.phyphox_pauses", "value", "no", "pauses")], want=False,
      note="'no pauses' declared, but the count is 1"),
    T("R11", "Phyphox logged no pauses.", [F("ride.phyphox_pauses", "value", "no", "pauses")], note="'no pauses' true (0)"),
    # direction, ranking and equality words
    T("R11", "BTH-MLE led LST-BTH, a gap of 0.67 dB.", [F("trip.contrast_db", "value", "0.67", "gap of")], quals=REL, want=False,
      note="'led'"),
    T("R11", "BTH-MLE was ahead of LST-BTH by a relative contrast of 0.67 dB.",
      [F("trip.contrast_db", "value", "0.67", "relative contrast of")], quals=REL, want=False, note="'ahead of'"),
    T("R11", "BTH-MLE outpaced LST-BTH in relative level.", [], cites=["trip.contrast_db"], quals=REL, want=False, note="'outpaced'"),
    T("R11", "BTH-MLE eclipsed LST-BTH in relative level.", [], cites=["trip.contrast_db"], quals=REL, want=False, note="'eclipsed'"),
    T("R11", "BTH-MLE was the stronger section.", [], cites=["trip.contrast_db"], quals=REL, want=False, note="'stronger'"),
    T("R11", "BTH-MLE was the top section.", [], cites=["trip.contrast_db"], quals=REL, want=False, note="'top'"),
    T("R11", "BTH-MLE ranked first.", [], cites=["trip.contrast_db"], quals=REL, want=False, note="'ranked first'"),
    T("R11", "The relative level improved from BTH-MLE to LST-BTH.", [], cites=["trip.contrast_db"], quals=REL, want=False,
      note="'improved'"),
    T("R11", "The relative level went up from LST-BTH to BTH-MLE.", [], cites=["trip.contrast_db"], quals=REL, want=False,
      note="'went up'"),
    T("R11", "BTH-MLE was as loud as LST-BTH.", [], cites=["trip.contrast_db"], quals=REL, want=False, note="'as loud as' undeclared"),
    T("R11", "BTH-MLE was as loud as LST-BTH.", [], [C(LVL_B, LVL_A, "about_equal", "as loud as", "BTH-MLE", "LST-BTH")],
      quals=REL, want=False, note="'as loud as' declared, 0.67 dB apart"),
    T("R11", "LST-BTH took 143.0 s, in line with the timetable's 159.8 s.",
      [F(*DUR, "143.0", "took"), F(*DUR[:1], "timetable_expected_s", "159.8", "timetable's")], want=False,
      note="'in line with' undeclared"),
    T("R11", "LST-BTH took 143.0 s, the same as the timetable's 159.8 s.",
      [F(*DUR, "143.0", "took"), F(*DUR[:1], "timetable_expected_s", "159.8", "timetable's")],
      [C(DUR, (DUR[0], "timetable_expected_s"), "about_equal", "the same as", "LST-BTH", "timetable's")], want=False,
      note="'the same as' declared, 10 % apart"),
    T("R11", "BTH-MLE was the loudest at 100.0 % limiter share.",
      [F("noise.limiter_fraction", "limiter_pct_loudest_10pct", "100.0", "BTH-MLE was the loudest")], want=False,
      note="superlative smuggled into a role with a section"),
    T("R11", "BTH-MLE was the loudest at 100.0 % limiter share.",
      [F("noise.limiter_fraction", "limiter_pct_loudest_10pct", "100.0", "the loudest")], want=False,
      note="superlative in a role, not next to the field's words"),
    T("R11", "BTH-MLE (-19.45 dBFS) was behind LST-BTH (-18.78 dBFS).",
      [F(*LVL_A, "-18.78", "LST-BTH"), F(*LVL_B, "-19.45", "BTH-MLE")],
      [C(LVL_B, LVL_A, "less", "behind", "BTH-MLE", "LST-BTH")], quals=REL, note="'behind', right"),
    # difference fields: operand order
    T("R11", "The relative contrast BTH-MLE minus LST-BTH was 0.67 dB.", [F("trip.contrast_db", "value", "0.67", "relative contrast")],
      quals=REL, want=False, note="contrast operands reversed"),
    T("R11", "Dwell minus moving level was 6.0 dB.", [F("noise.moving_minus_dwell_db", "value", "6.0", "Dwell minus moving level")],
      quals=REL, want=False, note="moving-dwell operands reversed"),
    T("R11", "The LST-BTH minus BTH-MLE relative contrast was 0.67 dB.", [F("trip.contrast_db", "value", "0.67", "relative contrast")],
      quals=REL, note="contrast operands in order"),
    T("R11", "Moving minus dwell level was 6.0 dB.", [F("noise.moving_minus_dwell_db", "value", "6.0", "Moving minus dwell level")],
      quals=REL, note="moving-dwell operands in order"),
    # words between a role and its number
    T("R11", "The audio-sensor offset uncertainty was 3.95 s.", [F("sync.offset_s", "value", "3.95", "offset")], want=False,
      note="sibling's word between role and number"),
    T("R11", "The timetable baseline aside, the LST-BTH tracker median error was 17.6 m.",
      [F(*TRK[:1], "timetable_baseline_median_m", "17.6", "timetable baseline")], want=False, note="another quantity named in the gap"),
    T("R11", "The LST-BTH tracker median error, and not the timetable baseline, was 17.6 m.",
      [F(*TRK[:1], "timetable_baseline_median_m", "17.6", "timetable baseline")], want=False, note="'not' in the gap"),
    T("R11", "The LST-BTH limiter share (not the speech share) was 2.1 %.",
      [F("section.LST-BTH.level", "speech_pct", "2.1", "speech share")], want=False, note="parenthesis between role and number"),
    T("R4", "The magnetometer reset lasted 38.2 s.", [F("qc.magnetometer_reset.1", "value", "38.2", "magnetometer reset")],
      want=False, note="an event time given a duration verb"),
    T("R14", "The phone was handled for 9.0 s.", [F("qc.handled_mid_ride.1", "value", "9.0", "handled for")], want=False,
      note="event time (t0 = t1) called a duration"),
    T("R14", "The phone was handled at 9.0 s.", [F("qc.handled_mid_ride.1", "value", "9.0", "handled at")], note="event time, right"),
    # identifiers with digits
    T("R11", "LST-BTH took 143.0 s versus the WTT 120 s.", [F(*DUR, "143.0", "took")], want=False, note="masked WTT number"),
    T("R11", "The LST-BTH p50 percentile tracker error was 4.0 m.",
      [F(*TRK[:1], "p90_m", "4.0", "p50 percentile tracker error")], want=False, note="p50 label on the p90"),
    T("R11", "The LST-BTH tracker p90 error was 4.0 m.", [F(*TRK[:1], "p90_m", "4.0", "tracker p90 error")], note="p90 named"),
    T("R11", "On R5, LST-BTH took 143.0 s.", [F(*DUR, "143.0", "took")], want=False, note="another ride's id"),
    T("R11", "On R11, LST-BTH took 143.0 s.", [F(*DUR, "143.0", "took")], note="this ride's id"),
    # number words and vague quantities
    T("R11", "The limiter hit a tenfold share of seconds compared to speech.", [],
      cites=["noise.limiter_fraction", "noise.speech_fraction"], want=False, note="'tenfold'"),
    T("R11", "Dozens of QC flags were raised.", [], cites=["qc.flag_counts"], want=False, note="'dozens'"),
    T("R11", "Hundreds of seconds were speech-like.", [], cites=["noise.speech_fraction"], want=False, note="'hundreds'"),
    T("R11", "Most seconds hit the limiter ceiling.", [], cites=["noise.limiter_fraction"], want=False, note="'most'"),
    T("R14", "The phone was handled for XV s.", [], cites=["qc.handled_mid_ride.1"], want=False, note="Roman numeral"),
    # usability words
    T("R11", "Positioning on BTH-MLE was robust given the 5.0 s sync uncertainty and only 118.8 s of BTH-MLE recorded.",
      [F("sync.offset_s", "uncertainty_s", "5.0", "sync uncertainty"), F(*SPAN, "value", "118.8", "only")],
      cites=["positioning.BTH-MLE.status"], quals=["lower_bound", "approximate"], typ="inference", want=False,
      note="'robust' on a not-evaluated section"),
    T("R11", "The tracker is dependable on BTH-MLE.", [], cites=["positioning.BTH-MLE.status"], typ="inference", want=False,
      note="'dependable'"),
    T("R4", "The section levels of this ride are valid for multi-run averages.", [],
      cites=["section.MLE-BTH.level", "section.BTH-LST.level"], quals=REL + ["excluded_from_averages"], typ="inference",
      want=False, note="'valid' on excluded levels"),
    T("R11", "The relative levels are good enough to rank sections across the Tube.", [], cites=[LVL_A[0], LVL_B[0]],
      quals=REL, typ="inference", want=False, note="generalisation beyond the ride"),
    # direction bound to the comparative's own clause; 'between X and Y'; causes; glued codes
    T("R11", "BTH-MLE was measured relative to LST-BTH, and LST-BTH was quieter than BTH-MLE.", [],
      [C(LVL_B, LVL_A, "less", "quieter", "BTH-MLE", "LST-BTH")], quals=REL, want=False,
      note="sides placed in another clause"),
    T("R11", "The BTH-MLE figure aside, between Liverpool Street and Bethnal Green the level was -19.45 dBFS.",
      [F(*LVL_B, "-19.45", "level was")], quals=REL, want=False, note="'between X and Y' names the other section"),
    T("R14", "Phyphox logged 1 pause, lasting 15.2 s from 47.2 s, which caused a 103.5 s overlap interval in WCL-ALE.",
      [F("ride.phyphox_pauses", "value", "1", "pause"), F("qc.pause.1", "value", "15.2", "lasting"),
       F("qc.pause.1", "t0_s", "47.2", "from"), F("qc.section_overlaps_pause.1", "value", "103.5", "overlap interval")],
      want=False, note="causal verb in a measured claim"),
    T("R14", "Phyphox logged 1 pause, lasting 15.2 s from 47.2 s, creating a 103.5 s overlap interval in WCL-ALE.",
      [F("ride.phyphox_pauses", "value", "1", "pause"), F("qc.pause.1", "value", "15.2", "lasting"),
       F("qc.pause.1", "t0_s", "47.2", "from"), F("qc.section_overlaps_pause.1", "value", "103.5", "overlap interval")],
      want=False, note="'creating' (a cause) in a measured claim"),
    T("R5", "Ride R5 ran on the eastboundLST-MLE route.", [], cites=["ride.route"], want=False, note="word glued to a code"),
]


def ingest(got: list[dict], canary: bool = False) -> tuple[dict, set]:
    """Round-0 claims by id. A repeated id never overwrites the first: the repeat is kept as '<id>#dupN' and flagged."""
    claims, dup = {}, set()
    for c in got:
        if c["id"] in claims:
            n = sum(1 for k in claims if k.startswith(c["id"] + "#dup")) + 1
            claims[f"{c['id']}#dup{n}"] = {**c, "id": f"{c['id']}#dup{n}"}
            dup.add(f"{c['id']}#dup{n}")
        else:
            claims[c["id"]] = c
    return claims | ({"CANARY": dict(CANARY)} if canary else {}), dup


def selftest() -> int:
    cache, bad = {}, 0
    for case in SELFTEST:
        st = cache.setdefault(case["run"], RS.build(case["run"]))
        r = check(case["claim"], st)
        good = r["ok"] == case["want"]
        bad += not good
        print(f"{'ok ' if good else 'BAD'} want={'pass' if case['want'] else 'fail'} got={'pass' if r['ok'] else 'fail'} "
              f"[{case['note']}] {case['claim']['text'][:70]}"
              + ("" if r["ok"] else "\n      " + r["reasons"][0][:150]))
    for ex, run in zip(EXAMPLES, ("R14", "R11", "R11")):
        r = check(ex, cache.setdefault(run, RS.build(run)))
        bad += not r["ok"]
        print(f"{'ok ' if r['ok'] else 'BAD'} prompt example {ex['id']} passes on {run}" + ("" if r["ok"] else f": {r['reasons']}"))
    c, d = ingest([{"id": "C1", "text": "a"}, {"id": "C1", "text": "b"}, {"id": "C2", "text": "c"}])
    ok = list(c) == ["C1", "C1#dup1", "C2"] and c["C1"]["text"] == "a" and d == {"C1#dup1"}
    bad += not ok
    print(f"{'ok ' if ok else 'BAD'} duplicate ids: first kept, repeat flagged as {sorted(d)}")
    st = cache.setdefault("R11", RS.build("R11"))
    ok = not check(CANARY, st)["ok"]
    bad += not ok
    print(f"{'ok ' if ok else 'BAD'} canary rejected")
    n = len(SELFTEST) + 2 + len(EXAMPLES)
    print(f"selftest: {n - bad}/{n} as expected")
    return bad


# --------------------------------------------------------------------------- record, render, approve
def render_md(rec: dict, stats: dict) -> str:
    rv = rec["review"]
    head = ("**APPROVED** by " + rv["reviewer"] + " at " + rv["ts"]) if rv["status"] == "approved" else \
        "**DRAFT: not for use until a person has read it** (approve with `--approve NAME`)"
    out = [f"# Ride report: {rec['run']} ({stats['ride.route']['value']}, {stats['ride.route']['detail']['line']} "
           f"{stats['ride.route']['detail']['direction']})", "", head, "",
           f"Post-hoc (added 25 Oct 2026, after the pre-registration). Written by {rec['model']} (thinking "
           f"{rec['thinking']}) from precomputed pipeline outputs only (`{rec['stats_file']}`, sha {rec['stats_sha']}). "
           "Each sentence declared what every number in it is (stat, field, the words naming it); code checked the "
           "number against the stored field (value at the written precision, sign, unit), checked the naming words "
           "sit next to it and name that field, recomputed every comparison, and required the qualifiers shown as "
           "badges. Phone levels are uncalibrated relative dBFS(A).", ""]
    if not rec.get("n_claims_ok_range", True):
        out += [f"> **Warning: only {len(rec['verified_claims'])} claims passed the checks (4-8 expected); the "
                "account of this ride is incomplete.**", ""]
    for c in rec["verified_claims"]:
        badges = " ".join(f"`{BADGE.get(q, q)}`" for q in c.get("qualifiers") or [])
        tag = ("seeded canary, rewritten by Argon; " if c["id"] == "CANARY" else "") + c["type"]
        out.append(f"- {c['text']} {badges} _[{tag}]_".replace("  ", " "))
        v = check(c, stats)
        for t in v["trace"]:
            if t["kind"] == "fact":
                out.append(f"  - `{t['written']}` = `{t['stat_key']}.{t['field']}` = {t['stored']:g} {t['unit']} "
                           f"({t['label']}); named by \"{t['role']}\"")
            else:
                out.append(f"  - `{t['written']}` = difference `{t['a']}` minus `{t['b']}`")
        for cm in v["comparisons"]:
            out.append(f"  - comparison \"{cm['relation_word']}\": `{cm['a']}` = {cm['va']:g} vs `{cm['b']}` = {cm['vb']:g}; "
                       f"stated {cm['stated']}, recomputed {'/'.join(cm['recomputed'])}")
        if c.get("cites"):
            out.append("  - relies on: " + ", ".join(f"`{k}`" for k in c["cites"]))
    if rec["dropped_claims"]:
        out += ["", "## Dropped by the checker (after one retry)", ""]
        out += [f"- ~~{d['claim']['text']}~~ (reasons: {'; '.join(d['reasons'])})" for d in rec["dropped_claims"]]
    used = sorted({k for c in rec["verified_claims"] for k in
                   [f["stat_key"] for f in c.get("facts") or []] + [c2[s]["stat_key"] for c2 in c.get("comparisons") or [] for s in "ab"]
                   + list(c.get("cites") or []) if k in stats})
    out += ["", "## Stats cited", "", "| key | value | unit | source |", "|---|---|---|---|"]
    out += [f"| `{k}` | {stats[k]['value']} | {stats[k]['unit']} | {stats[k]['source']} |" for k in used]
    out += ["", "## Reviewer checklist (before approving)", "",
            "1. Each number's field and label (the indented lines) is what the sentence says it is.",
            "2. Sentences with no number say only what the stats they rely on show.",
            "3. Each inference follows from what it cites, and nothing reads as absolute dB or as a finding "
            "beyond this one ride."]
    if rec.get("seeded_canary"):
        out += ["", "A seeded false claim (`CANARY`, not written by Argon) was injected into round 0 to exercise the "
                "checker and the retry; its fate is in the JSON record."]
    out += ["", f"Rounds: {len(rec['rounds'])}; {rec['seconds_total']} s in total.", ""]
    return "\n".join(out)


def save(rec: dict, stats: dict, atlas: bool) -> str:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    (OUTDIR / f"{rec['run']}.json").write_text(json.dumps(rec, indent=1, default=float), encoding="utf-8")
    (OUTDIR / f"{rec['run']}.md").write_text(render_md(rec, stats), encoding="utf-8")
    if not atlas:
        return "skipped (--no-atlas)"
    try:
        from atlas.db import db
        db().ride_reports.replace_one({"_id": rec["run"]}, {"_id": rec["run"], **rec}, upsert=True)
        return "upserted ride_reports"
    except Exception as e:  # noqa: BLE001
        return f"skipped ({type(e).__name__})"


def claims_sha(claims: list[dict]) -> str:
    return hashlib.sha256(json.dumps(claims, sort_keys=True).encode()).hexdigest()[:16]


def approve(run: str, reviewer: str, atlas: bool) -> None:
    path = OUTDIR / f"{run}.json"
    if not path.exists():
        raise SystemExit(f"no report for {run}: generate it first (python -m agent.ride_report {run})")
    rec = json.loads(path.read_text(encoding="utf-8"))
    if rec["review"]["status"] == "approved":
        raise SystemExit(f"{run} was already approved by {rec['review']['reviewer']} at {rec['review']['ts']}: "
                         "regenerate the report to get a new draft")
    if rec.get("claims_sha_backfilled"):
        raise SystemExit("claims_sha was added after generation (claims_sha_backfilled): it proves nothing; regenerate")
    if claims_sha(rec["verified_claims"]) != rec.get("claims_sha"):
        raise SystemExit("the verified claims were edited after generation (claims_sha mismatch): regenerate the report")
    stats = RS.build(run)  # compare only: approving must not rewrite the stats file
    if RS.stats_hash(stats) != rec["stats_sha"]:
        raise SystemExit(f"stats changed since the report was written ({rec['stats_sha']} -> {RS.stats_hash(stats)}): "
                         "regenerate the report before approving")
    bad = [c["id"] for c in rec["verified_claims"] if not check(c, stats)["ok"]]
    if bad:
        raise SystemExit(f"stored claims no longer verify: {bad}")
    rec["review"] = {"status": "approved", "reviewer": reviewer,
                     "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "claims_sha": rec["claims_sha"]}
    print(render_md(rec, stats))
    print(f"approved by {reviewer}; atlas: {save(rec, stats, atlas)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run", nargs="?", help="R1..R15 (or a recording stem)")
    ap.add_argument("--selftest", action="store_true", help="offline: run the checker on known good/bad claims")
    ap.add_argument("--jsonl", action="store_true", help="one JSON event per line on stdout")
    ap.add_argument("--no-atlas", action="store_true")
    ap.add_argument("--approve", metavar="REVIEWER", help="record that this person read the stored report")
    ap.add_argument("--canary", action="store_true", help="inject a seeded false claim into round 0 (tests the retry)")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(1 if selftest() else 0)
    if not a.run:
        ap.error("a run is required (e.g. R14)")
    run = RS.stem_of(a.run).split("_")[0]
    if a.approve:
        return approve(run, a.approve, not a.no_atlas)

    def emit(event: str, **kw):
        if a.jsonl:
            print(json.dumps({"event": event, "run": run, **kw}, default=float), flush=True)
        elif event == "draft":
            print(f"round {kw['round']}: {len(kw['claims'])} claims ({kw['seconds']} s; usage {kw['usage']})", flush=True)
        elif event == "verdicts":
            for v in kw["results"]:
                print(f"  {'ok' if v['ok'] else 'x '} {v['id']}" + ("" if v["ok"] else f": {'; '.join(v['reasons'])[:300]}"))
        elif event in ("start", "stats", "final", "error"):
            print(f"[{event}] " + ", ".join(f"{k}={v}" for k, v in kw.items() if not isinstance(v, (list, dict))), flush=True)

    t0 = time.time()
    try:
        from google import genai
        from google.genai import types
        from atlas.db import load_env
        emit("start", model=MODEL, thinking=THINKING)
        path, stats = RS.write(run)
        sha = RS.stats_hash(stats)
        emit("stats", n=len(stats), file=str(path.relative_to(V.ROOT)).replace("\\", "/"), sha=sha, keys=list(stats))
        load_env()
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        cfg = types.GenerateContentConfig(system_instruction=SYSTEM, response_mime_type="application/json",
                                          response_schema=Report, max_output_tokens=32768,
                                          thinking_config=types.ThinkingConfig(thinking_level=THINKING))
        chat = client.chats.create(model=MODEL, config=cfg)
        msg = (f"RIDE STATS for {run} (cite by key; each stat's `fields` lists its numeric fields with unit, label, "
               f"words and required qualifiers):\n{json.dumps(stats, indent=1, default=float)}\n\nReturn the Report JSON.")
        rounds, claims, verdicts, failing, dup = [], {}, {}, [], set()
        for rnd in range(2):
            t = time.time()
            r = chat.send_message(msg)
            got = Report.model_validate_json(r.text).model_dump()["claims"]
            if rnd == 0:
                claims, dup = ingest(got, a.canary)
                todo = list(claims)
            else:
                todo = [c["id"] for c in got if c["id"] in failing]
                claims |= {c["id"]: c for c in got if c["id"] in failing}
            res = [check(claims[i], stats) for i in todo]
            for v in res:
                if rnd == 0 and v["id"] in dup:
                    v["reasons"].append("duplicate claim id in the draft (rename it or merge the sentences)")
                    v["ok"] = False
            verdicts |= {v["id"]: v for v in res}
            u = r.usage_metadata
            raw = u.model_dump(exclude_none=True, mode="json") if hasattr(u, "model_dump") else {}
            rounds.append({"round": rnd, "seconds": round(time.time() - t, 1), "claims": got,
                           "verdicts": [{k: v[k] for k in ("id", "ok", "reasons")} for v in res],
                           "usage": {"prompt": u.prompt_token_count, "output": u.candidates_token_count,
                                     "thoughts": u.thoughts_token_count},
                           "usage_raw": raw,
                           "thinking_note": ("thoughts_token_count returned by the API" if u.thoughts_token_count is not None
                                             else f"thoughts_token_count not returned in this chat usage record "
                                                  f"(thinking_level={THINKING} was sent in the request config)")})
            emit("draft", round=rnd, seconds=rounds[-1]["seconds"], claims=got, usage=rounds[-1]["usage"])
            emit("verdicts", round=rnd, results=[{k: v[k] for k in ("id", "ok", "reasons")} for v in res])
            failing = [v["id"] for v in verdicts.values() if not v["ok"]]
            if not failing or rnd == 1:
                break
            msg = ("The checker rejected these claims. Return ONLY corrected versions of them, with the SAME ids (omit "
                   "an id to drop that claim). Fix the numbers, the facts (stat_key, field, value_as_written, role), "
                   "the comparisons, the qualifiers or the wording:\n" +
                   json.dumps([{"claim": {k: v for k, v in claims[i].items() if k != "seeded"}, "reasons": verdicts[i]["reasons"]}
                               for i in failing], indent=1))
        ok = [c for i, c in claims.items() if verdicts[i]["ok"]]
        dropped = [{"claim": claims[i], "reasons": verdicts[i]["reasons"]} for i in claims if not verdicts[i]["ok"]]
        rec = {"run": run, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "model": MODEL,
               "thinking": THINKING, "post_hoc": "added 25 Oct 2026 after prereg-24oct; inputs are precomputed outputs",
               "schema": "claims with facts/comparisons/qualifiers, bound by code (ride_report v2)",
               "stats_file": f"outputs_posthoc/ride_stats/{run}.json", "stats_sha": sha,
               "verifier_sha": V.VERIFIER_SHA, "ride_checks_sha": CHECKS_SHA, "rounds": rounds,
               "verified_claims": ok, "claims_sha": claims_sha(ok), "dropped_claims": dropped,
               "n_claims_ok_range": 4 <= len(ok) <= 8, "claims_total": len(claims), "seeded_canary": a.canary,
               "review": {"status": "pending", "reviewer": None, "ts": None},
               "seconds_total": round(time.time() - t0, 1)}
        atl = save(rec, stats, not a.no_atlas)
        if not rec["n_claims_ok_range"]:
            print(f"WARNING: only {len(ok)} claims verified (4-8 expected): the report is incomplete; read the "
                  "dropped list before approving", file=sys.stderr, flush=True)
        emit("final", verified=len(ok), dropped=len(dropped), rounds=len(rounds), seconds=rec["seconds_total"],
             json=f"outputs_posthoc/ride_reports/{run}.json", md=f"outputs_posthoc/ride_reports/{run}.md", atlas=atl,
             review="pending", claims_in_range=rec["n_claims_ok_range"])
        if not a.jsonl:
            print("\n" + render_md(rec, stats))
    except Exception as e:  # noqa: BLE001
        emit("error", type=type(e).__name__, message=str(e)[:400], seconds=round(time.time() - t0, 1))
        sys.exit(1)


if __name__ == "__main__":
    main()
