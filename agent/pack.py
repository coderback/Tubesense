"""Render the evidence pack (Markdown) from verified claims only. The layout is fixed code, not model text.

Every sentence in sections 1-5 is a verified claim, labelled with its type, with its sources as
footnotes (title, publisher, date, page, URL). The method, uncertainty and privacy notes are fixed
text. pack_id is the SHA-256 of the rendered body, so the approval record pins exactly what was approved.
Run: PYTHONPATH=. .venv/Scripts/python -m agent.pack   (reads outputs_posthoc/investigation.json)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from . import verify as V

OUTDIR = Path(os.environ.get("TUBESENSE_POSTHOC_OUT", V.ROOT / "outputs_posthoc"))  # tests point this elsewhere
INV = OUTDIR / "investigation.json"
DRAFTS = OUTDIR / "drafts"
TITLES = {"tfl_data": "1. What TfL's own records say", "grinding": "2. Grinding and track work on record",
          "our_measurements": "3. What our phone measurements add (relative, indicative)",
          "limits": "4. What the evidence cannot show", "context": "5. Context"}
LABEL = {"sourced": "source", "measured": "our measurement", "derived": "calculated", "inference": "inference"}

FIXED_METHOD = """## Method, uncertainty and privacy
- **Measurements:** ordinary rides recorded on one phone (phyphox motion sensors, Voice Memos audio).
  Phone-audio levels are A-weighted but **uncalibrated** (dB relative to the phone's full scale) and
  are not comparable with TfL's dB figures. They did not reliably rank Liverpool Street–Bethnal Green
  against Bethnal Green–Mile End. It read higher on 10 of 11 trips, but the check fixed before the
  rides needs every trip, so it returned NO-GO. The mean difference, under 1 dB, is also inside the
  phone's limiter compression. Two **indicative** dB(A) readings come from the NIOSH Sound
  Level Meter app on the same phone, one whole trip per direction; the app is validated only up to
  about 85 dB(A), so they may read low. Position between stations comes from the gyroscope matched
  against maps built from our own rides (no GPS), with a 90 % uncertainty band.
- **Pre-registration:** the analysis was frozen (git tag `prereg-24oct`) before the 24 Oct rides;
  later changes are labelled post-hoc.
- **Checking:** every claim above was checked by a deterministic verifier (quotes verbatim against the
  stored source text; numbers traced to a quote, our statistics or a recomputed formula). Claims that
  failed were dropped and are listed in the appendix.
- **Privacy:** raw audio never leaves the laptop; only 1-second levels and bands are kept. No speech
  is stored. Only public documents and summary statistics were sent to the AI model.
- **Not:** a certified sound level meter, a safety system, or a measurement of rail corrugation."""


def for_verifier(claims: list[dict]) -> list[dict]:
    """The investigator stores formula inputs as a list; the verifier takes them keyed by name
    (as investigator.as_verifier does before every verification round)."""
    out = []
    for c in claims:
        f = c.get("formula")
        if f and isinstance(f.get("inputs"), list):
            f = {**f, "inputs": {i["name"]: ({"stat": i["stat"]} if i.get("stat") else
                                             {k: i.get(k) for k in ("value", "chunk_id", "quote")}) for i in f["inputs"]}}
        out.append({**c, "formula": f})
    return out


def _src(cid: str, chunks: dict) -> str:
    c = chunks.get(cid, {})
    pg = f", p.{c['page']}" if c.get("page") else ""
    return f"{c.get('title', cid)} ({c.get('publisher', '?')}, {c.get('doc_date', '?')}{pg}). {c.get('url', '')}"


def render(inv: dict) -> tuple[str, str]:
    chunks = V.load_chunks()
    claims = {c["id"]: c for c in inv["verified_claims"]}
    notes, order = [], {}

    def ref(cid):
        if cid not in order:
            order[cid] = len(order) + 1
            notes.append(f"[^{order[cid]}]: {_src(cid, chunks)}")
        return f"[^{order[cid]}]"

    def line(c):
        refs = "".join(dict.fromkeys(ref(x["chunk_id"]) for x in c.get("citations") or []))
        if c["type"] == "derived" and c.get("formula"):
            for i in c["formula"]["inputs"]:
                if i.get("chunk_id"):
                    r = ref(i["chunk_id"])
                    refs += "" if r in refs else r
        stats = f" _(stats: {', '.join(c['stat_keys'])})_" if c.get("stat_keys") else ""
        return f"- {c['text']} **[{LABEL[c['type']]}, {c['id']}]**{refs}{stats}"

    body = ["# Evidence pack: Central line noise, Liverpool Street – Bethnal Green – Mile End",
            "_Draft for Elly Baker AM's office. Prepared by Tubesense; requires human approval before any use._", ""]
    head = [claims[i] for i in inv.get("headline_claim_ids", []) if i in claims]
    if head:
        body += ["## Summary"] + [line(c) for c in head] + [""]
    for sec, title in TITLES.items():
        cs = [c for c in inv["verified_claims"] if c["section"] == sec]
        if cs:
            body += [f"## {title}"] + [line(c) for c in cs] + [""]
    if inv.get("asks"):
        body += ["## 6. Suggested information requests to TfL"]
        body += [f"{n}. {a['request']} _Why: {a['why']}_" for n, a in enumerate(inv["asks"], 1)] + [""]
    method = FIXED_METHOD
    if inv.get("revision"):
        method += ("\n- **Independent review:** before approval, the verified draft was also checked against the full "
                   "source texts for framing the verifier cannot catch (a quote out of context, an overstated "
                   "commitment, a too-broad negative). The agent revised the draft to resolve each finding and the "
                   "verifier re-checked every claim (`outputs_posthoc/pack_review*.json`).")
    body += [method, "", "## Sources"] + notes + [""]
    if inv.get("dropped_claims"):
        body += ["## Appendix: claims the verifier rejected (not used above)"]
        body += [f"- ~~{d['claim']['text']}~~ (reason: {'; '.join(d['reasons'])})" for d in inv["dropped_claims"]]
    md = "\n".join(body) + "\n"
    pack_id = hashlib.sha256(md.encode()).hexdigest()[:16]
    md += (f"\n---\n_pack_id {pack_id} · verifier {inv['verifier_sha'][:12]} · model {inv['model']} · "
           f"corpus {inv['corpus_hash']} · generated {inv['ts']}_\n")
    return md, pack_id


def main():
    inv = json.loads(INV.read_text(encoding="utf-8"))
    md, pid = render(inv)
    DRAFTS.mkdir(parents=True, exist_ok=True)
    p = DRAFTS / f"evidence_pack_{pid}.md"
    p.write_text(md, encoding="utf-8")
    print(f"{p} ({len(re.findall(r'^- ', md, re.M))} bullet lines)")


if __name__ == "__main__":
    main()
