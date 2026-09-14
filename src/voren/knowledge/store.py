"""SQLite-backed immutable documents and deterministic lexical retrieval."""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from voren.knowledge.models import (
    KnowledgeDocument,
    KnowledgeSearchHit,
    KnowledgeSourceKind,
    KnowledgeVersionRef,
)


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
        self, query: str, *, limit: int = 5
    ) -> tuple[KnowledgeSearchHit, ...]:
        if not query.strip():
            raise ValueError("knowledge query must be non-empty")
        if not 1 <= limit <= 20:
            raise ValueError("knowledge search limit must be between 1 and 20")
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
