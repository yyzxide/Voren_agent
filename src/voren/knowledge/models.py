"""Immutable contracts for provenance-bearing business documents."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class KnowledgeSourceKind(StrEnum):
    MEETING_NOTE = "meeting_note"
    EMAIL_ATTACHMENT = "email_attachment"
    OPERATOR_NOTE = "operator_note"


class KnowledgeVersionRef(FrozenModel):
    document_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:-]*$")
    version_id: str = Field(pattern=r"^[0-9a-f]{64}$")


class KnowledgeDocument(FrozenModel):
    ref: KnowledgeVersionRef
    title: str = Field(min_length=1, max_length=500)
    source_uri: str = Field(min_length=1, max_length=2_048)
    source_kind: KnowledgeSourceKind
    content: str = Field(min_length=1)
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        document_id: str,
        title: str,
        source_uri: str,
        source_kind: KnowledgeSourceKind,
        content: str,
        created_at: datetime,
    ) -> Self:
        normalized_content = content.strip()
        normalized_title = title.strip()
        normalized_uri = source_uri.strip()
        content_digest, version_id = cls._digests(
            document_id=document_id,
            title=normalized_title,
            source_uri=normalized_uri,
            source_kind=source_kind,
            content=normalized_content,
        )
        return cls(
            ref=KnowledgeVersionRef(
                document_id=document_id,
                version_id=version_id,
            ),
            title=normalized_title,
            source_uri=normalized_uri,
            source_kind=source_kind,
            content=normalized_content,
            content_digest=content_digest,
            created_at=created_at,
        )

    @staticmethod
    def _digests(
        *,
        document_id: str,
        title: str,
        source_uri: str,
        source_kind: KnowledgeSourceKind,
        content: str,
    ) -> tuple[str, str]:
        content_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        version_payload = json.dumps(
            {
                "document_id": document_id,
                "title": title,
                "source_uri": source_uri,
                "source_kind": source_kind.value,
                "content_digest": content_digest,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return (
            content_digest,
            hashlib.sha256(version_payload.encode("utf-8")).hexdigest(),
        )

    @model_validator(mode="after")
    def digests_match_content_and_metadata(self) -> Self:
        if self.created_at.utcoffset() is None:
            raise ValueError("knowledge created_at must be timezone-aware")
        content_digest, version_id = self._digests(
            document_id=self.ref.document_id,
            title=self.title,
            source_uri=self.source_uri,
            source_kind=self.source_kind,
            content=self.content,
        )
        if content_digest != self.content_digest:
            raise ValueError("knowledge content digest does not match content")
        if version_id != self.ref.version_id:
            raise ValueError("knowledge version digest does not match metadata")
        return self


class KnowledgeSearchHit(FrozenModel):
    ref: KnowledgeVersionRef
    title: str
    source_uri: str
    source_kind: KnowledgeSourceKind
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    snippet: str = Field(min_length=1)
    score: int = Field(gt=0)
    retrieval_method: Literal["lexical", "bm25", "dense", "hybrid"] = "lexical"
    ranking_score: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    chunk_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    chunk_start: int | None = Field(default=None, ge=0)
    chunk_end: int | None = Field(default=None, gt=0)
    chunk_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def chunk_metadata_is_complete(self) -> Self:
        metadata = (self.chunk_id, self.chunk_start, self.chunk_end, self.chunk_digest)
        if any(value is not None for value in metadata):
            if any(value is None for value in metadata):
                raise ValueError("knowledge chunk metadata must be complete")
            assert self.chunk_start is not None and self.chunk_end is not None
            if self.chunk_end <= self.chunk_start:
                raise ValueError("knowledge chunk end must follow its start")
            if len(self.snippet) != self.chunk_end - self.chunk_start:
                raise ValueError("knowledge chunk offsets must match snippet length")
            if hashlib.sha256(self.snippet.encode("utf-8")).hexdigest() != self.chunk_digest:
                raise ValueError("knowledge chunk digest does not match snippet")
            identity = json.dumps(
                {
                    "version_id": self.ref.version_id,
                    "start": self.chunk_start,
                    "end": self.chunk_end,
                    "content_digest": self.chunk_digest,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            if hashlib.sha256(identity.encode("utf-8")).hexdigest() != self.chunk_id:
                raise ValueError("knowledge chunk ID does not match source version and offsets")
        elif self.retrieval_method != "lexical":
            raise ValueError("chunk retrieval requires exact chunk metadata")
        return self
