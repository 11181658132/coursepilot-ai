# Tutorial 01 — A Practical RAG Evaluation Playbook

Course: DEMO101 — Building Grounded AI Applications

Begin with a small labelled dataset containing answerable questions, unanswerable
questions, paraphrases, and adversarial prompts. Each case should record expected
source files and a small set of concepts that a correct answer should mention.

Evaluate retrieval and generation independently. Retrieval hit rate asks whether an
expected document appeared in the retrieved evidence. Citation precision measures the
share of cited files that are relevant. Keyword recall is a lightweight signal for
whether an answer covered expected concepts, but human review remains important.

Operational evaluation should track latency, token usage, tool calls, and estimated
cost. Run a small smoke test in continuous integration with mocked model calls, and run
the paid end-to-end suite intentionally when documents, prompts, models, or retrieval
settings change.

An unanswerable question passes when the assistant clearly states that the supplied
material is insufficient. Guessing a plausible answer is a failure, even if the guess
happens to be true outside the knowledge base.
