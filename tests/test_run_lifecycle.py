from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from voren.actions.errors import (
    ApprovalMismatchError,
    ApprovalRejectedError,
    InvalidOperationStateError,
)
from voren.actions.gateway import ActionGateway
from voren.actions.ledger import SQLiteOperationLedger
from voren.actions.models import ApprovalDecision, ReceiptStatus
from voren.adapters.fake_workspace import FakeWorkspaceAdapter
from voren.adapters.workspace_contracts import (
    WORKSPACE_CONTRACT_VERSION,
    create_calendar_event_definition,
)
from voren.runs.manager import RunManager
from voren.runs.models import NewRunEvent, RunConfig, RunEventType, RunStatus
from voren.runs.store import SQLiteRunStore


class RunLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = Path(self.temporary_directory.name) / "voren.sqlite3"
        self.now = datetime(2026, 8, 30, 14, 0, tzinfo=UTC)
        self.event_number = 0
        self.ledger, self.store, self.adapter, self.gateway, self.manager = (
            self._open_stack()
        )

    def _open_stack(self, *, failure_mode="none"):
        ledger = SQLiteOperationLedger(self.database_path)
        store = SQLiteRunStore(self.database_path)
        adapter = FakeWorkspaceAdapter(failure_mode=failure_mode)
        gateway = ActionGateway(
            definitions=(create_calendar_event_definition(),),
            adapter=adapter,
            ledger=ledger,
            id_factory=lambda: "operation-run-1",
            clock=lambda: self.now,
        )

        def next_event_id() -> str:
            self.event_number += 1
            return f"event-{self.event_number}"

        manager = RunManager(
            store=store,
            operation_ledger=ledger,
            action_gateway=gateway,
            run_id_factory=lambda: "run-1",
            event_id_factory=next_event_id,
            clock=lambda: self.now,
        )
        self.addCleanup(ledger.close)
        self.addCleanup(store.close)
        return ledger, store, adapter, gateway, manager

    @staticmethod
    def _config() -> RunConfig:
        return RunConfig(
            workflow="single_calendar_action",
            world_adapter="fake_workspace",
            policy_version="exact-effects-v1",
            action_contract_versions=(WORKSPACE_CONTRACT_VERSION,),
            metadata={"case": "user_task_18"},
        )

    @staticmethod
    def _arguments() -> dict:
        return {
            "title": "Hiking Trip",
            "location": "island trailhead",
            "start_time": "2024-05-18 08:00",
            "end_time": "2024-05-18 13:00",
            "participants": ["mark.davies@hotmail.com"],
        }

    def _start_and_propose(self):
        run = self.manager.start_new_run(self._config())
        proposal = self.manager.propose_action(
            run.run_id, "create_calendar_event", self._arguments()
        )
        return run, proposal

    def _approval(self, proposal, *, approved=True, approval_id="approval-1"):
        return ApprovalDecision.for_proposal(
            proposal,
            approval_id=approval_id,
            decided_by="operator:sid",
            approved=approved,
            decided_at=self.now,
            ttl=timedelta(minutes=5),
        )

    def test_happy_path_has_ordered_append_only_events(self) -> None:
        run, proposal = self._start_and_propose()

        receipt = self.manager.resume_with_approval(
            run.run_id, self._approval(proposal)
        )

        completed = self.store.get_run(run.run_id)
        events = self.store.list_events(run.run_id)
        self.assertEqual(receipt.status, ReceiptStatus.VERIFIED)
        self.assertEqual(completed.status, RunStatus.COMPLETED)
        self.assertEqual(completed.last_receipt_status, "verified")
        self.assertIsNone(completed.pending_operation_id)
        self.assertEqual(
            [event.event_type for event in events],
            [
                RunEventType.RUN_CREATED,
                RunEventType.RUN_STARTED,
                RunEventType.ACTION_PROPOSED,
                RunEventType.RUN_WAITING_APPROVAL,
                RunEventType.APPROVAL_ACCEPTED,
                RunEventType.ACTION_RECEIPT,
                RunEventType.RUN_COMPLETED,
            ],
        )
        self.assertEqual(
            [event.sequence for event in events],
            sorted(event.sequence for event in events),
        )
        event_payloads = json.dumps(
            [event.payload for event in events], ensure_ascii=False
        )
        self.assertNotIn("Hiking Trip", event_payloads)
        self.assertNotIn("mark.davies@hotmail.com", event_payloads)

    def test_waiting_run_resumes_after_store_and_ledger_reopen(self) -> None:
        run, proposal = self._start_and_propose()
        self.ledger.close()
        self.store.close()
        ledger, store, adapter, _, manager = self._open_stack()

        receipt = manager.resume_with_approval(run.run_id, self._approval(proposal))

        self.assertEqual(receipt.status, ReceiptStatus.VERIFIED)
        self.assertEqual(store.get_run(run.run_id).status, RunStatus.COMPLETED)
        self.assertEqual(len(adapter.events), 1)
        self.assertEqual(len(adapter.emails), 1)
        self.assertEqual(ledger.get_receipt(proposal.operation_id), receipt)

    def test_rejected_approval_cancels_without_external_effect(self) -> None:
        run, proposal = self._start_and_propose()

        with self.assertRaises(ApprovalRejectedError):
            self.manager.resume_with_approval(
                run.run_id, self._approval(proposal, approved=False)
            )

        self.assertEqual(self.store.get_run(run.run_id).status, RunStatus.CANCELLED)
        self.assertEqual(self.adapter.commit_attempts, 0)
        self.assertEqual(
            self.store.list_events(run.run_id)[-1].event_type,
            RunEventType.RUN_CANCELLED,
        )

    def test_invalid_approval_is_audited_and_run_keeps_waiting(self) -> None:
        run, proposal = self._start_and_propose()
        invalid = self._approval(proposal).model_copy(
            update={"proposal_digest": "0" * 64}
        )

        with self.assertRaises(ApprovalMismatchError):
            self.manager.resume_with_approval(run.run_id, invalid)

        self.assertEqual(
            self.store.get_run(run.run_id).status, RunStatus.WAITING_APPROVAL
        )
        self.assertEqual(
            self.store.list_events(run.run_id)[-1].event_type,
            RunEventType.APPROVAL_INVALID,
        )
        self.assertEqual(self.adapter.commit_attempts, 0)

    def test_ambiguous_receipt_requires_reconciliation(self) -> None:
        self.ledger.close()
        self.store.close()
        self.ledger, self.store, self.adapter, self.gateway, self.manager = (
            self._open_stack(failure_mode="ambiguous_without_commit")
        )
        run, proposal = self._start_and_propose()

        receipt = self.manager.resume_with_approval(
            run.run_id, self._approval(proposal)
        )

        self.assertEqual(receipt.status, ReceiptStatus.AMBIGUOUS)
        self.assertEqual(
            self.store.get_run(run.run_id).status, RunStatus.NEEDS_RECONCILIATION
        )
        self.assertEqual(
            self.store.list_events(run.run_id)[-1].event_type,
            RunEventType.RUN_NEEDS_RECONCILIATION,
        )

    def test_durable_receipt_finalizes_run_after_simulated_process_crash(self) -> None:
        run, proposal = self._start_and_propose()
        approval = self._approval(proposal)
        authorized = self.gateway.authorize(proposal, approval)
        committed_receipt = self.gateway.commit(authorized)
        self.assertEqual(
            self.store.get_run(run.run_id).status, RunStatus.WAITING_APPROVAL
        )

        recovered_receipt = self.manager.recover_pending_receipt(run.run_id)

        self.assertEqual(recovered_receipt, committed_receipt)
        self.assertEqual(self.store.get_run(run.run_id).status, RunStatus.COMPLETED)
        self.assertEqual(self.adapter.commit_attempts, 1)
        self.assertIn(
            RunEventType.APPROVAL_ACCEPTED,
            [event.event_type for event in self.store.list_events(run.run_id)],
        )

    def test_resume_is_idempotent_after_approval_was_already_persisted(self) -> None:
        run, proposal = self._start_and_propose()
        approval = self._approval(proposal)
        self.gateway.authorize(proposal, approval)

        receipt = self.manager.resume_with_approval(run.run_id, approval)

        self.assertEqual(receipt.status, ReceiptStatus.VERIFIED)
        self.assertEqual(self.adapter.commit_attempts, 1)
        accepted_events = [
            event
            for event in self.store.list_events(run.run_id)
            if event.event_type is RunEventType.APPROVAL_ACCEPTED
        ]
        self.assertEqual(len(accepted_events), 1)

    def test_event_dedupe_key_requires_identical_content(self) -> None:
        run = self.manager.start_new_run(self._config())
        event = NewRunEvent(
            event_id="manual-event-1",
            dedupe_key="manual:test",
            event_type=RunEventType.APPROVAL_INVALID,
            payload={"reason": "first"},
            occurred_at=self.now,
        )
        self.store.append_events(run.run_id, (event,))
        self.store.append_events(
            run.run_id,
            (event.model_copy(update={"event_id": "manual-event-2"}),),
        )
        event_count = len(self.store.list_events(run.run_id))

        with self.assertRaises(InvalidOperationStateError):
            self.store.append_events(
                run.run_id,
                (
                    event.model_copy(
                        update={
                            "event_id": "manual-event-3",
                            "payload": {"reason": "different"},
                        }
                    ),
                ),
            )

        self.assertEqual(len(self.store.list_events(run.run_id)), event_count)


if __name__ == "__main__":
    unittest.main()
