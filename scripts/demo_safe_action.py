#!/usr/bin/env python3
"""Run the Phase 1 action protocol against the deterministic fake workspace."""

from __future__ import annotations

import argparse
import json
import tempfile
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path

from voren.actions.gateway import ActionGateway
from voren.actions.ledger import SQLiteOperationLedger
from voren.actions.models import ApprovalDecision
from voren.adapters.fake_workspace import FakeWorkspaceAdapter
from voren.adapters.workspace_contracts import create_calendar_event_definition


def _print_json(label: str, value) -> None:
    print(f"\n{label}")
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Demonstrate exact-effect approval without a model or real account."
    )
    parser.add_argument(
        "--simulate-approval",
        action="store_true",
        help="use a deterministic approval simulator instead of an interactive prompt",
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        help="optional SQLite path; a temporary ledger is used by default",
    )
    args = parser.parse_args()

    temporary_directory = (
        tempfile.TemporaryDirectory() if args.ledger is None else nullcontext(None)
    )
    with temporary_directory as directory:
        ledger_path = args.ledger or Path(directory) / "operations.sqlite3"
        ledger = SQLiteOperationLedger(ledger_path)
        try:
            adapter = FakeWorkspaceAdapter()
            gateway = ActionGateway(
                definitions=(create_calendar_event_definition(),),
                adapter=adapter,
                ledger=ledger,
            )
            proposal = gateway.prepare(
                "create_calendar_event",
                {
                    "title": "Hiking Trip",
                    "description": "Bring water.",
                    "start_time": "2024-05-18 08:00",
                    "end_time": "2024-05-18 13:00",
                    "location": "island trailhead",
                    "participants": ["mark.davies@hotmail.com"],
                },
            )
            _print_json(
                "APPROVAL REQUIRED — exact proposed effects",
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
                print("Action was not authorized; no external effect was attempted.")
                return 2

            approval = ApprovalDecision.for_proposal(
                proposal,
                approval_id=f"approval-{proposal.operation_id}",
                decided_by="operator:demo",
                decided_at=datetime.now(UTC),
            )
            authorized = gateway.authorize(proposal, approval)
            receipt = gateway.commit(authorized)
            _print_json("ACTION RECEIPT", receipt.model_dump(mode="json"))
            _print_json(
                "FAKE WORLD STATE",
                {"events": adapter.events, "emails": adapter.emails},
            )
            return 0 if receipt.verification.passed else 1
        finally:
            ledger.close()


if __name__ == "__main__":
    raise SystemExit(main())
