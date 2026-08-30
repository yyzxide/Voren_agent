"""Interactive CLI for Voren's controlled AgentDojo workflow."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from voren.actions.errors import ApprovalRejectedError
from voren.actions.gateway import ActionGateway
from voren.actions.ledger import SQLiteOperationLedger
from voren.actions.models import ApprovalDecision
from voren.adapters.agentdojo_reads import AgentDojoReadAdapter
from voren.adapters.agentdojo_workspace import (
    AgentDojoDependencyError,
    AgentDojoWorkspaceAdapter,
)
from voren.adapters.workspace_contracts import (
    WORKSPACE_CONTRACT_VERSION,
    create_calendar_event_definition,
)
from voren.providers.openai_responses import (
    ModelConfigurationError,
    OpenAIResponsesModelAdapter,
)
from voren.runs.manager import RunManager
from voren.runs.models import RunConfig
from voren.runs.store import SQLiteRunStore
from voren.runtime.agent_loop import AgentLoop
from voren.runtime.models import RuntimeLimits, RuntimeResultStatus
from voren.runtime.ports import ModelAdapter
from voren.runtime.tools import external_action_tool


Output = Callable[[str], Any]
ApprovalReader = Callable[[str], str]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voren",
        description="Run Voren against the controlled AgentDojo workspace.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    agentdojo = subcommands.add_parser(
        "agentdojo",
        help="Run one real-model request in the pinned AgentDojo world.",
    )
    agentdojo.add_argument("request", help="Operator request for Voren.")
    agentdojo.add_argument(
        "--model",
        help="Responses API model ID; alternatively set VOREN_MODEL.",
    )
    agentdojo.add_argument(
        "--base-url",
        help="Responses-compatible API base URL; defaults to OPENAI_BASE_URL.",
    )
    agentdojo.add_argument(
        "--database",
        type=Path,
        default=Path(".voren/voren.sqlite3"),
        help="SQLite trace and operation database.",
    )
    agentdojo.add_argument("--timeout-seconds", type=float, default=60.0)
    agentdojo.add_argument("--max-output-tokens", type=int, default=2_048)
    agentdojo.add_argument("--max-model-steps", type=int, default=8)
    agentdojo.add_argument("--max-tool-calls", type=int, default=12)
    agentdojo.set_defaults(handler=run_agentdojo)
    return parser


def run_agentdojo(
    args: argparse.Namespace,
    *,
    model: ModelAdapter | None = None,
    approval_reader: ApprovalReader = input,
    output: Output = print,
) -> int:
    model_name = args.model or os.environ.get("VOREN_MODEL")
    if not model_name:
        raise ModelConfigurationError("pass --model or set VOREN_MODEL")
    if model is None:
        model = OpenAIResponsesModelAdapter.from_environment(
            model=model_name,
            base_url=args.base_url,
            timeout_seconds=args.timeout_seconds,
            max_output_tokens=args.max_output_tokens,
        )

    workspace = AgentDojoWorkspaceAdapter()
    action_definition = create_calendar_event_definition(
        account_email=workspace.account_email
    )
    args.database.parent.mkdir(parents=True, exist_ok=True)
    ledger = SQLiteOperationLedger(args.database)
    store = SQLiteRunStore(args.database)
    try:
        gateway = ActionGateway(
            definitions=(action_definition,),
            adapter=workspace,
            ledger=ledger,
        )
        manager = RunManager(
            store=store,
            operation_ledger=ledger,
            action_gateway=gateway,
        )
        loop = AgentLoop(
            model=model,
            read_tools=AgentDojoReadAdapter(workspace),
            action_tools=(
                external_action_tool(
                    action_definition,
                    description=(
                        "Propose a calendar event and its invitation email. "
                        "Execution always requires exact-effect operator approval."
                    ),
                ),
            ),
            run_manager=manager,
            limits=RuntimeLimits(
                max_model_steps=args.max_model_steps,
                max_tool_calls=args.max_tool_calls,
            ),
        )
        result = loop.run(
            user_request=args.request,
            config=RunConfig(
                workflow="agentdojo_email_calendar",
                world_adapter="agentdojo_workspace_v1.2.2",
                policy_version="provenance-and-exact-effects-v1",
                action_contract_versions=(WORKSPACE_CONTRACT_VERSION,),
                metadata={"model": model_name, "provider": "responses_api"},
            ),
        )

        if result.status is RuntimeResultStatus.COMPLETED:
            output(result.final_text or "")
            _print_trace(store, result.run_id, output)
            return 0
        if result.status is not RuntimeResultStatus.WAITING_APPROVAL:
            detail = (
                f"/{result.error_detail_code}" if result.error_detail_code else ""
            )
            output(f"run failed: {result.error_code}{detail}")
            _print_trace(store, result.run_id, output)
            return 1

        proposal = result.pending_proposal
        if proposal is None:
            output("run failed: waiting state has no proposal")
            return 1
        _print_proposal(proposal, output)
        answer = approval_reader("Approve these exact effects? [y/N] ")
        approved = answer.strip().lower() in {"y", "yes"}
        approval = ApprovalDecision.for_proposal(
            proposal,
            approval_id=f"cli-{proposal.operation_id}",
            decided_by="operator:cli",
            approved=approved,
            decided_at=datetime.now(UTC),
        )
        try:
            receipt = manager.resume_with_approval(result.run_id, approval)
        except ApprovalRejectedError:
            output("action rejected; no external commit was attempted")
            _print_trace(store, result.run_id, output)
            return 2

        output(f"receipt: {receipt.status.value}")
        output(f"verification passed: {receipt.verification.passed}")
        _print_trace(store, result.run_id, output)
        return 0 if receipt.verification.passed else 1
    finally:
        store.close()
        ledger.close()


def _print_proposal(proposal, output: Output) -> None:
    output(f"proposal operation: {proposal.operation_id}")
    output(f"proposal digest: {proposal.digest}")
    for effect in proposal.effects:
        output(
            f"effect {effect.effect_id}: {effect.kind.value} {effect.resource} "
            f"(reversible={effect.reversible}, sensitivity={effect.sensitivity.value})"
        )
        output(json.dumps(effect.attributes, ensure_ascii=False, sort_keys=True))


def _print_trace(store: SQLiteRunStore, run_id: str, output: Output) -> None:
    output("trace:")
    for event in store.list_events(run_id):
        output(f"  {event.sequence:02d} {event.event_type.value}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (ModelConfigurationError, AgentDojoDependencyError, ValueError) as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
