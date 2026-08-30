"""Deterministic model adapter used by tests and credential-free demos."""

from __future__ import annotations

from voren.runtime.models import ModelMessage, ModelResponse, ToolDefinition


class ScriptedModelAdapter:
    def __init__(self, responses: tuple[ModelResponse, ...]) -> None:
        self._responses = responses
        self.requests: list[
            tuple[tuple[ModelMessage, ...], tuple[ToolDefinition, ...]]
        ] = []

    def complete(
        self,
        *,
        messages: tuple[ModelMessage, ...],
        tools: tuple[ToolDefinition, ...],
    ) -> ModelResponse:
        self.requests.append((messages, tools))
        index = len(self.requests) - 1
        if index >= len(self._responses):
            raise RuntimeError("scripted model has no response for this step")
        return self._responses[index]
