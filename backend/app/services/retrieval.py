"""Inspectable BM25, exact cosine search, and reciprocal rank fusion.

Exact search is intentional for the small course corpus. Swap the dense search
for a vector database when profiling shows corpus size requires ANN indexing.
"""

import math
import re
from collections import Counter
from typing import List


def tokenize(text: str) -> List[str]:
    latin = re.findall(r"[a-z0-9_]+", text.lower())
    chinese = re.findall(r"[\u4e00-\u9fff]+", text)
    return latin + [word[i:i + 2] for word in chinese for i in range(max(1, len(word) - 1))]


def bm25(query: str, texts: List[str]) -> List[float]:
    tokens = [tokenize(text) for text in texts]
    if not tokens:
        return []
    average_length = sum(map(len, tokens)) / len(tokens) or 1
    frequencies = Counter(token for words in tokens for token in set(words))
    query_tokens = set(tokenize(query))
    scores = []
    for words in tokens:
        counts = Counter(words)
        score = 0.0
        for term in query_tokens:
            frequency = counts[term]
            if not frequency:
                continue
            idf = math.log(1 + (len(tokens) - frequencies[term] + 0.5)
                           / (frequencies[term] + 0.5))
            score += idf * frequency * 2.5 / (
                frequency + 1.5 * (0.25 + 0.75 * len(words) / average_length)
            )
        scores.append(score)
    return scores


def cosine(left: List[float], right: List[float]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("Embedding dimensions do not match; rebuild the index")
    denominator = math.sqrt(sum(x * x for x in left) * sum(x * x for x in right))
    return sum(a * b for a, b in zip(left, right)) / denominator if denominator else 0.0


def rank_candidates(query: str, rows: List[dict], vector: List[float], limit: int) -> List[dict]:
    lexical = bm25(query, [row["text"] for row in rows])
    dense = [cosine(vector, row["embedding"]) for row in rows]
    rankings = [
        sorted((i for i, score in enumerate(values) if score > 0),
               key=lambda i: (-values[i], rows[i]["chunk_id"]))[:limit]
        for values in (lexical, dense)
    ]
    fused = Counter()
    for ranking in rankings:
        for rank, i in enumerate(ranking, 1):
            fused[i] += 1 / (60 + rank)
    ordered = sorted(fused, key=lambda i: (-fused[i], rows[i]["chunk_id"]))[:limit]
    return [{**rows[i], "score": fused[i], "bm25_score": lexical[i],
             "vector_score": dense[i]} for i in ordered]
