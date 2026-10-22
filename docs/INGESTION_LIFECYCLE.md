# Reliable local upload → indexing → versioned evidence

## Workflow

1. Upload validates the streamed size/signature, fsyncs a temporary file and creates
   the target atomically without overwriting. Same path + identical bytes can be
   retried; different bytes at that path return 409. This is not cross-name dedupe.
2. By default the API only saves the file. `index_after_upload=true&engine=hybrid`
   is explicit embedding consent and returns a durable `index_job` with the upload
   result. The checkbox discloses that **all pending library documents**, not just
   the upload, may be processed. The request itself makes zero provider calls.
3. The separate `python -m app.worker` drains the queue. Progress phases distinguish
   waiting, scanning, parsing, chunking, embedding and committing; counts are files,
   not chunk counts or a completion-time prediction. The UI shows the running job
   even if a newer follow-up is waiting.
4. A whole document is published in one SQLite transaction only after parsing,
   every vector and a fresh source-hash check succeed. Failed replacement leaves
   the previous stored index intact. Unchanged committed documents skip embedding.
5. Citations/evidence carry `document_version` (SHA-256 of source bytes). Opening
   local conversation history revalidates source availability and version and adds
   current/stale/unknown status to the response, without rewriting the saved answer
   or invoking a provider. The UI warns about changed/unavailable sources. Legacy
   or hosted results without a local hash are not silently assigned a version.

## Uploads during a running scan

Discovery snapshots the file list. Returning the already-running job for a new
upload could therefore miss that file forever. Upload enqueues with `after_running`
and a scope/path/content key: simultaneous uploads coalesce into a durable queued
follow-up, rather than being falsely acknowledged by the earlier scan.

Schema v2 replaces the one-active-job constraint with one-running-job uniqueness.
Queue transactions serialize enqueue/claim; the OS writer lock still serializes
actual indexing. A crashed running job is reclaimed before queued follow-ups; its
attempt limit remains three. No distributed or multi-host guarantee is implied.

A temporary provider failure persists Retry-After across every waiting job in
the scope, so a follow-up cannot bypass it. Billing/authentication/configuration
failure stops queued follow-ups for explicit user retry. Failed upload-associated
jobs do not acquire a fresh attempt budget merely by uploading again. After a
succeeded upload job, a new explicit opt-in may create a new scan; it skips unchanged
index entries and can rebuild an index deleted since that success. Generic
`POST /api/index-jobs` idempotency-key replay semantics remain unchanged.

## Failure boundaries and non-goals

- File publication and queue commit are separate resources, not a distributed
  transaction. If enqueue fails after saving, return a sanitized 503 explicitly
  saying the file was saved. Retrying identical upload or manually enqueueing
  repairs the gap. Process death in this gap may leave a pending file visible in
  inventory; there is no outbox/watchdog claiming automatic reconciliation.
- Atomicity is **per document**, not a whole-library version swap. Do not claim
  zero-downtime old-source serving: changed on-disk files are excluded from
  retrieval until reindexed. This prevents silently citing an obsolete source.
  No immutable archive of old source files or old answer regeneration is added.
- Successful unchanged documents do not re-embed; a process crash after a paid
  request but before its commit may repeat work. Exactly-once external billing is
  not promised. Costs shown by the queue are recorded estimates, not an invoice.
- This remains a trusted local, single-user, shared-volume system. Public demo
  upload is still disabled; no real course files are bundled or uploaded by tests.
- Opening history checks freshness at that boundary, not continuously while a
  browser tab sits open. Hash availability checks are not semantic fact checking.

Restart API and worker together after upgrading. V2 migration preserves job IDs,
request keys, costs and attempts. Old binaries intentionally refuse a newer queue
schema. Back up source and database volumes before changing deployment versions.

Official API boundary: embedding responses contain indexed vector rows and token
usage; the existing client checks complete row coverage and finite vectors before
publication. See [OpenAI Docs: embeddings](https://developers.openai.com/api/reference/resources/embeddings/methods/create).
This upgrade is tested with synthetic data and fake providers; new real API spend is $0.
