# CoursePilot AI

CoursePilot AI is a portfolio-ready course knowledge base. It discovers course
folders automatically, incrementally syncs PDF/PPTX files, and answers questions
with citations through the OpenAI Responses API and File Search.

## Current milestone

- FastAPI backend with health, course inventory, sync preview, sync, and chat APIs
- Next.js + TypeScript frontend
- Course and lecture/tutorial filters
- SHA-256 incremental indexing (unchanged files are skipped)
- File citations and retrieved evidence in every answer
- Local JSON sync state with atomic writes
- Unit tests for folder discovery and state persistence
- Local RAG evaluation suite with retrieval, citation, refusal, latency, and cost metrics
- Privacy-conscious JSONL telemetry (question hashes instead of question text)
- Persistent local conversation history and thumbs-up/down answer feedback
- Browser-based document inventory, safe upload, explicit deletion, and re-index controls
- Upload/path validation, per-client rate limiting, bounded API retries, and prompt-injection tests
- Docker development setup
- GitHub Actions CI for backend lint/tests and frontend type-check/build

The course files and API key are deliberately excluded from Git.

## Folder convention

The scanner expects this shape, but discovers course names dynamically:

```text
知识库/
├── 90016/
│   ├── lecture/
│   └── tutorial/
└── 90049/
    ├── lecture/
    └── tutorial/
```

Files containing `tute` or `tutorial` are classified as tutorial material even
when they are not inside a tutorial folder.

## Run locally

### 1. Configure secrets

```bash
cp .env.example .env
```

Put your key in `.env`. Never paste it into source code or commit it.

### 2. Backend

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn app.main:app --app-dir backend --reload --port 8000
```

### 3. Frontend

Requires Node.js 20+.

```bash
cd frontend
cp .env.local.example .env.local
npm install
npm run dev
```

Open <http://localhost:3000>.

## First sync

Previewing is free and does not upload anything:

```bash
python -m app.cli sync --dry-run
```

Run this from `backend/` to upload the discovered documents:

```bash
python -m app.cli sync
```

Running it again only uploads new or changed files. This operation sends course
documents to OpenAI vector stores, so keep the project private and only upload
material you are allowed to process.

## Tests

```bash
python -m unittest discover -s backend/tests -v
```

## Evaluation

The labelled dataset lives in `evals/questions.jsonl`. Run a low-cost smoke test
from `backend/` with:

```bash
python -m app.cli eval --limit 3
```

Run the full suite by omitting `--limit`. Each run writes machine-readable JSON
and a standalone visual report to `evals/reports/latest.json` and
`evals/reports/latest.html`. The suite measures expected-file retrieval hit rate,
citation precision, bilingual concept recall, expected refusals, latency, token usage, and
estimated cost. Token prices are configurable environment values because API
pricing can change.

If a run is interrupted by an API rate limit, retry only blocked cases later:

```bash
python -m app.cli eval --resume
```

## API

- `GET /health` — readiness check
- `GET /api/courses` — local course inventory and index status
- `GET /api/documents` — list local and indexed documents
- `POST /api/documents/upload` — safely add a supported document (indexing is separate)
- `DELETE /api/documents` — explicitly delete one local and indexed document
- `POST /api/sync/preview` — show pending changes without uploading
- `POST /api/sync` — synchronize new and changed documents
- `POST /api/chat` — grounded question answering
- `GET /api/conversations` — list local conversation history
- `GET /api/conversations/{id}/messages` — load a conversation
- `POST /api/feedback` — save human feedback for an assistant answer

Interactive API documentation is available at <http://localhost:8000/docs>.

## Roadmap toward the internship-ready version

1. Add a self-managed PostgreSQL/pgvector retrieval pipeline and compare it with
   the hosted File Search baseline.
2. Add page-level PDF extraction, hybrid retrieval, reranking, and page citations.
3. Add model-graded faithfulness after calibrating the grader against human labels.
4. Add authentication, CI, and cloud deployment.
