"""Search over the public corpus in Atlas (tubesense.documents), for the investigator.

  text_search      Atlas Search (index docs_text): BM25 full text over text + title.
  semantic_search  Atlas Vector Search with Automated Embedding (index docs_auto, Voyage voyage-4):
                   the query string is embedded server-side ({'text': q}); no client-side vectors.
  hybrid_search    $rankFusion (reciprocal rank fusion) over the two pipelines above, with weights;
                   scoreDetails kept so the investigator can show why a chunk ranked where it did.

Filters (all optional): doc_type, sections, publisher; each a string or a list (match any).
doc_type and sections are filter fields in both indexes; publisher only in docs_text, so the
semantic pipeline applies it as a post-filter over an over-fetched candidate list.
Every result is a chunk dict: _id, doc_id, chunk_no, title, publisher, doc_date, doc_type,
reliability, url, page, page_end, sections, score (+ scoreDetails for hybrid, text if asked).

Run:   .venv/Scripts/python -m atlas.search              (eval: hit@5, hit@1, MRR per mode, doc + chunk level)
       .venv/Scripts/python -m atlas.search "was Liverpool Street to Bethnal Green ground?"
"""
from __future__ import annotations

import sys

from atlas.db import db

TEXT_INDEX, AUTO_INDEX = "docs_text", "docs_auto"
FIELDS = ["doc_id", "chunk_no", "title", "publisher", "doc_date", "doc_type", "reliability", "url",
          "page", "page_end", "sections", "note", "attribution"]
FILTER_KEYS = {"doc_type", "sections", "publisher"}


def _as_list(v) -> list[str]:
    return [v] if isinstance(v, str) else list(v)


def _check(filters: dict | None) -> dict:
    filters = {k: _as_list(v) for k, v in (filters or {}).items() if v}
    bad = set(filters) - FILTER_KEYS
    if bad:
        raise ValueError(f"unsupported filter(s) {sorted(bad)}; use {sorted(FILTER_KEYS)}")
    return filters


def _search_stage(q: str, filters: dict, paths: tuple[str, ...] = ("text", "title")) -> dict:
    must = [{"text": {"query": q, "path": list(paths)}}]
    flt = [{"in": {"path": k, "value": v}} for k, v in filters.items()]
    return {"$search": {"index": TEXT_INDEX, "compound": {"must": must, "filter": flt} if flt else {"must": must}}}


def _vector_stage(q: str, k: int, filters: dict) -> list[dict]:
    pre = {f: {"$in": v} for f, v in filters.items() if f != "publisher"}
    vs = {"index": AUTO_INDEX, "path": "text", "query": {"text": q},
          "numCandidates": max(100, 20 * k), "limit": k * (4 if "publisher" in filters else 1)}
    if pre:
        vs["filter"] = pre if len(pre) == 1 else {"$and": [{f: c} for f, c in pre.items()]}
    stages = [{"$vectorSearch": vs}]
    if "publisher" in filters:
        stages += [{"$match": {"publisher": {"$in": filters["publisher"]}}}, {"$limit": k}]
    return stages


def _project(with_text: bool, details: bool = False) -> dict:
    p = {f: 1 for f in FIELDS} | {"score": {"$meta": "score" if details else "searchScore"}}
    if details:
        p["scoreDetails"] = {"$meta": "scoreDetails"}
    if with_text:
        p["text"] = 1
    return {"$project": p}


def text_search(q: str, k: int = 5, filters: dict | None = None, with_text: bool = True,
                paths: tuple[str, ...] = ("text", "title")) -> list[dict]:
    """paths=('text',) searches the source text only, not our descriptive titles."""
    f = _check(filters)
    pipe = [_search_stage(q, f, paths), {"$limit": k}, _project(with_text)]
    return list(db()["documents"].aggregate(pipe))


def semantic_search(q: str, k: int = 5, filters: dict | None = None, with_text: bool = True) -> list[dict]:
    f = _check(filters)
    proj = _project(with_text)
    proj["$project"]["score"] = {"$meta": "vectorSearchScore"}
    return list(db()["documents"].aggregate(_vector_stage(q, k, f) + [proj]))


def hybrid_search(q: str, k: int = 5, filters: dict | None = None, weights: dict | None = None,
                  with_text: bool = True, depth: int = 20) -> list[dict]:
    """Reciprocal rank fusion of the full-text and semantic rankings (each `depth` deep).
    weights: {'text': w1, 'semantic': w2}, default 1:1."""
    f = _check(filters)
    bad = set(weights or {}) - {"text", "semantic"}
    if bad:
        raise ValueError(f"unsupported weight(s) {sorted(bad)}; use 'text' and/or 'semantic'")
    w = {"text": 1.0, "semantic": 1.0} | (weights or {})
    stage = {"$rankFusion": {
        "input": {"pipelines": {
            "text": [_search_stage(q, f), {"$limit": depth}],
            "semantic": _vector_stage(q, depth, f),
        }},
        "combination": {"weights": w},
        "scoreDetails": True,
    }}
    pipe = [stage, {"$limit": k}, _project(with_text, details=True)]
    return list(db()["documents"].aggregate(pipe))


