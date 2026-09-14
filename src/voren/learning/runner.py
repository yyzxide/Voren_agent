"""Run the same held-out cases against exact base and candidate versions."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from voren.learning.evaluation import (
    CandidateEvaluationArtifact,
    CandidateTrialResult,
    HeldOutCase,
    PairedCaseResult,
    TrialMeasurement,
)
from voren.learning.models import SkillCandidate
from voren.skills.models import SkillVersionRef


class EvaluationInfrastructureFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        if not code.strip():
            raise ValueError("infrastructure failure code must be non-empty")
        super().__init__(code)
        self.code = code


TrialEvaluator = Callable[[HeldOutCase, SkillVersionRef], TrialMeasurement]


class PairedEvaluationRunner:
    def run(
        self,
        *,
        evaluation_id: str,
        candidate: SkillCandidate,
        suite_id: str,
        manifest_digest: str,
        cases: tuple[HeldOutCase, ...],
        evaluator: TrialEvaluator,
        created_at: datetime,
    ) -> CandidateEvaluationArtifact:
        if not cases:
            raise ValueError("paired evaluation requires held-out cases")
        case_ids = [case.case_id for case in cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("paired evaluation case IDs must be unique")

        pairs = tuple(
            PairedCaseResult(
                case=case,
                base=self._evaluate(case, candidate.base_ref, evaluator),
                candidate=self._evaluate(
                    case, candidate.candidate_ref, evaluator
                ),
            )
            for case in cases
        )
        return CandidateEvaluationArtifact.create(
            evaluation_id=evaluation_id,
            candidate_id=candidate.candidate_id,
            base_ref=candidate.base_ref,
            candidate_ref=candidate.candidate_ref,
            evidence_ids=tuple(item.evidence_id for item in candidate.evidence),
            suite_id=suite_id,
            manifest_digest=manifest_digest,
            created_at=created_at,
            pairs=pairs,
        )

    @staticmethod
    def _evaluate(
        case: HeldOutCase,
        ref: SkillVersionRef,
        evaluator: TrialEvaluator,
    ) -> CandidateTrialResult:
        try:
            measurement = evaluator(case, ref)
        except EvaluationInfrastructureFailure as error:
            measurement = TrialMeasurement(
                infrastructure_error_code=error.code
            )
        return CandidateTrialResult(
            case_id=case.case_id,
            kind=case.kind,
            skill_ref=ref,
            measurement=measurement,
        )
