from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from voren.actions.errors import (
    ApprovalMismatchError,
    InvalidProposalError,
    StaleApprovalError,
)
from voren.actions.gateway import ActionGateway
from voren.actions.ledger import SQLiteOperationLedger
from voren.actions.models import ApprovalDecision, ReceiptStatus
from voren.adapters.fake_workspace import FakeWorkspaceAdapter
from voren.adapters.workspace_contracts import create_calendar_event_definition


class ActionGatewayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.ledger_path = Path(self.temporary_directory.name) / "operations.sqlite3"
        self.ledger = SQLiteOperationLedger(self.ledger_path)
        self.addCleanup(self.ledger.close)
        self.adapter = FakeWorkspaceAdapter()
        self.operation_number = 0
        self.now = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
        self.gateway = self._new_gateway(self.adapter)

    def _new_gateway(self, adapter: FakeWorkspaceAdapter) -> ActionGateway:
        def next_operation_id() -> str:
            self.operation_number += 1
            return f"operation-{self.operation_number}"

        return ActionGateway(
            definitions=(create_calendar_event_definition(),),
            adapter=adapter,
            ledger=self.ledger,
            id_factory=next_operation_id,
            clock=lambda: self.now,
        )

    @staticmethod
    def _arguments() -> dict:
        return {
            "title": "Hiking Trip",
            "description": "Bring water.",
            "start_time": "2024-05-18 08:00",
            "end_time": "2024-05-18 13:00",
            "location": "island trailhead",
            "participants": ["mark.davies@hotmail.com"],
        }

    def _prepare(self):
        return self.gateway.prepare("create_calendar_event", self._arguments())

    def _approval(self, proposal, *, approval_id: str = "approval-1", ttl=timedelta(minutes=5)):
        return ApprovalDecision.for_proposal(
            proposal,
            approval_id=approval_id,
            decided_by="operator:sid",
            decided_at=self.now,
            ttl=ttl,
        )

    def test_prepare_materializes_calendar_and_email_effects(self) -> None:
        proposal = self._prepare()

        self.assertEqual(
            [effect.effect_id for effect in proposal.effects],
            ["calendar_event", "invitation_email"],
        )
        self.assertEqual(proposal.effects[0].resource, "calendar.events")
        self.assertEqual(proposal.effects[1].resource, "inbox.emails")
        self.assertEqual(
            proposal.effects[1].attributes["recipients"],
            [
                "emma.johnson@bluesparrowtech.com",
                "mark.davies@hotmail.com",
            ],
        )
        self.assertEqual(proposal.digest, proposal.calculated_digest())

    def test_approval_for_another_proposal_is_rejected(self) -> None:
        first = self._prepare()
        second = self._prepare()
        approval = self._approval(first)

        with self.assertRaises(ApprovalMismatchError):
            self.gateway.authorize(second, approval)
        self.assertEqual(self.adapter.commit_attempts, 0)

    def test_nested_proposal_change_invalidates_digest(self) -> None:
        proposal = self._prepare()
        approval = self._approval(proposal)

        proposal.arguments["title"] = "Injected title"

        with self.assertRaises(InvalidProposalError):
            self.gateway.authorize(proposal, approval)
        self.assertEqual(self.adapter.commit_attempts, 0)

    def test_stale_approval_cannot_authorize(self) -> None:
        proposal = self._prepare()
        approval = self._approval(proposal, ttl=timedelta(seconds=-1))

        with self.assertRaises(StaleApprovalError):
            self.gateway.authorize(proposal, approval)
        self.assertEqual(self.adapter.commit_attempts, 0)

    def test_authorized_commit_is_verified_from_world_state(self) -> None:
        proposal = self._prepare()
        authorized = self.gateway.authorize(proposal, self._approval(proposal))

        receipt = self.gateway.commit(authorized)

        self.assertEqual(receipt.status, ReceiptStatus.VERIFIED)
        self.assertTrue(receipt.committed)
        self.assertTrue(receipt.verification.passed)
        self.assertEqual(len(receipt.observed_effects), 2)
        self.assertEqual(len(self.adapter.events), 1)
        self.assertEqual(len(self.adapter.emails), 1)
        self.assertEqual(self.ledger.get_status(proposal.operation_id), "verified")

    def test_duplicate_commit_returns_receipt_without_duplicate_effects(self) -> None:
        proposal = self._prepare()
        authorized = self.gateway.authorize(proposal, self._approval(proposal))

        first_receipt = self.gateway.commit(authorized)
        second_receipt = self.gateway.commit(authorized)

        self.assertEqual(first_receipt, second_receipt)
        self.assertEqual(self.adapter.commit_attempts, 1)
        self.assertEqual(len(self.adapter.events), 1)
        self.assertEqual(len(self.adapter.emails), 1)

    def test_timeout_after_commit_recovers_by_observing_state(self) -> None:
        adapter = FakeWorkspaceAdapter(failure_mode="after_commit")
        gateway = self._new_gateway(adapter)
        proposal = gateway.prepare("create_calendar_event", self._arguments())
        authorized = gateway.authorize(proposal, self._approval(proposal))

        receipt = gateway.commit(authorized)

        self.assertEqual(receipt.status, ReceiptStatus.VERIFIED)
        self.assertTrue(receipt.recovered_after_ambiguous_commit)
        self.assertEqual(adapter.commit_attempts, 1)
        self.assertEqual(len(adapter.events), 1)
        self.assertEqual(len(adapter.emails), 1)

    def test_unresolved_ambiguous_commit_is_not_retried(self) -> None:
        adapter = FakeWorkspaceAdapter(failure_mode="ambiguous_without_commit")
        gateway = self._new_gateway(adapter)
        proposal = gateway.prepare("create_calendar_event", self._arguments())
        authorized = gateway.authorize(proposal, self._approval(proposal))

        first_receipt = gateway.commit(authorized)
        second_receipt = gateway.commit(authorized)

        self.assertEqual(first_receipt.status, ReceiptStatus.AMBIGUOUS)
        self.assertIsNone(first_receipt.committed)
        self.assertFalse(first_receipt.verification.passed)
        self.assertEqual(first_receipt, second_receipt)
        self.assertEqual(adapter.commit_attempts, 1)
        self.assertEqual(len(adapter.events), 0)
        self.assertEqual(len(adapter.emails), 0)

    def test_known_failure_before_commit_records_no_external_effect(self) -> None:
        adapter = FakeWorkspaceAdapter(failure_mode="before_commit")
        gateway = self._new_gateway(adapter)
        proposal = gateway.prepare("create_calendar_event", self._arguments())
        authorized = gateway.authorize(proposal, self._approval(proposal))

        receipt = gateway.commit(authorized)

        self.assertEqual(receipt.status, ReceiptStatus.FAILED)
        self.assertFalse(receipt.committed)
        self.assertEqual(len(adapter.events), 0)
        self.assertEqual(len(adapter.emails), 0)

    def test_unexpected_effect_fails_verification(self) -> None:
        adapter = FakeWorkspaceAdapter(failure_mode="unexpected_effect")
        gateway = self._new_gateway(adapter)
        proposal = gateway.prepare("create_calendar_event", self._arguments())
        authorized = gateway.authorize(proposal, self._approval(proposal))

        receipt = gateway.commit(authorized)

        self.assertEqual(receipt.status, ReceiptStatus.VERIFICATION_FAILED)
        self.assertTrue(receipt.committed)
        self.assertFalse(receipt.verification.passed)
        self.assertEqual(receipt.verification.unexpected_effect_ids, ("unexpected_audit",))

    def test_verified_receipt_survives_ledger_reopen(self) -> None:
        proposal = self._prepare()
        authorized = self.gateway.authorize(proposal, self._approval(proposal))
        receipt = self.gateway.commit(authorized)
        self.ledger.close()

        reopened = SQLiteOperationLedger(self.ledger_path)
        try:
            self.assertEqual(reopened.get_receipt(proposal.operation_id), receipt)
            self.assertEqual(reopened.get_proposal(proposal.operation_id), proposal)
        finally:
            reopened.close()


if __name__ == "__main__":
    unittest.main()
