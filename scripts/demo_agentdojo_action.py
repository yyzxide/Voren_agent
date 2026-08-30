#!/usr/bin/env python3
"""Run Voren's safe-action protocol in the pinned AgentDojo workspace."""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from voren.actions.gateway import ActionGateway
from voren.actions.ledger import SQLiteOperationLedger
from voren.actions.models import ApprovalDecision
from voren.adapters.agentdojo_workspace import AgentDojoWorkspaceAdapter
from voren.adapters.workspace_contracts import create_calendar_event_definition


def _print_json(label: str, value) -> None:
    print(f"\n{label}")
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Execute the user_task_18 action through Voren and AgentDojo."
    )
    parser.add_argument(
        "--simulate-approval",
        action="store_true",
        help="use a deterministic approval simulator instead of an interactive prompt",
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as directory:
        ledger = SQLiteOperationLedger(Path(directory) / "operations.sqlite3")
        try:
            adapter = AgentDojoWorkspaceAdapter()
            pre_environment = adapter.environment.model_copy(deep=True)
            gateway = ActionGateway(
                definitions=(
                    create_calendar_event_definition(account_email=adapter.account_email),
                ),
                adapter=adapter,
                ledger=ledger,
            )
            proposal = gateway.prepare(
                "create_calendar_event",
                {
                    "title": "Hiking Trip",
                    "location": "island trailhead",
                    "start_time": "2024-05-18 08:00",
                    "end_time": "2024-05-18 13:00",
                    "participants": ["mark.davies@hotmail.com"],
                },
            )
            _print_json(
                "AGENTDOJO APPROVAL REQUIRED",
                {
                    "operation_id": proposal.operation_id,
                    "digest": proposal.digest,
                    "effects": [effect.model_dump(mode="json") for effect in proposal.effects],
                },
            )

            confirmation = f"approve {proposal.digest[:12]}"
            if args.simulate_approval:
                answer = confirmation
                print(f"\nApproval simulator entered: {answer}")
            else:
                answer = input(f"\nType '{confirmation}' to authorize: ").strip()
            if answer != confirmation:
                print("Action was not authorized; AgentDojo state was not changed.")
                return 2

            approval = ApprovalDecision.for_proposal(
                proposal,
                approval_id=f"approval-{proposal.operation_id}",
                decided_by="operator:demo",
                decided_at=datetime.now(UTC),
            )
            receipt = gateway.commit(gateway.authorize(proposal, approval))
            task = adapter.suite.get_user_task_by_id("user_task_18")
            official_utility = task.utility(
                "", pre_environment, adapter.environment
            )
            _print_json("ACTION RECEIPT", receipt.model_dump(mode="json"))
            _print_json(
                "OFFICIAL AGENTDOJO GRADER",
                {
                    "task_id": task.ID,
                    "utility": official_utility,
                    "calendar_event_delta": len(adapter.environment.calendar.events)
                    - len(pre_environment.calendar.events),
                    "email_delta": len(adapter.environment.inbox.emails)
                    - len(pre_environment.inbox.emails),
                },
            )
            return 0 if receipt.verification.passed and official_utility else 1
        finally:
            ledger.close()


if __name__ == "__main__":
    raise SystemExit(main())
