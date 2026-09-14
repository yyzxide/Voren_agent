from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from voren.adapters.knowledge_reads import KnowledgeReadAdapter
from voren.cli import build_parser, run_knowledge_ingest, run_knowledge_inspect
from voren.knowledge.models import KnowledgeDocument, KnowledgeSourceKind
from voren.knowledge.store import KnowledgeStoreError, SQLiteKnowledgeStore
from voren.observations.models import ObservationStatus, SourceKind, TrustLevel


class KnowledgeRetrievalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database = Path(self.temporary_directory.name) / "nested" / "voren.sqlite3"
        self.store = SQLiteKnowledgeStore(self.database)
        self.addCleanup(self.store.close)
        self.now = datetime(2026, 9, 14, 9, 30, tzinfo=UTC)

    def document(
        self,
        *,
        document_id: str = "meeting:weekly-2026-09-14",
        title: str = "Agent weekly meeting",
        content: str = "Sid will present the approval and receipt design on Friday.",
    ) -> KnowledgeDocument:
        return KnowledgeDocument.create(
            document_id=document_id,
            title=title,
            source_uri=f"meeting://{document_id}",
            source_kind=KnowledgeSourceKind.MEETING_NOTE,
            content=content,
            created_at=self.now,
        )

    def install_active(self, document: KnowledgeDocument) -> None:
        self.store.install(document)
        self.store.activate(document.ref, reason="operator reviewed import")

    def test_search_returns_exact_active_source_version_and_digest(self) -> None:
        document = self.document()
        self.install_active(document)

        hits = self.store.search("approval receipt")

        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].ref, document.ref)
        self.assertEqual(hits[0].source_uri, document.source_uri)
        self.assertEqual(hits[0].content_digest, document.content_digest)
        self.assertIn("approval and receipt", hits[0].snippet)

    def test_new_version_does_not_enter_search_until_explicit_activation(self) -> None:
        first = self.document(content="The meeting is on Friday at 10:00.")
        second = self.document(content="The reviewed meeting time is Monday at 14:00.")
        self.install_active(first)
        self.store.install(second)

        self.assertEqual(self.store.search("Friday")[0].ref, first.ref)
        self.assertEqual(self.store.search("Monday"), ())

        self.store.activate(second.ref, reason="operator selected corrected note")

        self.assertEqual(self.store.search("Friday"), ())
        self.assertEqual(self.store.search("Monday")[0].ref, second.ref)

    def test_chinese_query_uses_deterministic_character_and_bigram_terms(self) -> None:
        document = self.document(
            title="项目评审会议",
            content="会议决定周五演示审批、回执与失败恢复流程。",
        )
        self.install_active(document)

        hits = self.store.search("审批回执")

        self.assertEqual(hits[0].ref, document.ref)
        self.assertGreater(hits[0].score, 0)

    def test_tampered_persisted_content_fails_digest_validation(self) -> None:
        document = self.document()
        self.install_active(document)
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE knowledge_versions SET content = ? WHERE version_id = ?",
                ("tampered", document.ref.version_id),
            )

        with self.assertRaises(ValidationError):
            self.store.load(document.ref)

    def test_unknown_or_unactivated_document_is_not_returned(self) -> None:
        document = self.document()
        self.store.install(document)

        self.assertEqual(self.store.search("approval"), ())
        with self.assertRaises(KnowledgeStoreError):
            self.store.get_active(document.ref.document_id)

    def test_adapter_marks_every_hit_as_non_authoritative_external_data(self) -> None:
        document = self.document()
        self.install_active(document)
        adapter = KnowledgeReadAdapter(self.store, clock=lambda: self.now)

        observation = adapter.execute(
            tool_call_id="knowledge-call-1",
            tool_name="search_meeting_knowledge",
            arguments={"query": "approval", "limit": 3},
        )

        self.assertEqual(observation.status, ObservationStatus.SUCCEEDED)
        self.assertEqual(len(observation.items), 1)
        provenance = observation.items[0].provenance
        self.assertEqual(provenance.trust, TrustLevel.EXTERNAL_UNTRUSTED)
        self.assertEqual(provenance.source, SourceKind.KNOWLEDGE)
        self.assertFalse(provenance.instruction_authority)
        self.assertIn(document.ref.version_id, provenance.source_ref)
        self.assertEqual(
            observation.items[0].data["content_digest"], document.content_digest
        )

    def test_adapter_rejects_invalid_arguments_without_querying(self) -> None:
        adapter = KnowledgeReadAdapter(self.store)

        observation = adapter.execute(
            tool_call_id="knowledge-call-invalid",
            tool_name="search_meeting_knowledge",
            arguments={"query": "", "limit": 100},
        )

        self.assertEqual(observation.status, ObservationStatus.FAILED)
        self.assertEqual(observation.error_code, "invalid_arguments")

    def test_cli_ingests_and_redacts_active_document_by_default(self) -> None:
        source = Path(self.temporary_directory.name) / "meeting.md"
        source.write_text("Decide the demo order on Friday.\n", encoding="utf-8")
        ingest_args = build_parser().parse_args(
            [
                "knowledge",
                "ingest",
                str(source),
                "--document-id",
                "meeting:demo-order",
                "--title",
                "Demo order",
                "--source-uri",
                "file:///controlled/meeting.md",
                "--source-kind",
                "meeting_note",
                "--reason",
                "operator reviewed import",
                "--database",
                str(self.database),
            ]
        )
        output: list[str] = []

        self.assertEqual(
            run_knowledge_ingest(ingest_args, output=output.append), 0
        )

        inspect_args = build_parser().parse_args(
            [
                "knowledge",
                "inspect",
                "--document-id",
                "meeting:demo-order",
                "--database",
                str(self.database),
            ]
        )
        inspected: list[str] = []
        self.assertEqual(
            run_knowledge_inspect(inspect_args, output=inspected.append), 0
        )
        self.assertNotIn("Decide the demo order", inspected[0])
        self.assertIn('"content_bytes"', inspected[0])


if __name__ == "__main__":
    unittest.main()
