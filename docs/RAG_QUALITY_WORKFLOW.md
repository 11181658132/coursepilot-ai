# RAG quality improvement: evidence before tuning

This workflow is entirely offline. It reuses completed **fictional DEMO101
development** responses; no model/embedding calls, private-course uploads or
changes to hosted settings are made. The 20 holdout cases remain excluded from
the review packet and tuning. The dataset/corpus hashes, scorer version, case
coverage, questions and reference drafts are validated before export.

## 1. Generate a fresh local packet

From the repository root:

```sh
PYTHONPATH=backend .venv/bin/python -m app.quality_review \
  --output evals/reports/quality-review-20261021
```

Use a new directory name on subsequent runs. Existing review packets/labels are
never overwritten. CLI output is restricted to ignored `evals/reports/`; never put
these files in `frontend/public`, a deployed API route, Git or a shared artifact.

Outputs:

- `diagnosis.md` / `diagnosis.json`: failure-tag counts and same-question reranker
  pass/fail transitions. These are proxies/hypotheses, not causal explanations.
- `review.html`: standalone local human-review interface. It includes question,
  answer, citations, retrieved text, draft references and all five fictional sources.
- `manifest.json`: exact mapping to the original engine/case/response hashes.
- `review-analysis.json` / `review-analysis.md`: human coverage and explicitly
  denominated correctness/support metrics. A fresh packet has zero human reviews,
  null rates and an empty repair queue, not assumed passes.

No engine name, automatic grade, score, latency or cost is displayed in the review
interface. Engine order is deterministically shuffled per question. This is
display blinding, not cryptographic concealment: engine identity may be inferred
from answer style or source formatting. Failures are prioritized, but passing
answers are retained too; a partial review is a selected development sample.

## 2. Human review

Open `review.html` locally. Enter your reviewer alias, read the actual source and
grade **both** dimensions independently:

| Dimension | Pass | Fail |
|---|---|---|
| Correctness | Answers the actual question accurately and sufficiently; appropriate refusal when information is missing | Wrong statement, important omission, unwarranted refusal or incorrect scope |
| Faithfulness / support | Every substantive claim is supported by the cited/retrieved course text | A real quote exists but does not support the conclusion, or a substantive claim has no support |

For an example: answering that retrieval fetches external evidence is supported
by the introductory document. Claiming that retrieval guarantees a factually
correct answer is not supported merely because the same paragraph mentions RAG.
For a question about an absent instructor contact, declining to invent it can be
correct; do not require a citation solely to make a refusal look grounded.

The reference answers and expected source sets are **drafts, not gold labels**.
If a valid answer cites a supporting source omitted from the draft label, flag
`reference_label` and explain it rather than silently changing the historical
benchmark. Leave uncertain grades blank; do not force a pass/fail guess. Negative
grades or label disputes require notes. A reviewer must personally attest that
they checked the source. AI-generated opinions cannot be entered as human review.

Select a primary failure stage: retrieval, answer, citation, reference label,
upstream, none or uncertain. Stages are human diagnoses, separate from automatic
failure tags. Where possible, have a second reviewer independently assess a sample
and resolve disagreements before calling the labels a reference set.

### Practical first session

Start with 5–10 questions, reviewing **all three anonymous responses per question**
before comparing engines. Across sessions cover ordinary concepts, multi-source
reasoning, absent facts and adversarial/wrong-premise cases. The complete packet
contains 30 development questions / 90 responses; the 20 holdout answers remain
excluded. Use the answer jump menu and “下一条未完成审核” to navigate. Progress
shows both completed responses and questions whose available responses are all
reviewed; this UI count is not a paired-quality metric when an engine is blocked.
Do not check the human attestation or fill grades unless you personally checked
the source. Unknown remains ungraded. A failed grade cannot select “未发现问题”.

**Export the JSONL before closing or refreshing.** The page makes no network calls
and does not store grades in browser storage. The export includes completed rows
only; restore the exported file to continue. This avoids leaving review content
in persistent browser storage, but requires explicit downloads for backup.

## 3. Import without changing original reports

```sh
PYTHONPATH=backend .venv/bin/python -m app.quality_review \
  --packet evals/reports/quality-review-20261021 \
  --labels /absolute/path/to/coursepilot-human-reviews.jsonl \
  --output evals/reports/quality-review-import-20261021
```

Import rejects duplicate/unknown records, changed answers/evidence, mismatched
fingerprints, incomplete scores and unattested labels. It writes new per-engine
JSONL compatible with the existing `apply_reviews` scorer, plus
`human-summary.json`; original `latest.json`, historical metrics and existing
`review.jsonl` files remain untouched. Cross-engine human correctness/support
averages use **only the intersection of reviewed case IDs across all three
engines**; if there are no paired reviewed cases, both metrics stay null. Coverage
and denominators are explicit. These are not held-out generalization metrics.

The new `review-analysis.json` additionally includes per-category correctness and
support (numerator, denominator, rate), blocked/eligible/reviewed counts, the
intersection-only paired scores, primary human problem stages and a confusion
table comparing the proxy pass with human correctness **and** support. This is
descriptive agreement on selected reviewed outputs, not independent validation
of the proxy grader. A local repair queue retains response hashes, reviewer notes
and suggested investigations, not automatically proven root causes. Reference
disputes appear even if the reviewer accepts the answer. Noisy draft-reference
labels must be resolved before treating them as gold.

`review-analysis.md` gives a compact dashboard and investigation counts without
copying answers, quotes, reviewer names or notes. All artifacts remain ignored;
review them deliberately before sharing even aggregate results. These are cached
**historical** model responses, not evidence of current deployed-code quality.
The importer rejects extra fields, overlong labels, invalid stages and negative
grades claiming no issue. No labels or historical reports are overwritten.

## 4. Decide the next change from failure evidence

1. Evidence missing/incomplete: inspect chunk boundaries, course/type filtering
   and candidate recall. Do not immediately change the generator prompt.
2. Evidence sufficient but answer wrong: inspect context noise, instructions,
   refusals and completeness separately from retrieval.
3. Citation mismatch: check claim support and label completeness, not only exact
   quote presence. Citation formatting repair alone does not establish accuracy.
4. Reranker regression: inspect lost useful evidence and added latency; retain
   the no-rerank baseline. Do not claim a reranking improvement without measurement.

Freeze this review snapshot, change one variable, add offline regression tests,
then request a **new explicit API budget** before regenerating development answers.
Run the untouched holdout only as a separately authorized release evaluation.

## Current historical triage (2026-10-21)

The original 90 completed responses have 6/30, 10/30 and 11/30 proxy failures for
File Search, hybrid without reranking and hybrid with reranking respectively.
Reranking changes 2 failures to passes and 3 passes to failures; 17 remain passes
and 8 remain failures. Both hybrid variants have 8 missing-citation and 8
unexpected-refusal flags; flags overlap and do not prove root cause. A separate
citation-format diagnostic previously repaired 7 formatting failures on its own
selected traces; it did **not** rerun these 90 historical responses. Human review
coverage remains zero until actual reviewers submit labels. New API cost: $0.

This follows the task-specific evaluation and human-calibration guidance in
[official OpenAI documentation](https://developers.openai.com/api/docs/guides/evaluation-best-practices).
