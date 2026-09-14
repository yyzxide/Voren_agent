"""Integrity-bound policy ablation for direct versus gated Skill learning."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Self

from pydantic import Field, model_validator

from voren.learning.evaluation import CandidateEvaluationArtifact, EvaluationCaseKind
from voren.learning.models import CandidateStatus, FrozenModel, SkillCandidate


ABLATION_SCHEMA = "voren-learning-policy-ablation/v1"


class CandidateAblationResult(FrozenModel):
    candidate_id: str
    evaluation_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision_status: CandidateStatus
    decision_reason: str
    measured_improvement: bool
    utility_regression: bool
    security_regression: bool
    direct_reflection_would_activate: bool = True
    gated_eligible_for_operator_promotion: bool


class LearningAblationArtifact(FrozenModel):
    schema_version: str = ABLATION_SCHEMA
    experiment_id: str = Field(min_length=1, max_length=200)
    created_at: datetime
    results: tuple[CandidateAblationResult, ...] = Field(min_length=1)
    direct_reflection_activation_count: int = Field(ge=0)
    gated_promotion_eligible_count: int = Field(ge=0)
    unsafe_activations_avoided: int = Field(ge=0)
    beneficial_candidates_retained: int = Field(ge=0)
    artifact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def create(
        cls,
        *,
        experiment_id: str,
        created_at: datetime,
        results: tuple[CandidateAblationResult, ...],
    ) -> Self:
        direct_count = sum(item.direct_reflection_would_activate for item in results)
        gated_count = sum(
            item.gated_eligible_for_operator_promotion for item in results
        )
        unsafe_avoided = sum(
            item.direct_reflection_would_activate
            and not item.gated_eligible_for_operator_promotion
            and (item.utility_regression or item.security_regression)
            for item in results
        )
        beneficial_retained = sum(
            item.gated_eligible_for_operator_promotion
            and item.measured_improvement
            and not item.utility_regression
            and not item.security_regression
            for item in results
        )
        provisional = cls(
            experiment_id=experiment_id,
            created_at=created_at,
            results=results,
            direct_reflection_activation_count=direct_count,
            gated_promotion_eligible_count=gated_count,
            unsafe_activations_avoided=unsafe_avoided,
            beneficial_candidates_retained=beneficial_retained,
            artifact_digest="0" * 64,
        )
        return cls.model_validate(
            {
                **provisional.model_dump(mode="python"),
                "artifact_digest": provisional.calculated_digest(),
            }
        )

    @model_validator(mode="after")
    def summary_matches_results(self) -> Self:
        if self.schema_version != ABLATION_SCHEMA:
            raise ValueError("unsupported learning ablation schema")
        if len({item.candidate_id for item in self.results}) != len(self.results):
            raise ValueError("ablation candidate IDs must be unique")
        expected = type(self).create_summary(self.results)
        actual = (
            self.direct_reflection_activation_count,
            self.gated_promotion_eligible_count,
            self.unsafe_activations_avoided,
            self.beneficial_candidates_retained,
        )
        if actual != expected:
            raise ValueError("learning ablation summary does not match results")
        return self

    @staticmethod
    def create_summary(
        results: tuple[CandidateAblationResult, ...]
    ) -> tuple[int, int, int, int]:
        return (
            sum(item.direct_reflection_would_activate for item in results),
            sum(item.gated_eligible_for_operator_promotion for item in results),
            sum(
                item.direct_reflection_would_activate
                and not item.gated_eligible_for_operator_promotion
                and (item.utility_regression or item.security_regression)
                for item in results
            ),
            sum(
                item.gated_eligible_for_operator_promotion
                and item.measured_improvement
                and not item.utility_regression
                and not item.security_regression
                for item in results
            ),
        )

    def calculated_digest(self) -> str:
        return _digest(self.model_dump(mode="json", exclude={"artifact_digest"}))

    def assert_integrity(self) -> None:
        if self.artifact_digest != self.calculated_digest():
            raise ValueError("learning ablation artifact digest does not match")


def compare_learning_policies(
    *,
    experiment_id: str,
    created_at: datetime,
    candidates: tuple[SkillCandidate, ...],
    evaluations: tuple[CandidateEvaluationArtifact, ...],
) -> LearningAblationArtifact:
    evaluation_by_candidate = {item.candidate_id: item for item in evaluations}
    if len(evaluation_by_candidate) != len(evaluations):
        raise ValueError("ablation evaluations must have unique candidate IDs")
    results = []
    for candidate in candidates:
        if candidate.status not in {
            CandidateStatus.ACCEPTED,
            CandidateStatus.REJECTED,
            CandidateStatus.PROMOTED,
            CandidateStatus.ROLLED_BACK,
        }:
            raise ValueError("ablation requires a decided candidate")
        artifact = evaluation_by_candidate.get(candidate.candidate_id)
        if artifact is None:
            raise ValueError("ablation candidate has no paired evaluation")
        artifact.assert_integrity()
        if candidate.evaluation_artifact_digest != artifact.artifact_digest:
            raise ValueError("ablation evaluation is not bound to its candidate")
        utility_regression = any(
            pair.base.measurement.utility_passed is True
            and pair.candidate.measurement.utility_passed is False
            for pair in artifact.pairs
        )
        security_regression = any(
            pair.case.kind is EvaluationCaseKind.ATTACK
            and pair.base.measurement.attack_success is False
            and pair.candidate.measurement.attack_success is True
            for pair in artifact.pairs
        )
        measured_improvement = any(
            (
                pair.base.measurement.utility_passed is False
                and pair.candidate.measurement.utility_passed is True
            )
            or (
                pair.case.kind is EvaluationCaseKind.ATTACK
                and pair.base.measurement.attack_success is True
                and pair.candidate.measurement.attack_success is False
            )
            for pair in artifact.pairs
        )
        eligible = candidate.status in {
            CandidateStatus.ACCEPTED,
            CandidateStatus.PROMOTED,
            CandidateStatus.ROLLED_BACK,
        }
        results.append(
            CandidateAblationResult(
                candidate_id=candidate.candidate_id,
                evaluation_digest=artifact.artifact_digest,
                decision_status=candidate.status,
                decision_reason=candidate.decision_reason or "missing decision reason",
                measured_improvement=measured_improvement,
                utility_regression=utility_regression,
                security_regression=security_regression,
                gated_eligible_for_operator_promotion=eligible,
            )
        )
    if set(evaluation_by_candidate) != {item.candidate_id for item in candidates}:
        raise ValueError("ablation contains an evaluation for another candidate")
    return LearningAblationArtifact.create(
        experiment_id=experiment_id,
        created_at=created_at,
        results=tuple(results),
    )


def render_learning_ablation(artifact: LearningAblationArtifact) -> str:
    artifact.assert_integrity()
    lines = [
        f"# Learning Policy Ablation: `{artifact.experiment_id}`",
        "",
        "## Summary",
        "",
        f"- Direct-reflection candidates activated: {artifact.direct_reflection_activation_count}",
        f"- Gated candidates eligible for operator promotion: {artifact.gated_promotion_eligible_count}",
        f"- Unsafe activations avoided by the gate: {artifact.unsafe_activations_avoided}",
        f"- Beneficial candidates retained by the gate: {artifact.beneficial_candidates_retained}",
        f"- Artifact digest: `{artifact.artifact_digest}`",
        "",
        "| Candidate | Decision | Direct would activate | Gated eligible | Improvement | Utility regression | Security regression |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in artifact.results:
        lines.append(
            "| "
            + " | ".join(
                (
                    item.candidate_id,
                    item.decision_status.value,
                    str(item.direct_reflection_would_activate).lower(),
                    str(item.gated_eligible_for_operator_promotion).lower(),
                    str(item.measured_improvement).lower(),
                    str(item.utility_regression).lower(),
                    str(item.security_regression).lower(),
                )
            )
            + " |"
        )
    lines.extend(
        (
            "",
            "## Interpretation limits",
            "",
            "- Direct reflection is a counterfactual policy baseline: every syntactically admitted edit would become active without held-out evaluation.",
            "- The measurements come from deterministic instruction-contract trials, not live-model behavior or AgentDojo benchmark scores.",
            "- Gated eligibility is still not automatic activation; production promotion remains a separate reason-bearing operator action.",
            "",
        )
    )
    return "\n".join(lines)


def write_learning_ablation(
    *,
    artifact_path: Path,
    report_path: Path,
    artifact: LearningAblationArtifact,
) -> tuple[str, str]:
    artifact.assert_integrity()
    artifact_text = json.dumps(
        artifact.model_dump(mode="json"),
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    ) + "\n"
    report_text = render_learning_ablation(artifact)
    _atomic_write(artifact_path, artifact_text)
    _atomic_write(report_path, report_text)
    return (
        hashlib.sha256(artifact_text.encode("utf-8")).hexdigest(),
        hashlib.sha256(report_text.encode("utf-8")).hexdigest(),
    )


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _digest(value: object) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
