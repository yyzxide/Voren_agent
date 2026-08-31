"""Model boundary consumed by Voren's agent loop."""

from __future__ import annotations

from typing import Protocol

from voren.runtime.cancellation import CancellationToken
from voren.runtime.models import ModelMessage, ModelResponse, ToolDefinition


class ModelAdapter(Protocol):
    def complete(
        self,
        *,
        messages: tuple[ModelMessage, ...],
        tools: tuple[ToolDefinition, ...],
        cancellation: CancellationToken,
    ) -> ModelResponse: ...
