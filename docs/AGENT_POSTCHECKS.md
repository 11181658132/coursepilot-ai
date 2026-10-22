# Agent execution → independent checks → explicit confirmation

The bounded tool loop now writes a versioned `verification` report before a plan
or self-test feedback draft is published. This is deterministic validation, **not
an LLM judge or semantic accuracy score**; it adds no provider calls or quota.

## Hard gates

- Plans: exactly the requested days, unique task IDs, fixed daily minutes, false
  initial completion flags, nonempty learning/self-test fields, course-scoped
  current sources and verbatim quote presence. Local source hashes are retained
  in citations. A source changed after retrieval cannot pass solely because the
  quote existed in the earlier model context.
- Feedback: current parent version, only unfinished later tasks may be edited,
  no injected ID/day/minute/progress fields, no repeated task IDs, no unsupported
  uncertainty-to-replanning transition, and current evidence for feedback/edits.
- A failed generation postcheck records blocked check IDs, discards rejected task
  or adjustment content and does not activate or apply anything. Provider errors
  and source text are not copied into the check report.
- Local API and signed public worker confirmation paths re-read sources via a
  verifier inside the store's short confirmation transaction. A rejected check
  rolls back the state transition; no model call or new charge is made.
- Replaying an already-confirmed operation remains idempotent: it does not apply
  changes a second time or retroactively undo completed work.

Both local and public paths use the canonical implementation. Public confirmation
may load the allowlisted precomputed fictional index, but never invokes embedding
or generation. Anonymous namespace checks occur before a source verifier runs.
No public upload permission, cost budget or database schema is expanded.

## Human judgment remains a separate boundary

Reports explicitly mark `goal_coverage` and `semantic_support` as
`requires_human_review`. The UI requires an unchecked-by-default acknowledgment
before enabling confirmation and invalidates that acknowledgment on plan/version
change. It does **not** claim keyword overlap, structured JSON, or literal quotation
proves that a study goal is covered, an answer is correct or a quote entails a claim.
The existing explicit `confirmed: true` request is the user's approval, not evidence
that independent human grading has happened. No human evaluation labels are created.

Existing drafts without a report remain readable. HTTP confirmation runs the fresh
checks; it may reject a legacy draft whose sources are no longer available.
Low-level store callers can omit a verifier for legacy fixture/import compatibility;
production HTTP gateways supply it. This is a trusted single-user local store / a
separately scoped public gateway, not a new authentication system.

Filesystem freshness is checked at generation/confirmation boundaries, not an
atomic cross-filesystem snapshot or continuous monitoring after delivery. Explicit
source deletion does not rewrite previously saved model prose. Quote validation is
not a guarantee against all prompt injection or semantic errors.

This distinction follows [OpenAI Docs: structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs):
schema-constrained outputs still require application handling and content review.

## Offline/browser acceptance · 2026-10-22

`app.ingestion_demo` is an opt-in loopback-only acceptance server, with empty API
credentials, fake embeddings/answers, and isolated ignored report-directory DBs.
The fictional browser walkthrough exercised upload → durable queue → synthetic
failure → explicit UI retry → indexed document → mocked question/answer → changed
source warning → replacement reindex. A fixed scripted Agent draft displayed the
check report and disabled confirmation while its review checkbox was unchecked.
These are functional checks, not real-model accuracy or live API acceptance.

Example local-only commands (do not run a real worker against private course data):

```sh
PYTHONPATH=backend .venv/bin/python -m app.ingestion_demo \
  --directory evals/reports/<fresh-fictional-acceptance>
cd frontend
COURSEPILOT_ACCEPTANCE_MODE=1 NEXT_PUBLIC_API_URL=http://localhost:8014 \
  NEXT_PUBLIC_DEMO_MODE=false npm run dev -- --port 3014 --hostname 127.0.0.1
```

POST `/acceptance/worker?fail=true|false` controls only that test server's fake
worker; `/acceptance/agent-draft` uses a fixed scripted tool loop. These routes
are not registered by the production entry point or bundled for Vercel. Use
`--resume` only for your own existing isolated test directory. Frontend acceptance
builds use a separate ignored `.next-acceptance` directory, leaving an existing
development server untouched. Source files, DBs, transcripts, screenshots and raw
reports remain local and ignored; this work adds **$0 actual API spend**.
