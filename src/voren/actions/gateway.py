"""Prepare, authorize, commit, and verify external actions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from voren.actions.errors import (
    AmbiguousCommitError,
    ApprovalMismatchError,
    ApprovalRejectedError,
    InvalidOperationStateError,
    InvalidProposalError,
    KnownPreCommitFailure,
    StaleApprovalError,
    UnknownActionError,
)
from voren.actions.ledger import SQLiteOperationLedger
from voren.actions.models import (
    ActionProposal,
    ActionReceipt,
    ApprovalDecision,
    AuthorizedAction,
    Effect,
    ReceiptStatus,
    VerificationResult,
    calculate_proposal_digest,
    verify_exact_effects,
)
from voren.actions.ports import ActionAdapter


@dataclass(frozen=True, slots=True)
class ActionDefinition:
    name: str
    version: str
    input_model: type[BaseModel]
    effect_builder: Callable[[BaseModel], tuple[Effect, ...]]


class ActionGateway:
    def __init__(
        self,
        *,
        definitions: tuple[ActionDefinition, ...],
        adapter: ActionAdapter,
        ledger: SQLiteOperationLedger,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._definitions = {definition.name: definition for definition in definitions}
        if len(self._definitions) != len(definitions):
            raise ValueError("action definition names must be unique")
        self._adapter = adapter
        self._ledger = ledger
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._clock = clock or (lambda: datetime.now(UTC))

    def prepare(self, action_name: str, arguments: dict[str, Any]) -> ActionProposal:
        definition = self._definitions.get(action_name)
        if definition is None:
            raise UnknownActionError(action_name)
        try:
            validated_input = definition.input_model.model_validate(arguments)
        except ValidationError as error:
            raise InvalidProposalError(str(error)) from error

        normalized_arguments = validated_input.model_dump(mode="json")
        effects = definition.effect_builder(validated_input)
        operation_id = self._id_factory()
        digest = calculate_proposal_digest(
            operation_id=operation_id,
            action_name=definition.name,
            action_version=definition.version,
            arguments=normalized_arguments,
            effects=effects,
        )
        proposal = ActionProposal(
            operation_id=operation_id,
            action_name=definition.name,
            action_version=definition.version,
            arguments=normalized_arguments,
            effects=effects,
            digest=digest,
        )
        self._ledger.register(proposal)
        return proposal

    def authorize(
        self, proposal: ActionProposal, approval: ApprovalDecision
    ) -> AuthorizedAction:
        self._validate_registered_proposal(proposal)
        self._validate_approval_binding(proposal, approval)
        self._validate_approval_freshness(approval)
        if not approval.approved:
            raise ApprovalRejectedError(f"approval {approval.approval_id!r} was rejected")
        self._ledger.authorize(proposal.operation_id, approval)
        return AuthorizedAction(proposal=proposal, approval=approval)

    def commit(self, action: AuthorizedAction) -> ActionReceipt:
        proposal = action.proposal
        approval = action.approval
        self._validate_registered_proposal(proposal)
        self._validate_approval_binding(proposal, approval)

        existing_receipt = self._ledger.get_receipt(proposal.operation_id)
        if existing_receipt is not None:
            return existing_receipt

        self._validate_approval_freshness(approval)
        if not approval.approved:
            raise ApprovalRejectedError(f"approval {approval.approval_id!r} was rejected")
        if self._ledger.get_status(proposal.operation_id) != "authorized":
            raise InvalidOperationStateError(
                f"operation {proposal.operation_id!r} is not authorized"
            )

        self._ledger.mark_committing(proposal.operation_id)
        recovered_after_ambiguous_commit = False
        try:
            self._adapter.commit(proposal)
        except KnownPreCommitFailure as error:
            receipt = self._failure_receipt(
                proposal,
                approval,
                status=ReceiptStatus.FAILED,
                committed=False,
                error=str(error),
            )
            self._ledger.store_receipt(receipt)
            return receipt
        except AmbiguousCommitError as error:
            self._ledger.mark_ambiguous(proposal.operation_id)
            recovered_after_ambiguous_commit = True
            return self._observe_and_record(
                proposal,
                approval,
                recovered_after_ambiguous_commit=True,
                ambiguous_error=str(error),
            )

        return self._observe_and_record(
            proposal,
            approval,
            recovered_after_ambiguous_commit=recovered_after_ambiguous_commit,
        )

    def _observe_and_record(
        self,
        proposal: ActionProposal,
        approval: ApprovalDecision,
        *,
        recovered_after_ambiguous_commit: bool,
        ambiguous_error: str | None = None,
    ) -> ActionReceipt:
        observed = self._adapter.observe(proposal)
        verification = verify_exact_effects(proposal.effects, observed)
        if verification.passed:
            status = ReceiptStatus.VERIFIED
            committed: bool | None = True
            error = None
        elif recovered_after_ambiguous_commit:
            status = ReceiptStatus.AMBIGUOUS
            committed = None
            error = ambiguous_error
        else:
            status = ReceiptStatus.VERIFICATION_FAILED
            committed = True
            error = "observed state does not match the approved effect manifest"

        receipt = ActionReceipt(
            operation_id=proposal.operation_id,
            proposal_digest=proposal.digest,
            approval_id=approval.approval_id,
            status=status,
            committed=committed,
            recovered_after_ambiguous_commit=recovered_after_ambiguous_commit,
            expected_effects=proposal.effects,
            observed_effects=observed,
            verification=verification,
            error=error,
            completed_at=self._clock(),
        )
        self._ledger.store_receipt(receipt)
        return receipt

    def _failure_receipt(
        self,
        proposal: ActionProposal,
        approval: ApprovalDecision,
        *,
        status: ReceiptStatus,
        committed: bool | None,
        error: str,
    ) -> ActionReceipt:
        missing = tuple(effect.effect_id for effect in proposal.effects)
        return ActionReceipt(
            operation_id=proposal.operation_id,
            proposal_digest=proposal.digest,
            approval_id=approval.approval_id,
            status=status,
            committed=committed,
            expected_effects=proposal.effects,
            observed_effects=(),
            verification=VerificationResult(passed=False, missing_effect_ids=missing),
            error=error,
            completed_at=self._clock(),
        )

    def _validate_registered_proposal(self, proposal: ActionProposal) -> None:
        if proposal.calculated_digest() != proposal.digest:
            raise InvalidProposalError("proposal content no longer matches its digest")
        registered = self._ledger.get_proposal(proposal.operation_id)
        if registered != proposal:
            raise InvalidProposalError("proposal does not match the operation ledger")

    @staticmethod
    def _validate_approval_binding(
        proposal: ActionProposal, approval: ApprovalDecision
    ) -> None:
        if (
            approval.operation_id != proposal.operation_id
            or approval.proposal_digest != proposal.digest
        ):
            raise ApprovalMismatchError(
                "approval is not bound to this operation and exact effect manifest"
            )

    def _validate_approval_freshness(self, approval: ApprovalDecision) -> None:
        now = self._clock()
        expires_at = approval.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if now > expires_at:
            raise StaleApprovalError(f"approval {approval.approval_id!r} has expired")
