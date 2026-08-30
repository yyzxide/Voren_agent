from __future__ import annotations

import importlib.util
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from voren.actions.gateway import ActionGateway
from voren.actions.ledger import SQLiteOperationLedger
from voren.actions.models import ApprovalDecision, ReceiptStatus, verify_exact_effects
from voren.adapters.agentdojo_workspace import (
    AGENTDOJO_BENCHMARK_VERSION,
    AGENTDOJO_DISTRIBUTION_VERSION,
    AgentDojoWorkspaceAdapter,
)
from voren.adapters.workspace_contracts import create_calendar_event_definition


AGENTDOJO_AVAILABLE = importlib.util.find_spec("agentdojo") is not None


@unittest.skipUnless(AGENTDOJO_AVAILABLE, "AgentDojo optional dependency is not installed")
class AgentDojoWorkspaceAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.ledger = SQLiteOperationLedger(
            Path(self.temporary_directory.name) / "operations.sqlite3"
        )
        self.addCleanup(self.ledger.close)
        self.adapter = AgentDojoWorkspaceAdapter()
        self.gateway = ActionGateway(
            definitions=(
                create_calendar_event_definition(account_email=self.adapter.account_email),
            ),
            adapter=self.adapter,
            ledger=self.ledger,
            id_factory=lambda: "agentdojo-user-task-18",
            clock=lambda: datetime(2026, 8, 30, 12, 0, tzinfo=UTC),
        )

    @staticmethod
    def _task_18_arguments() -> dict:
        return {
            "title": "Hiking Trip",
            "location": "island trailhead",
            "start_time": "2024-05-18 08:00",
            "end_time": "2024-05-18 13:00",
            "participants": ["mark.davies@hotmail.com"],
        }

    def test_pin_matches_research_snapshot(self) -> None:
        self.assertEqual(AGENTDOJO_DISTRIBUTION_VERSION, "0.1.35")
        self.assertEqual(AGENTDOJO_BENCHMARK_VERSION, "v1.2.2")
        self.assertEqual(self.adapter.suite.benchmark_version, (1, 2, 2))

    def test_gateway_action_passes_official_user_task_18_utility(self) -> None:
        task = self.adapter.suite.get_user_task_by_id("user_task_18")
        pre_environment = self.adapter.environment.model_copy(deep=True)
        proposal = self.gateway.prepare(
            "create_calendar_event", self._task_18_arguments()
        )
        approval = ApprovalDecision.for_proposal(
            proposal,
            approval_id="approval-agentdojo-user-task-18",
            decided_by="test:deterministic-operator",
            decided_at=datetime(2026, 8, 30, 12, 0, tzinfo=UTC),
        )

        receipt = self.gateway.commit(self.gateway.authorize(proposal, approval))

        self.assertEqual(receipt.status, ReceiptStatus.VERIFIED)
        self.assertTrue(receipt.verification.passed)
        self.assertEqual(
            [item.effect.effect_id for item in receipt.observed_effects],
            ["calendar_event", "invitation_email"],
        )
        self.assertTrue(
            task.utility("", pre_environment, self.adapter.environment),
            "Voren's committed state must pass AgentDojo's official task utility",
        )

    def test_duplicate_gateway_commit_does_not_duplicate_agentdojo_state(self) -> None:
        proposal = self.gateway.prepare(
            "create_calendar_event", self._task_18_arguments()
        )
        approval = ApprovalDecision.for_proposal(
            proposal,
            approval_id="approval-agentdojo-user-task-18",
            decided_by="test:deterministic-operator",
            decided_at=datetime(2026, 8, 30, 12, 0, tzinfo=UTC),
        )
        authorized = self.gateway.authorize(proposal, approval)

        first = self.gateway.commit(authorized)
        second = self.gateway.commit(authorized)

        self.assertEqual(first, second)
        self.assertEqual(self.adapter.commit_attempts, 1)
        self.assertEqual(
            len(self.adapter.environment.calendar.events)
            - len(self.adapter.initial_environment.calendar.events),
            1,
        )
        self.assertEqual(
            len(self.adapter.environment.inbox.emails)
            - len(self.adapter.initial_environment.inbox.emails),
            1,
        )

    def test_unrecognized_agentdojo_delta_becomes_unexpected_effect(self) -> None:
        proposal = self.gateway.prepare(
            "create_calendar_event", self._task_18_arguments()
        )
        self.adapter.commit(proposal)
        existing_email_id = next(
            email_id
            for email_id in self.adapter.environment.inbox.emails
            if str(email_id) != "34"
        )
        existing_email = self.adapter.environment.inbox.emails[existing_email_id]
        existing_email.read = not existing_email.read

        observed = self.adapter.observe(proposal)
        verification = verify_exact_effects(proposal.effects, observed)

        self.assertFalse(verification.passed)
        self.assertTrue(
            any(
                effect_id.startswith("unexpected_state_delta_")
                for effect_id in verification.unexpected_effect_ids
            )
        )


if __name__ == "__main__":
    unittest.main()
