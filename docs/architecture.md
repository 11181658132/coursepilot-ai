# Architecture

```text
Browser (Next.js)
       |
       | JSON/HTTP
       v
FastAPI API  ----> local sync state (file hashes and remote IDs)
       |
       | Responses API + File Search
       v
OpenAI vector stores
       ^
       |
Incremental scanner <---- local course folders (read only)
```

Each `course:material_type` combination gets a separate vector store. This keeps
filtering explicit and makes it easy to compare lecture-only, tutorial-only, and
all-material queries. The state file records content hashes so unchanged files
are not uploaded twice.

