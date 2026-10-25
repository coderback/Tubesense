# Why Argon? gemini-4-argon vs gemini-3.8-flash on the same investigator task (post-hoc)

## Protocol (written 2026-10-25T04:04Z, before any comparison run; not edited after results)

**Question.** On the investigator task that produced the evidence pack, does gemini-4-argon do better
than gemini-3.8-flash when both are judged by the same deterministic verifier (agent/verify.py)? This
is a post-hoc engineering comparison, not part of the pre-registered sensor analysis.

**What is held fixed.**
- Code: `agent/investigator.py` unchanged except the new flags `--model`, `--out DIR`, `--no-atlas`.
  Same SYSTEM prompt, same response schema, same feedback message, `MAX_ROUNDS = 2` (up to 3 calls).
- Inputs: same corpus (`data/corpus/chunks.jsonl`, corpus_hash recorded), same section stats
  (`outputs_posthoc/section_stats.json`, hash recorded), same retrieval block. `--no-atlas` reuses the
  Atlas hybrid-search hits recorded in `outputs_posthoc/investigation.json` (CACHED; they were identical
  in all three earlier Argon runs) instead of querying Atlas, so the prompt is byte-identical across
  runs, and no claims are written to Atlas.
- `thinking_level = "medium"` for both models. No temperature or other sampling settings (Argon accepts none).
- Fresh runs only (no `--review`). Each run writes `outputs_posthoc/compare/<slot>/investigation.json`;
  the production `outputs_posthoc/investigation.json` is never written.

**Runs.** 4 per model, 8 in total, one at a time, in counterbalanced ABBA order:
argon, flash, flash, argon, argon, flash, flash, argon. Slot names `01_argon` ... `08_argon`.
- Transport/API errors (HTTP 429/5xx, timeouts, connection resets) are not model outcomes: the slot is
  logged and retried (at most 2 retries).
- Schema/parse failures (response is not valid Investigation JSON) are model outcomes: counted as a
  failed run, not replaced; the rounds completed before the failure are kept.
- If time and quota allow, a further ABBA block of 4 may be added; any block is added whole.
- Pilot (declared, not counted): one trivial structured-output call per model to confirm that both
  model IDs accept `thinking_level="medium"` with a response schema.

**Deterministic metrics, per run** (computed by `agent/compare_models.py` from the run records):
1. claims proposed in round 0 (the first response);
2. round-0 verifier rejections, as claims rejected and as reasons by category: quote not found,
   quote too short, unknown chunk, unknown stat key, missing citation/stat, unsupported number,
   banned phrasing, formula (sub-reason kept), other;
3. rounds used (1-3); converged = 0 rejections in the last round;
4. final verified claims, final dropped claims, asks, headline ids that survived;
5. seconds per round and in total (wall clock, includes network);
6. prompt / output / thinking tokens per round if `usage_metadata` reports them;
7. schema/parse failures and API errors.

Primary summary: round-0 rejection rate (rejected / proposed), final dropped, converged within the
round budget, total seconds. Everything else is secondary.

**Secondary check (single judge, clearly labelled).** The verifier cannot catch framing errors. I (the
comparison agent, Claude) will read each run's final verified claims and asks against the full text of
the cited chunks and record issues in the categories that the independent review found
(`pack_review.json`): out-of-context quote, overstated commitment, overgeneralised scope, other
misleading framing (e.g. phone levels set against TfL figures). Each issue is graded major or minor.
Blinding: `compare_models.py --blind` writes the claims of all completed runs in a shuffled order under
opaque labels (P1..Pn), with the key in a separate file that I do not open until every judgement is
written. Claim style or count may still reveal the model; this is a single-judge assessment, not
ground truth.

**Analysis.** Medians and ranges per model, plus every run's values. With n = 4 per model no
significance tests are reported; a difference is described as consistent only if every run of one
model is on the same side of every run of the other, and even then it is n = 4 on one task and one
day. Whatever the result, it is reported; if Flash does as well as or better than Argon, this file
says so and the "why Argon" argument must rest on something else or be dropped.
