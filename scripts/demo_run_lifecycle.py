#!/usr/bin/env python3
"""Demonstrate durable approval pause/resume with a simulated process restart."""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from voren.actions.gateway import ActionGateway
from voren.actions.ledger import SQLiteOperationLedger
from voren.actions.models import ApprovalDecision
from voren.adapters.fake_workspace import FakeWorkspaceAdapter
from voren.adapters.workspace_contracts import (
    WORKSPACE_CONTRACT_VERSION,
    create_calendar_event_definition,
)
from voren.runs.manager import RunManager
from voren.runs.models import RunConfig
from voren.runs.store import SQLiteRunStore


def _print_json(label: str, value) -> None:
    print(f"\n{label}")
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def _open_stack(database_path: Path):
    ledger = SQLiteOperationLedger(database_path)
    store = SQLiteRunStore(database_path)
    adapter = FakeWorkspaceAdapter()
    gateway = ActionGateway(
        definitions=(create_calendar_event_definition(),),
        adapter=adapter,
        ledger=ledger,
    )
    manager = RunManager(
        store=store,
        operation_ledger=ledger,
        action_gateway=gateway,
    )
    return ledger, store, adapter, manager


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pause a run for approval, rebuild the runtime, and resume it."
    )
    parser.add_argument(
        "--simulate-approval",
        action="store_true",
        help="use a deterministic approval simulator instead of an interactive prompt",
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "voren.sqlite3"
        ledger, store, _, manager = _open_stack(database_path)
        run = manager.start_new_run(
            RunConfig(
                workflow="single_calendar_action",
                world_adapter="fake_workspace",
                policy_version="exact-effects-v1",
                action_contract_versions=(WORKSPACE_CONTRACT_VERSION,),
                metadata={"demo": "approval_pause_resume"},
            )
        )
        proposal = manager.propose_action(
            run.run_id,
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
            "RUN PAUSED",
            {
                "run": store.get_run(run.run_id).model_dump(mode="json"),
                "effects": [effect.model_dump(mode="json") for effect in proposal.effects],
            },
        )
        ledger.close()
        store.close()
        print("\n--- simulated process restart: SQLite connections rebuilt ---")

        ledger, store, adapter, manager = _open_stack(database_path)
        try:
            confirmation = f"approve {proposal.digest[:12]}"
            if args.simulate_approval:
                answer = confirmation
                print(f"Approval simulator entered: {answer}")
            else:
                answer = input(f"Type '{confirmation}' to authorize: ").strip()
            if answer != confirmation:
                print("No approval was issued; the persisted run remains paused.")
                return 2

            approval = ApprovalDecision.for_proposal(
                proposal,
                approval_id=f"approval-{proposal.operation_id}",
                decided_by="operator:demo",
                decided_at=datetime.now(UTC),
            )
            receipt = manager.resume_with_approval(run.run_id, approval)
            _print_json("FINAL RUN", store.get_run(run.run_id).model_dump(mode="json"))
            _print_json(
                "APPEND-ONLY EVENTS",
                [event.model_dump(mode="json") for event in store.list_events(run.run_id)],
            )
            _print_json("ACTION RECEIPT", receipt.model_dump(mode="json"))
            _print_json(
                "FAKE WORLD DELTA",
                {"events": len(adapter.events), "emails": len(adapter.emails)},
            )
            return 0 if receipt.verification.passed else 1
        finally:
            ledger.close()
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
