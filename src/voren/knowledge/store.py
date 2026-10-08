"""Immutable source versions and explicit, version-bound retrieval indexes."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from voren.knowledge.models import (
    KnowledgeDocument,
    KnowledgeSearchHit,
    KnowledgeSourceKind,
    KnowledgeVersionRef,
)
from voren.knowledge.retrieval import (
    KnowledgeChunk,
    chunk_document,
    fuse_rankings,
    rank_bm25,
    rank_dense,
    validate_vectors,
)

if TYPE_CHECKING:
    from voren.knowledge.embeddings import EmbeddingProvider


class KnowledgeStoreError(RuntimeError):
    """Raised when a document or active version cannot be resolved safely."""


class SQLiteKnowledgeStore:
    """Keep source-bound document versions and search only active snapshots."""

    def __init__(self, database: Path) -> None:
        database.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(database))
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS knowledge_versions (
                document_id TEXT NOT NULL,
                version_id TEXT NOT NULL,
                title TEXT NOT NULL,
                source_uri TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                content TEXT NOT NULL,
                content_digest TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (document_id, version_id)
            );
            CREATE TABLE IF NOT EXISTS active_knowledge_versions (
                document_id TEXT PRIMARY KEY,
                version_id TEXT NOT NULL,
                activated_at TEXT NOT NULL,
                activation_reason TEXT NOT NULL,
                FOREIGN KEY (document_id, version_id)
                    REFERENCES knowledge_versions(document_id, version_id)
            );
            CREATE TABLE IF NOT EXISTS knowledge_chunk_embeddings (
                fingerprint TEXT NOT NULL,
                chunk_id TEXT NOT NULL,
                document_id TEXT NOT NULL,
                version_id TEXT NOT NULL,
                chunk_start INTEGER NOT NULL,
                chunk_end INTEGER NOT NULL,
                chunk_digest TEXT NOT NULL,
                dimensions INTEGER NOT NULL,
                vector_json TEXT NOT NULL,
                vector_digest TEXT NOT NULL,
                PRIMARY KEY (fingerprint, chunk_id),
                FOREIGN KEY (document_id, version_id)
                    REFERENCES knowledge_versions(document_id, version_id)
            );
            """
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def install(self, document: KnowledgeDocument) -> KnowledgeVersionRef:
        with self._connection:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO knowledge_versions (
                    document_id, version_id, title, source_uri, source_kind,
                    content, content_digest, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document.ref.document_id,
                    document.ref.version_id,
                    document.title,
                    document.source_uri,
                    document.source_kind.value,
                    document.content,
                    document.content_digest,
                    document.created_at.isoformat(),
                ),
            )
        self.load(document.ref)
        return document.ref

    def activate(
        self,
        ref: KnowledgeVersionRef,
        *,
        reason: str,
        activated_at: datetime | None = None,
    ) -> None:
        if not reason.strip():
            raise ValueError("knowledge activation reason must be non-empty")
        self.load(ref)
        timestamp = activated_at or datetime.now(UTC)
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO active_knowledge_versions (
                    document_id, version_id, activated_at, activation_reason
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(document_id) DO UPDATE SET
                    version_id = excluded.version_id,
                    activated_at = excluded.activated_at,
                    activation_reason = excluded.activation_reason
                """,
                (
                    ref.document_id,
                    ref.version_id,
                    timestamp.isoformat(),
                    reason.strip(),
                ),
            )

    def load(self, ref: KnowledgeVersionRef) -> KnowledgeDocument:
        row = self._connection.execute(
            """
            SELECT * FROM knowledge_versions
            WHERE document_id = ? AND version_id = ?
            """,
            (ref.document_id, ref.version_id),
        ).fetchone()
        if row is None:
            raise KnowledgeStoreError(
                f"unknown knowledge version {ref.document_id!r}@{ref.version_id}"
            )
        return self._document_from_row(row)

    def get_active(self, document_id: str) -> KnowledgeDocument:
        row = self._connection.execute(
            """
            SELECT v.*
            FROM active_knowledge_versions AS a
            JOIN knowledge_versions AS v
              ON v.document_id = a.document_id
             AND v.version_id = a.version_id
            WHERE a.document_id = ?
            """,
            (document_id,),
        ).fetchone()
        if row is None:
            raise KnowledgeStoreError(
                f"knowledge document {document_id!r} has no active version"
            )
        return self._document_from_row(row)

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        mode: str = "bm25",
        embedder: EmbeddingProvider | None = None,
    ) -> tuple[KnowledgeSearchHit, ...]:
        if not query.strip():
            raise ValueError("knowledge query must be non-empty")
        if not 1 <= limit <= 20:
            raise ValueError("knowledge search limit must be between 1 and 20")
        if mode not in ("lexical", "bm25", "dense", "hybrid"):
            raise ValueError("unknown knowledge retrieval mode")
        if mode == "lexical":
            return self._search_lexical(query, limit=limit)
        documents = self._active_documents()
        chunks = tuple(chunk for document in documents for chunk in chunk_document(document))
        if mode == "bm25":
            ranked = rank_bm25(query, chunks)
        else:
            fingerprint = self._embedding_fingerprint(embedder)
            vectors = self._load_chunk_vectors(chunks, fingerprint, require_complete=True)
            if not chunks:
                return ()
            assert embedder is not None
            query_vectors = self._embed(
                embedder,
                (query,),
                expected_dimension=len(vectors[0]),
            )
            if self._embedding_fingerprint(embedder) != fingerprint:
                raise KnowledgeStoreError("embedding provider fingerprint changed during retrieval")
            dense = rank_dense(chunks, vectors, query_vectors[0])
            ranked = dense if mode == "dense" else fuse_rankings(rank_bm25(query, chunks), dense)
        # Preserve the established limit as a document count. Select each source's
        # strongest chunk instead of letting a long document fill all result slots.
        selected = []
        seen = set()
        for chunk, score in ranked:
            ref = chunk.document.ref
            if ref.document_id in seen:
                continue
            seen.add(ref.document_id)
            selected.append(
                KnowledgeSearchHit(
                    ref=ref,
                    title=chunk.document.title,
                    source_uri=chunk.document.source_uri,
                    source_kind=chunk.document.source_kind,
                    content_digest=chunk.document.content_digest,
                    snippet=chunk.content,
                    # This integer remains for existing clients. ranking_score
                    # carries the algorithm's uncalibrated floating-point score.
                    score=max(1, round(score * 1_000_000)),
                    ranking_score=score,
                    retrieval_method=mode,
                    chunk_id=chunk.chunk_id,
                    chunk_start=chunk.start,
                    chunk_end=chunk.end,
                    chunk_digest=chunk.content_digest,
                )
            )
            if len(selected) == limit:
                break
        return tuple(selected)

    def _active_documents(self) -> tuple[KnowledgeDocument, ...]:
        rows = self._connection.execute(
            """
            SELECT v.*
            FROM active_knowledge_versions AS a
            JOIN knowledge_versions AS v
              ON v.document_id = a.document_id
             AND v.version_id = a.version_id
            ORDER BY v.document_id
            """
        ).fetchall()
        return tuple(self._document_from_row(row) for row in rows)

    @staticmethod
    def _embedding_fingerprint(embedder: EmbeddingProvider | None) -> str:
        if embedder is None:
            raise KnowledgeStoreError("dense retrieval requires an explicit embedding provider")
        fingerprint = embedder.fingerprint
        if (
            not isinstance(fingerprint, str)
            or not fingerprint.strip()
            or fingerprint != fingerprint.strip()
            or len(fingerprint) > 2_048
        ):
            raise KnowledgeStoreError("embedding provider fingerprint must be non-empty and bounded")
        return fingerprint

    @staticmethod
    def _embed(
        embedder: EmbeddingProvider,
        texts: tuple[str, ...],
        *,
        expected_dimension: int | None = None,
    ) -> tuple[tuple[float, ...], ...]:
        try:
            values = embedder.embed(texts)
        except Exception as error:
            raise KnowledgeStoreError(f"embedding provider failed ({type(error).__name__})") from None
        try:
            return validate_vectors(
                values,
                expected_count=len(texts),
                expected_dimension=expected_dimension,
            )
        except (ValueError, TypeError, OverflowError) as error:
            raise KnowledgeStoreError(f"invalid embedding provider vectors: {error}") from None

    def _load_chunk_vectors(
        self,
        chunks: tuple[KnowledgeChunk, ...],
        fingerprint: str,
        *,
        require_complete: bool,
    ) -> tuple[tuple[float, ...], ...]:
        vectors = []
        dimension = None
        for chunk in chunks:
            row = self._connection.execute(
                "SELECT * FROM knowledge_chunk_embeddings WHERE fingerprint = ? AND chunk_id = ?",
                (fingerprint, chunk.chunk_id),
            ).fetchone()
            if row is None:
                if require_complete:
                    raise KnowledgeStoreError("embedding index is incomplete for active source chunks; index explicitly")
                continue
            if (
                row["document_id"] != chunk.document.ref.document_id
                or row["version_id"] != chunk.document.ref.version_id
                or row["chunk_start"] != chunk.start
                or row["chunk_end"] != chunk.end
                or row["chunk_digest"] != chunk.content_digest
                or hashlib.sha256(row["vector_json"].encode("utf-8")).hexdigest() != row["vector_digest"]
            ):
                raise KnowledgeStoreError("embedding index source or vector digest mismatch")
            try:
                values = json.loads(row["vector_json"])
                if isinstance(row["dimensions"], bool) or row["dimensions"] != len(values):
                    raise ValueError("stored embedding dimensions do not match vector")
                validated = validate_vectors(
                    (values,), expected_count=1, expected_dimension=dimension
                )[0]
            except (ValueError, TypeError, OverflowError) as error:
                raise KnowledgeStoreError(f"invalid stored embedding vector: {error}") from None
            dimension = len(validated)
            vectors.append(validated)
        return tuple(vectors)

    def index_embeddings(self, embedder: EmbeddingProvider, *, max_new_chunks: int = 2_000) -> int:
        """Embed only missing active chunks, committing all new vectors together.

        Search never builds or repairs an index implicitly. A changed active source
        version or provider fingerprint requires this explicit operation again.
        """
        fingerprint = self._embedding_fingerprint(embedder)
        if not isinstance(max_new_chunks, int) or isinstance(max_new_chunks, bool) or not 1 <= max_new_chunks <= 2_000:
            raise ValueError("embedding indexing chunk budget must be between 1 and 2000")
        chunks = tuple(
            chunk for document in self._active_documents() for chunk in chunk_document(document)
        )
        existing = self._load_chunk_vectors(chunks, fingerprint, require_complete=False)
        missing = tuple(
            chunk for chunk in chunks
            if self._connection.execute(
                "SELECT 1 FROM knowledge_chunk_embeddings WHERE fingerprint = ? AND chunk_id = ?",
                (fingerprint, chunk.chunk_id),
            ).fetchone() is None
        )
        if not missing:
            return 0
        if len(missing) > max_new_chunks:
            raise KnowledgeStoreError("missing active chunks exceed the explicit embedding indexing budget")
        dimension = len(existing[0]) if existing else None
        new_vectors = []
        for start in range(0, len(missing), 32):
            batch = missing[start : start + 32]
            vectors = self._embed(
                embedder,
                tuple(chunk.embedding_text for chunk in batch),
                expected_dimension=dimension,
            )
            dimension = len(vectors[0])
            new_vectors.extend(vectors)
        if self._embedding_fingerprint(embedder) != fingerprint:
            raise KnowledgeStoreError("embedding provider fingerprint changed during indexing")
        with self._connection:
            for chunk, vector in zip(missing, new_vectors, strict=True):
                payload = json.dumps(vector, separators=(",", ":"), allow_nan=False)
                self._connection.execute(
                    """
                    INSERT INTO knowledge_chunk_embeddings (
                        fingerprint, chunk_id, document_id, version_id,
                        chunk_start, chunk_end, chunk_digest, dimensions,
                        vector_json, vector_digest
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fingerprint,
                        chunk.chunk_id,
                        chunk.document.ref.document_id,
                        chunk.document.ref.version_id,
                        chunk.start,
                        chunk.end,
                        chunk.content_digest,
                        len(vector),
                        payload,
                        hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                    ),
                )
        return len(missing)

    def _search_lexical(self, query: str, *, limit: int) -> tuple[KnowledgeSearchHit, ...]:
        query_terms = tuple(dict.fromkeys(self._terms(query)))
        phrase = query.casefold().strip()
        rows = self._connection.execute(
            """
            SELECT v.*
            FROM active_knowledge_versions AS a
            JOIN knowledge_versions AS v
              ON v.document_id = a.document_id
             AND v.version_id = a.version_id
            ORDER BY v.document_id
            """
        ).fetchall()
        scored: list[tuple[int, KnowledgeDocument]] = []
        for row in rows:
            document = self._document_from_row(row)
            searchable = f"{document.title}\n{document.content}".casefold()
            counts = Counter(self._terms(searchable))
            score = sum(min(counts[term], 4) for term in query_terms)
            if phrase in searchable:
                score += max(3, len(query_terms))
            if score > 0:
                scored.append((score, document))
        scored.sort(
            key=lambda item: (
                -item[0],
                item[1].ref.document_id,
                item[1].ref.version_id,
            )
        )
        return tuple(
            KnowledgeSearchHit(
                ref=document.ref,
                title=document.title,
                source_uri=document.source_uri,
                source_kind=document.source_kind,
                content_digest=document.content_digest,
                snippet=self._snippet(document.content, phrase),
                score=score,
            )
            for score, document in scored[:limit]
        )

    @staticmethod
    def _document_from_row(row: sqlite3.Row) -> KnowledgeDocument:
        return KnowledgeDocument(
            ref=KnowledgeVersionRef(
                document_id=row["document_id"],
                version_id=row["version_id"],
            ),
            title=row["title"],
            source_uri=row["source_uri"],
            source_kind=KnowledgeSourceKind(row["source_kind"]),
            content=row["content"],
            content_digest=row["content_digest"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _terms(text: str) -> tuple[str, ...]:
        folded = text.casefold()
        latin = re.findall(r"[a-z0-9]+", folded)
        cjk = re.findall(r"[\u3400-\u9fff]", folded)
        cjk_bigrams = [
            "".join(cjk[index : index + 2])
            for index in range(len(cjk) - 1)
        ]
        return (*latin, *cjk, *cjk_bigrams)

    @staticmethod
    def _snippet(content: str, phrase: str, *, size: int = 360) -> str:
        folded = content.casefold()
        index = folded.find(phrase)
        if index < 0:
            index = 0
        start = max(0, index - size // 3)
        end = min(len(content), start + size)
        prefix = "…" if start else ""
        suffix = "…" if end < len(content) else ""
        return f"{prefix}{content[start:end].strip()}{suffix}"
