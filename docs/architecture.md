# Architecture

![CoursePilot AI architecture](assets/architecture.svg)

## Query flow

1. The Next.js client sends a question with an explicit course and material scope.
2. The local application uses FastAPI; the public demo uses a same-origin Next.js
   Route Handler. Both validate the request and apply a per-client rate limit.
3. Hybrid mode filters SQLite chunks by scope, runs BM25/vector fusion and optional
   LLM reranking. The hosted baseline selects only matching vector stores.
4. The Responses API generates an answer from retrieved passages (hybrid) or calls
   File Search (baseline), returning citations, evidence, token usage, and latency.
5. FastAPI stores local exchanges and optional feedback in SQLite. The public demo
   keeps conversation state in the browser and emits only structured operational
   telemetry: request IDs, status, latency, token counts, tool calls, citation count,
   and estimated cost. It never logs questions, answers, evidence, IPs, or secrets.
6. The client renders the answer, citations, evidence, latency, and estimated model
   token cost so the result remains inspectable.

## Ingestion flow

### Self-managed hybrid engine

`HybridKnowledgeBase` is selected by the local client's `engine=hybrid` request or
the CLI's `--engine hybrid` option. Both clients default to hybrid and support
switching engines; backend/CLI configuration can still select the hosted baseline.

- `parsing.py` extracts PDF pages and PPTX slides, including slide tables; every chunk
  stays within one source location. DOCX and text use sections because they do not
  provide stable rendered page numbers. Week metadata is inferred only from explicit
  `week`/`wk` filename or path markers.
- `chunk_store.py` stores the document hash, extraction/embedding configuration
  signature, source metadata, original chunk text, and embeddings in SQLite. It
  atomically replaces a document only after all new embeddings are available. Changed
  and deleted files are excluded from search and source inspection by hash checks.
- `retrieval.py` computes BM25 and exact cosine rankings **after** course/type
  filtering. RRF merges both rankings. `hybrid_kb.py` optionally sends at most the
  configured candidate count to an LLM relevance scorer, validating returned IDs.
- Generation receives only the final passages and bounded same-course conversation
  history. The server checks that inline source IDs match returned citations and
  every quote occurs in the selected original chunk. Invalid citations fail closed.
- `GET /api/sources/{chunk_id}` returns current chunk text; the `/file` suffix serves
  the validated source file. Files are resolved inside the configured knowledge root.
  Only PDFs render inline, with the page fragment supplied by the client.
- Query costs include embeddings, reranking, and generation. Indexing reports include
  consumed embedding tokens and cost. Operational logs contain no raw questions,
  answers, filenames, or passages. The SQLite index is local and ignored by Git.

This intentionally targets a small, single-user course corpus. It does not implement
OCR, ANN indexing, or multi-tenant source authorization. The public Vercel demo
reuses the same Python core via a signed Python Function behind its Next.js limits.
A CI-verified allowlisted bundle mirrors the canonical `backend/app` modules.
Only five versioned fictional files and their precomputed embeddings are deployed;
cold starts hydrate temporary SQLite without document embedding calls. Public source
inspection/download routes resolve IDs exclusively inside that fictional index.
Exposing private source endpoints still requires a separate authenticated deployment.

### Hosted baseline

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
- Public portfolio deployments use a clearly labelled, fictional, read-only library
  and a server-side model route isolated in a dedicated OpenAI project.

## Current trade-offs

Separate vector stores make scope enforcement straightforward, but a growing number
of courses increases store-management overhead. Local SQLite and JSON are ideal for
a single-user desktop workflow but are not suitable for horizontally scaled or
multi-tenant deployments. A production evolution would introduce authentication,
PostgreSQL/pgvector, object storage, durable job queues, tracing, and tenant-aware
authorization. The public demo currently uses serverless in-memory counters, so the
OpenAI project hard spend limit is its final cost boundary until a durable Redis/KV
rate limiter is provisioned.
