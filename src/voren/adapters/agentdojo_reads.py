"""Pure, provenance-labelled reads over the pinned AgentDojo workspace."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from voren.adapters.agentdojo_workspace import AgentDojoWorkspaceAdapter
from voren.observations.models import (
    ObservationItem,
    Provenance,
    SourceKind,
    ToolObservation,
    TrustLevel,
)
from voren.observations.read_tools import READ_TOOL_DEFINITIONS, ReadToolDefinition


class AgentDojoReadAdapter:
    """Run an allowlist of reads against a disposable environment snapshot.

    AgentDojo's ``get_unread_emails`` marks messages as read, so it is
    intentionally absent. Even allowlisted functions execute on a deep copy;
    an upstream behavior change therefore cannot mutate the live workspace.
    """

    def __init__(
        self,
        workspace: AgentDojoWorkspaceAdapter,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._workspace = workspace
        self._clock = clock or (lambda: datetime.now(UTC))
        self._runtime = workspace.create_functions_runtime()
        self._definitions = {item.name: item for item in READ_TOOL_DEFINITIONS}

    @property
    def definitions(self) -> tuple[ReadToolDefinition, ...]:
        return READ_TOOL_DEFINITIONS

    def execute(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        arguments: dict,
    ) -> ToolObservation:
        definition = self._definitions.get(tool_name)
        if definition is None:
            return ToolObservation.failed(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                error_code="unsupported_read_tool",
                error_message=f"read tool {tool_name!r} is not allowlisted",
            )
        try:
            validated = definition.input_model.model_validate(arguments)
        except ValidationError as error:
            return ToolObservation.failed(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                error_code="invalid_arguments",
                error_message=str(error),
            )

        # A read executes against a snapshot, not the live external world. This
        # makes purity an enforced adapter property rather than a tool-name claim.
        environment_snapshot = self._workspace.environment.model_copy(deep=True)
        result, error_message = self._runtime.run_function(
            environment_snapshot,
            tool_name,
            validated.model_dump(mode="json"),
        )
        if error_message is not None:
            return ToolObservation.failed(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                error_code="provider_read_failed",
                error_message=error_message,
            )

        retrieved_at = self._clock()
        items = tuple(
            ObservationItem(
                data=self._normalize(tool_name, item),
                provenance=Provenance(
                    trust=TrustLevel.EXTERNAL_UNTRUSTED,
                    source=self._source_kind(tool_name),
                    source_ref=self._source_ref(tool_name, item),
                    retrieved_by=f"agentdojo:{tool_name}",
                    retrieved_at=retrieved_at,
                    instruction_authority=False,
                ),
            )
            for item in result
        )
        return ToolObservation.succeeded(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            items=items,
        )

    @staticmethod
    def _source_kind(tool_name: str) -> SourceKind:
        return {
            "search_emails": SourceKind.EMAIL,
            "get_day_calendar_events": SourceKind.CALENDAR,
            "search_contacts_by_name": SourceKind.CONTACT,
        }[tool_name]

    @staticmethod
    def _source_ref(tool_name: str, item: Any) -> str:
        if tool_name == "search_contacts_by_name":
            return str(item.email)
        return str(item.id_)

    @staticmethod
    def _normalize(tool_name: str, item: Any) -> dict[str, Any]:
        if tool_name == "search_emails":
            return {
                "id": str(item.id_),
                "sender": str(item.sender),
                "recipients": sorted(map(str, item.recipients)),
                "cc": sorted(map(str, item.cc)),
                "subject": item.subject,
                "body": item.body,
                "status": str(item.status),
                "read": item.read,
                "timestamp": item.timestamp.isoformat(),
                "attachment_count": len(item.attachments),
            }
        if tool_name == "get_day_calendar_events":
            return {
                "id": str(item.id_),
                "title": item.title,
                "description": item.description,
                "start_time": item.start_time.isoformat(),
                "end_time": item.end_time.isoformat(),
                "location": item.location,
                "participants": sorted(map(str, item.participants)),
                "all_day": item.all_day,
                "status": str(item.status),
            }
        if tool_name == "search_contacts_by_name":
            return {"name": item.name, "email": str(item.email)}
        raise ValueError(f"cannot normalize unsupported read tool {tool_name!r}")
