"""Application service joining HTTP idempotency to the controlled Agent Loop."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from uuid import uuid4

from voren.actions.errors import ApprovalRejectedError
from voren.actions.gateway import ActionGateway
from voren.actions.ledger import SQLiteOperationLedger
from voren.actions.models import ApprovalDecision
from voren.adapters.agentdojo_reads import AgentDojoReadAdapter
from voren.adapters.agentdojo_workspace import AgentDojoWorkspaceAdapter
from voren.adapters.workspace_contracts import (
    WORKSPACE_CONTRACT_VERSION,
    create_calendar_event_definition,
    send_email_definition,
)
from voren.knowledge.store import SQLiteKnowledgeStore
from voren.mcp_bridge.adapter import MCPKnowledgeReadAdapter
from voren.mcp_bridge.server import create_knowledge_mcp_server
from voren.observations.read_tools import CompositeReadToolAdapter
from voren.runs.manager import RunManager
from voren.runs.models import RunConfig, RunEvent
from voren.runs.store import SQLiteRunStore
from voren.runtime.agent_loop import AgentLoop
from voren.runtime.models import RuntimeLimits, RuntimeResultStatus
from voren.runtime.ports import ModelAdapter
from voren.runtime.tools import external_action_tool
from voren.web.index import (
    SQLiteWebRunIndex,
    WebRequestInProgressError,
)
from voren.web.models import CreateRunRequest, DecideRunRequest, RunView


ModelFactory = Callable[[], ModelAdapter]


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
        limits: RuntimeLimits | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.database = database
        self.mode = mode
        self._model_factory = model_factory
        self._limits = limits or RuntimeLimits()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._index = SQLiteWebRunIndex(database)
        self._pending_workspaces: dict[str, AgentDojoWorkspaceAdapter] = {}
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
            workspace = AgentDojoWorkspaceAdapter()
        except Exception:
            self._index.abandon(
                client_request_id=request.client_request_id,
                run_id=run_id,
            )
            raise
        knowledge_store = SQLiteKnowledgeStore(self.database)
        ledger = SQLiteOperationLedger(self.database)
        run_store = SQLiteRunStore(self.database)
        try:
            calendar_action = create_calendar_event_definition(
                account_email=workspace.account_email
            )
            email_action = send_email_definition()
            gateway = ActionGateway(
                definitions=(calendar_action, email_action),
                adapter=workspace,
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
            loop = AgentLoop(
                model=model,
                read_tools=CompositeReadToolAdapter(
                    AgentDojoReadAdapter(workspace),
                    MCPKnowledgeReadAdapter(
                        knowledge_server,
                        source_id="local-knowledge",
                    ),
                ),
                action_tools=(
                    external_action_tool(
                        calendar_action,
                        description=(
                            "Propose a calendar event and invitation email. "
                            "Execution requires exact-effect operator approval."
                        ),
                    ),
                    external_action_tool(
                        email_action,
                        description=(
                            "Propose an outbound email. Execution requires "
                            "exact-effect operator approval."
                        ),
                    ),
                ),
                run_manager=manager,
                limits=self._limits,
            )
            result = loop.run(
                user_request=normalized,
                config=RunConfig(
                    workflow="web_email_calendar",
                    world_adapter="agentdojo-workspace+mcp-knowledge",
                    policy_version="provenance-and-approval-v1",
                    action_contract_versions=(WORKSPACE_CONTRACT_VERSION,),
                    metadata={"surface": "web", "mode": self.mode},
                ),
            )
            timestamp = self._clock()
            view = RunView(
                client_request_id=request.client_request_id,
                run_id=result.run_id,
                status=result.status.value,
                final_text=result.final_text,
                proposal=result.pending_proposal,
                usage=result.usage,
                error_code=result.error_code,
                error_detail_code=result.error_detail_code,
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
                raise WebApprovalRecoveryRequiredError(
                    "the controlled workspace handle was lost after process restart; "
                    "no action was dispatched"
                )
            calendar_action = create_calendar_event_definition(
                account_email=workspace.account_email
            )
            email_action = send_email_definition()
            manager = RunManager(
                store=run_store,
                operation_ledger=ledger,
                action_gateway=ActionGateway(
                    definitions=(calendar_action, email_action),
                    adapter=workspace,
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
        return view.model_copy(update={"recovery_required": not recoverable})
