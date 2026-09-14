"""Typed, immutable profile and episode memory records."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MemoryKind(StrEnum):
    PROFILE_PREFERENCE = "profile_preference"
    EPISODE_SUMMARY = "episode_summary"


class MemoryEvidenceSource(StrEnum):
    OPERATOR_CORRECTION = "operator_correction"
    VERIFIED_RUN = "verified_run"


class MemoryRef(FrozenModel):
    kind: MemoryKind
    memory_id: str = Field(
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:-]*$", max_length=200
    )
    version_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class MemoryRecord(FrozenModel):
    ref: MemoryRef
    content: str = Field(min_length=1, max_length=16_000)
    evidence_id: str = Field(min_length=1, max_length=200)
    evidence_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_source: MemoryEvidenceSource
    instruction_authority: bool = False
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        kind: MemoryKind,
        memory_id: str,
        content: str,
        evidence_id: str,
        evidence_digest: str,
        evidence_source: MemoryEvidenceSource,
        created_at: datetime,
    ) -> Self:
        normalized = content.strip()
        content_digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        version_id = _digest(
            {
                "kind": kind.value,
                "memory_id": memory_id,
                "content_digest": content_digest,
                "evidence_id": evidence_id,
                "evidence_digest": evidence_digest,
                "evidence_source": evidence_source.value,
            }
        )
        return cls(
            ref=MemoryRef(
                kind=kind,
                memory_id=memory_id,
                version_id=version_id,
                content_digest=content_digest,
            ),
            content=normalized,
            evidence_id=evidence_id,
            evidence_digest=evidence_digest,
            evidence_source=evidence_source,
            created_at=created_at,
        )

    @model_validator(mode="after")
    def content_and_source_match_reference(self) -> Self:
        if self.instruction_authority:
            raise ValueError("memory data cannot carry procedural instruction authority")
        if hashlib.sha256(self.content.encode("utf-8")).hexdigest() != (
            self.ref.content_digest
        ):
            raise ValueError("memory content digest does not match")
        expected_version = _digest(
            {
                "kind": self.ref.kind.value,
                "memory_id": self.ref.memory_id,
                "content_digest": self.ref.content_digest,
                "evidence_id": self.evidence_id,
                "evidence_digest": self.evidence_digest,
                "evidence_source": self.evidence_source.value,
            }
        )
        if self.ref.version_id != expected_version:
            raise ValueError("memory version digest does not match its evidence")
        if (
            self.ref.kind is MemoryKind.PROFILE_PREFERENCE
            and self.evidence_source is not MemoryEvidenceSource.OPERATOR_CORRECTION
        ):
            raise ValueError("profile preference requires operator correction evidence")
        if (
            self.ref.kind is MemoryKind.EPISODE_SUMMARY
            and self.evidence_source is not MemoryEvidenceSource.VERIFIED_RUN
        ):
            raise ValueError("episode summary requires verified-run evidence")
        return self


def _digest(value: object) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
