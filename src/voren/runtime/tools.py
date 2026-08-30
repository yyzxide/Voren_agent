"""Adapters from Voren action contracts to model-visible tool schemas."""

from __future__ import annotations

from voren.actions.gateway import ActionDefinition
from voren.runtime.models import ToolDefinition, ToolKind


def external_action_tool(
    definition: ActionDefinition, *, description: str
) -> ToolDefinition:
    """Expose the same validated action schema used by ``ActionGateway``."""

    return ToolDefinition(
        name=definition.name,
        description=description,
        input_schema=definition.input_model.model_json_schema(),
        kind=ToolKind.EXTERNAL_ACTION,
    )
