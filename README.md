# CoursePilot AI

[![CoursePilot CI](https://github.com/11181658132/coursepilot-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/11181658132/coursepilot-ai/actions/workflows/ci.yml)

**Live demo:** [coursepilot-ai-ebon.vercel.app](https://coursepilot-ai-ebon.vercel.app)

CoursePilot AI is a grounded course knowledge base that turns lecture and tutorial
files into answers with inspectable citations. It combines a FastAPI orchestration
layer, a Next.js interface, OpenAI Responses + File Search, incremental indexing,
evaluation, and privacy-conscious local persistence. The local application now also
includes a self-managed hybrid RAG engine with page/slide citations, alongside the
hosted File Search baseline. The public Vercel demo now defaults to the same Python
hybrid retrieval and citation core, with File Search available as a baseline.

> The public portfolio demo is fully interactive: it uses GPT-6 Luna and hybrid RAG
> against five fictional DEMO101 documents. The API key stays server-side, requests
> are rate-limited, and no university course files are deployed. The local application
> uses the same interface with private course material.

![CoursePilot AI chat with a grounded answer and source citation](docs/assets/coursepilot-chat.png)

## Why this project is more than a chatbot

| Area | Implementation |
| --- | --- |
| Grounding | Separate vector stores by course and material type, retrieved evidence, file citations, and explicit refusal instructions |
| Ingestion | PDF/PPTX/DOCX/TXT/MD discovery, SHA-256 change detection, incremental upload, replacement, deletion, and index preview |
| Product | Course filters, lecture/tutorial scope, multi-turn history, source inspection, feedback, document management, and responsive UI |
| Safety | File extension/signature/size/path validation, prompt-injection resistance, bounded retries, per-IP demo limits, and server-only secrets |
| Evaluation | 13 legacy course cases plus 50 fictional source-checked draft cases, frozen dev/holdout splits, three-engine comparisons, failure analysis, and hash-bound human review |
| Engineering | Typed API models, backend and public-route tests, frontend type checking/build, shared-core drift checks, Docker development, and GitHub Actions CI |
| Privacy | API keys, raw course files, telemetry, conversations, and remote index state are excluded from Git |

## Public demo safeguards

The deployed demo uses a precomputed fictional hybrid index (and a dedicated fictional
vector store for the optional File Search baseline), and accepts real questions
through a same-origin Next.js Route Handler. It limits each visitor to five questions
per day, caps the application at 300 questions per month, bounds question and answer
length, validates scope, and returns safe errors without logging prompts. Provider-level
project spend limits remain the final cost boundary because serverless in-memory limits
can reset when instances are replaced.

Public requests emit privacy-safe structured telemetry for request IDs, latency,
token use, tool calls, citation count, and estimated cost. Prompts, answers, retrieved
evidence, API keys, and client IPs are never logged.

The five public source documents are versioned in `frontend/demo-knowledge/`, making
the demo reproducible and allowing reviewers to verify that citations are genuine.
Hybrid citations expose the source passage and downloadable fictional original.
The Python worker requires a short-lived HMAC signature from the rate-limited route;
direct unsigned worker calls are rejected. Only the explicitly allowlisted RAG modules
are bundled: no private corpus, databases, environment files, or telemetry are copied.

## Architecture

![CoursePilot AI architecture](docs/assets/architecture.svg)

The backend deliberately owns retrieval and secrets. The browser never receives
the OpenAI API key. Each `course:material_type` combination maps to a separate
vector store, and local hashes prevent unchanged files from being uploaded twice.
See [the architecture notes](docs/architecture.md) for the request and ingestion
flows and their trade-offs.

## Evaluation snapshot

The checked-in dataset contains 13 cases across three courses:

- grounded factual questions with expected source files and bilingual concepts;
- questions that are intentionally unanswerable from the course material;
- a direct prompt-injection attempt requesting the system prompt and API key.

The latest full evaluation completed all **13/13** cases: **11 passed** and **2 failed**
(**84.62% pass rate**), with **100% retrieval hit rate**, **94.44% citation
precision**, **90% keyword recall**, **8.62 s average latency**, and **$0.035976**
estimated API cost. Runs can be resumed without paying for completed cases.
Generated reports remain local because they can contain course-derived answers and
filenames.

```bash
cd backend
python -m app.cli eval --resume
```

The evaluator writes machine-readable JSON and a standalone HTML dashboard to
`evals/reports/`. Its estimated cost combines model-token usage with the number
of hosted File Search calls; both prices remain configurable because rates change.

## Run the real application locally

### 1. Configure secrets

```bash
cp .env.example .env
```

Set `OPENAI_API_KEY` and `KNOWLEDGE_BASE_DIR` in `.env`. Never paste a real key
into source code or commit it.

The scanner discovers course names dynamically and expects this shape:

```text
knowledge-base/
├── 90016/
│   ├── lecture/
│   └── tutorial/
└── 90049/
    ├── lecture/
    └── tutorial/
```

Files containing `tute` or `tutorial` are classified as tutorial material even
when they are outside a tutorial folder.

### 2. Start the backend

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn app.main:app --app-dir backend --reload --port 8000
```

### 3. Start the frontend

Node.js 20+ is required.

```bash
cd frontend
cp .env.local.example .env.local
npm install
npm run dev
```

Open <http://localhost:3000>. Interactive backend documentation is available at
<http://localhost:8000/docs>.

## Index documents

### Self-managed hybrid RAG

Select **自建 RAG · 混合检索与重排序** in the local UI, open **资料管理**, preview
the changes, and build the index. The engine selection applies to indexing,
inventory, and questions. Existing File Search indexes remain available as a baseline.

The self-managed pipeline implements:

1. PDF page text, PPTX slide text and tables, DOCX sections, and UTF-8 text extraction.
2. Page/slide-local chunks (1,600 characters, 200-character overlap by default),
   with course, material type, inferred week, source location, and stable chunk IDs.
3. `text-embedding-3-small` embeddings stored in local SQLite, BM25 keyword scoring,
   exact cosine vector search, and reciprocal rank fusion of the top candidates.
4. An optional LLM reranker scoring each candidate from 0 to 3; only useful or direct
   evidence reaches generation when reranking is enabled.
5. Structured answers with server-validated passage IDs and verbatim supporting quotes.
   Clicking a citation opens the indexed original passage, and PDFs open at the cited page.

Previewing scans local hashes only and makes no API calls. Building the hybrid index
sends extracted text to the configured OpenAI embeddings API; queries send their text
to that API and selected passages to the configured generation model. The local
database is excluded from Git. Do not enable this on a shared/public backend without
authentication and document-level access controls.

```bash
cd backend
python -m app.cli sync --engine hybrid --dry-run
python -m app.cli sync --engine hybrid
```

Select the engine in the UI, or set `RAG_ENGINE=hybrid` for backend/CLI defaults.
The UI explicitly sends the selected engine. `RAG_RERANK=false` disables the reranker
for ablation runs. Embeddings, reranking, and generation all contribute to the cost
estimate; pricing remains configurable. The per-run embedding token budget uses the
UTF-8 byte count as a conservative token upper bound before sending each document.
An unchanged document is skipped; changed documents are replaced atomically, and
missing files are pruned. Failed replacements retain the old index but changed or
deleted source content cannot be retrieved until reindexed.

The following small live smoke test uses only the five fictional public documents
and removes its temporary local index afterwards:

```bash
cd backend
PYTHONPATH=. python scripts/smoke_hybrid.py
```

### Compare with the File Search baseline

Run the same dataset with separately stored reports (each run incurs API charges):

```bash
cd backend
python -m app.cli eval --engine file_search --output-dir ../evals/reports/baseline
python -m app.cli eval --engine hybrid --output-dir ../evals/reports/hybrid
python -m app.cli eval --engine hybrid --no-rerank --output-dir ../evals/reports/hybrid-no-rerank
```

The public benchmark now has 50 source-checked **draft**, not human-reviewed, cases:
30 development and 20 frozen holdout. It compares File Search, hybrid without
reranking, and hybrid with reranking. Scoring v3 separates retrieved evidence from
citations, counts missing citations as zero precision, reports coverage and P50/P95,
and supports hash-bound human correctness/faithfulness labels. No semantic accuracy
claim is made from keywords or quote presence. The historical 13-course-case snapshot
above used legacy metrics; do not compare its scores directly with v3 or the fictional
corpus. See [the evaluation protocol](evals/README.md) for free validation, bounded
paid runs, failure taxonomy, paired comparisons and the manual-review rubric.

### Fictional-corpus development comparison · 2026-10-21

30 paired development cases per engine; 90 completed responses, zero blocked.
Labels are source-checked drafts, **not human-reviewed gold**. Proxy pass is not
answer accuracy. The 20 holdout cases were not run or used for tuning.

| Engine | Proxy pass | Retrieval hit | Citation precision | Keyword recall | P50 / P95 ms | Estimated USD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| File Search | 80.00% | 100% | 88.33% | 97.22% | 4,691 / 6,894 | 0.085911 |
| Hybrid, no reranker | 66.67% | 100% | 70.00% | 70.00% | 1,966 / 3,337 | 0.005255 |
| Hybrid + reranker | 63.33% | 100% | 68.33% | 69.44% | 4,366 / 16,268 | 0.007480 |

Total estimated additional API cost: **$0.09864598**, reconciled with the independent
budget ledger; no uncertain charges. Scores use v3 offline regrading of preserved
responses to fix a whitespace-quote grader bug and hosted report-rate omission,
without new generation calls. Correctness and faithfulness remain unmeasured until
human review (0/90 reviewed). No reranking improvement is claimed: in this small
run it increased latency/cost without improving the proxy pass rate. With four
results over five documents, retrieval hit is inherently easy; these results do not
establish private-course quality, large-corpus retrieval, or production latency.

### Hosted File Search indexing

Previewing is local and free:

```bash
cd backend
python -m app.cli sync --dry-run
```

Run the actual sync only for material you are allowed to process:

```bash
python -m app.cli sync
```

Subsequent runs skip unchanged files. New and changed documents are sent to an
OpenAI vector store and can incur API storage/processing charges.

## Reliable local indexing

The local UI now defaults to hybrid retrieval. **Start indexing** creates a durable
job and returns HTTP 202, instead of holding a request open during parsing/embedding.
Start a separate worker alongside the API:

```bash
# From the repository root; uses the same local .env as the API.
PYTHONPATH=backend .venv/bin/python -m app.worker
# Process at most one eligible task, then exit:
PYTHONPATH=backend .venv/bin/python -m app.worker --once
```

Uploading alone does not start a worker or spend API credits. The UI shows persisted
progress, attempt counts, recorded cost, and sanitized failures; refreshing reloads
the job. If it stays queued, check that the worker uses the same configuration.

- Jobs use `index_jobs.sqlite3` beside `RAG_DB_PATH`: a versioned schema, WAL,
  transactional claims, and one active job per configuration. Repeated submissions
  coalesce while active. An optional `Idempotency-Key` replays the original job even
  after completion. Successful unchanged documents are skipped on retry.
- Worker, legacy sync API, CLI sync, and deletion share a nonblocking OS writer
  lock. Process death releases it; the next worker recovers interrupted work.
  Attempts are capped at three per job. Transient provider failures schedule retries
  no earlier than **Retry-After**; quota/authentication failures stop for user action,
  and parse failures require manual retry. No API request sleeps while waiting.
- Each replacement commits only after parsing, embedding, and a fresh source hash
  check succeed. Failed replacements preserve the previous stored index. Changed
  sources are excluded from retrieval until reindexed, not silently served as current.
  This is **per-document**, not whole-library atomicity.
- Concurrent same-name uploads use atomic create-if-absent instead of overwriting.
  Uploads arriving during a scan may need another job after that scan completes.

This remains a **single-host, single-user** queue on Linux/macOS with a shared local
disk. It is not a distributed queue, tenant authorization, PostgreSQL migration, or
exactly-once external billing. A crash after a paid provider call but before local
commit can repeat that call and lose its local cost record. Displayed costs are
recorded estimates, not a billing ledger. Keep provider spend controls. Back up source
files and databases together; never delete live WAL files. Do not expose the local
administration API to the internet without authentication.

Docker Compose includes a restartable `index-worker` sharing the index volume and
an API healthcheck/restart policy. The API source mount is writable for uploads;
the worker's source mount is read-only. No new cloud service or subscription is needed.
The Vercel public demo remains fictional/read-only and does not expose these admin APIs.

## Public portfolio demo

The frontend includes an explicit read-only mode for public deployment. It uses
five versioned fictional files, disables destructive document operations, and sends
questions to a same-origin server Route Handler. That handler keeps the API key on
the server, scopes both engines to `DEMO101`, validates input, applies request limits,
and returns real citations and retrieved evidence. Hybrid mode uses a Python Vercel
Function with the canonical backend's BM25/vector fusion, LLM reranker, and verified
quotation pipeline. It does not need an additional paid backend server.

`backend/app` is authoritative. After changing its RAG modules, regenerate the
deployment bundle with `node frontend/scripts/prepare-rag.mjs`; CI fails on drift.
`frontend/demo-index.json` contains only embeddings/chunks from the five fictional
sources. To rebuild it, run `PYTHONPATH=backend .venv/bin/python
backend/scripts/export_demo_index.py` from the repository root (uses the configured
embedding API; never reads private course folders). Cold starts rehydrate this
index into temporary SQLite without re-embedding documents.

```bash
cd frontend
NEXT_PUBLIC_DEMO_MODE=true npm run dev
```

Local Next.js-only development cannot run the Python function automatically: also
serve `frontend/api/rag.py` with Python's HTTPServer and configure `DEMO_RAG_URL`
to that trusted local worker URL, using the same `DEMO_RATE_LIMIT_SALT` on both.
Vercel discovers `api/rag.py` automatically alongside Next.js.

For a Vercel deployment, set the project root to `frontend` and add:

```text
NEXT_PUBLIC_DEMO_MODE=true
OPENAI_API_KEY=...
OPENAI_DEMO_VECTOR_STORE_ID=...
DEMO_RATE_LIMIT_SALT=...
```

Keep the last three values server-only secrets. The production deployment uses a
dedicated OpenAI project with an enforced monthly hard spend limit. The service key
must be rotated before its configured expiry date.

## Tests

```bash
PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/tests -v
cd frontend && npm test && npm run typecheck && npm run build
```

GitHub Actions repeats these checks on every push and pull request.

## API surface

- `GET /health` — readiness check
- `GET /api/courses` — course inventory and index status
- `GET /api/documents` — local and indexed document inventory
- `POST /api/documents/upload` — validated local upload
- `DELETE /api/documents` — explicit local and remote deletion
- `POST /api/sync/preview` — pending index changes without upload
- `POST /api/sync` — incremental synchronization
- `POST /api/index-jobs` — durable hybrid-index job submission (202)
- `GET /api/index-jobs` — latest 20 jobs in the current index configuration
- `GET /api/index-jobs/{id}` — persisted progress and results
- `POST /api/index-jobs/{id}/retry` — retry a failed job below its attempt cap
- `POST /api/chat` — grounded question answering
- `GET /api/conversations` — conversation history
- `GET /api/conversations/{id}/messages` — conversation messages
- `POST /api/feedback` — human feedback for an answer

## Design decisions and limitations

- Hosted File Search made the first reliable baseline fast to build. A pgvector
  migration can replace the self-managed engine's exact cosine scan when the corpus
  outgrows local SQLite. BM25, fusion, chunking, and reranking are already inspectable
  application code, not delegated to File Search.
- Image-only scans require OCR and are reported as indexing failures, rather than
  producing empty evidence. DOCX/TXT/MD use section locations, not fabricated pages.
- Quote validation checks that quoted text exists in the cited chunk. It does not
  establish that every generated claim is supported by that quote. This needs an
  expanded, human-reviewed evaluation set.
- Conversation history is intentionally local-first. A multi-user deployment
  needs authentication, PostgreSQL, tenant isolation, migrations, and backups.
- Public inference is isolated from private course data: it uses a dedicated OpenAI
  project and fictional files. Its in-memory application counters are intentionally
  backed by a provider hard spend limit; durable multi-instance rate limiting is the
  next production step.

## Roadmap

1. Benchmark hosted File Search against hybrid retrieval, with/without reranking.
2. Add OCR and visual/table parsing for material that lacks extractable text.
3. Calibrate a model-graded faithfulness metric against human labels.
4. Add authentication, tenant isolation, durable cloud storage, and observability.
