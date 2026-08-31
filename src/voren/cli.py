"""Interactive CLI for Voren's controlled AgentDojo workflow."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

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
from voren.evaluation.agentdojo import (
    AgentDojoEvaluationRunner,
    ModelFactory,
    evaluation_input_digests,
    phase1_smoke_manifest,
    select_trials,
)
from voren.evaluation.artifacts import detect_source_revision, write_artifact
from voren.evaluation.models import EvaluationMode, ExperimentConfig
from voren.providers.openai_responses import (
    ModelConfigurationError,
    OpenAIResponsesModelAdapter,
)
from voren.runs.manager import RunManager
from voren.runs.models import RunConfig
from voren.runs.store import SQLiteRunStore
from voren.runtime.agent_loop import AgentLoop
from voren.runtime.cancellation import CancellationToken
from voren.runtime.models import RuntimeLimits, RuntimeResultStatus, RuntimeUsage
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
    agentdojo.add_argument(
        "--timeout-seconds",
        type=float,
        default=60.0,
        help="Overall model-response deadline and per-HTTP-call timeout.",
    )
    agentdojo.add_argument("--max-output-tokens", type=int, default=2_048)
    agentdojo.add_argument("--max-model-steps", type=int, default=8)
    agentdojo.add_argument("--max-tool-calls", type=int, default=12)
    agentdojo.set_defaults(handler=run_agentdojo)

    evaluation = subcommands.add_parser(
        "eval-agentdojo",
        help="Run frozen AgentDojo cases and write a verifiable JSON artifact.",
    )
    evaluation.add_argument(
        "--case",
        action="append",
        required=True,
        choices=("all", *(case.case_id for case in phase1_smoke_manifest().cases)),
        help="Frozen case ID; repeat for multiple cases or pass 'all'.",
    )
    evaluation.add_argument(
        "--mode",
        action="append",
        required=True,
        choices=tuple(mode.value for mode in EvaluationMode),
        help="Evaluation boundary; repeat to compare both modes.",
    )
    evaluation.add_argument(
        "--model",
        help="Responses API model ID; alternatively set VOREN_MODEL.",
    )
    evaluation.add_argument(
        "--base-url",
        help="Responses-compatible API base URL; defaults to OPENAI_BASE_URL.",
    )
    evaluation.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Destination for the integrity-bound JSON artifact.",
    )
    evaluation.add_argument(
        "--database",
        type=Path,
        default=Path(".voren/evaluations.sqlite3"),
        help="SQLite trace and operation database.",
    )
    evaluation.add_argument("--experiment-id")
    evaluation.add_argument(
        "--timeout-seconds",
        type=float,
        default=60.0,
        help="Overall model-response deadline and per-HTTP-call timeout.",
    )
    evaluation.add_argument("--max-output-tokens", type=int, default=2_048)
    evaluation.add_argument("--max-model-steps", type=int, default=8)
    evaluation.add_argument("--max-tool-calls", type=int, default=12)
    evaluation.set_defaults(handler=run_agentdojo_evaluation)
    return parser


def run_agentdojo(
    args: argparse.Namespace,
    *,
    model: ModelAdapter | None = None,
    cancellation: CancellationToken | None = None,
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
            cancellation=cancellation,
        )
        _print_usage(result.usage, output)

        if result.status is RuntimeResultStatus.COMPLETED:
            output(result.final_text or "")
            _print_trace(store, result.run_id, output)
            return 0
        if result.status is RuntimeResultStatus.CANCELLED:
            confirmation = (
                "not_applicable"
                if result.cancellation_confirmed is None
                else str(result.cancellation_confirmed).lower()
            )
            output(
                f"run cancelled: {result.cancellation_reason.value}; "
                f"provider_confirmed={confirmation}"
            )
            _print_trace(store, result.run_id, output)
            return 130
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


def run_agentdojo_evaluation(
    args: argparse.Namespace,
    *,
    model_factory: ModelFactory | None = None,
    output: Output = print,
) -> int:
    """Run an explicitly selected paid evaluation and persist its evidence."""

    model_name = args.model or os.environ.get("VOREN_MODEL")
    if not model_name:
        raise ModelConfigurationError("pass --model or set VOREN_MODEL")
    manifest = phase1_smoke_manifest()
    modes = tuple(EvaluationMode(value) for value in args.mode)
    if len(modes) != len(set(modes)):
        raise ValueError("evaluation modes must be unique")
    case_ids = tuple(args.case)
    if "all" in case_ids and case_ids != ("all",):
        raise ValueError("'all' cannot be combined with explicit case IDs")
    selections = select_trials(manifest, case_ids=case_ids, modes=modes)

    resolved_model_factory = model_factory
    if resolved_model_factory is None:

        def create_model(_case, _mode):
            return OpenAIResponsesModelAdapter.from_environment(
                model=model_name,
                base_url=args.base_url,
                timeout_seconds=args.timeout_seconds,
                max_output_tokens=args.max_output_tokens,
            )

        resolved_model_factory = create_model

    source = detect_source_revision(Path.cwd())
    prompt_digest, tool_digest, attack_digest = evaluation_input_digests()
    config = ExperimentConfig(
        experiment_id=args.experiment_id or f"eval-{uuid4()}",
        created_at=datetime.now(UTC),
        code_revision=source.revision,
        code_dirty=source.dirty,
        provider="responses_api",
        model=model_name,
        manifest_id=manifest.manifest_id,
        manifest_digest=manifest.calculated_digest(),
        system_prompt_digest=prompt_digest,
        tool_schema_digest=tool_digest,
        dataset=manifest.dataset,
        dataset_version=manifest.dataset_version,
        attack_template_version=manifest.attack_template_version,
        attack_template_digest=attack_digest,
        sampling={
            "max_output_tokens": args.max_output_tokens,
            "max_model_steps": args.max_model_steps,
            "max_tool_calls": args.max_tool_calls,
            "timeout_seconds": args.timeout_seconds,
            "temperature": "provider_default",
            "seed": None,
        },
    )
    runner = AgentDojoEvaluationRunner(
        model_factory=resolved_model_factory,
        database=args.database,
        limits=RuntimeLimits(
            max_model_steps=args.max_model_steps,
            max_tool_calls=args.max_tool_calls,
        ),
    )
    artifact = runner.run(
        config=config,
        manifest=manifest,
        selections=selections,
    )
    write_artifact(args.output, artifact)
    output(f"artifact: {args.output}")
    output(f"artifact digest: {artifact.artifact_digest}")
    output(f"code revision: {source.revision} (dirty={source.dirty})")
    for summary in artifact.summaries:
        attack_rate = (
            "n/a"
            if summary.attack_success_rate is None
            else f"{summary.attack_success_rate:.3f}"
        )
        output(
            f"{summary.mode.value}: utility={summary.utility_rate:.3f}, "
            f"attack_success={attack_rate}, trials={summary.total_trials}, "
            f"cancelled={summary.cancelled_runs}, "
            f"tokens={summary.model_usage.total_tokens}, "
            f"usage_reported={summary.model_usage.reported_model_requests}/"
            f"{summary.model_usage.model_requests}"
        )
    return 0


def _print_usage(usage: RuntimeUsage, output: Output) -> None:
    output(
        "model usage: "
        f"requests={usage.model_requests}, "
        f"reported={usage.reported_model_requests}, "
        f"input={usage.input_tokens}, "
        f"cached_input={usage.cached_input_tokens}, "
        f"cache_write_input={usage.cache_write_input_tokens}, "
        f"output={usage.output_tokens}, "
        f"reasoning_output={usage.reasoning_output_tokens}, "
        f"total={usage.total_tokens}, "
        f"complete={str(usage.complete).lower()}"
    )


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
