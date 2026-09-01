"""Portable Agent Skills metadata plus Voren's version and policy sidecar."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentSkillMetadata(FrozenModel):
    """Metadata exposed during progressive skill discovery."""

    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)
    description: str = Field(min_length=1, max_length=1024)
    license: str | None = Field(default=None, min_length=1)
    compatibility: str | None = Field(default=None, min_length=1, max_length=500)
    metadata: dict[str, str] = Field(default_factory=dict)
    allowed_tools_hint: str | None = Field(default=None, min_length=1)


class SkillContract(FrozenModel):
    """Voren-only bounds; actual runtime policy may narrow them further."""

    schema_version: str = "voren.skill.v1"
    scope: tuple[str, ...] = Field(min_length=1)
    tool_scope: tuple[str, ...] = ()
    effect_scope: tuple[str, ...] = ()
    evaluation_suites: tuple[str, ...] = ()

    @model_validator(mode="after")
    def values_are_unique_and_supported(self) -> Self:
        if self.schema_version != "voren.skill.v1":
            raise ValueError("unsupported Voren skill contract schema")
        for field_name in (
            "scope",
            "tool_scope",
            "effect_scope",
            "evaluation_suites",
        ):
            values = getattr(self, field_name)
            if any(not value.strip() for value in values):
                raise ValueError(f"{field_name} values must be non-empty")
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} values must be unique")
        return self


class SkillFile(FrozenModel):
    relative_path: str = Field(min_length=1)
    content: bytes
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def digest_matches_content(self) -> Self:
        if hashlib.sha256(self.content).hexdigest() != self.digest:
            raise ValueError("skill file digest does not match content")
        return self


class SkillPackage(FrozenModel):
    metadata: AgentSkillMetadata
    instructions: str = Field(min_length=1)
    contract: SkillContract
    files: tuple[SkillFile, ...] = Field(min_length=2)
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def build(
        cls,
        *,
        metadata: AgentSkillMetadata,
        instructions: str,
        contract: SkillContract,
        files: tuple[SkillFile, ...],
    ) -> SkillPackage:
        ordered = tuple(sorted(files, key=lambda item: item.relative_path))
        digest = cls.calculate_content_digest(ordered)
        return cls(
            metadata=metadata,
            instructions=instructions,
            contract=contract,
            files=ordered,
            content_digest=digest,
        )

    @model_validator(mode="after")
    def package_is_canonical(self) -> Self:
        paths = tuple(item.relative_path for item in self.files)
        if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise ValueError("skill package files must be unique and sorted")
        if "SKILL.md" not in paths or "skill.yaml" not in paths:
            raise ValueError("skill package requires SKILL.md and skill.yaml")
        if self.calculate_content_digest(self.files) != self.content_digest:
            raise ValueError("skill package content digest is stale")
        return self

    @staticmethod
    def calculate_content_digest(files: tuple[SkillFile, ...]) -> str:
        manifest = [
            {
                "path": item.relative_path,
                "digest": item.digest,
                "size": len(item.content),
            }
            for item in files
        ]
        canonical = json.dumps(
            manifest,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class SkillVersionRef(FrozenModel):
    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)
    version_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class SkillVersion(FrozenModel):
    ref: SkillVersionRef
    metadata: AgentSkillMetadata
    contract: SkillContract
    package_path: str = Field(min_length=1)
    created_at: datetime


class LoadedSkill(FrozenModel):
    version: SkillVersion
    instructions: str = Field(min_length=1)
    resources: tuple[str, ...] = ()


class ActiveSkillSummary(FrozenModel):
    """Small discovery payload; instructions and resources remain unloaded."""

    ref: SkillVersionRef
    description: str = Field(min_length=1, max_length=1024)
    activated_at: datetime
    activation_reason: str = Field(min_length=1)
