"""Versioned fictional passage challenge; never reads a private course directory."""

import hashlib
import json
import math
from types import SimpleNamespace

from app.config import PROJECT_ROOT
from app.services.hybrid_kb import HybridKnowledgeBase

DATASET = PROJECT_ROOT / "evals/challenge_v1.json"
VARIANTS = ("bm25", "vector", "hybrid", "hybrid-rerank")


def load_dataset():
    data = json.loads(DATASET.read_text())
    if data["version"] != 1 or data["scope"] != "fictional-challenge-development-not-human-gold":
        raise ValueError("Unknown challenge dataset")
    documents, passages = {}, {}
    for document in data["documents"]:
        identifier = document["id"]
        if (identifier in documents or not identifier.replace("-", "").isalnum()
                or document["course"] not in {"LAB202", "OTHER303"}
                or document["material_type"] not in {"lecture", "tutorial"}):
            raise ValueError("Unsafe/duplicate fictional document")
        documents[identifier] = document
        for passage in document["passages"]:
            if passage["id"] in passages or not passage["text"].strip():
                raise ValueError("Empty/duplicate passage")
            passages[passage["id"]] = {**passage, "document": document}
    families, seen = {}, set()
    for case in data["cases"]:
        if (case["id"] in seen or case["split"] not in {"dev", "holdout"}
                or case["course"] != "LAB202" or not case["question"].strip()
                or len(set(case["relevant"])) != len(case["relevant"])):
            raise ValueError("Invalid/duplicate challenge question")
        seen.add(case["id"])
        if families.setdefault(case["family"], case["split"]) != case["split"]:
            raise ValueError("Question family crosses frozen split")
        for key in case["relevant"] + case["hard_negatives"]:
            if key not in passages:
                raise ValueError("Unknown passage label")
        if any(passages[key]["document"]["course"] != case["course"]
               for key in case["relevant"]):
            raise ValueError("Relevant source outside course scope")
        if set(case["relevant"]) & set(case["hard_negatives"]):
            raise ValueError("Positive and negative labels overlap")
    if (len(documents), len(passages), sum(c["split"] == "dev" for c in data["cases"]),
            sum(c["split"] == "holdout" for c in data["cases"])) != (20, 40, 24, 8):
        raise ValueError("Frozen challenge coverage changed; create a new version")
    return data, passages


def source_text(document):
    return (f"# Fictional {document['id']}\nCourse: {document['course']}\n"
            f"Status: {document['status']}; revision: {document['revision']}\n\n"
            + "\n\n".join(p["text"] for p in document["passages"]) + "\n")


def passage_text(passage):
    # Exact parity: dense embeddings and BM25 must see the SAME text bytes.
    # Adding metadata to one side confounds a retrieval-mode comparison.
    return passage["text"]


def chunk_id(identifier):
    return hashlib.sha256(f"challenge-v1:{identifier}".encode()).hexdigest()[:32]


def materialize(config, data, passages, client, output):
    """Generate only verified fictional sources and persist vectors in the ignored run."""
    base = HybridKnowledgeBase(config, client)
    path = output / "document-vectors.json"
    texts = {key: passage_text(passage) for key, passage in passages.items()}
    digest = hashlib.sha256(json.dumps(texts, sort_keys=True).encode()).hexdigest()
    if path.exists():
        cache = json.loads(path.read_text())
        if cache["text_sha256"] != digest or cache["model"] != config.rag_embedding_model:
            raise ValueError("Embedding cache does not match this fictional corpus")
        vectors = cache["vectors"]
    else:
        values = base._embed(list(texts.values()))
        vectors = dict(zip(texts, values))
        from app.services.state import save_state
        save_state(path, {"text_sha256": digest, "model": config.rag_embedding_model,
                          "vectors": vectors})
    if set(vectors) != set(passages):
        raise ValueError("Incomplete fictional embedding cache")
    for doc in data["documents"]:
        relative = f"{doc['course']}/{doc['material_type']}/{doc['id']}.md"
        target = config.knowledge_base_dir / relative
        content = source_text(doc)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_text() != content:
            raise ValueError("Existing fictional source changed; never silently overwrite")
        if not target.exists():
            target.write_text(content)
        sha = hashlib.sha256(content.encode()).hexdigest()
        rows = [{"chunk_id": chunk_id(p["id"]), "path": relative,
                 "filename": target.name, "course": doc["course"],
                 "material_type": doc["material_type"], "page": 1,
                 "locator_type": "section", "week": None, "ordinal": i,
                 "text": p["text"]} for i, p in enumerate(doc["passages"])]
        base.store.replace(SimpleNamespace(relative_path=relative, sha256=sha,
                                           course=doc["course"],
                                           material_type=doc["material_type"]),
                           base.signature, rows, [vectors[p["id"]] for p in doc["passages"]])
    return base


def passage_metrics(case, retrieved, passages, k=3):
    expected = set(case["relevant"])
    selected = list(dict.fromkeys(retrieved))[:k]
    if any(key not in passages for key in selected):
        raise ValueError("Cannot score invented passage IDs")
    relevant = [key in expected for key in selected]
    first = next((i for i, hit in enumerate(relevant, 1) if hit), None)
    ideal = sum(1 / math.log2(i + 2) for i in range(min(k, len(expected))))
    dcg = sum(1 / math.log2(i + 2) for i, hit in enumerate(relevant) if hit)
    return {"recall_at_3": len(set(selected) & expected) / len(expected) if expected else None,
            "precision_at_3": sum(relevant) / k if expected else None,
            "mrr_at_3": 1 / first if first else 0 if expected else None,
            "ndcg_at_3": dcg / ideal if ideal else None,
            "returned": len(selected),
            "cross_course_count": sum(passages[key]["document"]["course"] != case["course"]
                                      for key in selected),
            "archived_negative_hits": sum(key in case["hard_negatives"]
                                          and passages[key]["document"]["status"] == "archived"
                                          for key in selected)}
