"""Application service joining HTTP idempotency to the controlled Agent Loop."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from threading import RLock
from uuid import uuid4

from voren.actions.errors import ApprovalRejectedError
from voren.actions.gateway import ActionDefinition, ActionGateway
from voren.actions.ledger import SQLiteOperationLedger
from voren.actions.models import ApprovalDecision
from voren.actions.ports import ActionAdapter
from voren.adapters.agentdojo_reads import AgentDojoReadAdapter
from voren.adapters.agentdojo_workspace import AgentDojoWorkspaceAdapter
from voren.adapters.google_workspace import (
    GOOGLE_WORKSPACE_CONTRACT_VERSION,
    GoogleWorkspaceConfig,
    GoogleWorkspaceConnector,
)
from voren.adapters.workspace_contracts import (
    WORKSPACE_CONTRACT_VERSION,
    create_calendar_event_definition,
    send_email_definition,
)
from voren.knowledge.store import SQLiteKnowledgeStore
from voren.memory.context import MemoryContextAssembler, MemoryContextSnapshot
from voren.memory.store import SQLiteMemoryStore
from voren.mcp_bridge.adapter import MCPKnowledgeReadAdapter
from voren.mcp_bridge.server import create_knowledge_mcp_server
from voren.observations.read_tools import (
    CompositeReadToolAdapter,
    ReadToolAdapter,
)
from voren.runs.manager import RunManager
from voren.runs.models import RunConfig, RunEvent
from voren.runs.store import SQLiteRunStore
from voren.runtime.agent_loop import AgentLoop
from voren.runtime.models import RuntimeLimits, RuntimeResultStatus
from voren.runtime.ports import ModelAdapter
from voren.runtime.tools import external_action_tool
from voren.skills.context import SkillContextAssembler, SkillContextSnapshot
from voren.skills.parser import AgentSkillParser
from voren.skills.routing import (
    SkillRouteDecision,
    SkillRouter,
    SkillRoutingMode,
)
from voren.skills.store import SQLiteSkillStore
from voren.web.index import (
    SQLiteWebRunIndex,
    WebRequestInProgressError,
)
from voren.web.models import CreateRunRequest, DecideRunRequest, RunView


ModelFactory = Callable[[], ModelAdapter]
WorkspaceFactory = Callable[[], "WebWorkspaceRuntime"]


class WebProfileMemoryMode(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class WebWorkspaceRuntime:
    name: str
    world_adapter: str
    action_adapter: ActionAdapter
    read_tools: ReadToolAdapter
    action_definitions: tuple[ActionDefinition, ...]
    action_descriptions: Mapping[str, str]
    contract_versions: tuple[str, ...]
    recoverable_after_restart: bool


def create_agentdojo_web_workspace() -> WebWorkspaceRuntime:
    workspace = AgentDojoWorkspaceAdapter()
    calendar_action = create_calendar_event_definition(
        account_email=workspace.account_email
    )
    email_action = send_email_definition()
    return WebWorkspaceRuntime(
        name="agentdojo",
        world_adapter="agentdojo_workspace_v1.2.2+mcp-knowledge",
        action_adapter=workspace,
        read_tools=AgentDojoReadAdapter(workspace),
        action_definitions=(calendar_action, email_action),
        action_descriptions={
            calendar_action.name: (
                "Propose a calendar event and invitation email. Execution "
                "requires exact-effect operator approval."
            ),
            email_action.name: (
                "Propose an outbound email. Execution requires exact-effect "
                "operator approval."
            ),
        },
        contract_versions=(WORKSPACE_CONTRACT_VERSION,),
        recoverable_after_restart=False,
    )


def create_google_web_workspace(
    config: GoogleWorkspaceConfig | None = None,
    *,
    connector: GoogleWorkspaceConnector | None = None,
) -> WebWorkspaceRuntime:
    workspace = connector or GoogleWorkspaceConnector(
        config or GoogleWorkspaceConfig.from_environment()
    )
    draft_action, calendar_action = workspace.action_definitions
    return WebWorkspaceRuntime(
        name="google",
        world_adapter="google_workspace_rest_v1+mcp-knowledge",
        action_adapter=workspace,
        read_tools=workspace,
        action_definitions=(draft_action, calendar_action),
        action_descriptions={
            draft_action.name: (
                "Propose a Gmail draft. This never sends the message and "
                "requires exact-effect operator approval."
            ),
            calendar_action.name: (
                "Propose a private Google Calendar hold with no attendees or "
                "invitation request. Execution requires exact-effect approval."
            ),
        },
        contract_versions=(GOOGLE_WORKSPACE_CONTRACT_VERSION,),
        recoverable_after_restart=True,
    )


class WebDecisionConflictError(RuntimeError):
    pass


class WebApprovalRecoveryRequiredError(RuntimeError):
    pass


class VorenWebService:
    """Keep only controlled workspace handles in memory; persist public views."""

    def __init__(
        self,
        *,
        database: Path,
        model_factory: ModelFactory,
        mode: str,
        workspace_factory: WorkspaceFactory = create_agentdojo_web_workspace,
        workspace_name: str = "agentdojo",
        workspace_recoverable: bool = False,
        knowledge_database: Path | None = None,
        memory_database: Path | None = None,
        profile_memory_mode: WebProfileMemoryMode = WebProfileMemoryMode.ACTIVE,
        skill_database: Path | None = None,
        skill_store_root: Path | None = None,
        skill_routing_mode: SkillRoutingMode = SkillRoutingMode.AUTO,
        limits: RuntimeLimits | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.database = database
        self.mode = mode
        self.workspace_name = workspace_name
        self.workspace_recoverable = workspace_recoverable
        self.knowledge_database = knowledge_database or database
        self.knowledge_database.parent.mkdir(parents=True, exist_ok=True)
        self.memory_database = memory_database or database
        self.memory_database.parent.mkdir(parents=True, exist_ok=True)
        if profile_memory_mode not in {
            WebProfileMemoryMode.ACTIVE,
            WebProfileMemoryMode.DISABLED,
        }:
            raise ValueError("Web Profile Memory supports only active or disabled")
        self.profile_memory_mode = profile_memory_mode
        self.skill_database = skill_database or database
        self.skill_database.parent.mkdir(parents=True, exist_ok=True)
        self.skill_store_root = skill_store_root or (database.parent / "skills")
        if skill_routing_mode not in {
            SkillRoutingMode.AUTO,
            SkillRoutingMode.DISABLED,
        }:
            raise ValueError("Web Skill routing supports only auto or disabled")
        self.skill_routing_mode = skill_routing_mode
        self._model_factory = model_factory
        self._workspace_factory = workspace_factory
        self._limits = limits or RuntimeLimits()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._index = SQLiteWebRunIndex(database)
        self._pending_workspaces: dict[str, WebWorkspaceRuntime] = {}
        self._lock = RLock()

    def submit(self, request: CreateRunRequest) -> RunView:
        normalized = request.request.strip()
        request_digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        proposed_run_id = f"web-{uuid4()}"
        now = self._clock()
        run_id, existing, created = self._index.reserve(
            client_request_id=request.client_request_id,
            request_digest=request_digest,
            run_id=proposed_run_id,
            now=now,
        )
        if existing is not None:
            return self._with_recovery_state(existing)
        if not created:
            raise WebRequestInProgressError(
                f"request {request.client_request_id!r} is already running"
            )

        try:
            model = self._model_factory()
            workspace = self._create_workspace()
        except Exception:
            self._index.abandon(
                client_request_id=request.client_request_id,
                run_id=run_id,
            )
            raise
        knowledge_store = SQLiteKnowledgeStore(self.knowledge_database)
        ledger = SQLiteOperationLedger(self.database)
        run_store = SQLiteRunStore(self.database)
        try:
            gateway = ActionGateway(
                definitions=workspace.action_definitions,
                adapter=workspace.action_adapter,
                ledger=ledger,
                clock=self._clock,
            )
            manager = RunManager(
                store=run_store,
                operation_ledger=ledger,
                action_gateway=gateway,
                run_id_factory=lambda: run_id,
                clock=self._clock,
            )
            knowledge_server = create_knowledge_mcp_server(knowledge_store)
            runtime_read_tools = CompositeReadToolAdapter(
                workspace.read_tools,
                MCPKnowledgeReadAdapter(
                    knowledge_server,
                    source_id="local-knowledge",
                ),
            )
            skill_context, skill_route = self._select_skill_context(
                request=normalized,
                workspace=workspace,
                read_tools=runtime_read_tools,
            )
            memory_context = self._select_memory_context()
            loop = AgentLoop(
                model=model,
                read_tools=runtime_read_tools,
                action_tools=tuple(
                    external_action_tool(
                        definition,
                        description=workspace.action_descriptions[definition.name],
                    )
                    for definition in workspace.action_definitions
                ),
                run_manager=manager,
                limits=self._limits,
                memory_context=memory_context,
                skill_context=skill_context,
            )
            result = loop.run(
                user_request=normalized,
                config=RunConfig(
                    workflow="web_email_calendar",
                    world_adapter=workspace.world_adapter,
                    policy_version="provenance-and-approval-v1",
                    action_contract_versions=workspace.contract_versions,
                    memory_versions=memory_context.memory_versions,
                    skill_versions=skill_context.skill_versions,
                    metadata={
                        "surface": "web",
                        "mode": self.mode,
                        "workspace": workspace.name,
                        "profile_memory_mode": self.profile_memory_mode.value,
                        "skill_routing": skill_route.model_dump(mode="json"),
                    },
                ),
            )
            timestamp = self._clock()
            view = RunView(
                client_request_id=request.client_request_id,
                run_id=result.run_id,
                workspace=workspace.name,
                status=result.status.value,
                final_text=result.final_text,
                proposal=result.pending_proposal,
                usage=result.usage,
                error_code=result.error_code,
                error_detail_code=result.error_detail_code,
                memory_versions=memory_context.memory_versions,
                skill_routing=skill_route,
                created_at=now,
                updated_at=timestamp,
            )
            if result.status is RuntimeResultStatus.WAITING_APPROVAL:
                with self._lock:
                    self._pending_workspaces[result.run_id] = workspace
            self._index.save(view)
            return view
        except Exception:
            with self._lock:
                self._pending_workspaces.pop(run_id, None)
            self._index.abandon(
                client_request_id=request.client_request_id,
                run_id=run_id,
            )
            raise
        finally:
            run_store.close()
            ledger.close()
            knowledge_store.close()

    def decide(self, run_id: str, request: DecideRunRequest) -> RunView:
        with self._lock:
            return self._decide_locked(run_id, request)

    def _decide_locked(
        self, run_id: str, request: DecideRunRequest
    ) -> RunView:
        current = self._index.get(run_id)
        proposal = current.proposal
        if proposal is None:
            raise WebDecisionConflictError("run has no action proposal")
        if request.proposal_digest != proposal.digest:
            raise WebDecisionConflictError(
                "decision proposal_digest does not match the pending proposal"
            )
        if current.decision_id is not None:
            if (
                current.decision_id == request.decision_id
                and current.decision_approved is request.approved
            ):
                return current
            raise WebDecisionConflictError("run already has another decision")
        ledger = SQLiteOperationLedger(self.database)
        run_store = SQLiteRunStore(self.database)
        try:
            existing_approval = ledger.get_approval(proposal.operation_id)
            existing_receipt = ledger.get_receipt(proposal.operation_id)
            run = run_store.get_run(run_id)
            if existing_approval is not None and (
                existing_receipt is not None
                or run.status.value in {"completed", "cancelled", "needs_reconciliation"}
            ):
                if (
                    existing_approval.approval_id != request.decision_id
                    or existing_approval.proposal_digest
                    != request.proposal_digest
                    or existing_approval.approved is not request.approved
                ):
                    raise WebDecisionConflictError(
                        "durable operation already has another decision"
                    )
                restored = current.model_copy(
                    update={
                        "status": run.status.value,
                        "receipt": existing_receipt,
                        "decision_id": request.decision_id,
                        "decision_approved": request.approved,
                        "updated_at": self._clock(),
                    }
                )
                self._index.save(restored)
                self._pending_workspaces.pop(run_id, None)
                return restored

            workspace = self._pending_workspaces.get(run_id)
            if workspace is None:
                if (
                    not self.workspace_recoverable
                    or current.workspace != self.workspace_name
                ):
                    raise WebApprovalRecoveryRequiredError(
                        "the controlled workspace handle was lost after process "
                        "restart; no action was dispatched"
                    )
                workspace = self._create_workspace()
            manager = RunManager(
                store=run_store,
                operation_ledger=ledger,
                action_gateway=ActionGateway(
                    definitions=workspace.action_definitions,
                    adapter=workspace.action_adapter,
                    ledger=ledger,
                    clock=self._clock,
                ),
                clock=self._clock,
            )
            approval = ApprovalDecision.for_proposal(
                proposal,
                approval_id=request.decision_id,
                decided_by="operator:web-local",
                approved=request.approved,
                decided_at=self._clock(),
            )
            receipt = None
            try:
                receipt = manager.resume_with_approval(run_id, approval)
            except ApprovalRejectedError:
                pass
            run = run_store.get_run(run_id)
            updated = current.model_copy(
                update={
                    "status": run.status.value,
                    "receipt": receipt,
                    "decision_id": request.decision_id,
                    "decision_approved": request.approved,
                    "updated_at": self._clock(),
                }
            )
            self._index.save(updated)
            self._pending_workspaces.pop(run_id, None)
            return updated
        finally:
            run_store.close()
            ledger.close()

    def get(self, run_id: str) -> RunView:
        return self._with_recovery_state(self._index.get(run_id))

    def events(self, run_id: str, *, after: int = 0) -> tuple[RunEvent, ...]:
        try:
            self._index.get(run_id)
        except WebRequestInProgressError:
            return ()
        store = SQLiteRunStore(self.database)
        try:
            return tuple(
                event
                for event in store.list_events(run_id)
                if event.sequence > after
            )
        finally:
            store.close()

    def _with_recovery_state(self, view: RunView) -> RunView:
        if view.status != RuntimeResultStatus.WAITING_APPROVAL.value:
            return view
        with self._lock:
            recoverable = view.run_id in self._pending_workspaces
        if (
            not recoverable
            and self.workspace_recoverable
            and view.workspace == self.workspace_name
        ):
            try:
                self._create_workspace()
            except Exception:
                pass
            else:
                recoverable = True
        return view.model_copy(update={"recovery_required": not recoverable})

    def _create_workspace(self) -> WebWorkspaceRuntime:
        workspace = self._workspace_factory()
        if (
            workspace.name != self.workspace_name
            or workspace.recoverable_after_restart
            is not self.workspace_recoverable
        ):
            raise ValueError(
                "web workspace factory does not match service configuration"
            )
        return workspace

    def active_skill_count(self) -> int:
        store = SQLiteSkillStore(
            self.skill_database,
            root=self.skill_store_root,
            parser=AgentSkillParser(),
        )
        try:
            return len(store.discover_active())
        finally:
            store.close()

    def active_profile_count(self) -> int:
        store = SQLiteMemoryStore(self.memory_database)
        try:
            return len(store.freeze())
        finally:
            store.close()

    def _select_memory_context(self) -> MemoryContextSnapshot:
        store = SQLiteMemoryStore(self.memory_database)
        try:
            assembler = MemoryContextAssembler(store)
            if self.profile_memory_mode is WebProfileMemoryMode.DISABLED:
                return assembler.empty()
            return assembler.current(profile_ids=None, episode_ids=())
        finally:
            store.close()

    def _select_skill_context(
        self,
        *,
        request: str,
        workspace: WebWorkspaceRuntime,
        read_tools: ReadToolAdapter,
    ) -> tuple[SkillContextSnapshot, SkillRouteDecision]:
        available_tools = tuple(
            definition.name for definition in read_tools.definitions
        ) + tuple(
            definition.name for definition in workspace.action_definitions
        )
        store = SQLiteSkillStore(
            self.skill_database,
            root=self.skill_store_root,
            parser=AgentSkillParser(),
        )
        try:
            router = SkillRouter(store)
            decision = (
                router.auto(request=request, available_tools=available_tools)
                if self.skill_routing_mode is SkillRoutingMode.AUTO
                else router.disabled(
                    request=request,
                    available_tools=available_tools,
                )
            )
            context = (
                SkillContextAssembler(store).from_frozen(
                    decision.selected_versions
                )
                if decision.selected_versions
                else SkillContextSnapshot.no_skill()
            )
            return context, decision
        finally:
            store.close()
