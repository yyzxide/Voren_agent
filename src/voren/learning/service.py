"""Stage bounded, evidence-eligible Skill edits without activating them."""

from __future__ import annotations

from datetime import UTC, datetime

from voren.learning.evaluation import CandidateEvaluationArtifact
from voren.learning.models import CandidateStatus, EvidenceRef, SkillCandidate
from voren.learning.policy import (
    CandidateAdmissionError,
    CandidateAdmissionPolicy,
    CandidateEvaluationError,
    CandidateEvaluationPolicy,
)
from voren.learning.store import SQLiteCandidateStore
from voren.skills.models import SkillPackage, SkillVersionRef
from voren.skills.store import SQLiteSkillStore


class SkillCandidateService:
    def __init__(
        self,
        *,
        skills: SQLiteSkillStore,
        candidates: SQLiteCandidateStore,
        policy: CandidateAdmissionPolicy | None = None,
        evaluation_policy: CandidateEvaluationPolicy | None = None,
    ) -> None:
        self._skills = skills
        self._candidates = candidates
        self._policy = policy or CandidateAdmissionPolicy()
        self._evaluation_policy = evaluation_policy or CandidateEvaluationPolicy()

    def stage(
        self,
        *,
        candidate_id: str,
        base_ref: SkillVersionRef,
        package: SkillPackage,
        evidence: tuple[EvidenceRef, ...],
        created_at: datetime | None = None,
    ) -> SkillCandidate:
        active = self._skills.freeze_active((base_ref.name,))[0]
        if active != base_ref:
            raise CandidateAdmissionError(
                "candidate base is stale and no longer the active Skill version"
            )
        self._policy.admit_evidence(evidence)
        base = self._skills.load_package(base_ref)
        diff = self._policy.compare_packages(base, package)
        candidate_version = self._skills.install(package, created_at=created_at)
        timestamp = created_at or datetime.now(UTC)
        record = SkillCandidate(
            candidate_id=candidate_id,
            base_ref=base_ref,
            candidate_ref=candidate_version.ref,
            evidence=evidence,
            diff=diff,
            created_at=timestamp,
            updated_at=timestamp,
        )
        return self._candidates.stage(record)

    def decide(
        self,
        *,
        candidate_id: str,
        artifact: CandidateEvaluationArtifact,
        decided_at: datetime | None = None,
    ) -> SkillCandidate:
        candidate = self._candidates.get(candidate_id)
        if candidate.status in {
            CandidateStatus.ACCEPTED,
            CandidateStatus.REJECTED,
        }:
            artifact.assert_integrity()
            stored = self._candidates.get_evaluation(artifact.evaluation_id)
            if (
                stored == artifact
                and candidate.evaluation_artifact_digest
                == artifact.artifact_digest
            ):
                return candidate
            raise CandidateEvaluationError(
                "candidate is already bound to another evaluation decision"
            )
        contract = self._skills.get_version(candidate.base_ref).contract
        decision = self._evaluation_policy.decide(
            candidate=candidate,
            artifact=artifact,
            allowed_suites=contract.evaluation_suites,
        )
        return self._candidates.record_decision(
            candidate_id=candidate_id,
            artifact=artifact,
            decision=decision,
            decided_at=decided_at or datetime.now(UTC),
        )

    def promote(
        self,
        *,
        candidate_id: str,
        reason: str,
        promoted_at: datetime | None = None,
    ) -> SkillCandidate:
        return self._candidates.promote(
            candidate_id=candidate_id,
            reason=reason,
            promoted_at=promoted_at or datetime.now(UTC),
        )

    def rollback(
        self,
        *,
        candidate_id: str,
        reason: str,
        rolled_back_at: datetime | None = None,
    ) -> SkillCandidate:
        return self._candidates.rollback(
            candidate_id=candidate_id,
            reason=reason,
            rolled_back_at=rolled_back_at or datetime.now(UTC),
        )
