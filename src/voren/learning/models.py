"""Immutable evidence and candidate records for offline Skill learning."""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from voren.skills.models import SkillVersionRef


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceSource(StrEnum):
    OPERATOR_CORRECTION = "operator_correction"
    VERIFIED_RUN = "verified_run"
    MODEL_REFLECTION = "model_reflection"
    EXTERNAL_OBSERVATION = "external_observation"


class EvidenceRef(FrozenModel):
    evidence_id: str = Field(
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:-]*$", max_length=200
    )
    source: EvidenceSource
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    instruction_authority: bool
    evaluation_case_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def authority_matches_source(self) -> Self:
        if self.source is EvidenceSource.OPERATOR_CORRECTION:
            if not self.instruction_authority:
                raise ValueError("operator correction evidence must carry authority")
        elif self.instruction_authority:
            raise ValueError(
                f"{self.source.value} evidence cannot carry instruction authority"
            )
        if len(self.evaluation_case_ids) != len(set(self.evaluation_case_ids)):
            raise ValueError("evidence evaluation case IDs must be unique")
        if any(not case_id.strip() for case_id in self.evaluation_case_ids):
            raise ValueError("evidence evaluation case IDs must be non-empty")
        return self


class CandidateStatus(StrEnum):
    STAGED = "staged"
    EVALUATING = "evaluating"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PROMOTED = "promoted"
    ROLLED_BACK = "rolled_back"


class SkillDiff(FrozenModel):
    base_content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    unified_diff: str = Field(min_length=1, max_length=50_000)
    diff_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    added_lines: int = Field(ge=0)
    removed_lines: int = Field(ge=0)

    @model_validator(mode="after")
    def digest_matches_diff(self) -> Self:
        digest = hashlib.sha256(self.unified_diff.encode("utf-8")).hexdigest()
        if digest != self.diff_digest:
            raise ValueError("Skill diff digest does not match its content")
        if self.added_lines + self.removed_lines == 0:
            raise ValueError("Skill candidate must contain a non-empty edit")
        return self


class SkillCandidate(FrozenModel):
    candidate_id: str = Field(
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:-]*$", max_length=200
    )
    base_ref: SkillVersionRef
    candidate_ref: SkillVersionRef
    evidence: tuple[EvidenceRef, ...] = Field(min_length=1)
    diff: SkillDiff
    status: CandidateStatus = CandidateStatus.STAGED
    created_at: datetime
    updated_at: datetime
    decision_reason: str | None = Field(default=None, min_length=1, max_length=2_000)
    evaluation_artifact_digest: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )

    @model_validator(mode="after")
    def references_are_consistent(self) -> Self:
        if self.base_ref.name != self.candidate_ref.name:
            raise ValueError("base and candidate must have the same Skill name")
        if self.base_ref == self.candidate_ref:
            raise ValueError("candidate must differ from its base version")
        if self.diff.base_content_digest != self.base_ref.content_digest:
            raise ValueError("Skill diff is not bound to the base version")
        if self.diff.candidate_content_digest != self.candidate_ref.content_digest:
            raise ValueError("Skill diff is not bound to the candidate version")
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("candidate evidence IDs must be unique")
        if self.updated_at < self.created_at:
            raise ValueError("candidate updated_at cannot precede created_at")
        if self.status is CandidateStatus.STAGED and (
            self.decision_reason is not None
            or self.evaluation_artifact_digest is not None
        ):
            raise ValueError("a staged candidate cannot contain a decision")
        if self.status in {
            CandidateStatus.ACCEPTED,
            CandidateStatus.REJECTED,
            CandidateStatus.PROMOTED,
            CandidateStatus.ROLLED_BACK,
        } and (
            self.decision_reason is None
            or self.evaluation_artifact_digest is None
        ):
            raise ValueError("a decided candidate requires reason and evaluation")
        return self
