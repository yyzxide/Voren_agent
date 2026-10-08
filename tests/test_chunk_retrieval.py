from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from voren.knowledge.models import KnowledgeDocument, KnowledgeSearchHit, KnowledgeSourceKind
from voren.knowledge.retrieval import chunk_document, fuse_rankings, terms
from voren.knowledge.store import KnowledgeStoreError, SQLiteKnowledgeStore


class ControlledEmbedder:
    """A test double with hand-written vectors, never a semantic quality benchmark."""

    fingerprint = "controlled-test-v1"

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self.calls.append(texts)
        return tuple(
            (1.0, 0.0) if any(word in text.lower() for word in ("buy", "laptop", "purchase"))
            else (0.0, 1.0) if "approval" in text.lower()
            else (-1.0, 0.0)
            for text in texts
        )


class ChunkRetrievalTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "knowledge.sqlite3"
        self.store = SQLiteKnowledgeStore(self.database)
        self.addCleanup(self.store.close)
        self.now = datetime(2026, 10, 8, tzinfo=UTC)

    def document(self, content: str, *, document_id: str = "note:1", title: str = "Reviewed note") -> KnowledgeDocument:
        return KnowledgeDocument.create(
            document_id=document_id,
            title=title,
            content=content,
            source_uri=f"note://{document_id}",
            source_kind=KnowledgeSourceKind.OPERATOR_NOTE,
            created_at=self.now,
        )

    def activate(self, document: KnowledgeDocument) -> None:
        self.store.install(document)
        self.store.activate(document.ref, reason="reviewed test data")

    def test_stable_unicode_character_chunks_cover_exact_document(self) -> None:
        document = self.document("审批😀receipt\n" * 170)
        first = chunk_document(document)
        self.assertEqual(first, chunk_document(document))
        self.assertGreater(len(first), 2)
        self.assertEqual(first[0].start, 0)
        self.assertEqual(first[0].end, 600)
        self.assertEqual(first[1].start, 500)
        self.assertEqual(first[-1].end, len(document.content))
        for chunk in first:
            self.assertEqual(chunk.content, document.content[chunk.start:chunk.end])
            self.assertEqual(chunk.content_digest, hashlib.sha256(chunk.content.encode("utf-8")).hexdigest())
        newer = self.document(document.content + " changed")
        self.assertNotEqual(first[0].chunk_id, chunk_document(newer)[0].chunk_id)

    def test_bm25_returns_relevant_tail_with_exact_offsets_and_full_source_digest(self) -> None:
        document = self.document("background text. " * 150 + "The receipt has confirmation code ABC-91.")
        self.activate(document)
        hit = self.store.search("receipt confirmation")[0]
        self.assertIn("ABC-91", hit.snippet)
        self.assertEqual(hit.retrieval_method, "bm25")
        self.assertEqual(hit.snippet, document.content[hit.chunk_start:hit.chunk_end])
        self.assertEqual(hit.content_digest, document.content_digest)
        self.assertEqual(hit.ref, document.ref)
        self.assertGreater(hit.ranking_score, 0)
        self.assertGreater(hit.score, 0)
        old = self.store.search("receipt confirmation", mode="lexical")[0]
        self.assertNotIn("ABC-91", old.snippet)
        self.assertIsNone(old.chunk_id)
        self.assertEqual(old.retrieval_method, "lexical")

    def test_document_limit_deduplicates_long_documents(self) -> None:
        first = self.document("approval " * 300, document_id="note:a")
        second = self.document("approval receipt", document_id="note:b")
        self.activate(first)
        self.activate(second)
        hits = self.store.search("approval", limit=2)
        self.assertEqual({hit.ref.document_id for hit in hits}, {"note:a", "note:b"})

    def test_empty_corpus_and_unmatched_bm25_return_no_hits(self) -> None:
        self.assertEqual(self.store.search("receipt"), ())
        self.activate(self.document("approval receipt"))
        self.assertEqual(self.store.search("quantum teleportation"), ())
        self.assertEqual(self.store.search("!@#$"), ())

    def test_cjk_bigrams_do_not_cross_punctuation_or_latin(self) -> None:
        self.assertIn("审批", terms("审批 回执"))
        self.assertNotIn("批回", terms("审批 回执"))
        self.assertNotIn("批回", terms("审批abc回执"))
        self.activate(self.document("周五进行审批和回执演示。"))
        self.assertTrue(self.store.search("审批回执"))

    def test_dense_and_hybrid_require_explicit_matching_complete_index(self) -> None:
        self.activate(self.document("Purchase a laptop."))
        provider = ControlledEmbedder()
        for mode in ("dense", "hybrid"):
            with self.assertRaisesRegex(KnowledgeStoreError, "explicit embedding"):
                self.store.search("buy", mode=mode)
            with self.assertRaisesRegex(KnowledgeStoreError, "incomplete"):
                self.store.search("buy", mode=mode, embedder=provider)
        self.assertEqual(provider.calls, [])
        self.assertEqual(self.store.index_embeddings(provider), 1)
        original_calls = len(provider.calls)
        self.assertEqual(self.store.index_embeddings(provider), 0)
        self.assertEqual(len(provider.calls), original_calls)
        for mode in ("dense", "hybrid"):
            hit = self.store.search("buy", mode=mode, embedder=provider)[0]
            self.assertEqual(hit.retrieval_method, mode)
            self.assertIn("laptop", hit.snippet)
        changed = ControlledEmbedder()
        changed.fingerprint = "controlled-test-v2"
        with self.assertRaisesRegex(KnowledgeStoreError, "incomplete"):
            self.store.search("buy", mode="dense", embedder=changed)
        self.assertEqual(changed.calls, [])

    def test_positive_cosine_candidates_only(self) -> None:
        self.activate(self.document("Purchase a laptop.", document_id="note:a"))
        self.activate(self.document("approval boundary", document_id="note:b"))
        self.activate(self.document("Friday meeting", document_id="note:c"))
        provider = ControlledEmbedder()
        self.store.index_embeddings(provider)
        hits = self.store.search("buy", mode="dense", embedder=provider)
        self.assertEqual([hit.ref.document_id for hit in hits], ["note:a"])
        self.assertAlmostEqual(hits[0].ranking_score, 1.0)

    def test_version_activation_invalidates_dense_coverage_until_explicit_indexing(self) -> None:
        first = self.document("Purchase a laptop on Friday.")
        second = self.document("Purchase a laptop on Monday.")
        self.activate(first)
        self.store.install(second)
        provider = ControlledEmbedder()
        self.store.index_embeddings(provider)
        self.assertEqual(self.store.search("buy", mode="dense", embedder=provider)[0].ref, first.ref)
        self.store.activate(second.ref, reason="corrected date")
        with self.assertRaisesRegex(KnowledgeStoreError, "incomplete"):
            self.store.search("buy", mode="dense", embedder=provider)
        self.assertEqual(self.store.index_embeddings(provider), 1)
        self.assertEqual(self.store.search("buy", mode="hybrid", embedder=provider)[0].ref, second.ref)
        self.assertEqual(self.store.search("Friday"), ())
        self.store.activate(first.ref, reason="rollback")
        self.assertEqual(self.store.index_embeddings(provider), 0)
        self.assertEqual(self.store.search("buy", mode="dense", embedder=provider)[0].ref, first.ref)

    def test_deleted_cached_chunk_fails_closed_without_embedding_queries(self) -> None:
        self.activate(self.document("Purchase a laptop. " * 100))
        provider = ControlledEmbedder()
        self.store.index_embeddings(provider)
        with sqlite3.connect(self.database) as connection:
            connection.execute("DELETE FROM knowledge_chunk_embeddings WHERE chunk_start = 500")
        calls = len(provider.calls)
        with self.assertRaisesRegex(KnowledgeStoreError, "incomplete"):
            self.store.search("buy", mode="hybrid", embedder=provider)
        self.assertEqual(len(provider.calls), calls)

    def test_stored_vector_digest_and_dimensions_are_validated(self) -> None:
        self.activate(self.document("Purchase a laptop."))
        provider = ControlledEmbedder()
        self.store.index_embeddings(provider)
        with sqlite3.connect(self.database) as connection:
            original = connection.execute("SELECT vector_json FROM knowledge_chunk_embeddings").fetchone()[0]
            connection.execute("UPDATE knowledge_chunk_embeddings SET vector_json = '[0,1]'")
        with self.assertRaisesRegex(KnowledgeStoreError, "digest mismatch"):
            self.store.search("buy", mode="dense", embedder=provider)
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE knowledge_chunk_embeddings SET vector_json = ?, dimensions = 3", (original,))
        with self.assertRaisesRegex(KnowledgeStoreError, "dimensions"):
            self.store.search("buy", mode="dense", embedder=provider)

    def test_stored_nonfinite_or_zero_vector_fails_even_with_matching_digest(self) -> None:
        self.activate(self.document("Purchase a laptop."))
        provider = ControlledEmbedder()
        self.store.index_embeddings(provider)
        for payload in ("[NaN,1]", "[0,0]", "[true,1]"):
            with self.subTest(payload=payload):
                with sqlite3.connect(self.database) as connection:
                    connection.execute(
                        "UPDATE knowledge_chunk_embeddings SET vector_json = ?, vector_digest = ?",
                        (payload, hashlib.sha256(payload.encode("utf-8")).hexdigest()),
                    )
                with self.assertRaises(KnowledgeStoreError):
                    self.store.search("buy", mode="dense", embedder=provider)

    def test_query_vector_dimension_mismatch_is_rejected(self) -> None:
        self.activate(self.document("Purchase a laptop."))
        provider = ControlledEmbedder()
        self.store.index_embeddings(provider)
        provider.embed = lambda texts: ((1.0, 0.0, 0.0),)
        with self.assertRaisesRegex(KnowledgeStoreError, "dimensions"):
            self.store.search("buy", mode="dense", embedder=provider)

    def test_source_corruption_fails_before_using_cached_vectors(self) -> None:
        self.activate(self.document("Purchase a laptop."))
        provider = ControlledEmbedder()
        self.store.index_embeddings(provider)
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE knowledge_versions SET content = 'forged source'")
        calls = len(provider.calls)
        with self.assertRaises(ValidationError):
            self.store.search("buy", mode="dense", embedder=provider)
        self.assertEqual(len(provider.calls), calls)

    def test_failed_embedding_batches_do_not_partially_commit_index(self) -> None:
        self.activate(self.document("Purchase a laptop. " * 950))
        provider = ControlledEmbedder()
        original = provider.embed

        def fail_second_batch(texts):
            if provider.calls:
                raise RuntimeError("private response or secret")
            return original(texts)

        provider.embed = fail_second_batch
        with self.assertRaises(KnowledgeStoreError) as raised:
            self.store.index_embeddings(provider)
        self.assertNotIn("private response", str(raised.exception))
        self.assertLessEqual(len(provider.calls[0]), 32)
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM knowledge_chunk_embeddings").fetchone()[0], 0)

    def test_embedding_budget_rejects_before_provider_call(self) -> None:
        self.activate(self.document("Purchase a laptop. " * 100))
        provider = ControlledEmbedder()
        with self.assertRaisesRegex(KnowledgeStoreError, "budget"):
            self.store.index_embeddings(provider, max_new_chunks=1)
        self.assertEqual(provider.calls, [])

    def test_provider_identity_cannot_change_during_indexing(self) -> None:
        self.activate(self.document("Purchase a laptop."))
        provider = ControlledEmbedder()
        original = provider.embed

        def change_identity(texts):
            values = original(texts)
            provider.fingerprint = "changed-identity"
            return values

        provider.embed = change_identity
        with self.assertRaisesRegex(KnowledgeStoreError, "fingerprint changed"):
            self.store.index_embeddings(provider)
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM knowledge_chunk_embeddings").fetchone()[0], 0)

    def test_rrf_combines_rank_positions_not_incompatible_score_scales(self) -> None:
        a, b, c = tuple(
            chunk_document(self.document(f"Content {name}", document_id=f"note:{name}"))[0]
            for name in ("a", "b", "c")
        )
        ranked = fuse_rankings(((a, 9000.0), (b, 8000.0)), ((b, 0.9), (c, 0.8)))
        self.assertEqual([chunk.chunk_id for chunk, _ in ranked], [b.chunk_id, a.chunk_id, c.chunk_id])
        self.assertAlmostEqual(ranked[0][1], 1 / 62 + 1 / 61)

    def test_hit_model_rejects_incomplete_or_incorrect_chunk_metadata(self) -> None:
        self.activate(self.document("approval receipt"))
        payload = self.store.search("approval")[0].model_dump()
        for change in (
            {"chunk_start": None}, {"chunk_end": 1}, {"chunk_digest": "0" * 64},
            {"chunk_id": "0" * 64},
        ):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                KnowledgeSearchHit(**(payload | change))


if __name__ == "__main__":
    unittest.main()
