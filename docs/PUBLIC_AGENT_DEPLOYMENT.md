# Public RAG + study Agent deployment

Status (2026-10-21): deployed at https://coursepilot-ai-ebon.vercel.app/ with a
dedicated Neon PostgreSQL database. CI passed all 151 backend tests (including the
three real PostgreSQL integration tests), 24 frontend tests, type checking, lint
and production build. The browser exposes the Agent interface and establishes its
visitor session; live HTTP acceptance verified the state transitions below.

One fictional three-day run used 4 API calls (3 model turns and one retrieval
embedding), estimated $0.00059144, with zero uncertain charges. Draft creation,
confirmation, progress persistence on re-read, source expansion, idempotent replay
without extra calls, stale-write conflict, foreign-visitor read/write denial,
cross-origin denial and unsigned-worker denial passed. This is a single smoke
test, not a model-success-rate, semantic-faithfulness or load benchmark. Raw plans,
visitor cookies and reports are not committed.

## Infrastructure boundary

Keep the current Vercel frontend and signed Python RAG worker. Add a **dedicated
public-demo PostgreSQL database**, not the local course/conversation database.
Vercel supports PostgreSQL providers via its
[Marketplace storage integrations](https://vercel.com/docs/marketplace-storage).
Provisioning, account creation, integration grants and any paid plan require the
owner's approval. Do not assume any vendor's free tier will always stay free.

Only five versioned fictional DEMO101 files/embeddings are packaged. No real course
files, local SQLite databases, reports, telemetry or private paths may be deployed.

## Setup checklist

1. In Vercel, select the existing CoursePilot project, then Storage. Connect a new,
   independent PostgreSQL database (for example, Neon through Marketplace). Check
   the displayed plan/limits and approve any new permissions personally. Do not
   upgrade, enter a paid agreement or accept terms on the owner's behalf.
2. In that database's SQL editor, run
   [001-public-agent.sql](../frontend/sql/001-public-agent.sql) once. Runtime handlers
   do not create tables or fall back to temporary SQLite. Use a pooled connection
   string with TLS. Keep it server-only; never paste it into chat or Git.
3. Set Vercel server environment variables for the deployment environment:

   | Variable | Meaning |
   | --- | --- |
   | `DATABASE_URL` | Dedicated demo PostgreSQL URL injected by the Neon integration; never a `NEXT_PUBLIC_*` variable |
   | `DEMO_RATE_LIMIT_SALT` | Strong random server secret of at least 32 characters; also signs visitor sessions/internal requests |
   | `OPENAI_API_KEY` | Existing dedicated public-demo project key |
   | `OPENAI_MODEL` | Keep `gpt-6-luna` for current metering |
   | `NEXT_PUBLIC_DEMO_MODE` | `true` |
   | `DEMO_AGENT_ENABLED` | Leave unset/false until DB schema is initialized; set `true` to enable |

   `DEMO_AGENT_URL` is optional: trusted server-only override for `/api/agent`.
   Otherwise the gateway uses the configured production project domain. Never
   derive the worker URL or identity from user JSON or forwarded host headers.

4. Before deploying, run backend tests/lint, Agent contracts, frontend typecheck,
   tests/build and `node frontend/scripts/prepare-rag.mjs --check`. The bundle now
   contains 17 allowlisted canonical Python modules, including the shared Agents.
5. Run the real PostgreSQL integration tests in CI's disposable Postgres service.
   They are deliberately skipped locally when no explicit loopback test DB is
   configured. For local PostgreSQL testing, use a disposable `coursepilot_test`
   database and `COURSEPILOT_TEST_DATABASE_URL`; tests create/drop only random test
   schemas. Never point this test suite at the public/production database.
6. Review the exact public Git diff, commit only code/docs/fictional fixtures, then
   deploy. Keep `DEMO_AGENT_ENABLED=false` if DB, credentials or checks are missing.

## Browser acceptance (requires a separate model-call budget)

Use two independent browser sessions/cookie jars:

- A creates a 1–3-day plan, opens its source quote, confirms and completes a task.
- Refresh A and verify progress persists. Reconnect/redeploy and verify DB state
  persists independently of the Python worker's temporary directory.
- B's list is empty; even with A's exact plan ID, B cannot read, confirm or modify it.
- Repeating the same request UUID/payload returns the same plan without extra model
  calls. A changed payload with that UUID is rejected. Stale progress writes conflict.
- Invalid/expired cookies cannot mutate state. Cross-origin requests and unsigned
  direct Python-worker calls fail. Database outage/feature-disable fails closed.
- Verify the shared quota and reservation behavior without consuming the live
  allowance unnecessarily. Do not call a mock or SQLite surrogate a PostgreSQL/live
  deployment acceptance test.

## Security and costs

`/api/demo/study-plans/...` issues a signed 30-day anonymous `HttpOnly`,
`SameSite=Strict`, HTTPS `Secure`, host-only cookie. Cookies identify independent
visitors, not authenticated accounts; clearing them or switching browsers loses
access to that visitor's plans. The server derives a namespace using HMAC and binds
all plan IDs, request deduplication and queries to it. Knowing a foreign plan ID
does not authorize access. All responses are noncacheable. Raw IPs are not stored;
the gateway HMACs the Vercel-supplied address for a persistent daily quota.

The Python worker requires a domain-separated, timestamped HMAC from the gateway,
validates every action, and allows only DEMO101, at most 3 days, 90 minutes/day and
a 400-character goal. The canonical Agent allows four model turns/two searches,
an inter-call 38-second deadline and eight-second SDK timeout. The model cannot
confirm or update progress. Confirmation and task updates do not invoke the model.
Recovery also invokes no model/index: the signed `recover` action marks only
expired 180-second plan/feedback leases interrupted. It preserves uncertain charges
and experience quotas, fences late workers, and never resumes paid execution.
The metadata lives in existing payload columns; no SQL migration is required.
See [Agent recovery](AGENT_RECOVERY.md) for versioning, checkpoint and test details.

PostgreSQL transaction-level advisory locks serialize **short state/budget
transactions**, never entire model calls. Defaults are 2 new plan/feedback runs/IP/day,
20 globally/day, estimated reservations of $0.05/run, $0.10/day globally and
$0.50/month globally (UTC). Failed/unknown provider calls keep reservations.
These are conservative **API list-price estimates**, not a provider billing
guarantee, and they do not cap Vercel/database hosting charges. Set appropriate
provider/hosting controls and keep the existing RAG costs separate: the older RAG
endpoint's in-memory request counter is not made durable by this Agent change.

Session/worker secrets and DB URLs are never returned or logged. Store exceptions
and provider bodies are replaced with safe error messages. Do not enable database
driver debug logging in production. Namespace enforcement is application-level,
not an assertion of PostgreSQL RLS or full account/tenant authentication.

For a larger multi-user product, add real authentication, retention/deletion and
recovery controls, least-privilege DB roles, operational monitoring and persistent
rate limiting for the older chat endpoint as well. Do not present a small anonymous
portfolio demo as production-grade security or compliance.
