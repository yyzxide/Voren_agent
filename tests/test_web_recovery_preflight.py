from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from tests.integration.test_web_app import WEB_AVAILABLE

if WEB_AVAILABLE:
    from tests.integration.test_web_app import TranscriptScriptedModel
    from tests.test_google_workspace_connector import FakeGoogleTransport
    from tests.test_multi_action_provider import OfflineResponsesTransport
    from voren.actions.ledger import SQLiteOperationLedger
    from voren.adapters.google_workspace import GoogleWorkspaceConfig, GoogleWorkspaceConnector
    from voren.providers.openai_responses import (
        OpenAIResponsesConfig, OpenAIResponsesModelAdapter, ResponsesCapabilities,
    )
    from voren.runs.store import SQLiteRunStore
    from voren.runtime.models import MessageRole, ModelMessage, ModelResponse, ToolCall
    from voren.runtime.transcripts import SQLiteTranscriptStore
    from voren.testing.scripted_model import ScriptedModelAdapter
    from voren.web.models import CreateRunRequest, DecideRunRequest
    from voren.web.service import (
        VorenWebService, WebApprovalRecoveryRequiredError, create_google_web_workspace,
    )


@unittest.skipUnless(WEB_AVAILABLE, "web dependencies are unavailable")
class WebRecoveryPreflightTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "web.sqlite3"
        self.now = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
        self.transport = FakeGoogleTransport()
        self.call = ToolCall(
            call_id="draft-1", name="create_email_draft",
            arguments={"recipients": ["alice@example.com"], "subject": "Review", "body": "Please review."},
        )
        self.responses = (ModelResponse(tool_calls=(self.call,)), ModelResponse(text="Draft verified."))

    def service(self, *, model_factory=None, mode="live", workspace_factory=None):
        def workspace():
            return create_google_web_workspace(connector=GoogleWorkspaceConnector(
                GoogleWorkspaceConfig(access_token="fake-token", account_email="sid@example.com"),
                transport=self.transport, clock=lambda: self.now,
            ))
        return VorenWebService(
            database=self.database,
            model_factory=model_factory or (lambda: TranscriptScriptedModel(self.responses)),
            mode=mode, workspace_name="google", workspace_recoverable=True,
            workspace_factory=workspace_factory or workspace, clock=lambda: self.now,
        )

    def submit(self, service):
        return service.submit(CreateRunRequest(client_request_id="preflight-case", request="Prepare a draft for approval."))

    @staticmethod
    def decision(view, *, approved=True):
        return DecideRunRequest(decision_id="preflight-decision", proposal_digest=view.proposal.digest, approved=approved)

    def writes(self):
        return sum(method == "POST" for method, _, _ in self.transport.calls)

    def assert_no_authorization(self, view):
        ledger = SQLiteOperationLedger(self.database)
        try:
            self.assertIsNone(ledger.get_approval(view.proposal.operation_id))
            self.assertIsNone(ledger.get_receipt(view.proposal.operation_id))
            self.assertEqual(ledger.get_status(view.proposal.operation_id), "prepared")
        finally:
            ledger.close()
        self.assertEqual(self.writes(), 0)

    def assert_blocked(self, service, pending, *, reason, message):
        view = service.get(pending.run_id)
        self.assertTrue(view.recovery_required)
        self.assertEqual(view.recovery_reason, reason)
        with self.assertRaisesRegex(WebApprovalRecoveryRequiredError, message):
            service.decide(pending.run_id, self.decision(pending))
        self.assert_no_authorization(pending)

    def mutate_checkpoint(self, run_id, change):
        store = SQLiteTranscriptStore.from_local_key(self.database)
        try:
            store.save(change(store.load(run_id)))
        finally:
            store.close()

    def test_corrupt_checkpoint_blocks_approval_but_rejection_still_works(self):
        pending = self.submit(self.service())
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE run_transcripts SET ciphertext = ?", (b"corrupted",))
        restarted = self.service()
        self.assert_blocked(restarted, pending, reason="transcript_unavailable", message="saved transcript")
        self.assertEqual(restarted.decide(pending.run_id, self.decision(pending, approved=False)).status, "cancelled")
        self.assertEqual(self.writes(), 0)

    def test_missing_checkpoint_blocks_approval(self):
        pending = self.submit(self.service())
        with sqlite3.connect(self.database) as connection:
            connection.execute("DELETE FROM run_transcripts")
        self.assert_blocked(self.service(), pending, reason="transcript_unavailable", message="saved transcript")

    def test_missing_key_is_not_replaced_and_approval_is_not_dispatched(self):
        pending = self.submit(self.service())
        key_path = self.database.with_name(self.database.name + ".transcript.key")
        key_path.unlink()
        restarted = self.service()
        self.assert_blocked(restarted, pending, reason="transcript_unavailable", message="saved transcript")
        self.assertFalse(key_path.exists())
        self.assertEqual(restarted.decide(pending.run_id, self.decision(pending, approved=False)).status, "cancelled")

    def test_authenticated_checkpoint_must_bind_to_the_run_configuration(self):
        pending = self.submit(self.service())
        self.mutate_checkpoint(pending.run_id, lambda item: item.model_copy(update={"config_digest": "0" * 64}))
        self.assert_blocked(self.service(), pending, reason="transcript_unavailable", message="saved transcript")

    def test_authenticated_checkpoint_must_bind_to_the_exact_pending_call(self):
        pending = self.submit(self.service())
        changed = self.call.model_copy(update={"arguments": {**self.call.arguments, "body": "A different body."}})
        self.mutate_checkpoint(pending.run_id, lambda item: item.model_copy(update={
            "pending_response": ModelResponse(tool_calls=(changed,)),
        }))
        self.assert_blocked(self.service(), pending, reason="transcript_unavailable", message="saved transcript")

    def test_old_call_in_history_does_not_authorize_a_different_pending_response(self):
        pending = self.submit(self.service())
        changed = self.call.model_copy(update={"call_id": "different-call"})
        self.mutate_checkpoint(pending.run_id, lambda item: item.model_copy(update={
            "messages": (*item.messages, ModelMessage(role=MessageRole.ASSISTANT, tool_calls=(self.call,))),
            "pending_response": ModelResponse(tool_calls=(changed,)),
        }))
        self.assert_blocked(self.service(), pending, reason="transcript_unavailable", message="saved transcript")

    def provider(self, *, model="offline-model", endpoint="https://offline.example/v1/responses", profile="openai", responses=()):
        transport = OfflineResponsesTransport(tuple(responses))
        transport.endpoint = endpoint
        adapter = OpenAIResponsesModelAdapter(
            config=OpenAIResponsesConfig(model=model, capabilities=ResponsesCapabilities.for_profile(profile)),
            transport=transport,
        )
        return adapter, transport

    def provider_pending(self):
        response = {"status": "completed", "output": [{
            "type": "function_call", "id": "function-1", "call_id": self.call.call_id,
            "name": self.call.name, "arguments": json.dumps(self.call.arguments),
        }]}
        model, transport = self.provider(responses=(response,))
        pending = self.submit(self.service(model_factory=lambda: model))
        self.assertEqual(len(transport.requests), 1)
        return pending

    def test_model_endpoint_and_profile_changes_are_rejected_before_dispatch(self):
        pending = self.provider_pending()
        for changed in (
            {"model": "replacement-model"},
            {"endpoint": "https://replacement.example/v1/responses"},
            {"profile": "deepseek"},
        ):
            with self.subTest(changed=changed):
                model, transport = self.provider(**changed)
                restarted = self.service(model_factory=lambda: model)
                self.assert_blocked(restarted, pending, reason="model_configuration_changed", message="model configuration")
                self.assertEqual(transport.requests, [])

    def test_same_responses_configuration_restores_provider_state_after_restart(self):
        pending = self.provider_pending()
        response = {"status": "completed", "output": [{
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "Draft verified after restart."}],
        }]}
        model, transport = self.provider(responses=(response,))
        restarted = self.service(model_factory=lambda: model)
        self.assertFalse(restarted.get(pending.run_id).recovery_required)
        self.assertEqual(transport.requests, [])
        completed = restarted.decide(pending.run_id, self.decision(pending))
        self.assertEqual(completed.final_text, "Draft verified after restart.")
        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(self.writes(), 1)

    def test_web_mode_change_does_not_replace_the_frozen_execution_mode(self):
        pending = self.submit(self.service())
        restarted = self.service(mode="demo")
        self.assert_blocked(restarted, pending, reason="model_configuration_changed", message="model configuration")
        self.assertEqual(restarted.decide(pending.run_id, self.decision(pending, approved=False)).status, "cancelled")

    def test_unavailable_model_is_sanitized_and_does_not_authorize_an_action(self):
        pending = self.submit(self.service())
        def unavailable():
            raise RuntimeError("private-provider-detail")
        restarted = self.service(model_factory=unavailable)
        self.assert_blocked(restarted, pending, reason="model_configuration_changed", message="model configuration")
        with self.assertRaises(WebApprovalRecoveryRequiredError) as result:
            restarted.decide(pending.run_id, self.decision(pending))
        self.assertNotIn("private-provider-detail", str(result.exception))
        self.assertEqual(restarted.decide(pending.run_id, self.decision(pending, approved=False)).status, "cancelled")

    def test_recovery_checks_do_not_call_the_model_and_continuation_uses_the_checked_instance(self):
        initial = ScriptedModelAdapter(self.responses)
        pending = self.submit(self.service(model_factory=lambda: initial))
        constructed = []
        def factory():
            model = ScriptedModelAdapter((self.responses[-1],))
            constructed.append(model)
            return model
        restarted = self.service(model_factory=factory)
        self.assertFalse(restarted.get(pending.run_id).recovery_required)
        self.assertEqual(len(constructed), 1)
        self.assertEqual(constructed[0].requests, [])
        completed = restarted.decide(pending.run_id, self.decision(pending))
        self.assertEqual(completed.status, "completed")
        self.assertEqual(len(constructed), 2)
        self.assertEqual(len(constructed[-1].requests), 1)
        self.assertEqual(self.writes(), 1)

    def test_changed_adapter_or_contract_is_blocked_before_authorization(self):
        pending = self.submit(self.service())
        original = self.service()._workspace_factory
        for changed in (
            {"world_adapter": "replacement-world-v2"},
            {"contract_versions": ("replacement-contract-v2",)},
        ):
            with self.subTest(changed=changed):
                restarted = self.service(workspace_factory=lambda: replace(original(), **changed))
                self.assert_blocked(restarted, pending, reason="workspace_contract_changed", message="adapter or action contracts")
        self.assertEqual(restarted.decide(pending.run_id, self.decision(pending, approved=False)).status, "cancelled")
        self.assertEqual(self.writes(), 0)

    def test_corrupt_checkpoint_blocks_continuation_after_a_verified_receipt(self):
        service = self.service()
        pending = self.submit(service)
        with patch.object(service, "_resume_loop", side_effect=KeyboardInterrupt("after receipt")):
            with self.assertRaises(KeyboardInterrupt):
                service.decide(pending.run_id, self.decision(pending))
        self.assertEqual(self.writes(), 1)
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE run_transcripts SET ciphertext = ?", (b"corrupted",))
        restarted = self.service()
        self.assertEqual(restarted.get(pending.run_id).recovery_reason, "transcript_unavailable")
        with self.assertRaisesRegex(WebApprovalRecoveryRequiredError, "saved transcript"):
            restarted.resume(pending.run_id)
        self.assertEqual(self.writes(), 1)

    def test_changed_model_blocks_continuation_after_a_verified_receipt(self):
        service = self.service()
        pending = self.submit(service)
        with patch.object(service, "_resume_loop", side_effect=KeyboardInterrupt("after receipt")):
            with self.assertRaises(KeyboardInterrupt):
                service.decide(pending.run_id, self.decision(pending))
        model = ScriptedModelAdapter((ModelResponse(text="Must not run."),))
        restarted = self.service(model_factory=lambda: model)
        self.assertEqual(restarted.get(pending.run_id).recovery_reason, "model_configuration_changed")
        with self.assertRaisesRegex(WebApprovalRecoveryRequiredError, "model configuration"):
            restarted.resume(pending.run_id)
        self.assertEqual(model.requests, [])
        self.assertEqual(self.writes(), 1)

    def test_legacy_run_without_model_identity_keeps_its_recorded_mode_and_factory_behavior(self):
        pending = self.submit(self.service())
        store = SQLiteRunStore(self.database)
        try:
            run = store.get_run(pending.run_id)
            metadata = {name: value for name, value in run.config.metadata.items() if name != "model_context"}
            config = run.config.model_copy(update={"metadata": metadata})
            digest = config.calculated_digest()
        finally:
            store.close()
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE runs SET config_json = ?, config_digest = ? WHERE run_id = ?",
                               (config.model_dump_json(), digest, pending.run_id))
        self.mutate_checkpoint(pending.run_id, lambda item: item.model_copy(update={"config_digest": digest}))
        model = ScriptedModelAdapter((ModelResponse(text="Legacy factory behavior retained."),))
        restarted = self.service(model_factory=lambda: model)
        self.assertFalse(restarted.get(pending.run_id).recovery_required)
        completed = restarted.decide(pending.run_id, self.decision(pending))
        self.assertEqual(completed.final_text, "Legacy factory behavior retained.")
        self.assertEqual(self.writes(), 1)

    def test_custom_model_boundary_does_not_claim_hidden_configuration_is_verified(self):
        pending = self.submit(self.service())
        store = SQLiteRunStore(self.database)
        try:
            context = store.get_run(pending.run_id).config.metadata["model_context"]
        finally:
            store.close()
        self.assertEqual(context["boundary"], "custom_adapter")
        self.assertFalse(context["configuration_verified"])
        self.assertNotIn("configuration_digest", context)
        self.assertNotIn("fake-token", json.dumps(context))


if __name__ == "__main__":
    unittest.main()
