"""Credential-free model identity for Web recovery checks.

Built-in Responses adapters already bind provider replay state to a digest of
the model, endpoint, capabilities and request limits. Reuse that same digest
before an approved write. A custom adapter's class is an integration boundary,
not a proof that its hidden configuration or behavior remained unchanged.
"""

from __future__ import annotations

from voren.providers.openai_responses import OpenAIResponsesModelAdapter
from voren.runtime.ports import ModelAdapter
from voren.web.demo_model import DemoWorkspaceModel


def model_context(model: ModelAdapter, *, mode: str) -> dict:
    adapter = f"{type(model).__module__}.{type(model).__qualname__}"
    context = {
        "schema_version": "voren.web-model-context.v1",
        "mode": mode,
        "adapter": adapter,
        "boundary": "custom_adapter",
        "configuration_verified": False,
    }
    if isinstance(model, OpenAIResponsesModelAdapter):
        # This existing provider helper deliberately excludes credentials and
        # binds exactly the configuration used for provider-state restoration.
        context.update({
            "boundary": "responses",
            "configuration_verified": True,
            "provider_profile": model.provider_profile.value,
            "configuration_digest": model._provider_config_digest(),
        })
    elif type(model) is DemoWorkspaceModel:
        context.update({"boundary": "demo", "configuration_verified": True})
    return context
