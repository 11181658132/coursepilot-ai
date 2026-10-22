# Study plan and answer deletion

Both local and public Agent UIs provide an explicit confirmation checkbox and a
delete action for the selected plan. `DELETE /api/study-plans/{id}` requires
`{"confirmed":true,"version":<current version>}`. The public gateway preserves
visitor-cookie scoping, signed worker requests and same-origin mutation checks.
Deletion does not instantiate an index or invoke a model.

A short transaction replaces the parent plan and all linked feedback payloads
with deletion tombstones. Goals, tasks, answers, assessment, evidence quotes and
traces are removed from the application's current records. GET returns 404;
history excludes tombstones. A stale version conflicts. Active model leases block
deletion; an expired interrupted operation can be deleted, fencing late workers.
Repeating deletion is idempotent.

Request identifiers, opaque visitor ownership, creation/deletion timestamps and
cost/quota records remain for accounting and replay prevention. Deletion does not
refund usage or permit the original request to be regenerated. This is application
record deletion, **not secure physical erasure**: database backups/WAL may contain
historical versions, and it cannot retract input already sent to the provider.
Anonymous visitors can delete only plans accessible with their current cookie;
this is not an authenticated account deletion service.

## Bounded live acceptance · 2026-10-22

The opt-in runner uses only the five hash-verified fictional DEMO101 documents,
the existing `gpt-6-luna` model and a persisted USD $0.05 ledger. Failed/unknown
requests retain their reservation; SDK retries are disabled. Run only with fresh
user approval, because `--live` incurs API usage:

```sh
PYTHONPATH=backend .venv/bin/python -m app.live_acceptance --live \
  --budget-usd 0.05 --output-dir evals/reports/<fresh-acceptance-directory>
```

The 2026-10-22 local-service smoke passed 13/13 checks in 33.149 seconds:
evidence-backed RAG, unsupported-question refusal, three-task draft, plan quote
matching, no automatic edits, feedback draft, weakness proposal, feedback quote
matching, explicitly confirmed edits, preserved progress, persistent reload,
unreadable deleted content and retained cost records. Fourteen API calls had
estimated total cost **$0.00243270**, with zero uncertain reservations. Estimates
use configured token rates and are not a provider invoice.

This exercises canonical services and local SQLite, not production HTTP/browser
end-to-end behavior or a multi-case model quality benchmark. Quote matching does
not establish claim support; semantic accuracy is unmeasured and human review
coverage is zero. Raw model responses, disposable DB and ledger remain ignored
under `evals/reports/`; no real course content is uploaded or committed.
