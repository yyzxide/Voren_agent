from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tests.integration.test_web_app import WEB_AVAILABLE

if WEB_AVAILABLE:
    from tests.integration.test_web_app import TranscriptScriptedModel
    from tests.test_google_workspace_connector import FakeGoogleTransport
    from tests.test_knowledge_corpus import RecordingEmbedder
    from voren.adapters.google_workspace import GoogleWorkspaceConfig, GoogleWorkspaceConnector
    from voren.knowledge.models import KnowledgeDocument, KnowledgeSourceKind
    from voren.knowledge.store import SQLiteKnowledgeStore
    from voren.mcp_bridge.adapter import MCPKnowledgeReadAdapter
    from voren.mcp_bridge.server import create_knowledge_mcp_server
    from voren.runs.models import RunConfig
    from voren.runs.store import SQLiteRunStore
    from voren.runtime.models import MessageRole, ModelResponse, ToolCall
    from voren.web.models import CreateRunRequest, DecideRunRequest
    from voren.web.service import (
        VorenWebService, WebApprovalRecoveryRequiredError, create_google_web_workspace,
    )


@unittest.skipUnless(WEB_AVAILABLE, "web dependencies are unavailable")
class KnowledgeSnapshotIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "web.sqlite3"
        self.now = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
        self.transport = FakeGoogleTransport()
        self.conversations = []
        self.responses = (
            self.search_response("knowledge-before"),
            ModelResponse(tool_calls=(ToolCall(
                call_id="private-hold", name="create_private_calendar_event",
                arguments={"title": "Review", "start_time": "2026-10-09T09:00:00+08:00", "end_time": "2026-10-09T09:30:00+08:00"},
            ),)),
            self.search_response("knowledge-after"),
            ModelResponse(tool_calls=(ToolCall(
                call_id="reply-draft", name="create_email_draft",
                arguments={"recipients": ["alice@example.com"], "subject": "Review", "body": "Please review the draft."},
            ),)),
            ModelResponse(text="Both actions verified."),
        )

    @staticmethod
    def search_response(call_id):
        return ModelResponse(tool_calls=(ToolCall(
            call_id=call_id, name="search_meeting_knowledge",
            arguments={"query": "approval", "limit": 5},
        ),))

    def service(self, **knowledge_settings):
        owner = self

        class RecordingModel(TranscriptScriptedModel):
            def complete(self, *, messages, tools, cancellation):
                owner.conversations.append(tuple(messages))
                return super().complete(messages=messages, tools=tools, cancellation=cancellation)

        config = GoogleWorkspaceConfig(access_token="test-token", account_email="sid@example.com", time_zone="Asia/Shanghai")
        return VorenWebService(
            database=self.database, model_factory=lambda: RecordingModel(self.responses),
            mode="live", workspace_name="google", workspace_recoverable=True,
            workspace_factory=lambda: create_google_web_workspace(connector=GoogleWorkspaceConnector(
                config, transport=self.transport, clock=lambda: self.now,
            )), clock=lambda: self.now, **knowledge_settings,
        )

    def document(self, content, *, document_id="review:meeting"):
        return KnowledgeDocument.create(
            document_id=document_id, title="Approval review",
            source_uri=f"meeting://{document_id}", source_kind=KnowledgeSourceKind.MEETING_NOTE,
            content=content, created_at=self.now,
        )

    def activate(self, document):
        store = SQLiteKnowledgeStore(self.database)
        try:
            store.install(document)
            store.activate(document.ref, reason="operator-reviewed fixture", activated_at=self.now)
        finally:
            store.close()

    def submit(self, service):
        return service.submit(CreateRunRequest(
            client_request_id="knowledge-snapshot-request",
            request="Read approval notes, create a private hold, read the notes again and prepare a draft.",
        ))

    @staticmethod
    def decision(view, *, approved=True):
        return DecideRunRequest(
            decision_id=f"decision-{view.proposal.operation_id}",
            proposal_digest=view.proposal.digest, approved=approved,
        )

    def writes(self):
        return sum(method == "POST" for method, _, _ in self.transport.calls)

    def observation(self, call_id):
        return next(
            message.content for conversation in reversed(self.conversations)
            for message in conversation
            if message.role is MessageRole.TOOL and message.tool_call_id == call_id
        )

    def frozen(self, run_id):
        store = SQLiteRunStore(self.database)
        try:
            return store.get_run(run_id).config.metadata["knowledge_corpus"]
        finally:
            store.close()

    def index(self, provider):
        store = SQLiteKnowledgeStore(self.database)
        try:
            store.index_embeddings(provider)
        finally:
            store.close()

    def test_resumed_multi_action_search_keeps_original_versions_and_membership(self):
        original = self.document("Approval policy version one requires review.")
        self.activate(original)
        service = self.service()
        pending = self.submit(service)
        frozen = self.frozen(pending.run_id)
        self.assertEqual(frozen["refs"], [original.ref.model_dump(mode="json")])
        before = self.observation("knowledge-before")
        self.assertEqual(before["items"][0]["data"]["ref"], original.ref.model_dump(mode="json"))

        replacement = self.document("Approval policy version two changes the reviewer.")
        addition = self.document("Approval policy for a newly added project.", document_id="review:new")
        self.activate(replacement)
        self.activate(addition)
        restarted = self.service()
        self.assertFalse(restarted.get(pending.run_id).recovery_required)
        next_action = restarted.decide(pending.run_id, self.decision(pending))

        self.assertEqual(next_action.status, "waiting_approval")
        after = self.observation("knowledge-after")
        self.assertEqual([item["data"]["ref"] for item in after["items"]], [original.ref.model_dump(mode="json")])
        self.assertIn("version one", after["items"][0]["data"]["snippet"])
        self.assertEqual(self.frozen(pending.run_id), frozen)
        self.assertEqual(self.writes(), 1)
        final = self.service().decide(pending.run_id, self.decision(next_action))
        self.assertEqual(final.status, "completed")
        self.assertEqual(self.writes(), 2)

    def test_empty_corpus_does_not_pick_up_new_active_documents(self):
        service = self.service()
        pending = self.submit(service)
        self.assertEqual(self.frozen(pending.run_id)["refs"], [])
        self.assertEqual(self.observation("knowledge-before")["items"], [])
        self.activate(self.document("Approval notes added while waiting for a decision."))
        next_action = self.service().decide(pending.run_id, self.decision(pending))
        self.assertEqual(next_action.status, "waiting_approval")
        self.assertEqual(self.observation("knowledge-after")["items"], [])

    def test_legacy_run_without_corpus_metadata_keeps_live_retrieval(self):
        original = self.document("Approval policy version one.")
        self.activate(original)
        service = self.service()

        def legacy_config(**kwargs):
            kwargs["metadata"].pop("knowledge_corpus")
            return RunConfig(**kwargs)

        # Simulate the old writer at creation; config and checkpoints retain
        # their matching original digests, with no database history rewritten.
        with patch("voren.web.service.RunConfig", side_effect=legacy_config):
            pending = self.submit(service)
        replacement = self.document("Approval policy version two.")
        self.activate(replacement)
        next_action = self.service().decide(pending.run_id, self.decision(pending))
        self.assertEqual(next_action.status, "waiting_approval")
        self.assertEqual(self.observation("knowledge-after")["items"][0]["data"]["ref"], replacement.ref.model_dump(mode="json"))

    def test_missing_source_blocks_approval_but_allows_rejection(self):
        original = self.document("Approval policy version one.")
        self.activate(original)
        pending = self.submit(self.service())
        with sqlite3.connect(self.database) as connection:
            connection.execute("DELETE FROM active_knowledge_versions")
            connection.execute("DELETE FROM knowledge_versions")
        restarted = self.service()
        view = restarted.get(pending.run_id)
        self.assertTrue(view.recovery_required)
        self.assertEqual(view.recovery_reason, "knowledge_evidence_unavailable")
        calls = len(self.conversations)
        with self.assertRaisesRegex(WebApprovalRecoveryRequiredError, "knowledge evidence"):
            restarted.decide(pending.run_id, self.decision(pending))
        self.assertEqual(self.writes(), 0)
        self.assertEqual(len(self.conversations), calls)
        rejected = restarted.decide(pending.run_id, self.decision(pending, approved=False))
        self.assertEqual(rejected.status, "cancelled")
        self.assertEqual(self.writes(), 0)

    def test_corrupted_source_blocks_approval_before_dispatch(self):
        original = self.document("Approval policy version one.")
        self.activate(original)
        pending = self.submit(self.service())
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE knowledge_versions SET content = ?", ("tampered content",))
        restarted = self.service()
        self.assertTrue(restarted.get(pending.run_id).recovery_required)
        with self.assertRaisesRegex(WebApprovalRecoveryRequiredError, "knowledge evidence"):
            restarted.decide(pending.run_id, self.decision(pending))
        self.assertEqual(self.writes(), 0)

    def test_malformed_snapshot_blocks_approval_before_dispatch(self):
        self.activate(self.document("Approval policy version one."))
        service = self.service()

        def malformed_config(**kwargs):
            kwargs["metadata"]["knowledge_corpus"]["digest"] = "0" * 64
            return RunConfig(**kwargs)

        with patch("voren.web.service.RunConfig", side_effect=malformed_config):
            pending = self.submit(service)
        self.assertTrue(self.service().get(pending.run_id).recovery_required)
        with self.assertRaisesRegex(WebApprovalRecoveryRequiredError, "knowledge evidence"):
            self.service().decide(pending.run_id, self.decision(pending))
        self.assertEqual(self.writes(), 0)

    def test_missing_source_blocks_continuation_after_verified_receipt(self):
        self.activate(self.document("Approval policy version one."))
        service = self.service()
        pending = self.submit(service)
        with patch.object(service, "_resume_loop", side_effect=KeyboardInterrupt("after receipt")):
            with self.assertRaises(KeyboardInterrupt):
                service.decide(pending.run_id, self.decision(pending))
        self.assertEqual(self.writes(), 1)
        self.assertEqual(service.get(pending.run_id).status, "running")
        with sqlite3.connect(self.database) as connection:
            connection.execute("DELETE FROM active_knowledge_versions")
            connection.execute("DELETE FROM knowledge_versions")
        calls = len(self.conversations)
        restarted = self.service()
        with self.assertRaisesRegex(WebApprovalRecoveryRequiredError, "knowledge evidence"):
            restarted.resume(pending.run_id)
        self.assertEqual(self.writes(), 1)
        self.assertEqual(len(self.conversations), calls)

    def test_mcp_explicit_snapshot_is_frozen_and_default_remains_live(self):
        original = self.document("Approval policy version one.")
        self.activate(original)
        store = SQLiteKnowledgeStore(self.database)
        self.addCleanup(store.close)
        frozen_adapter = MCPKnowledgeReadAdapter(create_knowledge_mcp_server(store, corpus=store.snapshot_corpus()))
        live_adapter = MCPKnowledgeReadAdapter(create_knowledge_mcp_server(store))
        replacement = self.document("Approval policy version two.")
        self.activate(replacement)
        for adapter, expected in ((frozen_adapter, original.ref), (live_adapter, replacement.ref)):
            with self.subTest(expected=expected):
                observation = adapter.execute(
                    tool_call_id="direct-search", tool_name="search_meeting_knowledge",
                    arguments={"query": "approval"},
                )
                self.assertEqual(observation.status.value, "succeeded")
                self.assertEqual(observation.items[0].data["ref"], expected.model_dump(mode="json"))

    def test_missing_frozen_dense_index_blocks_approval_but_allows_rejection(self):
        self.activate(self.document("Approval policy version one."))
        provider = RecordingEmbedder()
        self.index(provider)
        settings = {"knowledge_retrieval_mode": "dense", "knowledge_embedder": provider}
        pending = self.submit(self.service(**settings))
        with sqlite3.connect(self.database) as connection:
            connection.execute("DELETE FROM knowledge_chunk_embeddings")
        restarted = self.service(**settings)
        model_calls = len(self.conversations)
        embedding_calls = len(provider.calls)
        view = restarted.get(pending.run_id)
        self.assertTrue(view.recovery_required)
        self.assertEqual(view.recovery_reason, "knowledge_evidence_unavailable")
        with self.assertRaisesRegex(WebApprovalRecoveryRequiredError, "knowledge evidence"):
            restarted.decide(pending.run_id, self.decision(pending))
        self.assertEqual(self.writes(), 0)
        self.assertEqual(len(self.conversations), model_calls)
        self.assertEqual(len(provider.calls), embedding_calls)
        rejected = restarted.decide(pending.run_id, self.decision(pending, approved=False))
        self.assertEqual(rejected.status, "cancelled")
        self.assertEqual(self.writes(), 0)
        self.assertEqual(len(provider.calls), embedding_calls)

    def test_corrupted_frozen_hybrid_index_blocks_approval_but_allows_rejection(self):
        self.activate(self.document("Approval policy version one."))
        provider = RecordingEmbedder()
        self.index(provider)
        settings = {"knowledge_retrieval_mode": "hybrid", "knowledge_embedder": provider}
        pending = self.submit(self.service(**settings))
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE knowledge_chunk_embeddings SET vector_json = ?", ("[0.0,1.0]",))
        restarted = self.service(**settings)
        embedding_calls = len(provider.calls)
        model_calls = len(self.conversations)
        self.assertTrue(restarted.get(pending.run_id).recovery_required)
        with self.assertRaisesRegex(WebApprovalRecoveryRequiredError, "knowledge evidence"):
            restarted.decide(pending.run_id, self.decision(pending))
        self.assertEqual(self.writes(), 0)
        self.assertEqual(len(self.conversations), model_calls)
        self.assertEqual(len(provider.calls), embedding_calls)
        self.assertEqual(restarted.decide(pending.run_id, self.decision(pending, approved=False)).status, "cancelled")
        self.assertEqual(self.writes(), 0)

    def test_dense_resume_uses_complete_old_index_when_new_active_source_is_unindexed(self):
        original = self.document("Approval policy version one.")
        self.activate(original)
        provider = RecordingEmbedder()
        self.index(provider)
        settings = {"knowledge_retrieval_mode": "dense", "knowledge_embedder": provider}
        pending = self.submit(self.service(**settings))
        self.activate(self.document("Approval policy version two."))
        self.activate(self.document("Approval notes for a new source.", document_id="review:new"))
        restarted = self.service(**settings)
        embedding_calls = len(provider.calls)
        self.assertFalse(restarted.get(pending.run_id).recovery_required)
        self.assertEqual(len(provider.calls), embedding_calls)
        next_action = restarted.decide(pending.run_id, self.decision(pending))
        self.assertEqual(next_action.status, "waiting_approval")
        after = self.observation("knowledge-after")
        self.assertEqual(after["status"], "succeeded")
        self.assertEqual([item["data"]["ref"] for item in after["items"]], [original.ref.model_dump(mode="json")])
        # The continuation's one query is the only embedding call; preflight
        # checks and recovery-state reads never contact the provider.
        self.assertEqual(len(provider.calls), embedding_calls + 1)
        self.assertEqual(self.writes(), 1)

    def test_missing_frozen_index_blocks_continuation_after_verified_receipt(self):
        self.activate(self.document("Approval policy version one."))
        provider = RecordingEmbedder()
        self.index(provider)
        settings = {"knowledge_retrieval_mode": "hybrid", "knowledge_embedder": provider}
        service = self.service(**settings)
        pending = self.submit(service)
        with patch.object(service, "_resume_loop", side_effect=KeyboardInterrupt("after receipt")):
            with self.assertRaises(KeyboardInterrupt):
                service.decide(pending.run_id, self.decision(pending))
        self.assertEqual(self.writes(), 1)
        with sqlite3.connect(self.database) as connection:
            connection.execute("DELETE FROM knowledge_chunk_embeddings")
        model_calls = len(self.conversations)
        embedding_calls = len(provider.calls)
        restarted = self.service(**settings)
        self.assertTrue(restarted.get(pending.run_id).recovery_required)
        with self.assertRaisesRegex(WebApprovalRecoveryRequiredError, "knowledge evidence"):
            restarted.resume(pending.run_id)
        self.assertEqual(self.writes(), 1)
        self.assertEqual(len(self.conversations), model_calls)
        self.assertEqual(len(provider.calls), embedding_calls)


if __name__ == "__main__":
    unittest.main()
