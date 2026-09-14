"""Durable single-action run orchestration around the Action Gateway."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from voren.actions.errors import (
    ApprovalMismatchError,
    ApprovalRejectedError,
    InvalidOperationStateError,
    InvalidProposalError,
    StaleApprovalError,
)
from voren.actions.gateway import ActionGateway
from voren.actions.ledger import SQLiteOperationLedger
from voren.actions.models import ActionProposal, ActionReceipt, ApprovalDecision, ReceiptStatus
from voren.runs.models import (
    NewRunEvent,
    RunConfig,
    RunEventType,
    RunRecord,
    RunStatus,
)
from voren.runs.store import SQLiteRunStore


class RunManager:
    def __init__(
        self,
        *,
        store: SQLiteRunStore,
        operation_ledger: SQLiteOperationLedger,
        action_gateway: ActionGateway,
        run_id_factory: Callable[[], str] | None = None,
        event_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._operation_ledger = operation_ledger
        self._action_gateway = action_gateway
        self._run_id_factory = run_id_factory or (lambda: str(uuid4()))
        self._event_id_factory = event_id_factory or (lambda: str(uuid4()))
        self._clock = clock or (lambda: datetime.now(UTC))

    def start_new_run(self, config: RunConfig) -> RunRecord:
        now = self._clock()
        run_id = self._run_id_factory()
        created = RunRecord(
            run_id=run_id,
            status=RunStatus.CREATED,
            config=config,
            config_digest=config.calculated_digest(),
            version=0,
            created_at=now,
            updated_at=now,
        )
        self._store.create(
            created,
            self._event(
                RunEventType.RUN_CREATED,
                dedupe_key="run.created",
                payload={"config_digest": created.config_digest},
            ),
        )
        return self.start_run(run_id)

    def start_run(self, run_id: str) -> RunRecord:
        return self._store.transition(
            run_id,
            expected_status=RunStatus.CREATED,
            new_status=RunStatus.RUNNING,
            pending_operation_id=None,
            pending_proposal_digest=None,
            last_receipt_status=None,
            updated_at=self._clock(),
            events=(
                self._event(
                    RunEventType.RUN_STARTED,
                    dedupe_key="run.started",
                    payload={},
                ),
            ),
        )

    def get_run(self, run_id: str) -> RunRecord:
        """Load durable run state without exposing the store to the runtime."""

        return self._store.get_run(run_id)

    def propose_action(
        self,
        run_id: str,
        action_name: str,
        arguments: dict[str, Any],
        *,
        evidence_digests: tuple[str, ...] = (),
    ) -> ActionProposal:
        run = self._store.get_run(run_id)
        if run.status is not RunStatus.RUNNING:
            raise InvalidOperationStateError(
                f"run {run_id!r} cannot propose an action from {run.status.value!r}"
            )
        if any(
            len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            for digest in evidence_digests
        ):
            raise ValueError("evidence digests must be lowercase SHA-256 values")
        proposal = self._action_gateway.prepare(action_name, arguments)
        self._store.transition(
            run_id,
            expected_status=RunStatus.RUNNING,
            new_status=RunStatus.WAITING_APPROVAL,
            pending_operation_id=proposal.operation_id,
            pending_proposal_digest=proposal.digest,
            last_receipt_status=None,
            updated_at=self._clock(),
            events=(
                self._event(
                    RunEventType.ACTION_PROPOSED,
                    dedupe_key=f"action.proposed:{proposal.operation_id}",
                    payload=self._proposal_event_payload(
                        proposal, evidence_digests=evidence_digests
                    ),
                ),
                self._event(
                    RunEventType.RUN_WAITING_APPROVAL,
                    dedupe_key=f"run.waiting_approval:{proposal.operation_id}",
                    payload={
                        "operation_id": proposal.operation_id,
                        "proposal_digest": proposal.digest,
                    },
                ),
            ),
        )
        return proposal

    def record_runtime_event(
        self,
        run_id: str,
        event_type: RunEventType,
        *,
        dedupe_key: str,
        payload: dict[str, Any],
    ) -> None:
        """Append metadata about an in-flight context/model/tool boundary.

        Callers must keep raw prompts and tool payloads out of this event stream.
        The runtime uses digests, counts, names, and trust labels instead.
        """

        run = self._store.get_run(run_id)
        if run.status is not RunStatus.RUNNING:
            raise InvalidOperationStateError(
                f"run {run_id!r} cannot record runtime work from "
                f"{run.status.value!r}"
            )
        allowed_types = {
            RunEventType.SKILL_CONTEXT_ASSEMBLED,
            RunEventType.MODEL_REQUESTED,
            RunEventType.MODEL_RESPONDED,
            RunEventType.TOOL_CALLED,
            RunEventType.TOOL_OBSERVED,
        }
        if event_type not in allowed_types:
            raise ValueError(f"{event_type.value!r} is not a runtime boundary event")
        self._store.append_events(
            run_id,
            (
                self._event(
                    event_type,
                    dedupe_key=dedupe_key,
                    payload=payload,
                ),
            ),
        )

    def complete_without_action(self, run_id: str, *, outcome_digest: str) -> RunRecord:
        """Finish a read-only run without persisting the model's raw response."""

        return self._store.transition(
            run_id,
            expected_status=RunStatus.RUNNING,
            new_status=RunStatus.COMPLETED,
            pending_operation_id=None,
            pending_proposal_digest=None,
            last_receipt_status=None,
            updated_at=self._clock(),
            events=(
                self._event(
                    RunEventType.RUN_COMPLETED,
                    dedupe_key="run.completed",
                    payload={"outcome_digest": outcome_digest},
                ),
            ),
        )

    def fail_runtime(
        self,
        run_id: str,
        *,
        reason: str,
        limit_name: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> RunRecord:
        """Fail a running loop, optionally recording the budget it exhausted."""

        safe_details = details or {}
        events: list[NewRunEvent] = []
        if limit_name is not None:
            events.append(
                self._event(
                    RunEventType.RUNTIME_LIMIT_REACHED,
                    dedupe_key=f"runtime.limit_reached:{limit_name}",
                    payload={"limit": limit_name, **safe_details},
                )
            )
        events.append(
            self._event(
                RunEventType.RUN_FAILED,
                dedupe_key="run.failed",
                payload={"reason": reason, **safe_details},
            )
        )
        return self._store.transition(
            run_id,
            expected_status=RunStatus.RUNNING,
            new_status=RunStatus.FAILED,
            pending_operation_id=None,
            pending_proposal_digest=None,
            last_receipt_status=None,
            updated_at=self._clock(),
            events=tuple(events),
        )

    def cancel_runtime(
        self,
        run_id: str,
        *,
        reason: str,
        provider_confirmed: bool | None,
        details: dict[str, Any] | None = None,
    ) -> RunRecord:
        """Cancel a running loop without implying provider confirmation."""

        payload: dict[str, Any] = {"reason": reason, **(details or {})}
        if provider_confirmed is not None:
            payload["provider_confirmed"] = provider_confirmed
        return self._store.transition(
            run_id,
            expected_status=RunStatus.RUNNING,
            new_status=RunStatus.CANCELLED,
            pending_operation_id=None,
            pending_proposal_digest=None,
            last_receipt_status=None,
            updated_at=self._clock(),
            events=(
                self._event(
                    RunEventType.RUN_CANCELLED,
                    dedupe_key="run.cancelled",
                    payload=payload,
                ),
            ),
        )

    def resume_with_approval(
        self, run_id: str, approval: ApprovalDecision
    ) -> ActionReceipt:
        run, proposal = self._load_pending(run_id)
        try:
            authorized = self._action_gateway.authorize(proposal, approval)
        except ApprovalRejectedError:
            self._store.transition(
                run_id,
                expected_status=RunStatus.WAITING_APPROVAL,
                new_status=RunStatus.CANCELLED,
                pending_operation_id=None,
                pending_proposal_digest=None,
                last_receipt_status=None,
                updated_at=self._clock(),
                events=(
                    self._event(
                        RunEventType.APPROVAL_REJECTED,
                        dedupe_key=f"approval.rejected:{approval.approval_id}",
                        payload=approval.model_dump(mode="json"),
                    ),
                    self._event(
                        RunEventType.RUN_CANCELLED,
                        dedupe_key="run.cancelled",
                        payload={"reason": "operator_rejected_action"},
                    ),
                ),
            )
            raise
        except (ApprovalMismatchError, StaleApprovalError, InvalidProposalError) as error:
            self._store.append_events(
                run_id,
                (
                    self._event(
                        RunEventType.APPROVAL_INVALID,
                        dedupe_key=f"approval.invalid:{approval.approval_id}",
                        payload={
                            "approval_id": approval.approval_id,
                            "reason": type(error).__name__,
                        },
                    ),
                ),
            )
            raise

        self._store.append_events(
            run_id,
            (
                self._event(
                    RunEventType.APPROVAL_ACCEPTED,
                    dedupe_key=f"approval.accepted:{approval.approval_id}",
                    payload=approval.model_dump(mode="json"),
                ),
            ),
        )
        receipt = self._action_gateway.commit(authorized)
        return self._finalize(run, receipt)

    def recover_pending_receipt(self, run_id: str) -> ActionReceipt:
        run, _ = self._load_pending(run_id)
        if run.pending_operation_id is None:
            raise InvalidOperationStateError(f"run {run_id!r} has no pending operation")
        receipt = self._action_gateway.reconcile(run.pending_operation_id)
        approval = self._operation_ledger.get_approval(run.pending_operation_id)
        if approval is not None:
            self._store.append_events(
                run_id,
                (
                    self._event(
                        RunEventType.APPROVAL_ACCEPTED,
                        dedupe_key=f"approval.accepted:{approval.approval_id}",
                        payload=approval.model_dump(mode="json"),
                    ),
                ),
            )
        return self._finalize(run, receipt)

    def reconcile_pending_action(self, run_id: str) -> ActionReceipt:
        """Re-observe a visible ambiguous action without dispatching it again."""

        run = self._store.get_run(run_id)
        if run.status is RunStatus.WAITING_APPROVAL:
            return self.recover_pending_receipt(run_id)
        if run.status is not RunStatus.NEEDS_RECONCILIATION:
            raise InvalidOperationStateError(
                f"run {run_id!r} is not awaiting reconciliation"
            )
        if run.pending_operation_id is None or run.pending_proposal_digest is None:
            raise InvalidOperationStateError(
                f"run {run_id!r} has incomplete reconciliation state"
            )
        proposal = self._operation_ledger.get_proposal(run.pending_operation_id)
        if proposal.digest != run.pending_proposal_digest:
            raise InvalidOperationStateError(
                f"run {run_id!r} proposal digest disagrees with the operation ledger"
            )

        receipt = self._action_gateway.reconcile(run.pending_operation_id)
        if receipt.status is not ReceiptStatus.VERIFIED:
            return receipt
        self._store.transition(
            run_id,
            expected_status=RunStatus.NEEDS_RECONCILIATION,
            new_status=RunStatus.COMPLETED,
            pending_operation_id=None,
            pending_proposal_digest=None,
            last_receipt_status=receipt.status.value,
            updated_at=self._clock(),
            events=(
                self._event(
                    RunEventType.ACTION_RECONCILED,
                    dedupe_key=f"action.reconciled:{receipt.operation_id}",
                    payload=self._receipt_event_payload(receipt),
                ),
                self._event(
                    RunEventType.RUN_COMPLETED,
                    dedupe_key="run.completed",
                    payload={
                        "operation_id": receipt.operation_id,
                        "receipt_status": receipt.status.value,
                    },
                ),
            ),
        )
        return receipt

    def _load_pending(self, run_id: str) -> tuple[RunRecord, ActionProposal]:
        run = self._store.get_run(run_id)
        if run.status is not RunStatus.WAITING_APPROVAL:
            raise InvalidOperationStateError(
                f"run {run_id!r} is not waiting for approval"
            )
        if run.pending_operation_id is None or run.pending_proposal_digest is None:
            raise InvalidOperationStateError(
                f"run {run_id!r} has incomplete pending-action state"
            )
        proposal = self._operation_ledger.get_proposal(run.pending_operation_id)
        if proposal.digest != run.pending_proposal_digest:
            raise InvalidOperationStateError(
                f"run {run_id!r} proposal digest disagrees with the operation ledger"
            )
        return run, proposal

    def _finalize(self, run: RunRecord, receipt: ActionReceipt) -> ActionReceipt:
        status_mapping = {
            ReceiptStatus.VERIFIED: (
                RunStatus.COMPLETED,
                RunEventType.RUN_COMPLETED,
            ),
            ReceiptStatus.FAILED: (RunStatus.FAILED, RunEventType.RUN_FAILED),
            ReceiptStatus.AMBIGUOUS: (
                RunStatus.NEEDS_RECONCILIATION,
                RunEventType.RUN_NEEDS_RECONCILIATION,
            ),
            ReceiptStatus.VERIFICATION_FAILED: (
                RunStatus.NEEDS_RECONCILIATION,
                RunEventType.RUN_NEEDS_RECONCILIATION,
            ),
        }
        new_status, terminal_event_type = status_mapping[receipt.status]
        keep_for_reconciliation = new_status is RunStatus.NEEDS_RECONCILIATION
        self._store.transition(
            run.run_id,
            expected_status=RunStatus.WAITING_APPROVAL,
            new_status=new_status,
            pending_operation_id=(
                receipt.operation_id if keep_for_reconciliation else None
            ),
            pending_proposal_digest=(
                receipt.proposal_digest if keep_for_reconciliation else None
            ),
            last_receipt_status=receipt.status.value,
            updated_at=self._clock(),
            events=(
                self._event(
                    RunEventType.ACTION_RECEIPT,
                    dedupe_key=f"action.receipt:{receipt.operation_id}",
                    payload=self._receipt_event_payload(receipt),
                ),
                self._event(
                    terminal_event_type,
                    dedupe_key=f"run.{new_status.value}",
                    payload={
                        "operation_id": receipt.operation_id,
                        "receipt_status": receipt.status.value,
                    },
                ),
            ),
        )
        return receipt

    @staticmethod
    def _proposal_event_payload(
        proposal: ActionProposal, *, evidence_digests: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        return {
            "operation_id": proposal.operation_id,
            "proposal_digest": proposal.digest,
            "action_name": proposal.action_name,
            "action_version": proposal.action_version,
            "evidence_digests": list(evidence_digests),
            "effects": [
                {
                    "effect_id": effect.effect_id,
                    "resource": effect.resource,
                    "kind": effect.kind.value,
                    "target": effect.target,
                    "reversible": effect.reversible,
                    "sensitivity": effect.sensitivity.value,
                }
                for effect in proposal.effects
            ],
        }

    @staticmethod
    def _receipt_event_payload(receipt: ActionReceipt) -> dict[str, Any]:
        return {
            "operation_id": receipt.operation_id,
            "proposal_digest": receipt.proposal_digest,
            "approval_id": receipt.approval_id,
            "status": receipt.status.value,
            "committed": receipt.committed,
            "recovered_after_ambiguous_commit": (
                receipt.recovered_after_ambiguous_commit
            ),
            "verification": receipt.verification.model_dump(mode="json"),
            "external_references": [
                {
                    "effect_id": observed.effect.effect_id,
                    "external_reference": observed.external_reference,
                }
                for observed in receipt.observed_effects
            ],
            "error": receipt.error,
        }

    def _event(
        self,
        event_type: RunEventType,
        *,
        dedupe_key: str,
        payload: dict[str, Any],
    ) -> NewRunEvent:
        return NewRunEvent(
            event_id=self._event_id_factory(),
            dedupe_key=dedupe_key,
            event_type=event_type,
            payload=payload,
            occurred_at=self._clock(),
        )
