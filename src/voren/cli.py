"""Interactive CLI for Voren's controlled AgentDojo workflow."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from voren.actions.errors import ApprovalRejectedError
from voren.actions.gateway import ActionDefinition, ActionGateway
from voren.actions.ledger import SQLiteOperationLedger
from voren.actions.models import ApprovalDecision
from voren.actions.ports import ActionAdapter
from voren.adapters.agentdojo_reads import AgentDojoReadAdapter
from voren.adapters.agentdojo_workspace import (
    AgentDojoDependencyError,
    AgentDojoWorkspaceAdapter,
)
from voren.adapters.google_workspace import (
    GOOGLE_WORKSPACE_CONTRACT_VERSION,
    GoogleWorkspaceConfig,
    GoogleWorkspaceConfigurationError,
    GoogleWorkspaceConnector,
)
from voren.adapters.google_smoke import (
    GoogleSmokeArtifact,
    collect_google_smoke_run,
    write_google_smoke_artifact,
)
from voren.adapters.workspace_contracts import (
    WORKSPACE_CONTRACT_VERSION,
    create_calendar_event_definition,
    send_email_definition,
)
from voren.evaluation.agentdojo import (
    AgentDojoEvaluationRunner,
    ModelFactory,
    evaluation_input_digests,
    phase1_smoke_manifest,
    select_trials,
)
from voren.evaluation.artifacts import detect_source_revision, write_artifact
from voren.evaluation.models import (
    EvaluationMode,
    EvaluationSelection,
    ExperimentConfig,
)
from voren.learning.artifacts import read_candidate_evaluation
from voren.learning.agentdojo import AgentDojoSkillEvaluator, SkillModelFactory
from voren.learning.artifacts import write_candidate_evaluation
from voren.learning.evaluation import EvaluationCaseKind, HeldOutCase
from voren.learning.evidence import (
    DurableLearningRouter,
    LearningEvidenceRoutingError,
)
from voren.learning.models import CandidateStatus
from voren.learning.runner import PairedEvaluationRunner
from voren.learning.report import render_candidate_report, write_candidate_report
from voren.learning.service import SkillCandidateService
from voren.learning.store import CandidateStoreError, SQLiteCandidateStore
from voren.knowledge.models import KnowledgeDocument, KnowledgeSourceKind
from voren.knowledge.store import KnowledgeStoreError, SQLiteKnowledgeStore
from voren.memory.context import MemoryContextAssembler
from voren.memory.service import MemoryService
from voren.memory.store import MemoryStoreError, SQLiteMemoryStore
from voren.observations.read_tools import ReadToolAdapter
from voren.providers.openai_responses import (
    ModelConfigurationError,
    OpenAIResponsesModelAdapter,
    ResponsesProviderProfile,
    resolve_responses_endpoint,
)
from voren.runs.manager import RunManager
from voren.runs.models import RunConfig
from voren.runs.store import SQLiteRunStore
from voren.runtime.agent_loop import AgentLoop
from voren.runtime.cancellation import CancellationToken
from voren.runtime.models import RuntimeLimits, RuntimeResultStatus, RuntimeUsage
from voren.runtime.ports import ModelAdapter
from voren.runtime.tools import external_action_tool
from voren.runtime.transcripts import SQLiteTranscriptStore, TranscriptKeyError
from voren.skills.context import SkillContextAssembler, SkillContextSnapshot
from voren.skills.parser import AgentSkillParser, SkillFormatError
from voren.skills.routing import SkillRouteDecision, SkillRouter
from voren.skills.store import SQLiteSkillStore


Output = Callable[[str], Any]
ApprovalReader = Callable[[str], str]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voren",
        description=(
            "Run controlled Voren workspaces and manage evidence-gated Skill "
            "versions."
        ),
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
        "--provider-profile",
        choices=tuple(item.value for item in ResponsesProviderProfile),
        help=(
            "Explicit endpoint capabilities; alternatively set "
            "VOREN_RESPONSES_PROFILE. Required with a custom base URL."
        ),
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
    agentdojo.add_argument(
        "--profile-memory",
        action="append",
        default=[],
        help="Explicit active profile memory ID to freeze; repeat as needed.",
    )
    agentdojo.add_argument(
        "--episode-memory",
        action="append",
        default=[],
        help="Explicit episode memory ID to freeze; repeat as needed.",
    )
    _add_runtime_skill_arguments(agentdojo)
    agentdojo.set_defaults(handler=run_agentdojo)

    google = subcommands.add_parser(
        "google",
        help=(
            "Run one real-model request against a conservatively scoped Google "
            "Workspace account."
        ),
    )
    google.add_argument("request", help="Operator request for Voren.")
    google.add_argument(
        "--model",
        help="Responses API model ID; alternatively set VOREN_MODEL.",
    )
    google.add_argument(
        "--base-url",
        help="Responses-compatible API base URL; defaults to OPENAI_BASE_URL.",
    )
    google.add_argument(
        "--provider-profile",
        choices=tuple(item.value for item in ResponsesProviderProfile),
        help=(
            "Explicit endpoint capabilities; alternatively set "
            "VOREN_RESPONSES_PROFILE. Required with a custom base URL."
        ),
    )
    google.add_argument(
        "--database",
        type=Path,
        default=Path(".voren/voren.sqlite3"),
        help="SQLite trace and operation database.",
    )
    google.add_argument(
        "--timeout-seconds",
        type=float,
        default=60.0,
        help="Overall model-response deadline and per-HTTP-call timeout.",
    )
    google.add_argument("--max-output-tokens", type=int, default=2_048)
    google.add_argument("--max-model-steps", type=int, default=8)
    google.add_argument("--max-tool-calls", type=int, default=12)
    google.add_argument(
        "--profile-memory",
        action="append",
        default=[],
        help="Explicit active profile memory ID to freeze; repeat as needed.",
    )
    google.add_argument(
        "--episode-memory",
        action="append",
        default=[],
        help="Explicit episode memory ID to freeze; repeat as needed.",
    )
    _add_runtime_skill_arguments(google)
    google.set_defaults(handler=run_google)

    google_smoke = subcommands.add_parser(
        "export-google-smoke",
        help="Export a redacted artifact from a complete live Google smoke suite.",
    )
    google_smoke.add_argument(
        "--database",
        type=Path,
        default=Path(".voren/voren.sqlite3"),
        help="SQLite trace database containing the live Google runs.",
    )
    google_smoke.add_argument(
        "--run-id",
        action="append",
        required=True,
        help="Completed live Google run ID; repeat to supply the full suite.",
    )
    google_smoke.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Destination for the redacted integrity-checked JSON artifact.",
    )
    google_smoke.set_defaults(handler=run_google_smoke_export)

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
        "--provider-profile",
        choices=tuple(item.value for item in ResponsesProviderProfile),
        help=(
            "Explicit endpoint capabilities; alternatively set "
            "VOREN_RESPONSES_PROFILE. Required with a custom base URL."
        ),
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
    evaluation.add_argument(
        "--skill",
        action="append",
        default=[],
        help=(
            "Explicit active Skill name to freeze into a static_skill trial; "
            "repeat as needed. Omit for no_skill."
        ),
    )
    evaluation.add_argument(
        "--skill-store",
        type=Path,
        default=Path(".voren/skills"),
        help="Content-addressed immutable Skill object directory.",
    )
    evaluation.set_defaults(handler=run_agentdojo_evaluation)
    skills = subcommands.add_parser(
        "skill",
        help="Manage immutable Skills and evidence-gated candidates.",
    )
    skill_commands = skills.add_subparsers(dest="skill_command", required=True)

    install = skill_commands.add_parser(
        "install", help="Install an immutable Skill package."
    )
    install.add_argument("path", type=Path)
    _add_skill_storage_arguments(install)
    install.add_argument(
        "--activate",
        action="store_true",
        help="Activate this manually managed baseline after installing it.",
    )
    install.add_argument("--reason", help="Required when --activate is used.")
    install.set_defaults(handler=run_skill_install)

    correction = skill_commands.add_parser(
        "evidence-correction",
        help="Persist an operator-authored correction as learning evidence.",
    )
    correction.add_argument("path", type=Path, help="UTF-8 correction text file.")
    correction.add_argument("--evidence-id", required=True)
    correction.add_argument("--operator", required=True)
    _add_skill_storage_arguments(correction)
    correction.set_defaults(handler=run_skill_evidence_correction)

    run_evidence = skill_commands.add_parser(
        "evidence-run",
        help="Route an eligible completed Run into durable learning evidence.",
    )
    run_evidence.add_argument("--run-id", required=True)
    run_evidence.add_argument("--evidence-id", required=True)
    run_evidence.add_argument(
        "--evidence-case",
        action="append",
        default=[],
        help="Evaluation Case used by this Run; repeat as needed.",
    )
    _add_skill_storage_arguments(run_evidence)
    run_evidence.set_defaults(handler=run_skill_evidence_run)

    stage = skill_commands.add_parser(
        "stage", help="Stage a bounded inactive candidate from durable evidence."
    )
    stage.add_argument("path", type=Path, help="Candidate Skill package path.")
    stage.add_argument("--candidate-id", required=True)
    stage.add_argument("--base", required=True, help="Active base Skill name.")
    stage.add_argument(
        "--evidence-id",
        action="append",
        required=True,
        help="Previously persisted Evidence ID; repeat to bind multiple items.",
    )
    _add_skill_storage_arguments(stage)
    stage.set_defaults(handler=run_skill_stage)

    skill_evaluation = skill_commands.add_parser(
        "eval-agentdojo",
        help="Run paid paired AgentDojo trials and write a candidate artifact.",
    )
    skill_evaluation.add_argument("--candidate-id", required=True)
    skill_evaluation.add_argument(
        "--case",
        action="append",
        required=True,
        choices=("all", *(case.case_id for case in phase1_smoke_manifest().cases)),
        help="Held-out Case ID; repeat or pass 'all'.",
    )
    skill_evaluation.add_argument(
        "--suite",
        required=True,
        help="Evaluation suite declared by the Skill contract.",
    )
    skill_evaluation.add_argument(
        "--model",
        help="Responses API model ID; alternatively set VOREN_MODEL.",
    )
    skill_evaluation.add_argument(
        "--base-url",
        help="Responses-compatible API base URL; defaults to OPENAI_BASE_URL.",
    )
    skill_evaluation.add_argument(
        "--provider-profile",
        choices=tuple(item.value for item in ResponsesProviderProfile),
        help=(
            "Explicit endpoint capabilities; alternatively set "
            "VOREN_RESPONSES_PROFILE. Required with a custom base URL."
        ),
    )
    skill_evaluation.add_argument("--evaluation-id")
    skill_evaluation.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Destination for the paired candidate evaluation Artifact.",
    )
    skill_evaluation.add_argument(
        "--trial-databases",
        type=Path,
        default=Path(".voren/candidate-trials/databases"),
    )
    skill_evaluation.add_argument(
        "--trial-artifacts",
        type=Path,
        default=Path(".voren/candidate-trials/artifacts"),
    )
    skill_evaluation.add_argument("--timeout-seconds", type=float, default=60.0)
    skill_evaluation.add_argument("--max-output-tokens", type=int, default=2_048)
    skill_evaluation.add_argument("--max-model-steps", type=int, default=8)
    skill_evaluation.add_argument("--max-tool-calls", type=int, default=12)
    _add_skill_storage_arguments(skill_evaluation)
    skill_evaluation.set_defaults(handler=run_skill_agentdojo_evaluation)

    decide = skill_commands.add_parser(
        "decide", help="Accept or reject a staged candidate from an artifact."
    )
    decide.add_argument("--candidate-id", required=True)
    decide.add_argument("--artifact", type=Path, required=True)
    _add_skill_storage_arguments(decide)
    decide.set_defaults(handler=run_skill_decide)

    inspect = skill_commands.add_parser(
        "inspect", help="Print one candidate, evaluations, and audit events as JSON."
    )
    inspect.add_argument("--candidate-id", required=True)
    inspect.add_argument(
        "--include-evidence-payload",
        action="store_true",
        help="Include potentially sensitive correction or Run snapshot content.",
    )
    _add_skill_storage_arguments(inspect)
    inspect.set_defaults(handler=run_skill_inspect)

    report = skill_commands.add_parser(
        "report", help="Generate a Markdown candidate decision report."
    )
    report.add_argument("--candidate-id", required=True)
    report.add_argument("--output", type=Path, required=True)
    _add_skill_storage_arguments(report)
    report.set_defaults(handler=run_skill_report)

    promote = skill_commands.add_parser(
        "promote", help="Atomically activate an accepted candidate."
    )
    promote.add_argument("--candidate-id", required=True)
    promote.add_argument("--reason", required=True)
    _add_skill_storage_arguments(promote)
    promote.set_defaults(handler=run_skill_promote)

    rollback = skill_commands.add_parser(
        "rollback", help="Atomically restore a promoted candidate's exact base."
    )
    rollback.add_argument("--candidate-id", required=True)
    rollback.add_argument("--reason", required=True)
    _add_skill_storage_arguments(rollback)
    rollback.set_defaults(handler=run_skill_rollback)

    memories = subcommands.add_parser(
        "memory",
        help="Classify durable evidence into typed profile or episode memory.",
    )
    memory_commands = memories.add_subparsers(
        dest="memory_command", required=True
    )
    profile = memory_commands.add_parser(
        "profile", help="Record and activate an operator-authored preference."
    )
    profile.add_argument("--memory-id", required=True)
    profile.add_argument("--evidence-id", required=True)
    profile.add_argument("--reason", required=True)
    _add_memory_storage_argument(profile)
    profile.set_defaults(handler=run_memory_profile)

    episode = memory_commands.add_parser(
        "episode", help="Record a summary linked to verified Run evidence."
    )
    episode.add_argument("path", type=Path, help="UTF-8 episode summary file.")
    episode.add_argument("--memory-id", required=True)
    episode.add_argument("--evidence-id", required=True)
    _add_memory_storage_argument(episode)
    episode.set_defaults(handler=run_memory_episode)

    memory_inspect = memory_commands.add_parser(
        "inspect", help="Inspect an exact current memory snapshot as JSON."
    )
    memory_inspect.add_argument(
        "--profile-id",
        action="append",
        help="Active profile ID; omit to include all active profiles.",
    )
    memory_inspect.add_argument(
        "--episode-id",
        action="append",
        default=[],
        help="Episode ID to include; repeat as needed.",
    )
    memory_inspect.add_argument(
        "--include-content",
        action="store_true",
        help="Include potentially sensitive memory content.",
    )
    _add_memory_storage_argument(memory_inspect)
    memory_inspect.set_defaults(handler=run_memory_inspect)

    knowledge = subcommands.add_parser(
        "knowledge",
        help="Manage source-bound meeting and attachment knowledge.",
    )
    knowledge_commands = knowledge.add_subparsers(
        dest="knowledge_command", required=True
    )
    knowledge_ingest = knowledge_commands.add_parser(
        "ingest", help="Install and activate one immutable local document version."
    )
    knowledge_ingest.add_argument("path", type=Path)
    knowledge_ingest.add_argument("--document-id", required=True)
    knowledge_ingest.add_argument("--title", required=True)
    knowledge_ingest.add_argument("--source-uri", required=True)
    knowledge_ingest.add_argument(
        "--source-kind",
        required=True,
        choices=tuple(item.value for item in KnowledgeSourceKind),
    )
    knowledge_ingest.add_argument("--reason", required=True)
    _add_knowledge_storage_argument(knowledge_ingest)
    knowledge_ingest.set_defaults(handler=run_knowledge_ingest)

    knowledge_search = knowledge_commands.add_parser(
        "search", help="Search active document versions with source citations."
    )
    knowledge_search.add_argument("query")
    knowledge_search.add_argument("--limit", type=int, default=5)
    _add_knowledge_storage_argument(knowledge_search)
    knowledge_search.set_defaults(handler=run_knowledge_search)

    knowledge_inspect = knowledge_commands.add_parser(
        "inspect", help="Inspect one active version; content is redacted by default."
    )
    knowledge_inspect.add_argument("--document-id", required=True)
    knowledge_inspect.add_argument("--include-content", action="store_true")
    _add_knowledge_storage_argument(knowledge_inspect)
    knowledge_inspect.set_defaults(handler=run_knowledge_inspect)
    return parser


def _add_skill_storage_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(".voren/voren.sqlite3"),
        help="SQLite Skill and candidate database.",
    )
    parser.add_argument(
        "--skill-store",
        type=Path,
        default=Path(".voren/skills"),
        help="Content-addressed immutable Skill object directory.",
    )


def _add_runtime_skill_arguments(parser: argparse.ArgumentParser) -> None:
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--skill",
        action="append",
        help=(
            "Explicit active Skill name; repeat to freeze multiple Skills. "
            "Omit to use conservative metadata routing."
        ),
    )
    selection.add_argument(
        "--no-skill",
        action="store_true",
        help="Disable runtime Skill routing for this request.",
    )
    parser.add_argument(
        "--skill-store",
        type=Path,
        default=Path(".voren/skills"),
        help="Content-addressed immutable Skill object directory.",
    )


def _add_memory_storage_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(".voren/voren.sqlite3"),
        help="SQLite evidence, memory, and run database.",
    )


def _add_knowledge_storage_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(".voren/voren.sqlite3"),
        help="SQLite knowledge and runtime database.",
    )


def run_agentdojo(
    args: argparse.Namespace,
    *,
    model: ModelAdapter | None = None,
    cancellation: CancellationToken | None = None,
    transcript_key: str | None = None,
    approval_reader: ApprovalReader = input,
    output: Output = print,
) -> int:
    resolved_model, model_name, provider_name = _resolve_run_model(args, model)
    workspace = AgentDojoWorkspaceAdapter()
    calendar_action = create_calendar_event_definition(
        account_email=workspace.account_email
    )
    email_action = send_email_definition()
    return _run_workspace_request(
        args,
        model=resolved_model,
        model_name=model_name,
        provider_name=provider_name,
        action_adapter=workspace,
        read_tools=AgentDojoReadAdapter(workspace),
        action_definitions=(calendar_action, email_action),
        action_descriptions={
            calendar_action.name: (
                "Propose a calendar event and its invitation email. Execution "
                "always requires exact-effect operator approval."
            ),
            email_action.name: (
                "Propose an outbound email. Execution always requires "
                "exact-effect operator approval."
            ),
        },
        workflow="agentdojo_email_calendar",
        world_adapter="agentdojo_workspace_v1.2.2",
        action_contract_versions=(WORKSPACE_CONTRACT_VERSION,),
        cancellation=cancellation,
        transcript_key=transcript_key,
        approval_reader=approval_reader,
        output=output,
    )


def run_google(
    args: argparse.Namespace,
    *,
    model: ModelAdapter | None = None,
    connector: GoogleWorkspaceConnector | None = None,
    cancellation: CancellationToken | None = None,
    transcript_key: str | None = None,
    approval_reader: ApprovalReader = input,
    output: Output = print,
) -> int:
    """Run the same controlled loop against a real Google REST boundary."""

    model_was_injected = model is not None
    connector_was_injected = connector is not None
    resolved_model, model_name, provider_name = _resolve_run_model(args, model)
    active_connector = connector or GoogleWorkspaceConnector(
        GoogleWorkspaceConfig.from_environment()
    )
    resolved_endpoint = resolve_responses_endpoint(
        base_url=args.base_url,
        provider_profile=getattr(args, "provider_profile", None),
    ).endpoint
    source_revision = detect_source_revision(Path.cwd())
    draft_action, calendar_action = active_connector.action_definitions
    return _run_workspace_request(
        args,
        model=resolved_model,
        model_name=model_name,
        provider_name=provider_name,
        action_adapter=active_connector,
        read_tools=active_connector,
        action_definitions=(draft_action, calendar_action),
        action_descriptions={
            draft_action.name: (
                "Propose a Gmail draft. This never sends the email and always "
                "requires exact-effect operator approval."
            ),
            calendar_action.name: (
                "Propose a private Google Calendar hold with no attendees and no "
                "requested invitations. Execution always requires exact-effect "
                "operator approval."
            ),
        },
        workflow="google_email_calendar",
        world_adapter="google_workspace_rest_v1",
        action_contract_versions=(GOOGLE_WORKSPACE_CONTRACT_VERSION,),
        execution_metadata={
            "provider_endpoint": resolved_endpoint,
            "code_revision": source_revision.revision,
            "code_dirty": source_revision.dirty,
            "google_connector_boundary": (
                "injected_connector"
                if connector_was_injected
                else "environment_google_rest"
            ),
            "model_adapter_boundary": (
                "injected_model"
                if model_was_injected
                else "environment_responses_api"
            ),
        },
        cancellation=cancellation,
        transcript_key=transcript_key,
        approval_reader=approval_reader,
        output=output,
    )


def _resolve_run_model(
    args: argparse.Namespace,
    model: ModelAdapter | None,
) -> tuple[ModelAdapter, str, str]:
    model_name = args.model or os.environ.get("VOREN_MODEL")
    if not model_name:
        raise ModelConfigurationError("pass --model or set VOREN_MODEL")
    if model is None:
        model = OpenAIResponsesModelAdapter.from_environment(
            model=model_name,
            base_url=args.base_url,
            timeout_seconds=args.timeout_seconds,
            max_output_tokens=args.max_output_tokens,
            provider_profile=getattr(args, "provider_profile", None),
        )
    provider_name = (
        model.provider_profile.value
        if isinstance(model, OpenAIResponsesModelAdapter)
        else "injected_model_adapter"
    )
    return model, model_name, provider_name


def _run_workspace_request(
    args: argparse.Namespace,
    *,
    model: ModelAdapter,
    model_name: str,
    provider_name: str,
    action_adapter: ActionAdapter,
    read_tools: ReadToolAdapter,
    action_definitions: tuple[ActionDefinition, ...],
    action_descriptions: Mapping[str, str],
    workflow: str,
    world_adapter: str,
    action_contract_versions: tuple[str, ...],
    execution_metadata: Mapping[str, Any] | None = None,
    cancellation: CancellationToken | None,
    transcript_key: str | None,
    approval_reader: ApprovalReader,
    output: Output,
) -> int:
    args.database.parent.mkdir(parents=True, exist_ok=True)
    ledger = SQLiteOperationLedger(args.database)
    store = SQLiteRunStore(args.database)
    encoded_transcript_key = transcript_key or os.environ.get(
        "VOREN_TRANSCRIPT_KEY"
    )
    if not encoded_transcript_key:
        store.close()
        ledger.close()
        raise TranscriptKeyError(
            "set VOREN_TRANSCRIPT_KEY to a URL-safe base64-encoded 32-byte key"
        )
    try:
        transcript_store = SQLiteTranscriptStore.from_base64_key(
            args.database, encoded_key=encoded_transcript_key
        )
    except Exception:
        store.close()
        ledger.close()
        raise
    memory_store: SQLiteMemoryStore | None = None
    try:
        memory_store = SQLiteMemoryStore(args.database)
        profile_ids = tuple(getattr(args, "profile_memory", ()))
        episode_ids = tuple(getattr(args, "episode_memory", ()))
        memory_context = (
            MemoryContextAssembler(memory_store).current(
                profile_ids=profile_ids,
                episode_ids=episode_ids,
            )
            if profile_ids or episode_ids
            else MemoryContextAssembler(memory_store).empty()
        )
        available_tools = tuple(
            definition.name for definition in read_tools.definitions
        ) + tuple(definition.name for definition in action_definitions)
        skill_context, skill_route = _select_runtime_skill_context(
            args,
            request=args.request,
            available_tools=available_tools,
        )
        _print_skill_route(skill_route, output)
        gateway = ActionGateway(
            definitions=action_definitions,
            adapter=action_adapter,
            ledger=ledger,
        )
        manager = RunManager(
            store=store,
            operation_ledger=ledger,
            action_gateway=gateway,
        )
        loop = AgentLoop(
            model=model,
            read_tools=read_tools,
            action_tools=tuple(
                external_action_tool(
                    definition,
                    description=action_descriptions[definition.name],
                )
                for definition in action_definitions
            ),
            run_manager=manager,
            transcript_store=transcript_store,
            memory_context=memory_context,
            skill_context=skill_context,
            limits=RuntimeLimits(
                max_model_steps=args.max_model_steps,
                max_tool_calls=args.max_tool_calls,
            ),
        )
        result = loop.run(
            user_request=args.request,
            config=RunConfig(
                workflow=workflow,
                world_adapter=world_adapter,
                policy_version="provenance-and-exact-effects-v1",
                action_contract_versions=action_contract_versions,
                memory_versions=memory_context.memory_versions,
                skill_versions=skill_context.skill_versions,
                metadata={
                    "model": model_name,
                    "provider": provider_name,
                    "skill_routing": skill_route.model_dump(mode="json"),
                    **(execution_metadata or {}),
                },
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
            loop.discard_checkpoint(result.run_id)
            output("action rejected; no external commit was attempted")
            _print_trace(store, result.run_id, output)
            return 2

        loop.discard_checkpoint(result.run_id)
        output(f"receipt: {receipt.status.value}")
        output(f"verification passed: {receipt.verification.passed}")
        _print_trace(store, result.run_id, output)
        return 0 if receipt.verification.passed else 1
    finally:
        if memory_store is not None:
            memory_store.close()
        transcript_store.close()
        store.close()
        ledger.close()


def run_google_smoke_export(
    args: argparse.Namespace,
    *,
    output: Output = print,
) -> int:
    """Export only a complete, clean-revision, live-boundary Google suite."""

    store = SQLiteRunStore(args.database)
    try:
        runs = tuple(
            collect_google_smoke_run(
                store.get_run(run_id),
                store.list_events(run_id),
            )
            for run_id in args.run_id
        )
        artifact = GoogleSmokeArtifact.create(runs=runs)
        write_google_smoke_artifact(args.output, artifact)
        output(f"Google smoke artifact: {args.output}")
        output(f"artifact digest: {artifact.artifact_digest}")
        return 0
    finally:
        store.close()


def _select_runtime_skill_context(
    args: argparse.Namespace,
    *,
    request: str,
    available_tools: tuple[str, ...],
) -> tuple[SkillContextSnapshot, SkillRouteDecision]:
    skill_store = SQLiteSkillStore(
        args.database,
        root=Path(getattr(args, "skill_store", Path(".voren/skills"))),
        parser=AgentSkillParser(),
    )
    try:
        router = SkillRouter(skill_store)
        explicit_names = tuple(getattr(args, "skill", None) or ())
        if getattr(args, "no_skill", False):
            decision = router.disabled(
                request=request,
                available_tools=available_tools,
            )
        elif explicit_names:
            decision = router.explicit(
                request=request,
                available_tools=available_tools,
                names=explicit_names,
            )
        else:
            decision = router.auto(
                request=request,
                available_tools=available_tools,
            )
        context = (
            SkillContextAssembler(skill_store).from_frozen(
                decision.selected_versions
            )
            if decision.selected_versions
            else SkillContextSnapshot.no_skill()
        )
        return context, decision
    finally:
        skill_store.close()


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
    resolved_endpoint = resolve_responses_endpoint(
        base_url=args.base_url,
        provider_profile=getattr(args, "provider_profile", None),
    )
    manifest = phase1_smoke_manifest()
    modes = tuple(EvaluationMode(value) for value in args.mode)
    if len(modes) != len(set(modes)):
        raise ValueError("evaluation modes must be unique")
    case_ids = tuple(args.case)
    if "all" in case_ids and case_ids != ("all",):
        raise ValueError("'all' cannot be combined with explicit case IDs")
    selections = select_trials(manifest, case_ids=case_ids, modes=modes)
    skill_names = tuple(args.skill)
    if len(skill_names) != len(set(skill_names)):
        raise ValueError("evaluation skill names must be unique")
    if skill_names:
        args.database.parent.mkdir(parents=True, exist_ok=True)
        skill_store = SQLiteSkillStore(
            args.database,
            root=args.skill_store,
            parser=AgentSkillParser(),
        )
        try:
            skill_context = SkillContextAssembler(skill_store).static_skill(
                skill_names
            )
        finally:
            skill_store.close()
    else:
        skill_context = SkillContextSnapshot.no_skill()

    resolved_model_factory = model_factory
    if resolved_model_factory is None:

        def create_model(_case, _mode):
            return OpenAIResponsesModelAdapter.from_environment(
                model=model_name,
                base_url=args.base_url,
                timeout_seconds=args.timeout_seconds,
                max_output_tokens=args.max_output_tokens,
                provider_profile=getattr(args, "provider_profile", None),
            )

        resolved_model_factory = create_model

    source = detect_source_revision(Path.cwd())
    prompt_digest, tool_digest, attack_digest = evaluation_input_digests(
        skill_context
    )
    config = ExperimentConfig(
        experiment_id=args.experiment_id or f"eval-{uuid4()}",
        created_at=datetime.now(UTC),
        code_revision=source.revision,
        code_dirty=source.dirty,
        provider=resolved_endpoint.profile.value,
        model=model_name,
        endpoint=resolved_endpoint.endpoint,
        manifest_id=manifest.manifest_id,
        manifest_digest=manifest.calculated_digest(),
        system_prompt_digest=prompt_digest,
        tool_schema_digest=tool_digest,
        dataset=manifest.dataset,
        dataset_version=manifest.dataset_version,
        attack_template_version=manifest.attack_template_version,
        attack_template_digest=attack_digest,
        selected_trials=tuple(
            EvaluationSelection(case_id=case.case_id, mode=mode)
            for case, mode in selections
        ),
        sampling={
            "max_output_tokens": args.max_output_tokens,
            "max_model_steps": args.max_model_steps,
            "max_tool_calls": args.max_tool_calls,
            "timeout_seconds": args.timeout_seconds,
            "temperature": "provider_default",
            "seed": None,
            "skill_context": {
                "mode": skill_context.mode.value,
                "skill_versions": [
                    ref.model_dump(mode="json")
                    for ref in skill_context.skill_versions
                ],
                "context_digest": skill_context.context_digest,
                "instruction_bytes": skill_context.instruction_bytes,
            },
        },
    )
    runner = AgentDojoEvaluationRunner(
        model_factory=resolved_model_factory,
        database=args.database,
        limits=RuntimeLimits(
            max_model_steps=args.max_model_steps,
            max_tool_calls=args.max_tool_calls,
        ),
        skill_context=skill_context,
    )
    artifact = runner.run(
        config=config,
        manifest=manifest,
        selections=selections,
    )
    write_artifact(args.output, artifact)
    output(f"selected trials: {len(selections)} supported case/mode pair(s)")
    output(f"artifact: {args.output}")
    output(f"artifact digest: {artifact.artifact_digest}")
    output(f"code revision: {source.revision} (dirty={source.dirty})")
    output(
        "skill context: "
        f"{skill_context.mode.value} "
        f"({len(skill_context.skill_versions)} exact version(s), "
        f"digest={skill_context.context_digest})"
    )
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


def run_skill_install(
    args: argparse.Namespace,
    *,
    output: Output = print,
) -> int:
    if args.activate and not (args.reason or "").strip():
        raise ValueError("--activate requires --reason")
    if not args.activate and args.reason is not None:
        raise ValueError("--reason is only valid together with --activate")
    args.database.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteSkillStore(
        args.database,
        root=args.skill_store,
        parser=AgentSkillParser(),
    )
    try:
        version = store.install(AgentSkillParser().load(args.path))
        output(
            f"installed: {version.ref.name}@{version.ref.version_id} (inactive)"
        )
        if args.activate:
            store.activate(version.ref, reason=args.reason)
            output(f"active: {version.ref.name}@{version.ref.version_id}")
        return 0
    finally:
        store.close()


def run_skill_stage(
    args: argparse.Namespace,
    *,
    output: Output = print,
) -> int:
    skills, candidates = _open_skill_stores(args)
    try:
        base_ref = skills.freeze_active((args.base,))[0]
        evidence = tuple(
            candidates.get_evidence(evidence_id).as_ref()
            for evidence_id in args.evidence_id
        )
        candidate = SkillCandidateService(
            skills=skills,
            candidates=candidates,
            require_persisted_evidence=True,
        ).stage(
            candidate_id=args.candidate_id,
            base_ref=base_ref,
            package=AgentSkillParser().load(args.path),
            evidence=evidence,
        )
        output(f"candidate: {candidate.candidate_id}")
        output(f"status: {candidate.status.value}")
        output(f"base: {candidate.base_ref.version_id}")
        output(f"candidate version: {candidate.candidate_ref.version_id}")
        output(f"diff digest: {candidate.diff.diff_digest}")
        return 0
    finally:
        candidates.close()
        skills.close()


def run_skill_evidence_correction(
    args: argparse.Namespace,
    *,
    output: Output = print,
) -> int:
    correction = args.path.read_text(encoding="utf-8")
    skills, candidates = _open_skill_stores(args)
    try:
        evidence = DurableLearningRouter(
            evidence_store=candidates
        ).record_operator_correction(
            evidence_id=args.evidence_id,
            correction=correction,
            operator_ref=args.operator,
        )
        output(f"evidence: {evidence.evidence_id}")
        output(f"source: {evidence.source.value}")
        output(f"artifact digest: {evidence.digest}")
        return 0
    finally:
        candidates.close()
        skills.close()


def run_skill_evidence_run(
    args: argparse.Namespace,
    *,
    output: Output = print,
) -> int:
    skills, candidates = _open_skill_stores(args)
    runs = SQLiteRunStore(args.database)
    try:
        evidence = DurableLearningRouter(
            evidence_store=candidates,
            run_store=runs,
        ).select_verified_run(
            evidence_id=args.evidence_id,
            run_id=args.run_id,
            evaluation_case_ids=tuple(args.evidence_case),
        )
        output(f"evidence: {evidence.evidence_id}")
        output(f"source: {evidence.source.value}")
        output(f"artifact digest: {evidence.digest}")
        return 0
    finally:
        runs.close()
        candidates.close()
        skills.close()


def run_skill_agentdojo_evaluation(
    args: argparse.Namespace,
    *,
    model_factory: SkillModelFactory | None = None,
    output: Output = print,
) -> int:
    model_name = args.model or os.environ.get("VOREN_MODEL")
    if not model_name:
        raise ModelConfigurationError("pass --model or set VOREN_MODEL")
    resolved_endpoint = resolve_responses_endpoint(
        base_url=args.base_url,
        provider_profile=getattr(args, "provider_profile", None),
    )
    manifest = phase1_smoke_manifest()
    case_ids = tuple(args.case)
    if "all" in case_ids and case_ids != ("all",):
        raise ValueError("'all' cannot be combined with explicit case IDs")
    selected_ids = (
        tuple(case.case_id for case in manifest.cases)
        if case_ids == ("all",)
        else case_ids
    )
    if len(selected_ids) != len(set(selected_ids)):
        raise ValueError("candidate evaluation Case IDs must be unique")
    available = {case.case_id: case for case in manifest.cases}
    selected = tuple(available[case_id] for case_id in selected_ids)
    cases = tuple(
        HeldOutCase(
            case_id=case.case_id,
            kind=(
                EvaluationCaseKind.ATTACK
                if case.injection_task_id is not None
                else EvaluationCaseKind.BENIGN
            ),
        )
        for case in selected
    )
    if not any(case.kind is EvaluationCaseKind.BENIGN for case in cases):
        raise ValueError("candidate evaluation requires at least one benign Case")
    if not any(case.kind is EvaluationCaseKind.ATTACK for case in cases):
        raise ValueError("candidate evaluation requires at least one attack Case")

    skills, candidates = _open_skill_stores(args)
    try:
        candidate = candidates.get(args.candidate_id)
        if candidate.status is not CandidateStatus.STAGED:
            raise ValueError("only a staged candidate can start paid evaluation")
        contract = skills.get_version(candidate.base_ref).contract
        if args.suite not in contract.evaluation_suites:
            raise ValueError("--suite is not declared by the Skill contract")
        resolved_factory = model_factory
        if resolved_factory is None:

            def create_model(_ref, _case, _mode):
                return OpenAIResponsesModelAdapter.from_environment(
                    model=model_name,
                    base_url=args.base_url,
                    timeout_seconds=args.timeout_seconds,
                    max_output_tokens=args.max_output_tokens,
                    provider_profile=getattr(args, "provider_profile", None),
                )

            resolved_factory = create_model

        source = detect_source_revision(Path.cwd())
        evaluation_id = args.evaluation_id or f"candidate-eval-{uuid4()}"
        evaluator = AgentDojoSkillEvaluator(
            evaluation_id=evaluation_id,
            skills=skills,
            manifest=manifest,
            model_factory=resolved_factory,
            database_directory=args.trial_databases,
            artifact_directory=args.trial_artifacts,
            provider=resolved_endpoint.profile.value,
            model=model_name,
            endpoint=resolved_endpoint.endpoint,
            code_revision=source.revision,
            code_dirty=source.dirty,
            sampling={
                "max_output_tokens": args.max_output_tokens,
                "max_model_steps": args.max_model_steps,
                "max_tool_calls": args.max_tool_calls,
                "timeout_seconds": args.timeout_seconds,
                "temperature": "provider_default",
                "seed": None,
            },
            limits=RuntimeLimits(
                max_model_steps=args.max_model_steps,
                max_tool_calls=args.max_tool_calls,
            ),
        )
        artifact = PairedEvaluationRunner().run(
            evaluation_id=evaluation_id,
            candidate=candidate,
            suite_id=args.suite,
            manifest_digest=manifest.calculated_digest(),
            cases=cases,
            evaluator=evaluator,
            created_at=datetime.now(UTC),
        )
        write_candidate_evaluation(args.output, artifact)
        output(f"candidate evaluation: {args.output}")
        output(f"artifact digest: {artifact.artifact_digest}")
        output(f"candidate remains: {candidate.status.value}")
        for pair in artifact.pairs:
            output(
                _candidate_pair_summary(pair.case.case_id, "base", pair.base)
            )
            output(
                _candidate_pair_summary(
                    pair.case.case_id, "candidate", pair.candidate
                )
            )
        output(
            "next: review the Artifact, then run 'voren skill decide'; "
            "evaluation never promotes automatically"
        )
        return 0
    finally:
        candidates.close()
        skills.close()


def _candidate_pair_summary(case_id: str, label: str, result) -> str:
    measurement = result.measurement
    if measurement.infrastructure_error_code is not None:
        return (
            f"{case_id} {label}: infrastructure_error="
            f"{measurement.infrastructure_error_code}"
        )
    attack = (
        "n/a"
        if measurement.attack_success is None
        else str(measurement.attack_success).lower()
    )
    return (
        f"{case_id} {label}: utility="
        f"{str(measurement.utility_passed).lower()}, attack_success={attack}, "
        f"run={measurement.run_id}"
    )


def run_skill_decide(
    args: argparse.Namespace,
    *,
    output: Output = print,
) -> int:
    artifact = read_candidate_evaluation(args.artifact)
    skills, candidates = _open_skill_stores(args)
    try:
        candidate = SkillCandidateService(
            skills=skills,
            candidates=candidates,
        ).decide(
            candidate_id=args.candidate_id,
            artifact=artifact,
        )
        output(f"candidate: {candidate.candidate_id}")
        output(f"decision: {candidate.status.value}")
        output(f"reason: {candidate.decision_reason}")
        output(f"evaluation digest: {candidate.evaluation_artifact_digest}")
        return 0 if candidate.status.value == "accepted" else 3
    finally:
        candidates.close()
        skills.close()


def run_skill_inspect(
    args: argparse.Namespace,
    *,
    output: Output = print,
) -> int:
    skills, candidates = _open_skill_stores(args)
    try:
        candidate = candidates.get(args.candidate_id)
        payload = {
            "candidate": candidate.model_dump(mode="json"),
            "evidence": [
                _evidence_for_inspection(
                    candidates.get_evidence(item.evidence_id),
                    include_payload=args.include_evidence_payload,
                )
                for item in candidate.evidence
            ],
            "evaluations": [
                artifact.model_dump(mode="json")
                for artifact in candidates.list_evaluations(args.candidate_id)
            ],
            "events": [
                event.model_dump(mode="json")
                for event in candidates.list_events(args.candidate_id)
            ],
        }
        output(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    finally:
        candidates.close()
        skills.close()


def _evidence_for_inspection(artifact, *, include_payload: bool) -> dict:
    payload = artifact.model_dump(mode="json")
    payload["payload_bytes"] = len(artifact.payload.encode("utf-8"))
    if not include_payload:
        payload.pop("payload")
    return payload


def run_skill_report(
    args: argparse.Namespace,
    *,
    output: Output = print,
) -> int:
    skills, candidates = _open_skill_stores(args)
    try:
        candidate = candidates.get(args.candidate_id)
        evidence = tuple(
            candidates.get_evidence(item.evidence_id)
            for item in candidate.evidence
        )
        evaluations = candidates.list_evaluations(candidate.candidate_id)
        events = candidates.list_events(candidate.candidate_id)
        report = render_candidate_report(
            candidate=candidate,
            evidence=evidence,
            evaluations=evaluations,
            events=events,
        )
        digest = write_candidate_report(args.output, report)
        output(f"candidate report: {args.output}")
        output(f"report digest: {digest}")
        return 0
    finally:
        candidates.close()
        skills.close()


def run_skill_promote(
    args: argparse.Namespace,
    *,
    output: Output = print,
) -> int:
    skills, candidates = _open_skill_stores(args)
    try:
        candidate = SkillCandidateService(
            skills=skills,
            candidates=candidates,
        ).promote(candidate_id=args.candidate_id, reason=args.reason)
        output(f"candidate: {candidate.candidate_id}")
        output(f"status: {candidate.status.value}")
        output(
            f"active: {candidate.candidate_ref.name}@"
            f"{candidate.candidate_ref.version_id}"
        )
        return 0
    finally:
        candidates.close()
        skills.close()


def run_skill_rollback(
    args: argparse.Namespace,
    *,
    output: Output = print,
) -> int:
    skills, candidates = _open_skill_stores(args)
    try:
        candidate = SkillCandidateService(
            skills=skills,
            candidates=candidates,
        ).rollback(candidate_id=args.candidate_id, reason=args.reason)
        output(f"candidate: {candidate.candidate_id}")
        output(f"status: {candidate.status.value}")
        output(
            f"active: {candidate.base_ref.name}@{candidate.base_ref.version_id}"
        )
        return 0
    finally:
        candidates.close()
        skills.close()


def run_memory_profile(
    args: argparse.Namespace,
    *,
    output: Output = print,
) -> int:
    args.database.parent.mkdir(parents=True, exist_ok=True)
    evidence = SQLiteCandidateStore(args.database)
    memories = SQLiteMemoryStore(args.database)
    try:
        record = MemoryService(
            memories=memories,
            evidence_store=evidence,
        ).record_profile_preference(
            memory_id=args.memory_id,
            evidence_id=args.evidence_id,
            reason=args.reason,
        )
        output(f"profile: {record.ref.memory_id}@{record.ref.version_id}")
        output(f"evidence: {record.evidence_id}@{record.evidence_digest}")
        output("instruction authority: false")
        return 0
    finally:
        memories.close()
        evidence.close()


def run_memory_episode(
    args: argparse.Namespace,
    *,
    output: Output = print,
) -> int:
    summary = args.path.read_text(encoding="utf-8")
    args.database.parent.mkdir(parents=True, exist_ok=True)
    evidence = SQLiteCandidateStore(args.database)
    memories = SQLiteMemoryStore(args.database)
    try:
        record = MemoryService(
            memories=memories,
            evidence_store=evidence,
        ).record_episode_summary(
            memory_id=args.memory_id,
            evidence_id=args.evidence_id,
            summary=summary,
        )
        output(f"episode: {record.ref.memory_id}@{record.ref.version_id}")
        output(f"evidence: {record.evidence_id}@{record.evidence_digest}")
        output("instruction authority: false")
        return 0
    finally:
        memories.close()
        evidence.close()


def run_memory_inspect(
    args: argparse.Namespace,
    *,
    output: Output = print,
) -> int:
    memories = SQLiteMemoryStore(args.database)
    try:
        refs = memories.freeze(
            profile_ids=(
                None if args.profile_id is None else tuple(args.profile_id)
            ),
            episode_ids=tuple(args.episode_id),
        )
        snapshot = MemoryContextAssembler(memories).from_frozen(refs)
        records = []
        for ref in refs:
            record = memories.get(ref)
            item = record.model_dump(mode="json")
            item["content_bytes"] = len(record.content.encode("utf-8"))
            if not args.include_content:
                item.pop("content")
            records.append(item)
        output(
            json.dumps(
                {
                    "memory_versions": [
                        ref.model_dump(mode="json") for ref in refs
                    ],
                    "context_digest": snapshot.context_digest,
                    "context_bytes": snapshot.context_bytes,
                    "records": records,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    finally:
        memories.close()


def run_knowledge_ingest(
    args: argparse.Namespace,
    *,
    output: Output = print,
) -> int:
    document = KnowledgeDocument.create(
        document_id=args.document_id,
        title=args.title,
        source_uri=args.source_uri,
        source_kind=KnowledgeSourceKind(args.source_kind),
        content=args.path.read_text(encoding="utf-8"),
        created_at=datetime.now(UTC),
    )
    store = SQLiteKnowledgeStore(args.database)
    try:
        store.install(document)
        store.activate(document.ref, reason=args.reason)
        output(
            f"knowledge: {document.ref.document_id}@{document.ref.version_id}"
        )
        output(f"source: {document.source_uri}")
        output(f"content digest: {document.content_digest}")
        output("instruction authority: false")
        return 0
    finally:
        store.close()


def run_knowledge_search(
    args: argparse.Namespace,
    *,
    output: Output = print,
) -> int:
    store = SQLiteKnowledgeStore(args.database)
    try:
        hits = store.search(args.query, limit=args.limit)
        output(
            json.dumps(
                {
                    "query": args.query,
                    "results": [hit.model_dump(mode="json") for hit in hits],
                    "instruction_authority": False,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    finally:
        store.close()


def run_knowledge_inspect(
    args: argparse.Namespace,
    *,
    output: Output = print,
) -> int:
    store = SQLiteKnowledgeStore(args.database)
    try:
        document = store.get_active(args.document_id)
        payload = document.model_dump(mode="json")
        payload["content_bytes"] = len(document.content.encode("utf-8"))
        if not args.include_content:
            payload.pop("content")
        output(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    finally:
        store.close()


def _open_skill_stores(
    args: argparse.Namespace,
) -> tuple[SQLiteSkillStore, SQLiteCandidateStore]:
    args.database.parent.mkdir(parents=True, exist_ok=True)
    skills = SQLiteSkillStore(
        args.database,
        root=args.skill_store,
        parser=AgentSkillParser(),
    )
    try:
        candidates = SQLiteCandidateStore(args.database)
    except Exception:
        skills.close()
        raise
    return skills, candidates


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


def _print_skill_route(decision: SkillRouteDecision, output: Output) -> None:
    selected = ",".join(
        f"{ref.name}@{ref.version_id[:12]}"
        for ref in decision.selected_versions
    ) or "none"
    detail = ""
    if decision.ambiguous_skills:
        detail = "; ambiguous=" + ",".join(decision.ambiguous_skills)
    output(
        f"skill routing: {decision.mode.value}; selected={selected}{detail}; "
        f"digest={decision.decision_digest}"
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
    except (
        ModelConfigurationError,
        AgentDojoDependencyError,
        CandidateStoreError,
        GoogleWorkspaceConfigurationError,
        KnowledgeStoreError,
        MemoryStoreError,
        SkillFormatError,
        TranscriptKeyError,
        ValueError,
    ) as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
