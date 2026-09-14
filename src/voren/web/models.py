"""HTTP contracts that preserve exact action and run identities."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from voren.actions.models import ActionProposal, ActionReceipt
from voren.runtime.models import RuntimeUsage


class WebModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateRunRequest(WebModel):
    client_request_id: str = Field(
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{7,127}$"
    )
    request: str = Field(min_length=1, max_length=8_000)


class DecideRunRequest(WebModel):
    decision_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{7,127}$")
    proposal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved: bool


class RunView(WebModel):
    client_request_id: str
    run_id: str
    workspace: str = "agentdojo"
    status: str
    final_text: str | None = None
    proposal: ActionProposal | None = None
    receipt: ActionReceipt | None = None
    decision_id: str | None = None
    decision_approved: bool | None = None
    usage: RuntimeUsage = Field(default_factory=RuntimeUsage)
    error_code: str | None = None
    error_detail_code: str | None = None
    recovery_required: bool = False
    created_at: datetime
    updated_at: datetime


class HealthView(WebModel):
    status: str = "ok"
    mode: str
    workspace: str
    workspace_configured: bool
    writes_target: str
    authentication: str = "local_loopback_only"
    live_model_configured: bool
    knowledge_database: str
