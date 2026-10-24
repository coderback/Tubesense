"""Show, on screen, that MongoDB Atlas does real work in Tubesense (demo beat 1:50-2:30 and the video).

Read-only against the live cluster (nothing is written to Atlas):
  1. cluster: MongoDB version, collections with document counts, every search index
     (name, type, status, queryable; autoEmbed model / vector dims)
  2. one investigator question three ways over tubesense.documents:
       Atlas Search (docs_text, BM25)  |  Vector Search (docs_auto, autoEmbed voyage-4, query embedded
       by Atlas)  |  Hybrid Search ($rankFusion over both); the exact hybrid pipeline is printed and is
       the one that runs. Top hits with doc_id, page, a snippet and, for hybrid, each pipeline's rank.
       All three run on one connected client, so the [ms] shown are comparable server round trips.
  3. the analyst's hash-chained attempt log mirrored in Atlas `attempts` (checked against the local
     outputs_posthoc/attempts.jsonl chain head) and the `section_stats` the investigator may cite.
Fallback: --offline prints the cached snapshot (outputs_posthoc/atlas_snapshot.json, written by a live
run with --snapshot) without touching the network; a live run that cannot reach Atlas falls back to the
same snapshot. Either way the output says CACHED FALLBACK in capitals.

Run (repo root):
  PYTHONPATH=. .venv/Scripts/python -m demo.atlas_show
  PYTHONPATH=. .venv/Scripts/python -m demo.atlas_show "did TfL refuse to share noise data?"
  PYTHONPATH=. .venv/Scripts/python -m demo.atlas_show --snapshot     (live, then cache for the fallback)
  PYTHONPATH=. .venv/Scripts/python -m demo.atlas_show --offline      (cached fallback only)
  --pause S waits S seconds between the three parts (screen recording); --k N hits per mode (default 3);
  --full-pipeline prints the hybrid pipeline fully indented (default: one line per stage).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root, so `python demo/atlas_show.py` works

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "outputs_posthoc" / "atlas_snapshot.json"
LOCAL_LOG = ROOT / "outputs_posthoc" / "attempts.jsonl"
DEFAULT_Q = "Was Bethnal Green to Liverpool Street ground for noise?"
STOP = {"was", "were", "the", "for", "and", "did", "does", "what", "which", "with", "from", "this", "that",
        "noise", "how", "are", "has", "have", "to", "of", "is", "it", "on", "in"}
PAGE_MARK = re.compile(r"\[p\.\d+\]\s*")
RULE = "-" * 96
PHASE_S = 10  # client-side deadline per phase (cluster / searches / records), seconds


# --------------------------------------------------------------------------- gather (live, read-only)
def _cluster(d) -> dict:
    out = {"version": d.command("buildInfo")["version"], "database": d.name, "collections": [], "indexes": []}
    infos = {c["name"]: c for c in d.list_collections()}
    for name in sorted(infos):
        if name.startswith("system."):
            continue
        ts = infos[name].get("type") == "timeseries"
        out["collections"].append({"name": name, "count": d[name].count_documents({}),
                                   "kind": "time series" if ts else ""})
        if ts:
            continue  # no Atlas Search on time series collections
        for ix in d[name].list_search_indexes():
            fields = (ix.get("latestDefinition") or {}).get("fields") or []
            what = []
            for f in fields:
                if f.get("type") == "autoEmbed":
                    what.append(f"autoEmbed {f.get('model')} on '{f.get('path')}'")
                elif f.get("type") == "vector":
                    what.append(f"vector {f.get('numDimensions')}-d {f.get('similarity')} on '{f.get('path')}'")
            filters = [f["path"] for f in fields if f.get("type") == "filter"]
            if filters:
                what.append("filters " + ",".join(filters))
            if not fields:
                mf = ((ix.get("latestDefinition") or {}).get("mappings") or {}).get("fields") or {}
                what.append("full text on " + ",".join(k for k, v in mf.items() if v.get("type") == "string"))
            out["indexes"].append({"collection": name, "name": ix["name"], "type": ix.get("type", "search"),
                                   "status": ix.get("status"), "queryable": ix.get("queryable"),
                                   "what": "; ".join(what)})
    return out


def _hybrid_pipeline(q: str, k: int, depth: int = 20) -> list[dict]:
    """The same stages as atlas.search.hybrid_search (built from its helpers), so the printed JSON is
    exactly what runs."""
    from atlas import search as S
    f = S._check(None)
    return [{"$rankFusion": {
                "input": {"pipelines": {"text": [S._search_stage(q, f), {"$limit": depth}],
                                        "semantic": S._vector_stage(q, depth, f)}},
                "combination": {"weights": {"text": 1.0, "semantic": 1.0}},
                "scoreDetails": True}},
            {"$limit": k}, S._project(True, details=True)]


def _hit(r: dict, terms: list[str]) -> dict:
    h = {"_id": r["_id"], "doc_id": r.get("doc_id"), "page": r.get("page"), "title": r.get("title"),
         "publisher": r.get("publisher"), "doc_date": r.get("doc_date"), "score": r.get("score"),
         "snippet": _snippet(r.get("text") or "", terms)}
    if "scoreDetails" in r:
        h["ranks"] = {x["inputPipelineName"]: x.get("rank") for x in r["scoreDetails"].get("details", [])}
    return h


def _snippet(text: str, terms: list[str], n: int = 160) -> str:
    """n characters around the first query term in the chunk (page markers stripped)."""
    t = re.sub(r"\s+", " ", PAGE_MARK.sub("", text)).strip()
    low = t.lower()
    pairs = [f"{a} {b}" for a, b in zip(terms, terms[1:])]          # 'bethnal green' before 'street'
    pos = next((p for p in (low.find(w) for w in pairs + sorted(terms, key=len, reverse=True)) if p >= 0), 0)
    start = max(0, min(pos - 40, len(t) - n))
    s = t[start:start + n]
    return ("..." if start else "") + s + ("..." if start + n < len(t) else "")


def _mode_pipelines(q: str, k: int) -> dict[str, list[dict]]:
    """Text and vector pipelines exactly as atlas.search.text_search / semantic_search build them, so all
    three modes run on one connected client and their round-trip times are comparable."""
    from atlas import search as S
    f = S._check(None)
    vproj = S._project(True)
    vproj["$project"]["score"] = {"$meta": "vectorSearchScore"}
    return {"text": [S._search_stage(q, f), {"$limit": k}, S._project(True)],
            "vector": S._vector_stage(q, k, f) + [vproj],
            "hybrid": _hybrid_pipeline(q, k)}


def _queries(d, q: str, k: int) -> dict:
    terms = [w for w in re.findall(r"[a-z0-9]+", q.lower()) if len(w) > 2 and w not in STOP]
    out = {"question": q, "modes": {}}
    pipes = _mode_pipelines(q, k)
    labels = {"text": "Atlas Search  (docs_text, BM25 full text)",
              "vector": "Vector Search (docs_auto, autoEmbed voyage-4: Atlas embeds the query)",
              "hybrid": "Hybrid Search ($rankFusion: reciprocal rank fusion of the two above)"}
    for mode, pipe in pipes.items():
        t0 = time.perf_counter()
        rows = list(d["documents"].aggregate(pipe))
        out["modes"][mode] = {"label": labels[mode], "ms": round(1000 * (time.perf_counter() - t0)),
                              "hits": [_hit(r, terms) for r in rows]}
    out["hybrid_pipeline"] = pipes["hybrid"]
    return out


def _verdict(r: dict) -> str:
    if r.get("kind") == "dev":
        vs = r.get("vs_baseline")
        return "baseline" if vs is None else ("ACCEPTED by harness rule" if vs.get("accepted") else "rejected by harness rule")
    return {"submit": "submitted for the one sealed 24 Oct test", "test": "sealed test scored"}.get(r.get("kind"), "?")


def _records(d) -> dict:
    a = d["attempts"]
    last = list(a.find({}, {"attempt": 1, "kind": 1, "ts": 1, "hash": 1, "prev_hash": 1, "vs_baseline.accepted": 1,
                            "config": 1}).sort("attempt", -1).limit(3))
    out = {"attempts_count": a.count_documents({}),
           "attempts_last": [{"attempt": r.get("attempt"), "ts": r.get("ts"), "kind": r.get("kind"),
                              "verdict": _verdict(r), "hash": r.get("hash", ""), "prev_hash": r.get("prev_hash"),
                              "config": r.get("config")} for r in last],
           "section_stats_count": d["section_stats"].count_documents({}),
           "section_stats_examples": [{"key": s["_id"], "value": s.get("value"), "unit": s.get("unit"), "n": s.get("n")}
                                      for s in d["section_stats"].find(
                                          {"_id": {"$in": ["level.EB.LST-BTH", "level.EB.BTH-MLE",
                                                           "contrast.LST-BTH_minus_BTH-MLE", "v2.dev.median_error_m"]}})]}
    return out


def _local_chain() -> dict:
    """Head of the local hash chain (the record of truth) and whether every link checks out."""
    if not LOCAL_LOG.exists():
        return {"lines": 0}
    from agent.harness import verify_log
    rows = [json.loads(x) for x in LOCAL_LOG.read_text(encoding="utf-8").splitlines() if x.strip()]
    v = verify_log()
    return {"lines": len(rows), "broken": v["broken"], "head_hash": rows[-1]["hash"] if rows else None}


def gather_live(q: str, k: int) -> dict:
    """Each phase runs under a client-side deadline (pymongo.timeout), so a network stall mid-query raises
    and main() falls back to the snapshot instead of hanging on stage."""
    import pymongo
    from atlas.db import client, db
    c = client(timeout_ms=8000)
    try:
        with pymongo.timeout(PHASE_S):
            c.admin.command("ping")
            d = db(c)
            cluster = _cluster(d)
        with pymongo.timeout(PHASE_S):
            search = _queries(d, q, k)
        with pymongo.timeout(PHASE_S):
            records = _records(d)
    finally:
        c.close()
    return {"taken": datetime.now(timezone.utc).isoformat(timespec="seconds"), "cluster": cluster,
            "search": search, "records": records}


# --------------------------------------------------------------------------- render
def pipeline_lines(pipe: list[dict], full: bool = False) -> list[str]:
    """The pipeline as valid JSON laid out for a screen: one line per stage of each input pipeline.
    full=True prints it indented in full instead."""
    if full:
        return ("db.documents.aggregate(" + json.dumps(pipe, indent=2) + ")").splitlines()
    j = lambda x: json.dumps(x, separators=(", ", ": "))
    rf = pipe[0]["$rankFusion"]
    out = ["db.documents.aggregate([", '  {"$rankFusion": {', '    "input": {"pipelines": {']
    names = list(rf["input"]["pipelines"])
    for i, name in enumerate(names):
        stages = rf["input"]["pipelines"][name]
        out.append(f'      "{name}": [')
        out += [f"        {j(st)}{',' if n < len(stages) - 1 else ''}" for n, st in enumerate(stages)]
        out.append("      ]" + ("," if i < len(names) - 1 else ""))
    out += ["    }},", f'    "combination": {j(rf["combination"])},', f'    "scoreDetails": {j(rf["scoreDetails"])}}}}},']
    proj = pipe[2]["$project"]
    kept = [f for f, v in proj.items() if v == 1]
    metas = {f: v for f, v in proj.items() if v != 1}
    out.append(f"  {j(pipe[1])},")
    out.append('  {"$project": {' + ", ".join(f'"{f}": 1' for f in kept[:4]) + f", ... {len(kept) - 4} more fields, "
               + ", ".join(f'"{f}": {j(v)}' for f, v in metas.items()) + "}}")
    out.append("])")
    return out


def _p(s: str = "") -> None:
    print(s, flush=True)


def render(snap: dict, cached: bool, local: dict, pause: float, k: int, full: bool = False) -> None:
    if cached:
        _p("#" * 96)
        _p(f"#  CACHED FALLBACK: Atlas was not queried. Results below are a snapshot taken {snap['taken']}.")
        _p("#  Recorded earlier from the live cluster by this script (--snapshot); not a live query.")
        _p("#" * 96)
    else:
        _p(f"LIVE from MongoDB Atlas at {snap['taken']} (read-only: nothing is written)")
    cl = snap["cluster"]
    _p(RULE)
    _p(f"1. CLUSTER  MongoDB {cl['version']}   database '{cl['database']}'")
    _p(RULE)
    _p("   collection           docs  ")
    for c in cl["collections"]:
        _p(f"   {c['name']:18s} {c['count']:6d}  {c['kind']}")
    _p("")
    _p(f"   {'search index':28s} {'type':13s} {'status':8s} {'queryable':9s} definition")
    for ix in cl["indexes"]:
        _p(f"   {ix['collection'] + '.' + ix['name']:28s} {ix['type']:13s} {str(ix['status']):8s} "
           f"{str(ix['queryable']):9s} {ix['what']}")
    time.sleep(pause)

    s = snap["search"]
    _p(RULE)
    _p(f"2. INVESTIGATOR QUESTION, THREE WAYS:  \"{s['question']}\"")
    _p(RULE)
    _p("   Hybrid pipeline sent to Atlas, exactly as run:")
    for line in pipeline_lines(s["hybrid_pipeline"], full):
        _p("   " + line)
    _p("   fused score = sum over pipelines of weight / (60 + rank)   (reciprocal rank fusion)")
    for mode in ("text", "vector", "hybrid"):
        m = s["modes"][mode]
        _p("")
        _p(f"   {m['label']}   [{m['ms']} ms round trip{', cached' if cached else ''}]")
        for i, h in enumerate(m["hits"][:k], 1):
            extra = ("   ranks " + "  ".join(f"{n}=#{h['ranks'][n]}" if h["ranks"].get(n) else f"{n}=-"
                                          for n in ("text", "semantic"))
                     if "ranks" in h else "")
            _p(f"   {i}. {h['doc_id']:26s} p{h['page'] if h['page'] is not None else '-':<3} "
               f"score {h['score']:.4f}{extra}")
            _p(f"      {h['publisher']}, {h['doc_date']}: {h['title']}"[:110])
            _p(f"      \"{h['snippet']}\"")
    time.sleep(pause)

    r = snap["records"]
    _p(RULE)
    _p("3. RECORDS IN ATLAS")
    _p(RULE)
    _p(f"   attempts (Argon analyst's hash-chained log, mirrored): {r['attempts_count']} documents; last 3:")
    for a in r["attempts_last"]:
        _p(f"   #{a['attempt']}  {a['ts']}  {a['kind']:6s}  {a['verdict']:42s} hash {a['hash'][:12]}  "
           f"prev {(a['prev_hash'] or '-')[:12]}")
    if local.get("lines"):
        head = r["attempts_last"][0]["hash"] if r["attempts_last"] else None
        same = head == local["head_hash"] and r["attempts_count"] == local["lines"]
        _p(f"   local chain outputs_posthoc/attempts.jsonl: {local['lines']} lines, broken links {local['broken'] or 'none'}; "
           f"Atlas head {'MATCHES' if same else 'DIFFERS FROM'} local head {str(local['head_hash'])[:12]}")
    _p(f"   section_stats (numbers the investigator may cite; the verifier checks them): {r['section_stats_count']} documents")
    for e in r["section_stats_examples"]:
        _p(f"     {e['key']:34s} {e['value']} {e['unit']} (n={e['n']})")
    if cached:
        _p("#" * 96)
        _p("#  END OF CACHED FALLBACK (snapshot, not live)")
        _p("#" * 96)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("question", nargs="*", help=f"investigator question (default: {DEFAULT_Q!r})")
    ap.add_argument("--k", type=int, default=3, help="hits shown per mode")
    ap.add_argument("--offline", action="store_true", help="print the cached snapshot, no network")
    ap.add_argument("--snapshot", action="store_true", help=f"after a live run, write {SNAPSHOT.relative_to(ROOT)}")
    ap.add_argument("--pause", type=float, default=0.0, help="seconds between parts (screen recording)")
    ap.add_argument("--full-pipeline", action="store_true", help="print the hybrid pipeline fully indented")
    a = ap.parse_args()
    q = " ".join(a.question) or DEFAULT_Q
    local = _local_chain()

    snap, cached = None, a.offline
    if not a.offline:
        try:
            snap = gather_live(q, a.k)
        except (Exception, SystemExit) as e:  # noqa: BLE001  network / auth / timeout / missing .env -> cache
            _p(f"!! Atlas unreachable or too slow ({type(e).__name__}); switching to the CACHED FALLBACK")
            cached = True
    if cached:
        if not SNAPSHOT.exists():
            raise SystemExit(f"no snapshot at {SNAPSHOT}: run once online with --snapshot")
        snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        if snap["search"]["question"] != q:
            _p(f"(cached snapshot answers \"{snap['search']['question']}\", not the question asked)")
    elif a.snapshot:
        SNAPSHOT.write_text(json.dumps(snap, indent=1, default=str), encoding="utf-8")
    render(snap, cached, local, a.pause, a.k, a.full_pipeline)
    if a.snapshot and not cached:
        _p(f"\nsnapshot written: {SNAPSHOT.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
