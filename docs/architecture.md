# Architecture

![CoursePilot AI architecture](assets/architecture.svg)

## Query flow

1. The Next.js client sends a question with an explicit course and material scope.
2. FastAPI validates the request and applies an in-memory per-client rate limit.
3. The knowledge-base service selects only vector stores that match that scope.
4. The Responses API calls File Search and returns generated text, citations,
   retrieved evidence, token usage, and latency.
5. FastAPI stores the exchange and optional feedback in local SQLite. Telemetry
   stores a question hash instead of the raw question.
6. The client renders the answer, citations, evidence, latency, and estimated model
   token cost so the result remains inspectable.

## Ingestion flow

1. The scanner discovers supported documents under each course folder and assigns
   `lecture`, `tutorial`, or `other` material types.
2. Validation rejects traversal, unsafe names, unsupported extensions, mismatched
   file signatures, and oversized uploads.
3. SHA-256 hashes are compared with local JSON state. Unchanged files are skipped.
4. New or changed files are uploaded and attached to the appropriate vector store.
5. State changes use atomic writes. A replacement is recorded before the previous
   remote file is deleted, reducing the chance of losing a working index entry.

## Trust boundaries

- The browser never receives `OPENAI_API_KEY`.
- Retrieved documents are untrusted content; instructions inside them cannot
  replace the system policy.
- Raw documents, index IDs, conversations, telemetry, and evaluation reports are
  intentionally excluded from Git.
- Destructive document operations are explicit, rate-limited, and update both the
  local library and its remote index entry.
- Public portfolio deployments use a clearly labelled, fictional, read-only mode
  with no backend or model calls.

## Current trade-offs

Separate vector stores make scope enforcement straightforward, but a growing number
of courses increases store-management overhead. Local SQLite and JSON are ideal for
a single-user desktop workflow but are not suitable for horizontally scaled or
multi-tenant deployments. A production evolution would introduce authentication,
PostgreSQL/pgvector, object storage, durable job queues, tracing, and tenant-aware
authorization.
