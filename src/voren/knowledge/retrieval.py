"""Deterministic chunks, BM25 and rank fusion for source-bound retrieval.

Scores order candidates; none of these scores are calibrated confidence values.
Offsets count Python Unicode characters in the immutable document content.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass

from voren.knowledge.models import KnowledgeDocument


CHUNK_SIZE = 600
CHUNK_OVERLAP = 100
RRF_K = 60


@dataclass(frozen=True)
class KnowledgeChunk:
    document: KnowledgeDocument
    chunk_id: str
    start: int
    end: int
    content: str
    content_digest: str

    @property
    def embedding_text(self) -> str:
        return f"{self.document.title}\n{self.content}"

    @property
    def sort_key(self) -> tuple[str, str, int]:
        return (self.document.ref.document_id, self.document.ref.version_id, self.start)


def chunk_document(document: KnowledgeDocument) -> tuple[KnowledgeChunk, ...]:
    chunks = []
    start = 0
    while start < len(document.content):
        end = min(len(document.content), start + CHUNK_SIZE)
        content = document.content[start:end]
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        identity = json.dumps(
            {
                "version_id": document.ref.version_id,
                "start": start,
                "end": end,
                "content_digest": digest,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        chunks.append(
            KnowledgeChunk(
                document=document,
                chunk_id=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
                start=start,
                end=end,
                content=content,
                content_digest=digest,
            )
        )
        if end == len(document.content):
            break
        start = end - CHUNK_OVERLAP
    return tuple(chunks)


def terms(text: str) -> tuple[str, ...]:
    folded = text.casefold()
    latin = re.findall(r"[a-z0-9]+", folded)
    cjk = []
    # Bigrams stay inside a contiguous run; punctuation must not create words.
    for run in re.findall(r"[\u3400-\u9fff]+", folded):
        cjk.extend(run)
        cjk.extend(run[index : index + 2] for index in range(len(run) - 1))
    return (*latin, *cjk)


def rank_bm25(
    query: str, chunks: tuple[KnowledgeChunk, ...]
) -> tuple[tuple[KnowledgeChunk, float], ...]:
    query_terms = tuple(dict.fromkeys(terms(query)))
    if not chunks or not query_terms:
        return ()
    counts = tuple(Counter(terms(chunk.embedding_text)) for chunk in chunks)
    lengths = tuple(sum(count.values()) for count in counts)
    average_length = sum(lengths) / len(lengths)
    if not average_length:
        return ()
    frequencies = {
        term: sum(1 for count in counts if count[term]) for term in query_terms
    }
    scored = []
    for chunk, count, length in zip(chunks, counts, lengths, strict=True):
        score = 0.0
        for term in query_terms:
            frequency = count[term]
            if not frequency:
                continue
            document_frequency = frequencies[term]
            inverse_frequency = math.log1p(
                (len(chunks) - document_frequency + 0.5) / (document_frequency + 0.5)
            )
            denominator = frequency + 1.2 * (0.25 + 0.75 * length / average_length)
            score += inverse_frequency * frequency * 2.2 / denominator
        if score > 0:
            scored.append((chunk, score))
    return tuple(sorted(scored, key=lambda item: (-item[1], item[0].sort_key)))


def validate_vectors(
    vectors: object, *, expected_count: int, expected_dimension: int | None = None
) -> tuple[tuple[float, ...], ...]:
    if not isinstance(vectors, (tuple, list)) or len(vectors) != expected_count:
        raise ValueError("embedding result count does not match input count")
    normalized = []
    dimension = expected_dimension
    for vector in vectors:
        if not isinstance(vector, (tuple, list)) or not vector:
            raise ValueError("embedding vectors must be non-empty sequences")
        if dimension is None:
            dimension = len(vector)
        if len(vector) != dimension or dimension > 65_536:
            raise ValueError("embedding vector dimensions do not match")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in vector
        ):
            raise ValueError("embedding vectors must contain finite numbers")
        norm = math.hypot(*vector)
        if not math.isfinite(norm) or not norm:
            raise ValueError("embedding vectors must have finite non-zero norms")
        normalized.append(tuple(float(value) / norm for value in vector))
    return tuple(normalized)


def rank_dense(
    chunks: tuple[KnowledgeChunk, ...],
    vectors: tuple[tuple[float, ...], ...],
    query_vector: tuple[float, ...],
) -> tuple[tuple[KnowledgeChunk, float], ...]:
    scored = []
    for chunk, vector in zip(chunks, vectors, strict=True):
        similarity = math.fsum(
            left * right for left, right in zip(vector, query_vector, strict=True)
        )
        if similarity > 0:
            scored.append((chunk, min(similarity, 1.0)))
    return tuple(sorted(scored, key=lambda item: (-item[1], item[0].sort_key)))


def fuse_rankings(
    *rankings: tuple[tuple[KnowledgeChunk, float], ...],
) -> tuple[tuple[KnowledgeChunk, float], ...]:
    scores: dict[str, float] = {}
    candidates: dict[str, KnowledgeChunk] = {}
    for ranking in rankings:
        for rank, (chunk, _) in enumerate(ranking, start=1):
            candidates[chunk.chunk_id] = chunk
            scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1 / (RRF_K + rank)
    return tuple(
        sorted(
            ((candidates[identity], score) for identity, score in scores.items()),
            key=lambda item: (-item[1], item[0].sort_key),
        )
    )
