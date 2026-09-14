"""Structured MCP payloads kept separate from Tool Observations."""

from pydantic import BaseModel, ConfigDict, Field

from voren.knowledge.models import KnowledgeSearchHit


class KnowledgeSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=1)
    results: tuple[KnowledgeSearchHit, ...]
