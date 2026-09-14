"""Immutable contracts for provenance-bearing business documents."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Self

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
