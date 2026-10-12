# Lecture 01 — Retrieval-Augmented Generation Foundations

Course: DEMO101 — Building Grounded AI Applications

Retrieval-augmented generation (RAG) combines information retrieval with language
model generation. Before answering, the application searches an approved knowledge
base for passages related to the user's question. Those passages are supplied as
context to the model, which should base its answer on the retrieved evidence.

RAG is useful when knowledge changes frequently, belongs to a private collection,
or must be traceable to a source. Unlike fine-tuning, retrieval can update knowledge
without retraining model weights. Fine-tuning is better suited to changing behavior,
style, or repeated task patterns; retrieval is better suited to supplying facts.

A basic RAG pipeline contains ingestion, chunking, embedding, indexing, retrieval,
generation, and citation. Retrieval quality limits answer quality: a fluent model
cannot recover evidence that the retriever failed to return.

Grounded systems should distinguish retrieved facts from general explanation. If
the knowledge base lacks sufficient evidence, the assistant should abstain rather
than invent an answer.
