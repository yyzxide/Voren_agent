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


class ModelResponse(FrozenModel):
    text: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()


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


class RuntimeResult(FrozenModel):
    run_id: str
    status: RuntimeResultStatus
    final_text: str | None = None
    pending_proposal: ActionProposal | None = None
    model_steps: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    error_code: str | None = None
    error_detail_code: str | None = None
