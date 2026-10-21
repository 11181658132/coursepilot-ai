# Agent interruption handling

This is **state recovery**, not automatic continuation of a model conversation.
The local SQLite and public PostgreSQL store share the same behavior. No new
database migration, service, model setting or API permission is required.

## Durable boundaries

- Every new plan/feedback operation gets a server-owned 180-second lease, longer
  than the current 120-second local deadline plus 20-second provider timeout.
- Before/after tool execution, checkpoints save safe tool names, statuses and
  evidence counts. Raw tool outputs, model history and rejected drafts are not
  persisted for recovery. The submitted goal/answer is already part of plan data.
- A cost reservation renews the lease in the same short transaction. Model calls
  remain outside transactions. Checkpoints do not alter the plan's business version.
- `GET`/list is read-only. It reports `recovery_available` if a plan or its feedback
  has expired. It does not call the model, extend the lease or mark a run interrupted.
- The learner explicitly invokes `POST /api/study-plans/{id}/recover` with
  `{confirmed: true, version: current_version}`. The public path uses `/api/demo`.
  Only expired operations transition from `running` to `interrupted`; a live run
  cannot be cancelled this way. Recovery preserves completed steps and changes an
  unfinished tool's status to `interrupted`. It records a safe error code/timestamp.
- If feedback is interrupted, the parent stays active and task content/progress is
  unchanged. Its version advances to invalidate potentially stale adjustment views.
- SQLite `BEGIN IMMEDIATE` / the existing PostgreSQL advisory transaction lock
  serialize recovery, checkpoints, finishing and new reservations. Expired or
  interrupted workers cannot checkpoint, reserve another call or publish a draft.
  A late response may still settle its existing usage reservation, but cannot
  resurrect a draft or modify the plan.

## Costs, replays and existing data

Unknown charges stay reserved; recovery does not refund provider charges, free
reservations or reset IP/global experience quotas. An expired request can be
recovered before a late provider usage record is available. Actual billing and
provider processing cannot be cancelled by a database status change.

Reusing the old request UUID returns the existing terminal result without model
execution. The UI can fill back the original goal/answer; only another explicit
submission creates a new request and may incur costs. No background paid retry,
automatic resume, recovery-time model call or arbitrary tool replay is added.

Pre-lease `running` records use `created_at + 180 seconds` as the recovery threshold;
existing completed plans and drafts are not expired. There is no data migration.
Visitors may recover only their own parent plan and associated feedback, using
the existing signed namespace, same-origin checks and explicit confirmation.

This is not a distributed task queue, automatic job continuation, exactly-once
provider execution or a billing guarantee. Recovery requires an explicit user
action and a working database. Wall-clock skew can delay or advance lease expiry;
the lease is conservative relative to current provider timeouts.

## Offline acceptance

`backend/tests/test_study_recovery.py` covers live-run protection, checkpoint
persistence, legacy leases, concurrent/idempotent recovery, stale versions,
feedback/plan separation, cost retention, late settlement, late-worker fencing,
explicit API confirmation and simulated process death during both Agent loops.
Additional route/worker/isolation tests verify signed recovery without loading
an index or model. CI exercises actual PostgreSQL recovery/concurrency/persistence
and quota preservation in a disposable loopback test database.

The existing scripted Agent contract suite remains a separate offline check;
neither suite measures real-model success, answer quality or inference latency.
No new live API call is needed for this implementation.

Recovery design distinguishes persisted state from retry permission, consistent
with the [official OpenAI error/recovery guidance](https://developers.openai.com/api/docs/guides/agents-api/errors).
CoursePilot continues to use its own Responses-based tool loop, not Agents API.
