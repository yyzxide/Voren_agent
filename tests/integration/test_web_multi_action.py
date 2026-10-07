from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.integration.test_web_app import WEB_AVAILABLE

if WEB_AVAILABLE:
    from tests.integration.test_web_app import TranscriptScriptedModel
    from tests.test_google_workspace_connector import FakeGoogleTransport
    from voren.adapters.google_workspace import GoogleWorkspaceConfig, GoogleWorkspaceConnector
    from voren.runtime.models import ModelResponse, RuntimeLimits, ToolCall
    from voren.web.models import CreateRunRequest, DecideRunRequest
    from voren.web.service import VorenWebService, WebApprovalRecoveryRequiredError, WebDecisionConflictError, create_google_web_workspace


@unittest.skipUnless(WEB_AVAILABLE, "web dependencies are unavailable")
class WebMultiActionIntegrationTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "web.sqlite3"
        self.now = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
        self.transport = FakeGoogleTransport()
        self.responses = (
            ModelResponse(tool_calls=(ToolCall(
                call_id="private-hold", name="create_private_calendar_event",
                arguments={"title": "Review", "start_time": "2026-09-27T09:00:00+08:00", "end_time": "2026-09-27T09:30:00+08:00"},
            ),)),
            ModelResponse(tool_calls=(ToolCall(
                call_id="reply-draft", name="create_email_draft",
                arguments={"recipients": ["alice@example.com"], "subject": "Review", "body": "A private hold has been created; please review this draft."},
            ),)),
            ModelResponse(text="Created the private hold and reply draft; both were verified."),
        )

    def service(self, *, workspace_config=None, **kwargs):
        config = workspace_config or GoogleWorkspaceConfig(access_token="test-token", account_email="sid@example.com", time_zone="Asia/Shanghai")
        return VorenWebService(
            database=self.database, model_factory=lambda: TranscriptScriptedModel(self.responses),
            mode="live", workspace_name="google", workspace_recoverable=True,
            workspace_factory=lambda: create_google_web_workspace(connector=GoogleWorkspaceConnector(
                config, transport=self.transport, clock=lambda: self.now,
            )), clock=lambda: self.now, **kwargs,
        )

    def submit(self, service):
        return service.submit(CreateRunRequest(client_request_id="multi-action-request", request="Create a private hold, then prepare a reply draft. Approve each action separately."))

    @staticmethod
    def decision(view, sequence):
        return DecideRunRequest(decision_id=f"multi-decision-{sequence}", proposal_digest=view.proposal.digest, approved=True)

    def writes(self):
        return sum(method == "POST" for method, _, _ in self.transport.calls)

    def test_two_actions_resume_after_restart_with_independent_approvals(self):
        first = self.service()
        pending = self.submit(first)
        approval_one = self.decision(pending, 1)
        self.assertEqual(first.resume(pending.run_id).status, "waiting_approval")
        self.assertEqual(self.writes(), 0)
        # New process settings cannot reduce the limits frozen in this Run.
        second = self.service(limits=RuntimeLimits(max_model_steps=1))
        with patch.object(second, "_select_memory_context", side_effect=AssertionError("must use frozen refs")), patch.object(second, "_select_skill_context", side_effect=AssertionError("must use frozen refs")):
            next_action = second.decide(pending.run_id, approval_one)
        self.assertEqual(next_action.status, "waiting_approval")
        self.assertEqual(self.writes(), 1)
        self.assertNotEqual(next_action.proposal.digest, pending.proposal.digest)
        self.assertIsNone(next_action.decision_id)
        self.assertIsNone(next_action.action_history[1].receipt)
        self.assertEqual(next_action.action_history[0].receipt.status.value, "verified")
        self.assertEqual(second.decide(pending.run_id, approval_one), next_action)
        self.assertEqual(self.writes(), 1)
        with self.assertRaises(WebDecisionConflictError):
            second.decide(pending.run_id, approval_one.model_copy(update={"proposal_digest": next_action.proposal.digest}))
        third = self.service()
        final = third.decide(pending.run_id, self.decision(next_action, 2))
        self.assertEqual(final.status, "completed")
        self.assertIn("both were verified", final.final_text)
        self.assertIsNone(final.proposal)
        self.assertEqual(len(final.action_history), 2)
        self.assertTrue(all(item.receipt.verification.passed for item in final.action_history))
        self.assertEqual(final.usage.model_requests, 3)
        self.assertEqual(self.writes(), 2)
        self.assertEqual(third.resume(pending.run_id), final)

    def test_lost_next_proposal_response_does_not_reapply_old_approval(self):
        service = self.service()
        pending = self.submit(service)
        approval = self.decision(pending, 1)
        save = service._index.save
        def lose_next_proposal(view):
            if len(view.action_history) == 2:
                raise KeyboardInterrupt("lost next proposal HTTP response")
            save(view)
        with patch.object(service._index, "save", side_effect=lose_next_proposal):
            with self.assertRaises(KeyboardInterrupt):
                service.decide(pending.run_id, approval)
        restarted = self.service()
        recovered = restarted.decide(pending.run_id, approval)
        self.assertEqual(recovered.status, "waiting_approval")
        self.assertEqual(recovered.proposal.action_name, "create_email_draft")
        self.assertIsNone(recovered.decision_id)
        self.assertEqual(self.writes(), 1)
        self.assertEqual(restarted.resume(pending.run_id), recovered)

    def test_resume_after_verified_receipt_does_not_send_again(self):
        service = self.service()
        pending = self.submit(service)
        with patch.object(service, "_resume_loop", side_effect=KeyboardInterrupt("after receipt")):
            with self.assertRaises(KeyboardInterrupt):
                service.decide(pending.run_id, self.decision(pending, 1))
        self.assertEqual(service.get(pending.run_id).status, "running")
        next_action = self.service().resume(pending.run_id)
        self.assertEqual(next_action.status, "waiting_approval")
        self.assertEqual(next_action.proposal.action_name, "create_email_draft")
        self.assertEqual(self.writes(), 1)

    def test_initial_model_interruption_keeps_request_identity_and_checkpoint(self):
        service = self.service()
        with patch.object(TranscriptScriptedModel, "complete", side_effect=KeyboardInterrupt("model request interrupted")):
            with self.assertRaises(KeyboardInterrupt):
                self.submit(service)
        restarted = self.service()
        interrupted = self.submit(restarted)
        self.assertEqual(interrupted.status, "running")
        resumed = restarted.resume(interrupted.run_id)
        self.assertEqual(resumed.status, "waiting_approval")
        self.assertEqual(resumed.run_id, interrupted.run_id)
        self.assertEqual(self.writes(), 0)

    def test_terminal_text_recovers_without_model_or_workspace(self):
        service = self.service()
        pending = self.submit(service)
        next_action = service.decide(pending.run_id, self.decision(pending, 1))
        save = service._index.save
        def lose_final(view):
            if view.status == "completed":
                raise KeyboardInterrupt("lost final response")
            save(view)
        with patch.object(service._index, "save", side_effect=lose_final):
            with self.assertRaises(KeyboardInterrupt):
                service.decide(pending.run_id, self.decision(next_action, 2))
        restarted = self.service()
        with patch.object(restarted, "_create_workspace", side_effect=AssertionError("must not reconnect")), patch.object(restarted, "_model_factory", side_effect=AssertionError("must not call model")):
            final = restarted.resume(pending.run_id)
        self.assertEqual(final.final_text, self.responses[-1].text)
        self.assertEqual(final.status, "completed")
        self.assertEqual(self.writes(), 2)

    def test_parallel_old_decision_retries_cannot_approve_next_action(self):
        service = self.service()
        pending = self.submit(service)
        approval = self.decision(pending, 1)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = tuple(pool.map(lambda _: service.decide(pending.run_id, approval), range(2)))
        self.assertTrue(all(item.status == "waiting_approval" for item in responses))
        self.assertTrue(all(item.proposal.action_name == "create_email_draft" for item in responses))
        self.assertEqual(self.writes(), 1)

    def test_restart_cannot_move_old_approval_to_another_workspace_identity(self):
        pending = self.submit(self.service())
        approval = self.decision(pending, 1)
        original = GoogleWorkspaceConfig(access_token="rotated-token", account_email="sid@example.com", time_zone="Asia/Shanghai")
        for changed in (
            {"account_email": "other@example.com"},
            {"calendar_id": "other-calendar"},
            {"time_zone": "UTC"},
        ):
            with self.subTest(changed=changed):
                restarted = self.service(workspace_config=replace(original, **changed))
                self.assertTrue(restarted.get(pending.run_id).recovery_required)
                with self.assertRaisesRegex(WebApprovalRecoveryRequiredError, "workspace identity"):
                    restarted.decide(pending.run_id, approval)
                self.assertEqual(self.writes(), 0)
        # Credential rotation is allowed when the declared workspace identity is unchanged.
        self.assertEqual(self.service(workspace_config=original).decide(pending.run_id, approval).status, "waiting_approval")
        self.assertEqual(self.writes(), 1)

    def test_second_action_rejection_survives_restart_and_idempotent_retry(self):
        service = self.service()
        pending = self.submit(service)
        next_action = service.decide(pending.run_id, self.decision(pending, 1))
        rejection = self.decision(next_action, 2).model_copy(update={"approved": False})
        rejected = service.decide(pending.run_id, rejection)
        self.assertEqual(rejected.status, "cancelled")
        self.assertIs(rejected.decision_approved, False)
        self.assertEqual(rejected.decision_id, rejection.decision_id)
        self.assertIs(rejected.action_history[1].decision_approved, False)
        self.assertIsNone(rejected.action_history[1].receipt)
        self.assertTrue(rejected.action_history[0].receipt.verification.passed)
        restarted = self.service()
        self.assertEqual(restarted.get(pending.run_id), rejected)
        self.assertEqual(restarted.decide(pending.run_id, rejection), rejected)
        self.assertEqual(self.writes(), 1)


if __name__ == "__main__":
    unittest.main()
