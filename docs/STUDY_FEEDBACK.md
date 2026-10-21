# Evidence-grounded learning feedback loop

Implemented flow:

1. Human confirms an evidence-grounded study plan.
2. Human submits a self-test answer (explicit metered action).
3. Agent searches the selected course once and drafts formative feedback.
4. Agent may propose edits to unfinished tasks on later study days.
5. Human reviews the before/after tasks and source quotes, then explicitly confirms.
6. Store applies the proposal atomically only if the plan version is unchanged.

## Assessment is advice, not an exam grade

The UI uses `mostly_correct`, `needs_review` and `uncertain` suggestions, not
numeric marks, official scores or claims of teacher approval. The prompt tells
the model to assess against retrieved course text, **not treat the previous AI
quiz answer as ground truth**. All student answers, prior model outputs and
retrieval passages are untrusted data, not instructions to change scope or tools.

Insufficient retrieval yields deterministic `uncertain` feedback without a second
model call. Uncertain feedback cannot create weaknesses or propose task edits.
Non-uncertain feedback requires verified source IDs and verbatim quotations.
Quotes are checked against retrieved text; this does **not** establish semantic
support or scoring accuracy. The UI asks the learner to verify the source and
clearly labels possible weaknesses as suggestions.

## Tools and limits

`FeedbackAgent` exposes only `search_knowledge` then `propose_feedback`, using
strict schemas and server-side validation. The model has no confirmation,
progress-write, shell, web or arbitrary-database tool. At most two model turns,
one search and four retrieved passages are allowed. It reuses the existing
`MeteredClient` reservations, request timeouts and safe failure handling.

Each feedback run shares the existing $0.05 per-operation reservation cap and
global daily/monthly budgets with plan generation. Public generation and feedback
**share**, rather than each receive, the existing two-runs-per-IP-per-UTC-day and
20-global-runs-per-day request quotas. Global public estimates remain $0.10/day
and $0.50/month, excluding older RAG/hosting charges. Unknown provider calls retain
their reservations and are never automatically retried. Confirmation invokes no
model. The public answer limit is 400 characters (local 1000); each plan retains
at most 10 feedback records.

## State and persistence

No new database migration or provider grant is needed. Feedback operations reuse
the existing `study_runs` / public `cp_demo_study_runs` schema with a distinct
`_feedback:<course>` internal category, so they do not pollute the course plan
list. Each operation has its own idempotent request key, charge ledger, status,
submitted answer, source task snapshot, remaining-task snapshot, safe tool trace,
assessment, proposal and base-plan version. Confirmation records the applied plan
version and timestamp. Parent plans reference their feedback
IDs. The public store checks visitor ownership for parent and child IDs before SQL.

Submission atomically records a running operation and advances the parent version;
model calls happen **outside** database transactions. Replaying the exact request
returns existing results without model calls or quota consumption. Changed payloads
with the same request ID conflict. Overdue operations expose `recovery_available`;
explicit recovery marks them `interrupted` without replaying paid calls or changing
the active plan's tasks. Completed tool checkpoints and unknown-cost reservations
are retained. See [Agent recovery](AGENT_RECOVERY.md). Failed operations preserve original tasks.

Only an active plan can receive answers. Only `needs_review` proposals with edits
can be confirmed. Confirmation requires the current parent version to equal the
proposal's base version: later progress updates or another submission invalidate
old proposals. Confirmation edits descriptive task fields and source citations
only; task ID, day, minutes and completed state cannot change. Current/earlier days,
completed tasks, foreign task IDs and duplicate task edits are rejected. The
ordinary plan-confirmation endpoint cannot activate a feedback child record.

## API

Local routes (public gateway adds `/api/demo` instead of `/api`):

```text
POST /api/study-plans/{plan_id}/tasks/{task_id}/answers
  {request_id: UUID, version: integer, answer: string}

POST /api/study-plans/{plan_id}/feedbacks/{feedback_id}/confirm
  {confirmed: true, version: integer}
```

Both return the parent plan with a `feedback` history. Public routes keep the
existing same-origin checks, signed anonymous visitor cookie, signed internal
worker requests, DEMO101-only scope and server-only database credentials. Public
answers and feedback are persisted in the dedicated demo PostgreSQL database;
answers are transmitted to the model when the user submits. The UI discloses this
and warns against personal information. Local answers stay in local SQLite, but
real model generation still transmits the answer/evidence to the provider.
Neither raw answers nor provider errors are added to telemetry, Git or public docs.

## Verification boundary

Free tests exercise the real tool loop with scripted SDK responses, SQLite state,
API validation, idempotency/concurrent submission, stale versions, uncertain and
missing evidence, bad citations/tools, immutable progress fields, quota sharing,
cross-visitor isolation and unknown-cost reservation retention. CI also runs real
PostgreSQL persistence/confirmation/isolation tests against a disposable service.
Browser acceptance uses an isolated scripted-model backend, not a production
grading result. Simulated test-ledger costs are not actual API charges.

**No new live model calls were made for this implementation.** True feedback
quality, semantic faithfulness and live latency remain unmeasured for this feature.
A separately approved, bounded real-model smoke test should use fictional DEMO101
answers, then human-review the feedback before claiming scoring quality.