# ---------- eval ----------

# Investigator questions -> doc_ids of which at least one should be in the top 5. Written by the builder
# after reading the corpus (not blind); the stored titles are also the builder's, which helps 'text'
# (text + title) on several questions, so 'text_body' searches the source text only.
EVAL: list[tuple[str, set[str]]] = [
    ("Was Bethnal Green to Liverpool Street ground for noise?", {"MQ-2022-1791"}),
    ("TfL cab noise level Liverpool Street to Bethnal Green 2024", {"FOI-1231-2425"}),
    ("eight weeks grinding plan", {"MQT-2026-2156", "MQ-2022-2799"}),
    ("Hammersmith & City cab noise", {"FOI-3780-2324"}),
    ("distance Bethnal Green Mile End", {"FOI-3481-2425"}),
    ("deep Tube reconditioning Liverpool Street Bethnal Green eastbound", {"Weir-letter-2022-12"}),
    ("how often does rail grinding need to be repeated to stay effective", {"CSOPP-2022-12-tube-noise"}),
    ("rail roughness measurements between Bethnal Green and Mile End", {"FOI-1730-1819"}),
    ("which Tube sections are the loudest, 92.8 decibels", {"BrixtonBuzz-2026-01-27"}),
    ("did TfL refuse to share noise data with Assembly Members", {"MQT-2026-2156"}),
    ("Central line noise frequency spectrum in the driver's cab", {"FOI-2986-2324"}),
    ("were the planned Central line grinding sites completed in 2022", {"MQ-2022-2799"}),
]
# Chunk level: the chunk that holds the citable line (list entry, quote or table row) must be returned.
CHUNK_EVAL: list[tuple[str, set[str]]] = [
    ("Was Bethnal Green to Liverpool Street ground for noise?", {"MQ-2022-1791#001"}),
    ("deep Tube reconditioning Liverpool Street Bethnal Green eastbound", {"Weir-letter-2022-12#001"}),
    ("distance Bethnal Green Mile End", {"FOI-3481-2425#003"}),          # the parsed distance table
    ("how often does rail grinding need to be repeated to stay effective", {"CSOPP-2022-12-tube-noise#001"}),
]
MODES = {"text": text_search, "text_body": lambda q, k, **kw: text_search(q, k, paths=("text",), **kw),
         "semantic": semantic_search, "hybrid": hybrid_search}


def evaluate(cases: list[tuple[str, set[str]]] = EVAL, key: str = "doc_id", k: int = 5) -> dict:
    ranks = {m: [] for m in MODES}
    for q, want in cases:
        row = []
        for m, fn in MODES.items():
            got = [r[key] for r in fn(q, k, with_text=False)]
            rank = next((i + 1 for i, d in enumerate(got) if d in want), None)
            ranks[m].append(rank)
            row.append(f"{m}:{rank or '-'}")
        print(f"{'  '.join(row):44s} {q}  [{', '.join(sorted(want))}]")
    n = len(cases)
    out = {}
    for m, r in ranks.items():
        out[m] = {f"hit@{k}": sum(x is not None for x in r) / n, "hit@1": sum(x == 1 for x in r) / n,
                  "mrr": sum(1 / x for x in r if x) / n}
        print(f"{m:9s} hit@{k} {sum(x is not None for x in r)}/{n}  hit@1 {sum(x == 1 for x in r)}/{n}  "
              f"MRR@{k} {out[m]['mrr']:.2f}")
    return out


def main():
    if len(sys.argv) > 1:
        q = " ".join(sys.argv[1:])
        for r in hybrid_search(q, 5, with_text=True):
            ranks = {d["inputPipelineName"]: d.get("rank") for d in r["scoreDetails"]["details"]}
            print(f"{r['score']:.4f}  {r['_id']:30s} p{r.get('page')}  ranks {ranks}  {r['url']}")
            print("        " + r["text"][:240].replace("\n", " "))
        return
    print(f"doc level ({len(EVAL)} questions)")
    evaluate()
    print(f"\nchunk level ({len(CHUNK_EVAL)} questions)")
    evaluate(CHUNK_EVAL, key="_id")


if __name__ == "__main__":
    main()
