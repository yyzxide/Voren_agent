from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from voren.actions.errors import ApprovalMismatchError, ApprovalRejectedError, InvalidOperationStateError
from voren.actions.gateway import ActionGateway
from voren.actions.ledger import SQLiteOperationLedger
from voren.actions.models import ApprovalDecision, ReceiptStatus
from voren.adapters.fake_workspace import FakeWorkspaceAdapter
from voren.adapters.workspace_contracts import WORKSPACE_CONTRACT_VERSION, create_calendar_event_definition, send_email_definition
from voren.runs.manager import RunManager
from voren.runs.models import RunConfig, RunEventType, RunStatus
from voren.runs.store import SQLiteRunStore
from voren.runtime.agent_loop import AgentLoop
from voren.runtime.models import MessageRole, ModelResponse, ModelUsage, RuntimeLimits, RuntimeResultStatus, ToolCall
from voren.runtime.tools import external_action_tool
from voren.runtime.transcripts import SQLiteTranscriptStore, TranscriptCheckpoint, TranscriptKeyError
from voren.testing.scripted_model import ScriptedModelAdapter


class NoReads:
    definitions = ()

    def execute(self, **kwargs):
        raise AssertionError("this workflow should not read external data")


class DelayedWorkspace(FakeWorkspaceAdapter):
    visible = False

    def observe(self, proposal):
        return super().observe(proposal) if self.visible else ()


class MultiActionRuntimeTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "runs.sqlite3"
        self.world = FakeWorkspaceAdapter()
        self.resources = []
        self.addCleanup(self.close)

    def close(self):
        while self.resources:
            self.resources.pop().close()

    @staticmethod
    def config():
        return RunConfig(
            workflow="two_actions", world_adapter="recoverable_test_workspace",
            policy_version="test-v2", action_contract_versions=(WORKSPACE_CONTRACT_VERSION,),
            continue_after_action=True,
        )

    @staticmethod
    def calendar(call_id="calendar-1"):
        return ModelResponse(tool_calls=(ToolCall(
            call_id=call_id, name="create_calendar_event", arguments={
                "title": "Project review", "start_time": "2026-09-28 10:00",
                "end_time": "2026-09-28 10:30", "participants": ["alice@example.com"],
            },
        ),), usage=ModelUsage(input_tokens=10, output_tokens=2, total_tokens=12))

    @staticmethod
    def email(call_id="email-2"):
        return ModelResponse(tool_calls=(ToolCall(
            call_id=call_id, name="send_email", arguments={
                "recipients": ["alice@example.com"], "subject": "Review confirmed",
                "body": "The review is scheduled.",
            },
        ),), usage=ModelUsage(input_tokens=20, output_tokens=3, total_tokens=23))

    @staticmethod
    def final():
        return ModelResponse(text="The meeting and confirmation are verified.",
                             usage=ModelUsage(input_tokens=30, output_tokens=4, total_tokens=34))

    def open(self, responses=(), *, limits=None):
        self.close()
        ledger = SQLiteOperationLedger(self.database)
        store = SQLiteRunStore(self.database)
        transcripts = SQLiteTranscriptStore(self.database, key=b"t" * 32)
        self.resources.extend((ledger, store, transcripts))
        definitions = (create_calendar_event_definition(), send_email_definition())
        manager = RunManager(store=store, operation_ledger=ledger,
                             action_gateway=ActionGateway(definitions=definitions, adapter=self.world, ledger=ledger))
        model = ScriptedModelAdapter(tuple(responses))
        loop = AgentLoop(model=model, read_tools=NoReads(),
                         action_tools=tuple(external_action_tool(d, description=d.name) for d in definitions),
                         run_manager=manager, transcript_store=transcripts, limits=limits)
        return loop, manager, model, store, transcripts

    @staticmethod
    def approval(result, *, approved=True):
        proposal = result.pending_proposal
        return ApprovalDecision.for_proposal(proposal, approval_id="approval-" + proposal.operation_id,
                                              decided_by="operator:test", approved=approved)

    def test_two_separate_approvals_and_final_summary_survive_reconstruction(self):
        loop, manager, _, _, _ = self.open((self.calendar(),))
        first = loop.run(user_request="Schedule and send confirmation", config=self.config())
        self.assertEqual(self.world.commit_attempts, 0)
        loop, manager, model, _, _ = self.open((self.email(),))
        restored = loop.resume(first.run_id)
        self.assertEqual(restored.pending_proposal, first.pending_proposal)
        self.assertEqual(model.requests, [])
        first_approval = self.approval(first)
        manager.resume_with_approval(first.run_id, first_approval)
        self.assertEqual(manager.get_run(first.run_id).status, RunStatus.RUNNING)
        second = loop.resume(first.run_id)
        self.assertEqual(second.run_id, first.run_id)
        self.assertEqual(second.status, RuntimeResultStatus.WAITING_APPROVAL)
        self.assertEqual(self.world.commit_attempts, 1)
        receipt_message = model.requests[0][0][-1]
        self.assertEqual(receipt_message.content["receipt"]["status"], "verified")
        self.assertFalse(receipt_message.content["instruction_authority"])
        with self.assertRaises(ApprovalMismatchError):
            manager.resume_with_approval(second.run_id, first_approval)
        self.assertEqual(manager.get_run(second.run_id).pending_operation_id, second.pending_proposal.operation_id)
        manager.resume_with_approval(second.run_id, self.approval(second))
        loop, manager, model, store, transcripts = self.open((self.final(),))
        completed = loop.resume(first.run_id)
        self.assertEqual(completed.status, RuntimeResultStatus.COMPLETED)
        self.assertEqual((completed.model_steps, completed.tool_calls, completed.usage.total_tokens), (3, 2, 69))
        self.assertEqual(self.world.commit_attempts, 2)
        self.assertEqual(len([e for e in store.list_events(first.run_id) if e.event_type is RunEventType.RUN_COMPLETED]), 1)
        self.assertEqual(len([m for m in model.requests[0][0] if m.role is MessageRole.TOOL]), 2)
        self.assertEqual(transcripts.load(first.run_id).schema_version, "voren.transcript.v3")
        loop, _, model, _, _ = self.open()
        self.assertEqual(loop.resume(first.run_id), completed)
        self.assertEqual(model.requests, [])

    def test_restart_after_external_commit_before_run_finalization_never_resends(self):
        loop, manager, _, _, _ = self.open((self.calendar(),))
        result = loop.run(user_request="Schedule", config=self.config())
        with patch.object(manager, "_finalize", side_effect=KeyboardInterrupt("crash after commit")):
            with self.assertRaises(KeyboardInterrupt):
                manager.resume_with_approval(result.run_id, self.approval(result))
        self.assertEqual(self.world.commit_attempts, 1)
        loop, manager, _, _, _ = self.open((self.final(),))
        manager.recover_pending_receipt(result.run_id)
        self.assertEqual(loop.resume(result.run_id).status, RuntimeResultStatus.COMPLETED)
        self.assertEqual(self.world.commit_attempts, 1)

    def test_restart_after_receipt_checkpoint_before_acknowledgement(self):
        loop, manager, _, _, _ = self.open((self.calendar(),))
        result = loop.run(user_request="Schedule", config=self.config())
        manager.resume_with_approval(result.run_id, self.approval(result))
        with patch.object(manager, "acknowledge_action_result", side_effect=KeyboardInterrupt("crash before ack")):
            with self.assertRaises(KeyboardInterrupt):
                loop.resume(result.run_id)
        loop, _, model, _, _ = self.open((self.final(),))
        completed = loop.resume(result.run_id)
        self.assertEqual((completed.model_steps, completed.tool_calls), (2, 1))
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(self.world.commit_attempts, 1)

    def test_ambiguous_action_blocks_continuation_until_verified(self):
        self.world = DelayedWorkspace(failure_mode="after_commit")
        loop, manager, _, _, _ = self.open((self.calendar(),))
        result = loop.run(user_request="Schedule", config=self.config())
        receipt = manager.resume_with_approval(result.run_id, self.approval(result))
        self.assertEqual(receipt.status, ReceiptStatus.AMBIGUOUS)
        loop, manager, model, _, _ = self.open((self.final(),))
        with self.assertRaises(ValueError):
            loop.resume(result.run_id)
        self.assertEqual(model.requests, [])
        self.world.visible = True
        self.assertEqual(manager.reconcile_pending_action(result.run_id).status, ReceiptStatus.VERIFIED)
        self.assertEqual(loop.resume(result.run_id).status, RuntimeResultStatus.COMPLETED)
        self.assertEqual(self.world.commit_attempts, 1)

    def test_second_rejection_keeps_first_action_and_stops_run(self):
        loop, manager, _, _, _ = self.open((self.calendar(), self.email()))
        first = loop.run(user_request="Schedule and confirm", config=self.config())
        manager.resume_with_approval(first.run_id, self.approval(first))
        second = loop.resume(first.run_id)
        with self.assertRaises(ApprovalRejectedError):
            manager.resume_with_approval(first.run_id, self.approval(second, approved=False))
        self.assertEqual(manager.get_run(first.run_id).status, RunStatus.CANCELLED)
        self.assertEqual(self.world.commit_attempts, 1)
        self.assertEqual(len(self.world.events), 1)

    def test_budget_cannot_be_reset_or_increased_on_resume(self):
        limits = RuntimeLimits(max_tool_calls=1)
        loop, manager, _, _, _ = self.open((self.calendar(),), limits=limits)
        first = loop.run(user_request="Schedule and confirm", config=self.config())
        manager.resume_with_approval(first.run_id, self.approval(first))
        loop, _, model, _, _ = self.open((self.email(),))
        with self.assertRaisesRegex(ValueError, "runtime limits differ"):
            loop.resume(first.run_id)
        self.assertEqual(model.requests, [])
        loop, _, _, _, _ = self.open((self.email(),), limits=limits)
        failed = loop.resume(first.run_id)
        self.assertEqual(failed.status, RuntimeResultStatus.LIMIT_EXCEEDED)
        self.assertEqual((failed.model_steps, failed.tool_calls), (2, 1))
        self.assertEqual(self.world.commit_attempts, 1)

    def test_stale_finalization_cannot_complete_a_later_pending_proposal(self):
        loop, manager, _, _, _ = self.open((self.calendar(), self.email()))
        first = loop.run(user_request="Schedule and confirm", config=self.config())
        old_run = manager.get_run(first.run_id)
        first_receipt = manager.resume_with_approval(first.run_id, self.approval(first))
        second = loop.resume(first.run_id)
        with self.assertRaises(InvalidOperationStateError):
            manager._finalize(old_run, first_receipt)
        current = manager.get_run(first.run_id)
        self.assertEqual(current.status, RunStatus.WAITING_APPROVAL)
        self.assertEqual(current.pending_operation_id, second.pending_proposal.operation_id)

    def test_checkpoint_cannot_substitute_a_different_pending_call(self):
        loop, manager, _, _, transcripts = self.open((self.calendar(),))
        first = loop.run(user_request="Schedule", config=self.config())
        checkpoint = transcripts.load(first.run_id)
        transcripts.save(checkpoint.model_copy(update={"pending_response": self.email()}))
        with self.assertRaisesRegex(InvalidOperationStateError, "checkpoint call"):
            loop.resume(first.run_id)
        self.assertEqual(self.world.commit_attempts, 0)

    def test_multi_action_requires_encrypted_storage_before_starting(self):
        loop, _, _, store, _ = self.open((self.calendar(),))
        loop._transcript_store = None
        with self.assertRaisesRegex(ValueError, "encrypted transcript"):
            loop.run(user_request="Schedule", config=self.config())
        self.assertEqual(store._connection.execute("SELECT count(*) FROM runs").fetchone()[0], 0)

    def test_interrupted_model_request_spends_its_reserved_attempt(self):
        limits = RuntimeLimits(max_model_steps=1)
        loop, _, model, store, transcripts = self.open(limits=limits)
        with patch.object(model, "complete", side_effect=KeyboardInterrupt("response not saved")):
            with self.assertRaises(KeyboardInterrupt):
                loop.run(user_request="Schedule", config=self.config())
        run_id = store._connection.execute("SELECT run_id FROM runs").fetchone()[0]
        checkpoint = transcripts.load(run_id)
        self.assertEqual((checkpoint.usage.model_requests, checkpoint.next_model_step), (1, 2))
        loop, _, model, _, _ = self.open((self.calendar(),), limits=limits)
        exhausted = loop.resume(run_id)
        self.assertEqual(exhausted.status, RuntimeResultStatus.LIMIT_EXCEEDED)
        self.assertEqual(exhausted.model_steps, 1)
        self.assertFalse(exhausted.usage.complete)
        self.assertEqual(model.requests, [])
        self.assertEqual(self.world.commit_attempts, 0)

    def test_only_one_loop_executor_can_use_a_database_at_a_time(self):
        loop, _, _, store, transcripts = self.open((self.calendar(),))
        other = SQLiteTranscriptStore(self.database, key=b"t" * 32)
        try:
            with other.execution_lock():
                with self.assertRaisesRegex(InvalidOperationStateError, "another loop"):
                    loop.run(user_request="Schedule", config=self.config())
                self.assertEqual(store._connection.execute("SELECT count(*) FROM runs").fetchone()[0], 0)
            first = loop.run(user_request="Schedule", config=self.config())
            with other.execution_lock():
                with self.assertRaisesRegex(InvalidOperationStateError, "another loop"):
                    loop.resume(first.run_id)
            self.assertEqual(loop.resume(first.run_id).pending_proposal, first.pending_proposal)
        finally:
            other.close()

    def test_v2_checkpoint_preserves_its_original_digest(self):
        loop, _, _, _, transcripts = self.open((self.calendar(),))
        first = loop.run(user_request="Schedule", config=self.config())
        payload = transcripts.load(first.run_id).model_dump(mode="json")
        payload["schema_version"] = "voren.transcript.v2"
        expected = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        checkpoint = TranscriptCheckpoint.model_validate(payload)
        self.assertEqual(checkpoint.calculated_digest(), expected)
        transcripts.save(checkpoint)
        self.assertEqual(transcripts.load(first.run_id).calculated_digest(), expected)

    def test_local_key_is_private_persistent_and_loss_does_not_rotate(self):
        with patch.dict(os.environ, {}, clear=True):
            ledger = SQLiteOperationLedger(self.database)
            store = SQLiteRunStore(self.database)
            self.resources.extend((ledger, store))
            first = SQLiteTranscriptStore.from_local_key(self.database)
            key_id = first.key_id
            first.close()
            key_path = self.database.with_name(self.database.name + ".transcript.key")
            self.assertEqual(key_path.stat().st_mode & 0o777, 0o600)
            second = SQLiteTranscriptStore.from_local_key(self.database)
            self.assertEqual(second.key_id, key_id)
            second.close()
            key_path.chmod(0o644)
            with self.assertRaises(TranscriptKeyError):
                SQLiteTranscriptStore.from_local_key(self.database)
            key_path.chmod(0o600)
            loop, _, _, _, _ = self.open((self.calendar(),))
            loop.run(user_request="Schedule", config=self.config())
            key_path.write_text("")
            with self.assertRaisesRegex(TranscriptKeyError, "empty"):
                SQLiteTranscriptStore.from_local_key(self.database)
            self.assertEqual(key_path.read_text(), "")
            key_path.unlink()
            with self.assertRaisesRegex(TranscriptKeyError, "missing"):
                SQLiteTranscriptStore.from_local_key(self.database)
            self.assertFalse(key_path.exists())


if __name__ == "__main__":
    unittest.main()
