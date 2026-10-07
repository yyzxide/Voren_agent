from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from voren.actions.errors import ApprovalRejectedError
from voren.actions.gateway import ActionGateway
from voren.actions.ledger import SQLiteOperationLedger
from voren.actions.models import ApprovalDecision
from voren.adapters.fake_workspace import FakeWorkspaceAdapter
from voren.adapters.workspace_contracts import WORKSPACE_CONTRACT_VERSION, send_email_definition
from voren.learning.evidence import DurableLearningRouter, LearningEvidenceRoutingError
from voren.learning.store import SQLiteCandidateStore
from voren.runs.manager import RunManager
from voren.runs.models import RunConfig, RunStatus
from voren.runs.store import SQLiteRunStore
from voren.runtime.agent_loop import AgentLoop
from voren.runtime.models import ModelResponse, RuntimeResultStatus, ToolCall
from voren.runtime.tools import external_action_tool
from voren.runtime.transcripts import SQLiteTranscriptStore
from voren.testing.scripted_model import ScriptedModelAdapter


class NoReadTools:
    definitions = ()

    def execute(self, **kwargs):
        raise AssertionError("this workflow has no read calls")


class MultiActionLearningTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    @staticmethod
    def email(number):
        return ModelResponse(tool_calls=(ToolCall(
            call_id=f"email-{number}", name="send_email",
            arguments={
                "recipients": ["alice@example.com"],
                "subject": f"Approved message {number}", "body": f"Message {number}.",
            },
        ),))

    @staticmethod
    def approval(result, *, approved=True):
        proposal = result.pending_proposal
        return ApprovalDecision.for_proposal(
            proposal, approval_id=f"approval-{proposal.operation_id}",
            decided_by="operator:test", approved=approved,
        )

    def stack(self, name, *, final_response=True):
        database = self.root / f"{name}.sqlite3"
        ledger = SQLiteOperationLedger(database)
        runs = SQLiteRunStore(database)
        transcripts = SQLiteTranscriptStore(database, key=b"l" * 32)
        evidence = SQLiteCandidateStore(database)
        for resource in (ledger, runs, transcripts, evidence):
            self.addCleanup(resource.close)
        world = FakeWorkspaceAdapter()
        action = send_email_definition()
        manager = RunManager(
            store=runs, operation_ledger=ledger,
            action_gateway=ActionGateway(definitions=(action,), adapter=world, ledger=ledger),
        )
        responses = (self.email(1), self.email(2))
        if final_response:
            responses += (ModelResponse(text="Both approved messages were verified."),)
        loop = AgentLoop(
            model=ScriptedModelAdapter(responses), read_tools=NoReadTools(),
            action_tools=(external_action_tool(action, description=action.name),),
            run_manager=manager, transcript_store=transcripts,
        )
        router = DurableLearningRouter(evidence_store=evidence, run_store=runs)
        first = loop.run(
            user_request="Send two messages, approving each separately.",
            config=RunConfig(
                workflow="two-approved-emails", world_adapter="fake-workspace",
                policy_version="test", action_contract_versions=(WORKSPACE_CONTRACT_VERSION,),
                continue_after_action=True,
            ),
        )
        manager.resume_with_approval(first.run_id, self.approval(first))
        second = loop.resume(first.run_id)
        return loop, manager, runs, evidence, router, world, second

    def test_two_verified_actions_route_only_after_the_final_response(self):
        loop, manager, runs, evidence, router, world, second = self.stack("success")
        manager.resume_with_approval(second.run_id, self.approval(second))
        self.assertEqual(manager.get_run(second.run_id).status, RunStatus.RUNNING)
        with self.assertRaisesRegex(LearningEvidenceRoutingError, "only a completed Run"):
            router.select_verified_run(evidence_id="run:premature", run_id=second.run_id)

        result = loop.resume(second.run_id)
        self.assertEqual(result.status, RuntimeResultStatus.COMPLETED)
        self.assertEqual(runs.get_run(second.run_id).last_receipt_status, "verified")
        reference = router.select_verified_run(
            evidence_id="run:two-verified-actions", run_id=second.run_id,
        )
        artifact = evidence.get_evidence(reference.evidence_id)
        artifact.assert_integrity()
        payload = json.loads(artifact.payload)
        receipts = [event for event in payload["events"] if event["event_type"] == "action.receipt"]
        self.assertEqual(len(receipts), 2)
        self.assertTrue(all(event["payload"]["status"] == "verified" for event in receipts))
        self.assertEqual(len({event["payload"]["operation_id"] for event in receipts}), 2)
        self.assertEqual(world.commit_attempts, 2)
        self.assertFalse(artifact.instruction_authority)

    def test_partial_failure_or_rejection_cannot_become_verified_learning_evidence(self):
        for failure in ("rejected", "external_failure", "final_model_failure"):
            with self.subTest(failure=failure):
                loop, manager, runs, _, router, world, second = self.stack(
                    failure, final_response=failure != "final_model_failure"
                )
                if failure == "rejected":
                    with self.assertRaises(ApprovalRejectedError):
                        manager.resume_with_approval(
                            second.run_id, self.approval(second, approved=False)
                        )
                    self.assertEqual(len(world.emails), 1)
                else:
                    if failure == "external_failure":
                        world.failure_mode = "before_commit"
                    manager.resume_with_approval(second.run_id, self.approval(second))
                    if failure == "final_model_failure":
                        self.assertEqual(loop.resume(second.run_id).status, RuntimeResultStatus.FAILED)
                    else:
                        self.assertEqual(len(world.emails), 1)
                self.assertIn(runs.get_run(second.run_id).status, {RunStatus.FAILED, RunStatus.CANCELLED})
                with self.assertRaisesRegex(LearningEvidenceRoutingError, "only a completed Run"):
                    router.select_verified_run(evidence_id=f"run:{failure}", run_id=second.run_id)


if __name__ == "__main__":
    unittest.main()
