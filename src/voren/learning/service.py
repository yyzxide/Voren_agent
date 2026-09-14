"""Stage bounded, evidence-eligible Skill edits without activating them."""

from __future__ import annotations

from datetime import UTC, datetime

from voren.learning.models import EvidenceRef, SkillCandidate
from voren.learning.policy import CandidateAdmissionError, CandidateAdmissionPolicy
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
    ) -> None:
        self._skills = skills
        self._candidates = candidates
        self._policy = policy or CandidateAdmissionPolicy()

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
