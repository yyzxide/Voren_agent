"""Credential-free AgentDojo demo of read -> propose -> approve -> verify."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

from voren.actions.gateway import ActionGateway
from voren.actions.ledger import SQLiteOperationLedger
from voren.actions.models import ApprovalDecision
from voren.adapters.agentdojo_reads import AgentDojoReadAdapter
from voren.adapters.agentdojo_workspace import AgentDojoWorkspaceAdapter
from voren.adapters.workspace_contracts import (
    WORKSPACE_CONTRACT_VERSION,
    create_calendar_event_definition,
)
from voren.runs.manager import RunManager
from voren.runs.models import RunConfig
from voren.runs.store import SQLiteRunStore
from voren.runtime.agent_loop import AgentLoop
from voren.runtime.models import (
    ModelResponse,
    RuntimeResultStatus,
    ToolCall,
)
from voren.runtime.tools import external_action_tool
from voren.testing.scripted_model import ScriptedModelAdapter


def main() -> None:
    now = datetime(2026, 8, 30, 16, 0, tzinfo=UTC)
    workspace = AgentDojoWorkspaceAdapter()
    pre_environment = workspace.environment.model_copy(deep=True)
    task = workspace.suite.get_user_task_by_id("user_task_18")
    model = ScriptedModelAdapter(
        (
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="demo-read-email",
                        name="search_emails",
                        arguments={"query": "hiking"},
                    ),
                )
            ),
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="demo-read-calendar",
                        name="get_day_calendar_events",
                        arguments={"day": "2024-05-18"},
                    ),
                )
            ),
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="demo-propose-event",
                        name="create_calendar_event",
                        arguments={
                            "title": "Hiking Trip",
                            "start_time": "2024-05-18 08:00",
                            "end_time": "2024-05-18 13:00",
                            "participants": ["mark.davies@hotmail.com"],
                            "location": "island trailhead",
                        },
                    ),
                )
            ),
        )
    )

    with tempfile.TemporaryDirectory() as temporary_directory:
        database_path = Path(temporary_directory) / "voren.sqlite3"
        ledger = SQLiteOperationLedger(database_path)
        store = SQLiteRunStore(database_path)
        try:
            action_definition = create_calendar_event_definition(
                account_email=workspace.account_email
            )
            gateway = ActionGateway(
                definitions=(action_definition,),
                adapter=workspace,
                ledger=ledger,
                clock=lambda: now,
            )
            manager = RunManager(
                store=store,
                operation_ledger=ledger,
                action_gateway=gateway,
                clock=lambda: now,
            )
            loop = AgentLoop(
                model=model,
                read_tools=AgentDojoReadAdapter(workspace, clock=lambda: now),
                action_tools=(
                    external_action_tool(
                        action_definition,
                        description=(
                            "Propose a calendar event and invitation email; always "
                            "pause for exact-effect operator approval."
                        ),
                    ),
                ),
                run_manager=manager,
            )
            result = loop.run(
                user_request=task.PROMPT,
                config=RunConfig(
                    workflow="agentdojo_user_task_18",
                    world_adapter="agentdojo_workspace_v1.2.2",
                    policy_version="provenance-and-exact-effects-v1",
                    action_contract_versions=(WORKSPACE_CONTRACT_VERSION,),
                ),
            )
            if (
                result.status is not RuntimeResultStatus.WAITING_APPROVAL
                or result.pending_proposal is None
            ):
                raise RuntimeError(f"unexpected loop result: {result}")

            proposal = result.pending_proposal
            print(f"run paused: {result.run_id}")
            print(f"proposal: {proposal.action_name}@{proposal.action_version}")
            for effect in proposal.effects:
                print(f"  - {effect.kind.value} {effect.resource}: {effect.summary}")
            print(f"commit attempts before approval: {workspace.commit_attempts}")

            approval = ApprovalDecision.for_proposal(
                proposal,
                approval_id="demo-approval",
                decided_by="demo:operator",
                decided_at=now,
            )
            receipt = manager.resume_with_approval(result.run_id, approval)
            print(f"receipt: {receipt.status.value}")
            print(
                "AgentDojo user_task_18 utility: "
                f"{task.utility('', pre_environment, workspace.environment)}"
            )
            print("events:")
            for event in store.list_events(result.run_id):
                print(f"  {event.sequence:02d} {event.event_type.value}")
        finally:
            store.close()
            ledger.close()


if __name__ == "__main__":
    main()
