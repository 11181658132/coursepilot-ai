# Lecture 02 — Retrieval Quality and Citations

Course: DEMO101 — Building Grounded AI Applications

Semantic retrieval matches meaning, while keyword retrieval matches lexical overlap.
Hybrid retrieval combines both signals and is often more robust for course material,
where technical names, equations, and abbreviations matter alongside conceptual
similarity.

Chunk size creates a trade-off. Very small chunks may lose context; very large chunks
increase token cost and can bury the relevant sentence. A practical system evaluates
several chunking strategies against labelled questions instead of selecting a size by
intuition alone.

Citations serve two purposes. First, they let users verify that an answer is supported
by the approved files. Second, they help developers diagnose retrieval failures and
hallucinations. A citation should identify the actual retrieved file and must never be
fabricated by the model.

Useful retrieval metrics include hit rate, recall at k, reciprocal rank, and the
fraction of cited files that are relevant. Answer metrics should be evaluated
separately because retrieval can succeed while generation still misinterprets the
evidence.
