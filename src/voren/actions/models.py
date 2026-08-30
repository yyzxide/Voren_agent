"""Immutable wire models for Voren's external-action protocol."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EffectKind(StrEnum):
    PURE_READ = "pure_read"
    STATEFUL_READ = "stateful_read"
    LOCAL_DRAFT = "local_draft"
    CREATE = "create"
    UPDATE = "update"
    SEND = "send"
    DELETE = "delete"
    COMPENSATE = "compensate"


class Sensitivity(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class Effect(FrozenModel):
    """One operator-visible effect materialized before authorization."""

    effect_id: str = Field(min_length=1)
    resource: str = Field(min_length=1)
    kind: EffectKind
    target: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    attributes: dict[str, Any] = Field(default_factory=dict)
    reversible: bool
    sensitivity: Sensitivity = Sensitivity.INTERNAL


def calculate_proposal_digest(
    *,
    operation_id: str,
    action_name: str,
    action_version: str,
    arguments: dict[str, Any],
    effects: tuple[Effect, ...],
) -> str:
    payload = {
        "operation_id": operation_id,
        "action_name": action_name,
        "action_version": action_version,
        "arguments": arguments,
        "effects": [effect.model_dump(mode="json") for effect in effects],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ActionProposal(FrozenModel):
    operation_id: str = Field(min_length=1)
    action_name: str = Field(min_length=1)
    action_version: str = Field(min_length=1)
    arguments: dict[str, Any]
    effects: tuple[Effect, ...]
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def unique_effect_ids(self) -> Self:
        effect_ids = [effect.effect_id for effect in self.effects]
        if len(effect_ids) != len(set(effect_ids)):
            raise ValueError("effect_id values must be unique within a proposal")
        return self

    def calculated_digest(self) -> str:
        return calculate_proposal_digest(
            operation_id=self.operation_id,
            action_name=self.action_name,
            action_version=self.action_version,
            arguments=self.arguments,
            effects=self.effects,
        )


class ApprovalDecision(FrozenModel):
    approval_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    proposal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved: bool
    decided_by: str = Field(min_length=1)
    decided_at: datetime
    expires_at: datetime

    @classmethod
    def for_proposal(
        cls,
        proposal: ActionProposal,
        *,
        approval_id: str,
        decided_by: str,
        approved: bool = True,
        decided_at: datetime | None = None,
        ttl: timedelta = timedelta(minutes=5),
    ) -> Self:
        decision_time = decided_at or datetime.now(UTC)
        return cls(
            approval_id=approval_id,
            operation_id=proposal.operation_id,
            proposal_digest=proposal.digest,
            approved=approved,
            decided_by=decided_by,
            decided_at=decision_time,
            expires_at=decision_time + ttl,
        )


class AuthorizedAction(FrozenModel):
    proposal: ActionProposal
    approval: ApprovalDecision


class ObservedEffect(FrozenModel):
    effect: Effect
    external_reference: str | None = None


class VerificationResult(FrozenModel):
    passed: bool
    missing_effect_ids: tuple[str, ...] = ()
    unexpected_effect_ids: tuple[str, ...] = ()
    mismatched_effect_ids: tuple[str, ...] = ()


class ReceiptStatus(StrEnum):
    VERIFIED = "verified"
    VERIFICATION_FAILED = "verification_failed"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"


class ActionReceipt(FrozenModel):
    operation_id: str
    proposal_digest: str
    approval_id: str
    status: ReceiptStatus
    committed: bool | None
    recovered_after_ambiguous_commit: bool = False
    expected_effects: tuple[Effect, ...]
    observed_effects: tuple[ObservedEffect, ...]
    verification: VerificationResult
    error: str | None = None
    completed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def verify_exact_effects(
    expected: tuple[Effect, ...], observed: tuple[ObservedEffect, ...]
) -> VerificationResult:
    expected_by_id = {effect.effect_id: effect for effect in expected}
    observed_by_id = {item.effect.effect_id: item.effect for item in observed}

    missing = tuple(sorted(expected_by_id.keys() - observed_by_id.keys()))
    unexpected = tuple(sorted(observed_by_id.keys() - expected_by_id.keys()))
    mismatched = tuple(
        sorted(
            effect_id
            for effect_id in expected_by_id.keys() & observed_by_id.keys()
            if expected_by_id[effect_id] != observed_by_id[effect_id]
        )
    )
    return VerificationResult(
        passed=not missing and not unexpected and not mismatched,
        missing_effect_ids=missing,
        unexpected_effect_ids=unexpected,
        mismatched_effect_ids=mismatched,
    )
