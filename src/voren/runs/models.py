"""Persistent run lifecycle and append-only event models."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NEEDS_RECONCILIATION = "needs_reconciliation"


class RunEventType(StrEnum):
    RUN_CREATED = "run.created"
    RUN_STARTED = "run.started"
    MODEL_REQUESTED = "model.requested"
    MODEL_RESPONDED = "model.responded"
    TOOL_CALLED = "tool.called"
    TOOL_OBSERVED = "tool.observed"
    RUNTIME_LIMIT_REACHED = "runtime.limit_reached"
    ACTION_PROPOSED = "action.proposed"
    RUN_WAITING_APPROVAL = "run.waiting_approval"
    APPROVAL_ACCEPTED = "approval.accepted"
    APPROVAL_REJECTED = "approval.rejected"
    APPROVAL_INVALID = "approval.invalid"
    ACTION_RECEIPT = "action.receipt"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"
    RUN_NEEDS_RECONCILIATION = "run.needs_reconciliation"


class RunConfig(FrozenModel):
    workflow: str = Field(min_length=1)
    world_adapter: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    action_contract_versions: tuple[str, ...]
    metadata: dict[str, Any] = Field(default_factory=dict)

    def calculated_digest(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class RunRecord(FrozenModel):
    run_id: str = Field(min_length=1)
    status: RunStatus
    config: RunConfig
    config_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    pending_operation_id: str | None = None
    pending_proposal_digest: str | None = None
    last_receipt_status: str | None = None
    version: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime


class NewRunEvent(FrozenModel):
    event_id: str = Field(min_length=1)
    dedupe_key: str = Field(min_length=1)
    event_type: RunEventType
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime


class RunEvent(NewRunEvent):
    sequence: int = Field(gt=0)
    run_id: str = Field(min_length=1)
