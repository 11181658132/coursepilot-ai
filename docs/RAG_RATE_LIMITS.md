# Durable public RAG request limits

Both `hybrid` and `file_search` go through the same database-backed gate before
any paid model/embedding request. Limits remain **5 accepted requests per network
address per UTC day** and **300 globally per UTC month**. These are attempt/request
caps, not token-cost caps or a provider/hosting billing guarantee.

## Request path

1. Next validates origin, course, material, engine and question. Missing model
   configuration is rejected before quota reservation.
2. The gateway derives an HMAC-SHA256 network-address digest with a dedicated
   `rag-quota-v1` domain. On Vercel only `x-vercel-forwarded-for` is trusted; missing
   platform addresses share an `unknown` bucket. Cookies/client JSON/forged host
   headers cannot choose the identity or worker destination.
3. A timestamped `rag-limit-v1` signature protects the private `/api/rag-limit`
   worker. Its envelope accepts only `status` or `consume` and a 64-hex digest.
   Unsigned/expired/oversized requests are rejected before database work.
4. `consume` uses the existing transaction advisory lock and `cp_demo_limits`
   table. The `rag:ip:<digest>` daily and `rag:global` monthly counters are checked
   and updated together; a denied request changes neither. `BEGIN IMMEDIATE`
   SQLite fixtures test the same business logic, not PostgreSQL compatibility.
5. Only a valid allowed response permits the model request. No in-memory fallback,
   quota refund or automatic retry exists. A crash after quota commit but before
   model dispatch can use one attempt without model charges. Failed/timeout
   inference may already be billable, so it also retains the attempt.

The quota worker does not import/initialize an index or model client. It requires
only the existing server-only `DATABASE_URL` and `DEMO_RATE_LIMIT_SALT`; Agent's
enable flag is independent. No schema migration, new account or SDK dependency is
needed. `DEMO_RAG_LIMIT_URL` is an optional server-only destination for local tests;
production defaults to the configured canonical Vercel project domain.

## Read-only UX

`GET /api/demo/chat` reads remaining daily/monthly quota, never consumes it and
never invokes a model. Responses are `no-store`. The UI starts with unknown quota,
not a hard-coded five; it disables submission until quota is read, shows database
failure/exhaustion, and offers an explicit free refresh. POST also returns the
remaining counts after an accepted attempt, including upstream failures. Reads
are point-in-time snapshots, not reservations or a guarantee of subsequent access.

Quota denials return 429 and Retry-After until the relevant UTC boundary. Upstream
429/Retry-After remain distinct: they do not restore the already-reserved attempt.
No key/digest, database exception body, raw IP or prompt appears in the public quota
response or application telemetry. Daily digests are pseudonymous, not an assertion
of irreversibly anonymous data. Counts persist in the dedicated demo database;
automatic retention/pruning is not implemented. Secret rotation changes IP digests
and may reset per-address coverage, but does not reset the monthly global counter.

## Rollout and verification limits

Durable counters start at this release; old per-instance memory counts cannot be
reconstructed or retroactively merged. Once deployed they survive cold starts,
instance replacements and concurrent processes. Address-based limits are not
account authentication, cannot stop distributed-IP abuse and may group NAT users.
RAG keys are distinct from Agent request/budget keys and do not refund or spend
Agent allowances.

Offline tests cover status reads, restarts, concurrent daily/monthly caps, UTC/year
rollovers, rollback of partial updates, namespace separation, HMAC domain/expiry,
fail-closed behavior, header spoofing, module reloads, parsing and uncertain calls.
CI verifies actual PostgreSQL concurrency/persistence using a disposable loopback
database. Deployment acceptance should use GET/status and unsigned-worker rejection;
do not call a live model just to check rate limiting. New model API cost: **$0**.
