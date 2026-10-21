# Evaluation protocol

## Two datasets, different purposes

- `questions.jsonl`: the original 13 local university-course cases. Do not publish
  course answers, retrieved passages, raw reports, telemetry, or new private labels.
- `public_questions.jsonl`: 50 assistant-authored draft cases over the five
  fictional DEMO101 sources. They include reference answers and literal source
  anchors checked against the versioned files, **not human-reviewed gold labels**.
  They test the public example domain, not the full private course distribution.

The public split is frozen at 30 development and 20 holdout cases. Question families
stay within a split. Use development results for diagnosis. Do not tune prompts,
chunking, thresholds, or labels against holdout results, and do not silently move
failing cases across splits. The public holdout is a release checkpoint, not a
secret benchmark or proof that a pretrained model has never seen similar concepts.

## Free checks

From the repository root:

```bash
PYTHONPATH=backend .venv/bin/python -m app.benchmark --validate
PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/tests -v
```

CI validates the dataset, source anchors, grading logic, budgets, and reporting with
mock model calls. Passing unit tests does not establish real model quality.

## Intentional paid comparison

```bash
PYTHONPATH=backend .venv/bin/python -m app.benchmark --live --budget-usd 0.50
```

Defaults to development cases and compares:

1. Hosted File Search, forced retrieval, at most four results, course/type filters.
2. BM25 + cosine + RRF, without the LLM relevance reranker.
3. The same hybrid pipeline, with the relevance reranker.

All variants use the configured `gpt-6-luna`, an output cap of 1,500 tokens, no
conversation history, and the same five source files. Hybrid variants rehydrate
the versioned embeddings without document embedding calls. The hosted corpus is
read-only: every remote source must match its local filename, whitespace-normalized
parsed-text hash, course/type, and ready status. Original assistants-purpose files
cannot be downloaded; parsed text uses the official vector-store content endpoint.
If a configured store is stale, one exact-name existing public
store may be discovered and fully verified; no store is created or deleted.

Results checkpoint after every case under ignored `evals/reports/public-benchmark/`.
Rerunning resumes completed cases only when the dataset/corpus/configuration/code
fingerprint matches. Blocked cases can be retried; completed quality failures are
not rerun merely to obtain a passing answer. Use a new output directory deliberately
after changing code/configuration; remember a new directory also creates a new
budget ledger and is a new paid experiment.

The persisted ledger reserves costs **before** sending each paid request and locks
out concurrent runners. Unknown/failed requests retain their conservative reserve.
SDK retries are disabled, provider errors stop further paid calls, models/tier/tools
are restricted, and cached input discounts are not assumed. The cap is USD 0.50
per ledger, not an account-wide spending limit. Estimates exclude tax, pre-existing
storage, and unrelated application traffic. Keep provider-level spending controls.

Holdout evaluation requires explicit opt-in (`--split holdout --allow-holdout`) and
a separate output directory, with deliberate remaining-budget authorization. Do
not describe development scores as independent test performance.

## Metrics and failure analysis

Scoring version 2 intentionally changes legacy metric semantics:

- Retrieval hit: an expected document appears in **retrieved evidence**, not merely
  in generated citations. Recall is expected-document coverage; MRR uses the first
  relevant distinct retrieved document. These are file-level, not passage-level.
- Citation precision: relevant expected filenames / distinct cited filenames.
  No citations scores zero when a gold source set exists; it is not silently omitted.
  Source sets are draft and may miss legitimate alternatives until reviewed.
- Keyword recall: substring matches against bilingual alternative groups. This is
  a concept-coverage proxy, not semantic accuracy.
- Quote validity: supplied quote occurs verbatim in same-file returned evidence.
  No quoted citations means unavailable, not 100%. Presence is not entailment.
- Refusal: a bilingual phrase heuristic plus forbidden-canary checks. This is not
  a proof against prompt injection, and can produce false positives/negatives.
- Proxy pass requires relevant retrieval/citations and keyword coverage; multi-source
  cases can require every source. It is **not an answer-accuracy score**.
- P50/P95 use nearest-rank over completed requests; also report coverage, blocked
  count, token totals and cost. Never hide API failures by only showing pass rate.

`comparison.json` and `comparison.md` expose each full-run summary plus a paired
comparison over the intersection of completed case IDs in all three variants.
Different fingerprints cannot be compared. HTML reports show answers, source text,
reference answers and failure tags: retrieval miss/incomplete, missing/irrelevant
citations, invalid quote, incomplete-answer proxy, refusal proxy, forbidden output,
or upstream errors. Tags are diagnostic hypotheses, not causal proof.

These are small, sequential, fixed-order, one-sample-per-case experiments, not load
tests or significance estimates. File Search uses a hosted retrieval implementation;
its comparison to hybrid is end-to-end, not an isolated retriever ablation. Only the
two hybrid variants isolate reranking. Inspect paired failures before tuning.
With four returned results over only five documents, file-level retrieval hit is
inherently easy; this is a reproducible regression suite, not evidence of strong
large-corpus retrieval or performance on the private university-course collection.

## Human correctness and faithfulness review

```bash
PYTHONPATH=backend .venv/bin/python -m app.benchmark \
  --review-template evals/reports/public-benchmark/hybrid/latest.json
# Manually edit hybrid/review.jsonl, then:
PYTHONPATH=backend .venv/bin/python -m app.benchmark \
  --apply-reviews evals/reports/public-benchmark/hybrid/review.jsonl \
  --report evals/reports/public-benchmark/hybrid/latest.json
```

Templates contain **null grades**, not assumed passes. A human reads the question,
answer, evidence and draft reference in `latest.html`, then records:

- `correct`: the response addresses the question correctly and sufficiently; for
  missing facts, an explicit refusal is appropriate. Fix incorrect draft labels
  separately, version the dataset, and rerun rather than silently grading around them.
- `supported`: every material factual claim is supported by the supplied sources;
  no invented facts, contradictory interpretation, dates, locations, or credentials.
  For refusals, check absence against the full small corpus, not just the top results.
- `reviewer`: actual human identity/alias, and `notes` describing any failure.

Both grades and a nonempty reviewer are required for a reviewed row. Labels are
bound to a response/configuration hash; they cannot be reused for changed answers
or evidence. Correctness/faithfulness stay null until reviewed, with explicit review
coverage. Prefer blinded comparison and a second reviewer where practical.

Keep raw responses, review notes and reports local. Publish only deliberately
sanitized aggregate results with sample count, split, scoring version, label status,
coverage, limitations and cost. Never replace the original course benchmark's
historical results with fictional-corpus results without clearly distinguishing them.

Methodology reference: [OpenAI evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices).
