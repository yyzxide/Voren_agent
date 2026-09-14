"""Integrity-bound paired evaluation records for Skill candidates."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from voren.learning.models import FrozenModel
from voren.skills.models import SkillVersionRef


CANDIDATE_EVALUATION_SCHEMA = "voren-skill-candidate-evaluation/v1"


class EvaluationCaseKind(StrEnum):
    BENIGN = "benign"
    ATTACK = "attack"


class HeldOutCase(FrozenModel):
    case_id: str = Field(
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:-]*$", max_length=200
    )
    kind: EvaluationCaseKind


class TrialMeasurement(FrozenModel):
    """Evaluator output before the runner binds it to a case and Skill ref."""

    run_id: str | None = Field(default=None, min_length=1, max_length=200)
    utility_passed: bool | None = None
    attack_success: bool | None = None
    run_artifact_digest: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    infrastructure_error_code: str | None = Field(
        default=None, min_length=1, max_length=200
    )

    @model_validator(mode="after")
    def failure_is_not_a_behavioral_result(self) -> Self:
        if self.infrastructure_error_code is not None:
            if any(
                value is not None
                for value in (
                    self.run_id,
                    self.utility_passed,
                    self.attack_success,
                    self.run_artifact_digest,
                )
            ):
                raise ValueError(
                    "infrastructure failure cannot contain behavioral metrics"
                )
        elif (
            self.run_id is None
            or self.utility_passed is None
            or self.run_artifact_digest is None
        ):
            raise ValueError(
                "completed measurement requires run, utility, and artifact evidence"
            )
        return self


class CandidateTrialResult(FrozenModel):
    case_id: str
    kind: EvaluationCaseKind
    skill_ref: SkillVersionRef
    measurement: TrialMeasurement

    @model_validator(mode="after")
    def attack_metric_matches_case(self) -> Self:
        if self.measurement.infrastructure_error_code is not None:
            return self
        if self.kind is EvaluationCaseKind.ATTACK:
            if self.measurement.attack_success is None:
                raise ValueError("attack case requires an attack-success result")
        elif self.measurement.attack_success is not None:
            raise ValueError("benign case cannot contain an attack-success result")
        return self


class PairedCaseResult(FrozenModel):
    case: HeldOutCase
    base: CandidateTrialResult
    candidate: CandidateTrialResult

    @model_validator(mode="after")
    def trials_match_case(self) -> Self:
        for result in (self.base, self.candidate):
            if result.case_id != self.case.case_id or result.kind is not self.case.kind:
                raise ValueError("paired trial does not match its held-out case")
        if self.base.skill_ref == self.candidate.skill_ref:
            raise ValueError("paired trial must compare different Skill versions")
        return self


class CandidateEvaluationArtifact(FrozenModel):
    schema_version: str = CANDIDATE_EVALUATION_SCHEMA
    evaluation_id: str = Field(
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:-]*$", max_length=200
    )
    candidate_id: str = Field(
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:-]*$", max_length=200
    )
    base_ref: SkillVersionRef
    candidate_ref: SkillVersionRef
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    suite_id: str = Field(min_length=1, max_length=200)
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    pairs: tuple[PairedCaseResult, ...] = Field(min_length=1)
    artifact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def create(
        cls,
        *,
        evaluation_id: str,
        candidate_id: str,
        base_ref: SkillVersionRef,
        candidate_ref: SkillVersionRef,
        evidence_ids: tuple[str, ...],
        suite_id: str,
        manifest_digest: str,
        created_at: datetime,
        pairs: tuple[PairedCaseResult, ...],
    ) -> Self:
        provisional = cls(
            schema_version=CANDIDATE_EVALUATION_SCHEMA,
            evaluation_id=evaluation_id,
            candidate_id=candidate_id,
            base_ref=base_ref,
            candidate_ref=candidate_ref,
            evidence_ids=evidence_ids,
            suite_id=suite_id,
            manifest_digest=manifest_digest,
            created_at=created_at,
            pairs=pairs,
            artifact_digest="0" * 64,
        )
        return cls.model_validate(
            {
                **provisional.model_dump(mode="python"),
                "artifact_digest": provisional.calculated_digest(),
            }
        )

    @model_validator(mode="after")
    def exact_versions_and_cases_are_bound(self) -> Self:
        if self.schema_version != CANDIDATE_EVALUATION_SCHEMA:
            raise ValueError("unsupported candidate evaluation schema")
        if self.base_ref.name != self.candidate_ref.name:
            raise ValueError("evaluation versions must belong to the same Skill")
        if self.base_ref == self.candidate_ref:
            raise ValueError("evaluation must compare different Skill versions")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("evaluation evidence IDs must be unique")
        case_ids = [pair.case.case_id for pair in self.pairs]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("held-out case IDs must be unique")
        for pair in self.pairs:
            if pair.base.skill_ref != self.base_ref:
                raise ValueError("base trial is not bound to the artifact base")
            if pair.candidate.skill_ref != self.candidate_ref:
                raise ValueError(
                    "candidate trial is not bound to the artifact candidate"
                )
        return self

    def calculated_digest(self) -> str:
        return _digest(
            self.model_dump(mode="json", exclude={"artifact_digest"})
        )

    def assert_integrity(self) -> None:
        if self.artifact_digest != self.calculated_digest():
            raise ValueError(
                "candidate evaluation artifact digest does not match its content"
            )


def _digest(value: object) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
