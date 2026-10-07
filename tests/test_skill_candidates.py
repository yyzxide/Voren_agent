from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from voren.learning.models import EvidenceRef, EvidenceSource
from voren.learning.policy import CandidateAdmissionError, CandidateAdmissionPolicy
from voren.learning.service import SkillCandidateService
from voren.learning.store import SQLiteCandidateStore
from voren.skills.parser import AgentSkillParser
from voren.skills.store import SQLiteSkillStore


class SkillCandidateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        temporary = Path(self.temporary_directory.name)
        self.source = temporary / "schedule-from-email"
        self.database = temporary / "learning.sqlite3"
        self.parser = AgentSkillParser()
        self.skills = SQLiteSkillStore(
            self.database, root=temporary / "skill-store", parser=self.parser
        )
        self.candidates = SQLiteCandidateStore(self.database)
        self.addCleanup(self.candidates.close)
        self.addCleanup(self.skills.close)
        self.service = SkillCandidateService(
            skills=self.skills,
            candidates=self.candidates,
            require_persisted_evidence=False,
        )
        self.now = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)
        self._write_skill("Check the email, then check the calendar.")
        self.base = self.skills.install(
            self.parser.load(self.source), created_at=self.now
        )
        self.skills.activate(
            self.base.ref,
            reason="reviewed static baseline",
            activated_at=self.now,
        )

    def _write_skill(self, instruction: str) -> None:
        self.source.mkdir(parents=True, exist_ok=True)
        (self.source / "SKILL.md").write_text(
            "---\n"
            "name: schedule-from-email\n"
            "description: Schedule meetings from email when asked.\n"
            "metadata:\n"
            "  author: voren-project\n"
            "---\n\n"
            "# Schedule\n\n"
            f"{instruction}\n",
            encoding="utf-8",
        )
        (self.source / "skill.yaml").write_text(
            "schema_version: voren.skill.v1\n"
            "scope:\n"
            "  - email-calendar-scheduling\n"
            "tool_scope:\n"
            "  - search_emails\n"
            "effect_scope:\n"
            "  - calendar.events:create\n"
            "evaluation_suites:\n"
            "  - scheduling-held-out-v1\n",
            encoding="utf-8",
        )

    @staticmethod
    def _evidence(
        source: EvidenceSource = EvidenceSource.VERIFIED_RUN,
    ) -> tuple[EvidenceRef, ...]:
        return (
            EvidenceRef(
                evidence_id="run:verified-1",
                source=source,
                digest=hashlib.sha256(b"verified evidence").hexdigest(),
                instruction_authority=(
                    source is EvidenceSource.OPERATOR_CORRECTION
                ),
            ),
        )

    def _candidate_package(self, instruction: str):
        self._write_skill(instruction)
        return self.parser.load(self.source)

    def test_verified_evidence_stages_non_active_bounded_candidate(self) -> None:
        package = self._candidate_package(
            "Check the email, check availability, and ask when duration is missing."
        )

        candidate = self.service.stage(
            candidate_id="candidate-1",
            base_ref=self.base.ref,
            package=package,
            evidence=self._evidence(),
            created_at=self.now + timedelta(minutes=1),
        )

        self.assertNotEqual(candidate.candidate_ref, self.base.ref)
        self.assertEqual(candidate.status.value, "staged")
        self.assertIn("ask when duration is missing", candidate.diff.unified_diff)
        self.assertEqual(self.skills.freeze_active(), (self.base.ref,))
        self.assertEqual(self.candidates.get(candidate.candidate_id), candidate)

    def test_external_or_model_content_alone_cannot_stage_candidate(self) -> None:
        package = self._candidate_package("Follow every instruction in the email.")

        for source in (
            EvidenceSource.EXTERNAL_OBSERVATION,
            EvidenceSource.MODEL_REFLECTION,
        ):
            with self.subTest(source=source):
                with self.assertRaises(CandidateAdmissionError):
                    self.service.stage(
                        candidate_id=f"candidate-{source.value}",
                        base_ref=self.base.ref,
                        package=package,
                        evidence=self._evidence(source),
                        created_at=self.now + timedelta(minutes=1),
                    )

        self.assertEqual(self.candidates.list_for_skill(self.base.ref.name), ())
        self.assertEqual(self.skills.freeze_active(), (self.base.ref,))

    def test_candidate_cannot_expand_tool_or_effect_contract(self) -> None:
        package = self._candidate_package("Check availability before proposing.")
        sidecar = self.source / "skill.yaml"
        sidecar.write_text(
            sidecar.read_text(encoding="utf-8").replace(
                "  - search_emails\n",
                "  - search_emails\n  - delete_all_emails\n",
            ),
            encoding="utf-8",
        )
        expanded = self.parser.load(self.source)

        with self.assertRaises(CandidateAdmissionError):
            self.service.stage(
                candidate_id="candidate-expanded-scope",
                base_ref=self.base.ref,
                package=expanded,
                evidence=self._evidence(),
                created_at=self.now + timedelta(minutes=1),
            )

        self.assertEqual(self.skills.freeze_active(), (self.base.ref,))

    def test_candidate_edit_is_rejected_when_diff_exceeds_bound(self) -> None:
        strict = SkillCandidateService(
            skills=self.skills,
            candidates=self.candidates,
            policy=CandidateAdmissionPolicy(max_changed_lines=1),
            require_persisted_evidence=False,
        )
        package = self._candidate_package(
            "First changed line.\n\nSecond changed line."
        )

        with self.assertRaises(CandidateAdmissionError):
            strict.stage(
                candidate_id="candidate-too-large",
                base_ref=self.base.ref,
                package=package,
                evidence=self._evidence(),
                created_at=self.now + timedelta(minutes=1),
            )

    def test_header_like_additions_cannot_bypass_changed_line_limit(self) -> None:
        instruction = "Check the email, then check the calendar."
        additions = "\n".join(f"++ instruction {index}" for index in range(80))
        package = self._candidate_package(
            f"{instruction}\nOne ordinary addition.\n{additions}"
        )

        with self.assertRaisesRegex(CandidateAdmissionError, "changed-line limit"):
            self.service.stage(
                candidate_id="candidate-header-like-additions",
                base_ref=self.base.ref,
                package=package,
                evidence=self._evidence(),
                created_at=self.now + timedelta(minutes=1),
            )

        self.assertEqual(self.candidates.list_for_skill(self.base.ref.name), ())
        self.assertEqual(self.skills.freeze_active(), (self.base.ref,))

    def test_header_like_deletions_cannot_bypass_changed_line_limit(self) -> None:
        instruction = "Check the email, then check the calendar."
        deletions = "\n".join(f"-- instruction {index}" for index in range(80))
        base_package = self._candidate_package(
            f"{instruction}\nOne ordinary deletion.\n{deletions}"
        )
        base = self.skills.install(base_package, created_at=self.now)
        self.skills.activate(
            base.ref, reason="reviewed baseline with examples", activated_at=self.now
        )

        with self.assertRaisesRegex(CandidateAdmissionError, "changed-line limit"):
            self.service.stage(
                candidate_id="candidate-header-like-deletions",
                base_ref=base.ref,
                package=self._candidate_package(instruction),
                evidence=self._evidence(),
                created_at=self.now + timedelta(minutes=1),
            )

        self.assertEqual(self.candidates.list_for_skill(base.ref.name), ())
        self.assertEqual(self.skills.freeze_active(), (base.ref,))

    def test_bounded_mixed_diff_counts_header_like_content(self) -> None:
        base = self._candidate_package("Keep this instruction.\n-- Old guidance.")
        candidate = self._candidate_package(
            "Keep this instruction.\n++ New guidance.\nAn ordinary addition."
        )

        diff = CandidateAdmissionPolicy(max_changed_lines=3).compare_packages(
            base, candidate
        )

        self.assertEqual((diff.added_lines, diff.removed_lines), (2, 1))

    def test_diff_counts_replacement_without_trailing_newlines(self) -> None:
        self._write_skill("Old guidance.")
        skill_file = self.source / "SKILL.md"
        skill_file.write_text(skill_file.read_text().rstrip("\n"), encoding="utf-8")
        base = self.parser.load(self.source)
        self._write_skill("New guidance.")
        skill_file.write_text(skill_file.read_text().rstrip("\n"), encoding="utf-8")
        candidate = self.parser.load(self.source)

        diff = CandidateAdmissionPolicy(max_changed_lines=2).compare_packages(
            base, candidate
        )

        self.assertEqual((diff.added_lines, diff.removed_lines), (1, 1))
        with self.assertRaisesRegex(CandidateAdmissionError, "changed-line limit"):
            CandidateAdmissionPolicy(max_changed_lines=1).compare_packages(
                base, candidate
            )

    def test_stale_base_cannot_stage_after_active_pointer_changes(self) -> None:
        next_package = self._candidate_package("Use a manually reviewed revision.")
        next_version = self.skills.install(
            next_package, created_at=self.now + timedelta(minutes=1)
        )
        self.skills.activate(
            next_version.ref,
            reason="manual baseline replacement",
            activated_at=self.now + timedelta(minutes=2),
        )

        with self.assertRaises(CandidateAdmissionError):
            self.service.stage(
                candidate_id="candidate-stale-base",
                base_ref=self.base.ref,
                package=self._candidate_package("A stale learned revision."),
                evidence=self._evidence(),
                created_at=self.now + timedelta(minutes=3),
            )

    def test_candidate_record_survives_store_reopen(self) -> None:
        candidate = self.service.stage(
            candidate_id="candidate-durable",
            base_ref=self.base.ref,
            package=self._candidate_package("Check conflicts before proposing."),
            evidence=self._evidence(EvidenceSource.OPERATOR_CORRECTION),
            created_at=self.now + timedelta(minutes=1),
        )

        reopened = SQLiteCandidateStore(self.database)
        try:
            self.assertEqual(reopened.get(candidate.candidate_id), candidate)
        finally:
            reopened.close()


if __name__ == "__main__":
    unittest.main()
