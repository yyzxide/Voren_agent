from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from voren.knowledge.citations import CitationValidationError, validate_knowledge_hit
from voren.knowledge.models import (
    KnowledgeCorpusSnapshot,
    KnowledgeDocument,
    KnowledgeSearchHit,
    KnowledgeSourceKind,
)
from voren.knowledge.store import KnowledgeStoreError, SQLiteKnowledgeStore


class RecordingEmbedder:
    fingerprint = "corpus-binding-test-v1"

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self.calls.append(texts)
        return tuple((1.0, 0.0) for _ in texts)


class KnowledgeCorpusTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / "knowledge.sqlite3"
        self.store = SQLiteKnowledgeStore(self.database)
        self.addCleanup(self.store.close)

    def document(self, content: str, *, document_id: str = "note:one") -> KnowledgeDocument:
        return KnowledgeDocument.create(
            document_id=document_id,
            title="Reviewed source",
            source_uri=f"note://{document_id}",
            source_kind=KnowledgeSourceKind.OPERATOR_NOTE,
            content=content,
            created_at=datetime(2026, 10, 8, tzinfo=UTC),
        )

    def activate(self, document: KnowledgeDocument) -> None:
        self.store.install(document)
        self.store.activate(document.ref, reason="reviewed fixture")

    def test_snapshot_factory_is_order_independent_and_rejects_duplicate_documents(self) -> None:
        first = self.document("approval", document_id="note:a")
        second = self.document("receipt", document_id="note:b")
        snapshot = KnowledgeCorpusSnapshot.create((second.ref, first.ref))
        self.assertEqual(snapshot.refs, (first.ref, second.ref))
        self.assertEqual(snapshot, KnowledgeCorpusSnapshot.from_refs((first.ref, second.ref)))
        payload = snapshot.model_dump(mode="json")
        self.assertEqual(KnowledgeCorpusSnapshot.model_validate(payload), snapshot)
        with self.assertRaises(ValidationError):
            KnowledgeCorpusSnapshot.create((first.ref, first.ref))
        with self.assertRaises(ValidationError):
            KnowledgeCorpusSnapshot.create((first.ref, self.document("updated", document_id="note:a").ref))
        with self.assertRaises(ValidationError):
            KnowledgeCorpusSnapshot.model_validate(payload | {"refs": list(reversed(payload["refs"]))})
        with self.assertRaises(ValidationError):
            KnowledgeCorpusSnapshot.model_validate(payload | {"digest": "0" * 64})

    def test_frozen_search_reads_original_versions_after_activation_and_reopen(self) -> None:
        first = self.document("Friday approval meeting.")
        second = self.document("Monday receipt meeting.")
        self.activate(first)
        snapshot = self.store.snapshot_corpus()
        self.activate(second)
        reopened = SQLiteKnowledgeStore(self.database)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.load_corpus(snapshot), (first,))
        for mode in ("lexical", "bm25"):
            with self.subTest(mode=mode):
                self.assertEqual(reopened.search("Friday", mode=mode, corpus=snapshot)[0].ref, first.ref)
                self.assertEqual(reopened.search("Monday", mode=mode, corpus=snapshot), ())
                self.assertEqual(reopened.search("Friday", mode=mode), ())
                self.assertEqual(reopened.search("Monday", mode=mode)[0].ref, second.ref)

    def test_one_manifest_read_does_not_mix_concurrently_changed_active_versions(self) -> None:
        first = self.document("approval original", document_id="note:a")
        second = self.document("receipt original", document_id="note:b")
        corrected = self.document("receipt corrected", document_id="note:b")
        self.activate(first)
        self.activate(second)
        self.store.install(corrected)
        original_load = self.store.load
        activated = False

        def load_and_switch(ref):
            nonlocal activated
            document = original_load(ref)
            if not activated:
                activated = True
                self.store.activate(corrected.ref, reason="concurrent reviewed correction")
            return document

        self.store.load = load_and_switch
        snapshot = self.store.snapshot_corpus()
        self.assertEqual(snapshot.refs, (first.ref, second.ref))
        self.assertEqual(self.store.get_active("note:b").ref, corrected.ref)

    def test_new_documents_cannot_change_frozen_bm25_ranking_or_statistics(self) -> None:
        self.activate(self.document("approval receipt Friday", document_id="note:a"))
        self.activate(self.document("approval configuration boundary", document_id="note:b"))
        snapshot = self.store.snapshot_corpus()
        before = self.store.search("approval", corpus=snapshot)
        self.activate(self.document("unrelated new meeting text", document_id="note:c"))
        self.assertEqual(self.store.search("approval", corpus=snapshot), before)
        self.assertNotEqual(self.store.search("approval")[0].ranking_score, before[0].ranking_score)
        self.assertEqual(self.store.search("unrelated", corpus=snapshot), ())
        self.assertEqual(self.store.search("unrelated")[0].ref.document_id, "note:c")

    def test_explicit_empty_snapshot_stays_empty_while_none_uses_live_corpus(self) -> None:
        empty = self.store.snapshot_corpus()
        self.assertEqual(empty.refs, ())
        self.activate(self.document("approval receipt"))
        provider = RecordingEmbedder()
        self.assertEqual(self.store.load_corpus(empty), ())
        self.assertEqual(self.store.index_embeddings(provider, corpus=empty), 0)
        for mode in ("lexical", "bm25", "dense", "hybrid"):
            with self.subTest(mode=mode):
                self.assertEqual(self.store.search("approval", mode=mode, embedder=provider, corpus=empty), ())
        self.assertEqual(provider.calls, [])
        self.assertTrue(self.store.search("approval", corpus=None))

    def test_missing_frozen_source_fails_instead_of_substituting_active_version(self) -> None:
        first = self.document("Friday approval")
        self.activate(first)
        snapshot = self.store.snapshot_corpus()
        self.activate(self.document("Monday approval"))
        with sqlite3.connect(self.database) as connection:
            connection.execute("DELETE FROM knowledge_versions WHERE version_id = ?", (first.ref.version_id,))
        self.assertTrue(self.store.search("Monday"))
        with self.assertRaises(KnowledgeStoreError):
            self.store.load_corpus(snapshot)
        with self.assertRaises(KnowledgeStoreError):
            self.store.search("approval", corpus=snapshot)

    def test_corrupt_source_and_bypassed_snapshot_validation_fail_before_embedding(self) -> None:
        document = self.document("approval receipt")
        self.activate(document)
        snapshot = self.store.snapshot_corpus()
        provider = RecordingEmbedder()
        with self.assertRaises(ValidationError):
            self.store.load_corpus(snapshot.model_copy(update={"digest": "0" * 64}))
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE knowledge_versions SET content = 'forged source'")
        with self.assertRaises(ValidationError):
            self.store.search("approval", mode="dense", embedder=provider, corpus=snapshot)
        with self.assertRaises(ValidationError):
            self.store.snapshot_corpus()
        self.assertEqual(provider.calls, [])

    def test_dense_search_keeps_existing_frozen_vectors_after_new_active_version(self) -> None:
        first = self.document("Friday approval")
        self.activate(first)
        snapshot = self.store.snapshot_corpus()
        provider = RecordingEmbedder()
        self.store.index_embeddings(provider, corpus=snapshot)
        self.activate(self.document("Monday approval"))
        for mode in ("dense", "hybrid"):
            with self.subTest(mode=mode):
                self.assertEqual(self.store.search("approval", mode=mode, embedder=provider, corpus=snapshot)[0].ref, first.ref)
                calls = len(provider.calls)
                with self.assertRaises(KnowledgeStoreError):
                    self.store.search("approval", mode=mode, embedder=provider)
                self.assertEqual(len(provider.calls), calls)

    def test_explicit_indexing_can_target_original_snapshot_without_indexing_new_source(self) -> None:
        first = self.document("Friday approval")
        self.activate(first)
        snapshot = self.store.snapshot_corpus()
        self.activate(self.document("Monday approval"))
        provider = RecordingEmbedder()
        self.assertEqual(self.store.index_embeddings(provider, corpus=snapshot), 1)
        self.assertIn("Friday", provider.calls[0][0])
        self.assertNotIn("Monday", provider.calls[0][0])
        self.assertEqual(self.store.search("approval", mode="dense", embedder=provider, corpus=snapshot)[0].ref, first.ref)
        self.assertEqual(self.store.index_embeddings(provider, corpus=snapshot), 0)

    def test_valid_unicode_chunk_and_legacy_snippet_bind_to_actual_source(self) -> None:
        document = self.document("审批😀 receipt on Friday.\n" * 45)
        self.activate(document)
        for mode in ("lexical", "bm25"):
            with self.subTest(mode=mode):
                hit = self.store.search("receipt", mode=mode)[0]
                validate_knowledge_hit(hit, document)

    def test_internally_consistent_fabricated_chunk_is_rejected_against_source(self) -> None:
        document = self.document("Friday approval receipt.")
        self.activate(document)
        original = self.store.search("approval")[0]
        fabricated = "X" * len(original.snippet)
        digest = hashlib.sha256(fabricated.encode("utf-8")).hexdigest()
        identity = {
            "version_id": original.ref.version_id,
            "start": original.chunk_start,
            "end": original.chunk_end,
            "content_digest": digest,
        }
        chunk_id = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        forged = KnowledgeSearchHit.model_validate(original.model_dump() | {
            "snippet": fabricated, "chunk_digest": digest, "chunk_id": chunk_id,
        })
        with self.assertRaises(CitationValidationError) as raised:
            validate_knowledge_hit(forged, document)
        self.assertIn("snippet differs from exact source offsets", raised.exception.errors)
        self.assertNotIn(fabricated, str(raised.exception))

    def test_citation_rejects_wrong_source_metadata_version_and_empty_legacy_text(self) -> None:
        document = self.document("approval receipt")
        self.activate(document)
        hit = self.store.search("approval")[0]
        corrected = self.document("corrected approval receipt")
        with self.assertRaises(CitationValidationError):
            validate_knowledge_hit(hit, corrected)
        for field, value in (
            ("title", "forged title"), ("source_uri", "private://forged"),
            ("content_digest", "0" * 64),
        ):
            with self.subTest(field=field), self.assertRaises(CitationValidationError):
                validate_knowledge_hit(hit.model_copy(update={field: value}), document)
        legacy = self.store.search("approval", mode="lexical")[0]
        with self.assertRaises(CitationValidationError):
            validate_knowledge_hit(legacy.model_copy(update={"retrieval_method": "bm25"}), document)
        for snippet in ("…", "…   …", "forged quote"):
            with self.subTest(snippet=snippet), self.assertRaises(CitationValidationError):
                validate_knowledge_hit(legacy.model_copy(update={"snippet": snippet}), document)


if __name__ == "__main__":
    unittest.main()
