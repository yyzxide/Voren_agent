"""Synchronous Agent Loop adapter over an official MCP client session."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import anyio
from mcp import Client
from pydantic import ValidationError

from voren.adapters.knowledge_reads import (
    SEARCH_MEETING_KNOWLEDGE,
    SearchMeetingKnowledgeInput,
)
from voren.mcp_bridge.models import KnowledgeSearchResponse
from voren.mcp_bridge.server import MCP_PROTOCOL_VERSION
from voren.observations.models import (
    ObservationItem,
    Provenance,
    SourceKind,
    ToolObservation,
    TrustLevel,
)
from voren.observations.read_tools import ReadToolDefinition


class MCPKnowledgeReadAdapter:
    """Translate an MCP result into Voren's stricter provenance envelope."""

    def __init__(
        self,
        server: Any,
        *,
        clock: Callable[[], datetime] | None = None,
        timeout_seconds: float = 10.0,
        source_id: str = "voren-knowledge",
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("MCP timeout_seconds must be positive")
        if not source_id.strip() or ":" in source_id:
            raise ValueError("MCP source_id must be non-empty and contain no colon")
        self._server = server
        self._clock = clock or (lambda: datetime.now(UTC))
        self._timeout_seconds = timeout_seconds
        self._source_id = source_id.strip()

    @property
    def definitions(self) -> tuple[ReadToolDefinition, ...]:
        return (SEARCH_MEETING_KNOWLEDGE,)

    def execute(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        arguments: dict,
    ) -> ToolObservation:
        if tool_name != SEARCH_MEETING_KNOWLEDGE.name:
            return ToolObservation.failed(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                error_code="unsupported_read_tool",
                error_message=f"read tool {tool_name!r} is not allowlisted",
            )
        try:
            request = SearchMeetingKnowledgeInput.model_validate(arguments)
        except ValidationError as error:
            return ToolObservation.failed(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                error_code="invalid_arguments",
                error_message=str(error),
            )
        try:
            response, server_name, protocol_version = anyio.run(
                self._search, request
            )
        except Exception as error:
            return ToolObservation.failed(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                error_code="mcp_read_failed",
                error_message=f"MCP retrieval failed ({type(error).__name__})",
            )

        retrieved_at = self._clock()
        return ToolObservation.succeeded(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            items=tuple(
                ObservationItem(
                    data=hit.model_dump(mode="json"),
                    provenance=Provenance(
                        trust=TrustLevel.EXTERNAL_UNTRUSTED,
                        source=SourceKind.KNOWLEDGE,
                        source_ref=(
                            f"{hit.ref.document_id}@{hit.ref.version_id}"
                        ),
                        retrieved_by=(
                            f"mcp:{self._source_id}:reported={server_name}:"
                            f"{protocol_version}:"
                            "search_meeting_knowledge"
                        ),
                        retrieved_at=retrieved_at,
                        instruction_authority=False,
                    ),
                )
                for hit in response.results
            ),
        )

    async def _search(
        self,
        request: SearchMeetingKnowledgeInput,
    ) -> tuple[KnowledgeSearchResponse, str, str]:
        with anyio.fail_after(self._timeout_seconds):
            async with Client(
                self._server,
                mode=MCP_PROTOCOL_VERSION,
                raise_exceptions=True,
            ) as client:
                result = await client.call_tool(
                    SEARCH_MEETING_KNOWLEDGE.name,
                    request.model_dump(mode="json"),
                )
                if result.is_error or result.structured_content is None:
                    raise RuntimeError("MCP tool returned no structured result")
                response = KnowledgeSearchResponse.model_validate(
                    result.structured_content
                )
                server_name = (
                    "unknown"
                    if client.server_info is None
                    else client.server_info.name
                )
                return response, server_name, client.protocol_version
