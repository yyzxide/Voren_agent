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

from voren.actions.errors import ApprovalRejectedError, InvalidOperationStateError
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
from voren.knowledge.embeddings import (
    EmbeddingError,
    EmbeddingProvider,
    embedding_provider_from_env,
    retrieval_mode_from_env,
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
from voren.runtime.transcripts import SQLiteTranscriptStore
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
from voren.web.models import ActionHistoryView, CreateRunRequest, DecideRunRequest, RunView


ModelFactory = Callable[[], ModelAdapter]
WorkspaceFactory = Callable[[], "WebWorkspaceRuntime"]
_DATABASE_LOCKS: dict[Path, RLock] = {}
_DATABASE_LOCKS_GUARD = RLock()


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
    identity: Mapping[str, str] | None = None


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
        identity={
            "account_email": workspace.config.account_email,
            "calendar_id": workspace.config.calendar_id,
            "time_zone": workspace.config.time_zone,
        },
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
        knowledge_retrieval_mode: str | None = None,
        knowledge_embedder: EmbeddingProvider | None = None,
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
        if knowledge_retrieval_mode is not None and knowledge_retrieval_mode not in {
            "lexical", "bm25", "dense", "hybrid",
        }:
            raise ValueError("unknown knowledge retrieval mode")
        self._knowledge_retrieval_mode = knowledge_retrieval_mode
        self._knowledge_embedder = knowledge_embedder
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
        with _DATABASE_LOCKS_GUARD:
            self._lock = _DATABASE_LOCKS.setdefault(database.resolve(), RLock())

    def submit(self, request: CreateRunRequest) -> RunView:
        # HTTP workers may overlap retries with decisions or checkpoint resumes.
        with self._lock:
            return self._submit_locked(request)

    def _submit_locked(self, request: CreateRunRequest) -> RunView:
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
            return self.get(existing.run_id)
        if not created:
            raise WebRequestInProgressError(
                f"request {request.client_request_id!r} is already running"
            )

        try:
            retrieval_mode, embedder = self._knowledge_settings()
            model = self._model_factory()
            workspace = self._create_workspace()
        except Exception:
            self._index.abandon(
                client_request_id=request.client_request_id,
                run_id=run_id,
            )
            raise
        knowledge_store = ledger = run_store = transcript_store = None
        published = False
        try:
            knowledge_store = SQLiteKnowledgeStore(self.knowledge_database)
            ledger = SQLiteOperationLedger(self.database)
            run_store = SQLiteRunStore(self.database)
            transcript_store = SQLiteTranscriptStore.from_local_key(self.database)
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
            knowledge_server = create_knowledge_mcp_server(
                knowledge_store, mode=retrieval_mode, embedder=embedder,
            )
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
                transcript_store=transcript_store,
            )
            # Publish the identity before model execution so a process interruption
            # can be recovered using the original client_request_id.
            self._index.save(RunView(
                client_request_id=request.client_request_id,
                run_id=run_id,
                workspace=workspace.name,
                status="running",
                memory_versions=memory_context.memory_versions,
                skill_routing=skill_route,
                created_at=now,
                updated_at=now,
            ))
            published = True
            self._pending_workspaces[run_id] = workspace
            result = loop.run(
                user_request=normalized,
                config=RunConfig(
                    workflow="web_email_calendar",
                    world_adapter=workspace.world_adapter,
                    policy_version="provenance-and-approval-v1",
                    action_contract_versions=workspace.contract_versions,
                    memory_versions=memory_context.memory_versions,
                    skill_versions=skill_context.skill_versions,
                    continue_after_action=True,
                    metadata={
                        "surface": "web",
                        "mode": self.mode,
                        "workspace": workspace.name,
                        "workspace_identity": dict(workspace.identity or {}),
                        "profile_memory_mode": self.profile_memory_mode.value,
                        "skill_routing": skill_route.model_dump(mode="json"),
                        "knowledge_retrieval": self._knowledge_metadata(
                            retrieval_mode, embedder,
                        ),
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
            else:
                self._pending_workspaces.pop(result.run_id, None)
            view = self._durable_view(view, run_store, ledger)
            self._index.save(view)
            return view
        except Exception:
            unstarted = published and self._index.abandon_unstarted(
                client_request_id=request.client_request_id, run_id=run_id,
            )
            if unstarted or not published:
                self._pending_workspaces.pop(run_id, None)
            if not published:
                self._index.abandon(
                    client_request_id=request.client_request_id,
                    run_id=run_id,
                )
            raise
        finally:
            for store in (transcript_store, run_store, ledger, knowledge_store):
                if store is not None:
                    store.close()

    def decide(self, run_id: str, request: DecideRunRequest) -> RunView:
        with self._lock:
            return self._decide_locked(run_id, request)

    def _decide_locked(self, run_id: str, request: DecideRunRequest) -> RunView:
        ledger = SQLiteOperationLedger(self.database)
        run_store = SQLiteRunStore(self.database)
        try:
            current = self._durable_view(self._index.get(run_id), run_store, ledger)
            # A retry for action 1 must never authorize action 2.
            matched = next((item for item in current.action_history
                            if item.decision_id == request.decision_id), None)
            if matched is not None:
                if (matched.proposal.digest != request.proposal_digest
                        or matched.decision_approved is not request.approved):
                    raise WebDecisionConflictError(
                        "decision_id is already bound to another proposal or decision"
                    )
                if (current.proposal is None
                        or current.proposal.operation_id != matched.proposal.operation_id
                        or current.status in {"completed", "failed", "cancelled", "limit_exceeded"}):
                    if (current.status == "completed" and current.final_text is None
                            and run_store.get_run(run_id).config.continue_after_action):
                        return self._restore_completed(current, run_store)
                    return self._with_recovery_state(current)
            proposal = current.proposal
            if proposal is None or proposal.digest != request.proposal_digest:
                raise WebDecisionConflictError(
                    "decision proposal_digest does not match the pending proposal"
                )
            existing_approval = ledger.get_approval(proposal.operation_id)
            if existing_approval is not None and (
                existing_approval.approval_id != request.decision_id
                or existing_approval.proposal_digest != request.proposal_digest
                or existing_approval.approved is not request.approved
            ):
                raise WebDecisionConflictError("durable operation already has another decision")
            run = run_store.get_run(run_id)
            if run.status.value not in {"waiting_approval", "needs_reconciliation", "running"}:
                raise WebDecisionConflictError("run is no longer awaiting this decision")
            workspace = self._workspace_for_resume(
                current, config=run.config, check_retrieval=request.approved,
            )
            manager = self._manager(workspace, run_store, ledger)
            approval = existing_approval or ApprovalDecision.for_proposal(
                proposal, approval_id=request.decision_id,
                decided_by="operator:web-local", approved=request.approved,
                decided_at=self._clock(),
            )
            if run.status.value != "running":
                try:
                    operation_status = ledger.get_status(proposal.operation_id)
                    if run.status.value == "needs_reconciliation":
                        manager.reconcile_pending_action(run_id)
                    elif ledger.get_receipt(proposal.operation_id) is not None or operation_status in {
                        "committing", "ambiguous", "verification_failed"
                    }:
                        manager.recover_pending_receipt(run_id)
                    else:
                        manager.resume_with_approval(run_id, approval)
                except ApprovalRejectedError:
                    pass
            # Persist the receipt before requesting another model response.
            current = self._durable_view(current, run_store, ledger)
            self._index.save(current)
            if current.status == "running":
                return self._resume_loop(current, workspace, run_store, ledger)
            self._retain_workspace(current, workspace)
            return current
        finally:
            run_store.close()
            ledger.close()

    def resume(self, run_id: str) -> RunView:
        """Resume progress; an unapproved proposal remains unapproved."""
        with self._lock:
            ledger = SQLiteOperationLedger(self.database)
            run_store = SQLiteRunStore(self.database)
            try:
                current = self._durable_view(self._index.get(run_id), run_store, ledger)
                if current.status in {"failed", "cancelled", "limit_exceeded"}:
                    return current
                if current.status in {"waiting_approval", "needs_reconciliation"}:
                    if current.decision_id is None:
                        return self._with_recovery_state(current)
                    assert current.proposal is not None
                    return self._decide_locked(run_id, DecideRunRequest(
                        decision_id=current.decision_id,
                        proposal_digest=current.proposal.digest,
                        approved=bool(current.decision_approved),
                    ))
                if current.status == "completed" and current.final_text is not None:
                    return current
                if current.status == "completed":
                    if not run_store.get_run(run_id).config.continue_after_action:
                        return current
                    return self._restore_completed(current, run_store)
                workspace = self._workspace_for_resume(
                    current, config=run_store.get_run(run_id).config
                )
                return self._resume_loop(current, workspace, run_store, ledger)
            finally:
                run_store.close()
                ledger.close()

    def _restore_completed(self, current: RunView, run_store: SQLiteRunStore) -> RunView:
        # Reading a completed result needs neither an external workspace nor a
        # model call, including after the in-memory demo world has been lost.
        store = SQLiteTranscriptStore.from_local_key(self.database)
        try:
            checkpoint = store.load(current.run_id)
            run = run_store.get_run(current.run_id)
            response = checkpoint.pending_response
            if (checkpoint.config_digest != run.config_digest or response is None
                    or response.tool_calls or not response.text):
                raise WebApprovalRecoveryRequiredError("completed run has no recoverable final response")
            updated = current.model_copy(update={
                "final_text": response.text,
                "usage": checkpoint.usage,
            })
            self._index.save(updated)
            return updated
        finally:
            store.close()

    def _manager(self, workspace, run_store, ledger) -> RunManager:
        return RunManager(
            store=run_store, operation_ledger=ledger,
            action_gateway=ActionGateway(
                definitions=workspace.action_definitions, adapter=workspace.action_adapter,
                ledger=ledger, clock=self._clock,
            ), clock=self._clock,
        )

    def _workspace_for_resume(
        self, current: RunView, *, config: RunConfig, check_retrieval: bool = True,
    ) -> WebWorkspaceRuntime:
        if check_retrieval:
            self._knowledge_retrieval_for_run(config)
        workspace = self._pending_workspaces.get(current.run_id)
        if workspace is None:
            if not self.workspace_recoverable or current.workspace != self.workspace_name:
                raise WebApprovalRecoveryRequiredError(
                    "the controlled workspace handle was lost after process restart; "
                    "no action was dispatched; the original external world cannot be resumed"
                )
            workspace = self._create_workspace()
        self._assert_workspace_identity(workspace, config)
        if current.run_id not in self._pending_workspaces:
            self._pending_workspaces[current.run_id] = workspace
        return workspace

    @staticmethod
    def _assert_workspace_identity(workspace: WebWorkspaceRuntime, config: RunConfig) -> None:
        if workspace.recoverable_after_restart and (
            config.metadata.get("workspace_identity") != dict(workspace.identity or {})
        ):
            raise WebApprovalRecoveryRequiredError(
                "workspace identity differs from the original run (account, calendar, or time zone); "
                "no action was dispatched. Legacy runs without a frozen identity cannot resume writes."
            )

    def _resume_loop(self, current, workspace, run_store, ledger) -> RunView:
        knowledge_store = SQLiteKnowledgeStore(self.knowledge_database)
        transcript_store = SQLiteTranscriptStore.from_local_key(self.database)
        memory_store = SQLiteMemoryStore(self.memory_database)
        skill_store = SQLiteSkillStore(
            self.skill_database, root=self.skill_store_root, parser=AgentSkillParser()
        )
        try:
            run = run_store.get_run(current.run_id)
            retrieval_mode, embedder = self._knowledge_retrieval_for_run(run.config)
            memory_context = MemoryContextAssembler(memory_store).from_frozen(run.config.memory_versions)
            skill_context = (
                SkillContextAssembler(skill_store).from_frozen(run.config.skill_versions)
                if run.config.skill_versions else SkillContextSnapshot.no_skill()
            )
            loop = AgentLoop(
                model=self._model_factory(),
                read_tools=CompositeReadToolAdapter(
                    workspace.read_tools,
                    MCPKnowledgeReadAdapter(
                        create_knowledge_mcp_server(
                            knowledge_store, mode=retrieval_mode, embedder=embedder,
                        ), source_id="local-knowledge",
                    ),
                ),
                action_tools=tuple(
                    external_action_tool(
                        definition, description=workspace.action_descriptions[definition.name],
                    ) for definition in workspace.action_definitions
                ),
                run_manager=self._manager(workspace, run_store, ledger),
                limits=RuntimeLimits.model_validate(
                    run.config.metadata.get("runtime_limits", self._limits.model_dump())
                ),
                transcript_store=transcript_store,
                memory_context=memory_context, skill_context=skill_context,
            )
            result = loop.resume(current.run_id)
            updated = self._durable_view(current.model_copy(update={
                "status": result.status.value, "final_text": result.final_text,
                "usage": result.usage, "error_code": result.error_code,
                "error_detail_code": result.error_detail_code,
            }), run_store, ledger)
            self._index.save(updated)
            self._retain_workspace(updated, workspace)
            return updated
        finally:
            skill_store.close()
            memory_store.close()
            transcript_store.close()
            knowledge_store.close()

    def _retain_workspace(self, view: RunView, workspace: WebWorkspaceRuntime) -> None:
        if view.status in {"running", "waiting_approval", "needs_reconciliation"}:
            self._pending_workspaces[view.run_id] = workspace
        else:
            self._pending_workspaces.pop(view.run_id, None)

    def _durable_view(self, current, run_store, ledger) -> RunView:
        run = run_store.get_run(current.run_id)
        events = run_store.list_events(current.run_id)
        rejected = {
            event.payload["operation_id"]: ApprovalDecision.model_validate(event.payload)
            for event in events if event.event_type.value == "approval.rejected"
        }
        history = tuple(
            self._action_view(ledger, event.payload["operation_id"], rejected)
            for event in events
            if event.event_type.value == "action.proposed"
        )
        pending = next((item for item in history
                        if item.proposal.operation_id == run.pending_operation_id), None)
        latest = pending or (history[-1] if history else None)
        receipt = next((item.receipt for item in reversed(history) if item.receipt), None)
        status = run.status.value
        if status == "failed" and current.status == "limit_exceeded":
            status = current.status
        return current.model_copy(update={
            "status": status,
            "proposal": pending.proposal if pending else None,
            "receipt": receipt, "action_history": history,
            "decision_id": latest.decision_id if latest else None,
            "decision_approved": latest.decision_approved if latest else None,
            "recovery_required": False, "recovery_reason": None, "updated_at": run.updated_at,
        })

    @staticmethod
    def _action_view(ledger, operation_id, rejected) -> ActionHistoryView:
        proposal = ledger.get_proposal(operation_id)
        # Rejection is a Run event, never an authorization in the operation ledger.
        approval = ledger.get_approval(operation_id) or rejected.get(operation_id)
        if approval is not None and approval.proposal_digest != proposal.digest:
            raise InvalidOperationStateError("decision history does not match its proposal")
        return ActionHistoryView(
            proposal=proposal,
            decision_id=approval.approval_id if approval else None,
            decision_approved=approval.approved if approval else None,
            receipt=ledger.get_receipt(operation_id),
        )

    def get(self, run_id: str) -> RunView:
        with self._lock:
            current = self._index.get(run_id)
            run_store = SQLiteRunStore(self.database)
            ledger = SQLiteOperationLedger(self.database)
            try:
                try:
                    view = self._durable_view(current, run_store, ledger)
                except InvalidOperationStateError:
                    return current.model_copy(update={"recovery_required": True})
                return self._with_recovery_state(view)
            finally:
                ledger.close()
                run_store.close()

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
        view = view.model_copy(update={"recovery_reason": None})
        if view.status not in {"running", RuntimeResultStatus.WAITING_APPROVAL.value, "needs_reconciliation"}:
            return view
        retrieval_problem = False
        store = SQLiteRunStore(self.database)
        try:
            config = store.get_run(view.run_id).config
            try:
                self._knowledge_retrieval_for_run(config)
            except WebApprovalRecoveryRequiredError:
                retrieval_problem = True
        finally:
            store.close()
        with self._lock:
            workspace = self._pending_workspaces.get(view.run_id)
        recoverable = False
        if workspace is not None:
            try:
                self._assert_workspace_identity(workspace, config)
            except WebApprovalRecoveryRequiredError:
                pass
            else:
                recoverable = True
        if (
            not recoverable
            and self.workspace_recoverable
            and view.workspace == self.workspace_name
        ):
            try:
                workspace = self._create_workspace()
                self._assert_workspace_identity(workspace, config)
            except Exception:
                pass
            else:
                recoverable = True
        return view.model_copy(update={
            "recovery_required": not recoverable or retrieval_problem,
            "recovery_reason": (
                "knowledge_configuration_changed" if recoverable and retrieval_problem else None
            ),
        })

    def _knowledge_settings(self) -> tuple[str, EmbeddingProvider | None]:
        mode = self._knowledge_retrieval_mode or retrieval_mode_from_env()
        embedder = None
        if mode in {"dense", "hybrid"}:
            embedder = self._knowledge_embedder or embedding_provider_from_env()
        return mode, embedder

    @staticmethod
    def _knowledge_metadata(mode: str, embedder: EmbeddingProvider | None) -> dict:
        metadata = {"mode": mode}
        if mode in {"dense", "hybrid"}:
            if embedder is None:
                raise ValueError("dense/hybrid retrieval requires an embedding provider")
            metadata["embedding_fingerprint"] = embedder.fingerprint
        return metadata

    def _knowledge_retrieval_for_run(
        self, config: RunConfig,
    ) -> tuple[str, EmbeddingProvider | None]:
        frozen = config.metadata.get("knowledge_retrieval")
        if frozen is None:
            # Pre-0.3 Runs used lexical retrieval. A new process default must
            # not change their behavior or silently enable external queries.
            return "lexical", None
        try:
            mode, embedder = self._knowledge_settings()
            current = self._knowledge_metadata(mode, embedder)
        except (EmbeddingError, ValueError):
            raise WebApprovalRecoveryRequiredError(
                "knowledge retrieval configuration is unavailable; restore the "
                "original mode and embedding configuration before continuing"
            ) from None
        if frozen != current:
            raise WebApprovalRecoveryRequiredError(
                "knowledge retrieval configuration differs from the original run "
                "(mode or embedding fingerprint); restore it before continuing"
            )
        return mode, embedder

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
