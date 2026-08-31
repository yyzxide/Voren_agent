"""Provider-neutral model, tool, and bounded-loop contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from voren.actions.models import ActionProposal


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, revalidate_instances="always"
    )


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolKind(StrEnum):
    READ = "read"
    EXTERNAL_ACTION = "external_action"


class ToolCall(FrozenModel):
    call_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ModelMessage(FrozenModel):
    role: MessageRole
    content: str | dict[str, Any] | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None

    @model_validator(mode="after")
    def role_matches_payload_shape(self) -> Self:
        if self.role in {MessageRole.SYSTEM, MessageRole.USER}:
            if not isinstance(self.content, str):
                raise ValueError("system and user messages require text content")
            if self.tool_calls or self.tool_call_id is not None:
                raise ValueError("system and user messages cannot carry tool metadata")
        elif self.role is MessageRole.ASSISTANT:
            if self.tool_call_id is not None:
                raise ValueError("assistant messages cannot be tool results")
            if self.content is not None and not isinstance(self.content, str):
                raise ValueError("assistant content must be text")
        elif self.role is MessageRole.TOOL:
            if not isinstance(self.content, dict) or self.tool_call_id is None:
                raise ValueError("tool messages require structured content and call ID")
            if self.tool_calls:
                raise ValueError("tool messages cannot request more tools")
        return self


class ToolDefinition(FrozenModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    input_schema: dict[str, Any]
    kind: ToolKind


class ModelUsage(FrozenModel):
    """Provider-reported usage for one completed model response."""

    input_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    cache_write_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def details_match_totals(self) -> Self:
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached_input_tokens cannot exceed input_tokens")
        if self.reasoning_output_tokens > self.output_tokens:
            raise ValueError("reasoning_output_tokens cannot exceed output_tokens")
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("total_tokens must equal input_tokens + output_tokens")
        return self


class RuntimeUsage(ModelUsage):
    """Accumulated token usage plus completeness across one agent run."""

    model_requests: int = Field(default=0, ge=0)
    reported_model_requests: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def reported_requests_do_not_exceed_requests(self) -> Self:
        if self.reported_model_requests > self.model_requests:
            raise ValueError("reported model requests cannot exceed model requests")
        return self

    @property
    def complete(self) -> bool:
        return self.model_requests == self.reported_model_requests


class ModelResponse(FrozenModel):
    text: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    usage: ModelUsage | None = None


class RuntimeLimits(FrozenModel):
    max_model_steps: int = Field(default=8, ge=1)
    max_tool_calls: int = Field(default=12, ge=1)
    max_repeated_tool_call: int = Field(default=2, ge=1)
    max_observation_bytes: int = Field(default=64_000, ge=1)


class RuntimeResultStatus(StrEnum):
    COMPLETED = "completed"
    WAITING_APPROVAL = "waiting_approval"
    LIMIT_EXCEEDED = "limit_exceeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CancellationReason(StrEnum):
    OPERATOR = "operator"
    DEADLINE = "deadline"
    PROVIDER = "provider"


class RuntimeResult(FrozenModel):
    run_id: str
    status: RuntimeResultStatus
    final_text: str | None = None
    pending_proposal: ActionProposal | None = None
    model_steps: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    usage: RuntimeUsage = Field(default_factory=RuntimeUsage)
    error_code: str | None = None
    error_detail_code: str | None = None
    cancellation_reason: CancellationReason | None = None
    cancellation_confirmed: bool | None = None

    @model_validator(mode="after")
    def result_invariants_hold(self) -> Self:
        if self.usage.model_requests != self.model_steps:
            raise ValueError("usage model_requests must equal model_steps")
        if self.status is RuntimeResultStatus.CANCELLED:
            if self.cancellation_reason is None:
                raise ValueError("cancelled result requires cancellation_reason")
        elif (
            self.cancellation_reason is not None
            or self.cancellation_confirmed is not None
        ):
            raise ValueError("only cancelled results may carry cancellation metadata")
        return self
