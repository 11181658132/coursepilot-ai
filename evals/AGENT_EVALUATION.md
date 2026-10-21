# Study-plan Agent evaluation protocol

## Scope and command

This suite evaluates **scripted service contracts**, not live-model accuracy.
It injects predetermined Responses tool calls and fictional retrieval passages
into the actual `StudyAgent` and SQLite `StudyStore`. No network SDK is constructed;
the real SDK constructor is also patched to fail if accidentally reached. No API
key, private course documents or previously saved user plans are needed or read.

```bash
cd backend
../.venv/bin/python -m app.agent_evaluation --validate
../.venv/bin/python -m app.agent_evaluation
```

CI uses `python -m app.agent_evaluation` in its installed environment. A failed
check or runner exception produces a nonzero exit status; runner failures are
recorded per case rather than silently dropped from the denominator. There is
deliberately **no `--live` option**. A new live-model campaign needs a separately
approved budget and an explicitly bounded runner, not reuse of a past approval.

The versioned [scenario dataset](agent_cases.json) defines 30 development contracts:

| Category | Cases | What is exercised |
| --- | ---: | --- |
| Scripted completion | 5 | 1/3/7/14-day plans, a second search with source deduplication |
| State | 4 | Request idempotency, changed-payload rejection, confirmation gate, stale progress updates |
| Safety/failure | 21 | Missing course/evidence; false quotes/IDs; invalid days; unauthorized tools; injection-shaped calls; scope violations; malformed/incomplete/refusal output; parallel calls; search/loop limits; timeout/missing usage; run/day/embedding budgets; payload size |

All cases use a public fictional DEMO101 passage. Retrieval is a fixture and does
not measure BM25/vector recall. Injection tests simulate a model that has already
followed a malicious instruction, then verify that server-side scope/tool checks
block the dangerous call. They **do not measure model resistance to injection**.

## Scoring and denominators

- `contract_pass`: all checks pass for a case, including expected initial state,
  error code, scripted call count/bound, course scope and scenario-specific checks.
  The denominator includes every scheduled case, including runner errors.
- `scripted_completion`: the five positive scripted completion scenarios satisfy
  the workflow, plan structure, confirmation, persistence and source checks. It is
  **not the model's probability of completing a user task**.
- `displayed_quote_match`: normalized verbatim quotes exist in the expected fixture
  source, with correct source ID, chunk and path. The grader is independent of the
  production citation validator. Denominator: all citations in returned plans.
- `citation_coverage`: returned tasks with citations / returned tasks. Positive
  cases separately fail if expected tasks or citations are absent. A failed/no-task
  plan cannot gain a perfect citation score from an empty denominator.
- `harness_p50_ms` / `harness_p95_ms`: nearest-rank percentiles of local scripted
  execution, including state checks. No remote inference latency is present.
- `actual_additional_api_cost_usd` and `external_api_calls`: zero in this mode.
  `simulated_accounted_usd` includes fictional ledger reservations, including
  unknown charges in timeout tests; it is **not a bill or a live cost estimate**.

Semantic correctness, claim support, model task success and live inference latency
are explicitly `null`. Human review count is zero. Do not turn quote presence,
structural success, or scripted safety results into a model accuracy claim.

## Artifacts and reproducibility

Reports stay under ignored `evals/reports/agent-contract/`:

- `latest.md`: concise Chinese summary with explicit numerators/denominators.
- `latest.html`: escaped static per-case table, checks and detailed metrics.
- `latest.json`: per-case check booleans, safe tool traces and aggregated metrics.
- `<timestamp>-<random-id>/report.json`: preserved snapshot of each run.

Reports contain dataset, actual selected scenarios, source and implementation
digests, scorer version, UTC run timestamp and Python version. Reports deliberately
exclude raw questions/answers, retrieved text, API keys, exception bodies and user
file names. Keep reports/databases local; only sanitized aggregates belong in the
README. The historical RAG comparison and live Agent smoke test are not overwritten
or mixed into these rates.

Scorer regression tests include wrong quotes/IDs/chunks/paths, absent citations,
wrong days, deliberately wrong expected outcomes, zero-denominator behavior,
runner exceptions, HTML escaping and nonzero CI exits. This prevents an evaluator
that silently grades every output as a pass.

## Next: real-model and human evaluation

The earlier single fictional-corpus live smoke test only established an executable
happy-path workflow. It is not an independent multi-case benchmark and does not
enter these denominators.

For a future approved live campaign, predeclare task IDs, corpus/code/model hashes,
tool/cost limits and expected outcomes before generation. Separate development
from unseen tasks, preserve all failures and unknown cost reservations, and report
live task completion, latency, token usage and cost separately from this suite.
Do not silently run holdout questions during debugging or replace old results.

A human should grade the saved live draft *before* confirmation:

1. Does the plan address the requested learning goal and time budget?
2. Does each source actually support the associated learning goal and quiz answer?
3. Are the self-test questions and answers correct and useful, not just well formed?
4. For missing evidence, did the Agent avoid inventing study/exam requirements?
5. Were confirmation, scope and tool boundaries respected throughout the trace?

Bind labels to the exact response/configuration hash and task ID. Store
`correct: null`, `supported: null`, `reviewer: null` until an actual human reviews
them, plus reviewer identity/alias and notes. Report review coverage and unresolved
cases; never label AI-generated grades as human review.

Methodology reference: [OpenAI Agent evaluation guidance](https://developers.openai.com/api/docs/guides/agent-evals).
