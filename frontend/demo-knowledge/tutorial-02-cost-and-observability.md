# Tutorial 02 — Cost Control and Observability

Course: DEMO101 — Building Grounded AI Applications

The cost of a hosted RAG request can include model input tokens, model output tokens,
retrieval tool calls, and vector-store storage. Limit retrieved passages and output
length to control both cost and latency, then verify that the limits do not reduce
answer quality below the product requirement.

For a public demo, combine provider-level hard spending limits with application-level
controls. Useful controls include per-IP daily limits, a global monthly request budget,
maximum question length, timeouts, and a dedicated API project. A provider hard limit
is the final safety boundary because in-memory application limits may reset when a
serverless function starts a new instance.

Observability should avoid collecting raw private questions by default. Safer telemetry
includes a one-way question hash, character count, latency, token counts, tool-call
counts, response status, and estimated cost. Request identifiers can be retained for
support without logging the user's full prompt.
