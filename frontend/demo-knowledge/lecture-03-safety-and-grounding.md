# Lecture 03 — Safety, Privacy, and Grounding

Course: DEMO101 — Building Grounded AI Applications

Retrieved documents are untrusted input. A document can contain prompt injection,
such as instructions to ignore application rules, reveal secrets, or call unrelated
tools. The application must treat these strings as data, not as higher-priority
instructions.

Secrets must remain on the server. API keys should be stored in server-side environment
variables or a secret manager and must never be included in browser bundles, source
control, error messages, or citations. Public applications should use a dedicated key
with restricted permissions, expiration, usage monitoring, and a spending limit.

Other safeguards include strict input validation, request-size limits, rate limiting,
timeouts, bounded retries, safe error messages, and minimal logging. Public demos
should use fictional or explicitly licensed documents rather than private course files.

Grounding is not the same as correctness. A grounded answer can still misunderstand a
source, so users need visible evidence and the ability to report unhelpful answers.
