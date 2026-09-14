"""Deterministic admission rules for bounded Skill candidates."""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass

from voren.learning.models import EvidenceRef, EvidenceSource, SkillDiff
from voren.skills.models import SkillPackage


class CandidateAdmissionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CandidateAdmissionPolicy:
    max_changed_lines: int = 80
    max_diff_bytes: int = 16_000

    def __post_init__(self) -> None:
        if self.max_changed_lines < 1 or self.max_diff_bytes < 1:
            raise ValueError("candidate edit bounds must be positive")

    def admit_evidence(self, evidence: tuple[EvidenceRef, ...]) -> None:
        if not evidence:
            raise CandidateAdmissionError("candidate requires durable evidence")
        eligible = {
            EvidenceSource.OPERATOR_CORRECTION,
            EvidenceSource.VERIFIED_RUN,
        }
        if not any(item.source in eligible for item in evidence):
            raise CandidateAdmissionError(
                "model reflection or external content alone cannot create a candidate"
            )

    def compare_packages(
        self, base: SkillPackage, candidate: SkillPackage
    ) -> SkillDiff:
        if base.metadata.name != candidate.metadata.name:
            raise CandidateAdmissionError("candidate cannot change the Skill name")
        if base.metadata != candidate.metadata:
            raise CandidateAdmissionError("candidate cannot change routing metadata")
        if base.contract != candidate.contract:
            raise CandidateAdmissionError(
                "candidate prose cannot change tool, effect, or evaluation scope"
            )

        base_files = {item.relative_path: item for item in base.files}
        candidate_files = {item.relative_path: item for item in candidate.files}
        if base_files.keys() != candidate_files.keys():
            raise CandidateAdmissionError("candidate cannot add or remove package files")
        changed_paths = tuple(
            path
            for path in sorted(base_files)
            if base_files[path].content != candidate_files[path].content
        )
        if changed_paths != ("SKILL.md",):
            raise CandidateAdmissionError(
                "the first learning policy permits instruction-only SKILL.md edits"
            )
        try:
            before = base_files["SKILL.md"].content.decode("utf-8")
            after = candidate_files["SKILL.md"].content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise CandidateAdmissionError("SKILL.md must be valid UTF-8") from error

        diff = "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"{base.metadata.name}@{base.content_digest}/SKILL.md",
                tofile=f"{candidate.metadata.name}@{candidate.content_digest}/SKILL.md",
            )
        )
        added, removed = self._changed_lines(diff)
        if added + removed == 0:
            raise CandidateAdmissionError("candidate does not change Skill instructions")
        if added + removed > self.max_changed_lines:
            raise CandidateAdmissionError("candidate exceeds the changed-line limit")
        if len(diff.encode("utf-8")) > self.max_diff_bytes:
            raise CandidateAdmissionError("candidate exceeds the diff-size limit")
        return SkillDiff(
            base_content_digest=base.content_digest,
            candidate_content_digest=candidate.content_digest,
            unified_diff=diff,
            diff_digest=hashlib.sha256(diff.encode("utf-8")).hexdigest(),
            added_lines=added,
            removed_lines=removed,
        )

    @staticmethod
    def _changed_lines(diff: str) -> tuple[int, int]:
        added = 0
        removed = 0
        for line in diff.splitlines():
            if line.startswith("+++") or line.startswith("---"):
                continue
            if line.startswith("+"):
                added += 1
            elif line.startswith("-"):
                removed += 1
        return added, removed
