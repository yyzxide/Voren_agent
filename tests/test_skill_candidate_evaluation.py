from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from voren.learning.evaluation import (
    EvaluationCaseKind,
    HeldOutCase,
    TrialMeasurement,
)
from voren.learning.models import CandidateStatus, EvidenceRef, EvidenceSource
from voren.learning.policy import (
    CandidateEvaluationError,
    CandidateEvaluationIncomplete,
)
from voren.learning.runner import (
    EvaluationInfrastructureFailure,
    PairedEvaluationRunner,
)
from voren.learning.service import SkillCandidateService
from voren.learning.store import (
    CandidatePromotionError,
    CandidateStoreError,
    SQLiteCandidateStore,
)
from voren.skills.parser import AgentSkillParser
from voren.skills.store import SQLiteSkillStore


class SkillCandidateEvaluationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        temporary = Path(self.temporary_directory.name)
        self.source = temporary / "schedule-from-email"
        self.database = temporary / "evaluation.sqlite3"
        self.parser = AgentSkillParser()
        self.skills = SQLiteSkillStore(
            self.database, root=temporary / "skill-store", parser=self.parser
        )
        self.candidates = SQLiteCandidateStore(self.database)
        self.addCleanup(self.candidates.close)
        self.addCleanup(self.skills.close)
        self.service = SkillCandidateService(
            skills=self.skills, candidates=self.candidates
        )
        self.runner = PairedEvaluationRunner()
        self.now = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
        self._write_skill("Check the email, then check the calendar.")
        self.base = self.skills.install(
            self.parser.load(self.source), created_at=self.now
        )
        self.skills.activate(
            self.base.ref,
            reason="reviewed baseline",
            activated_at=self.now,
        )

    def _write_skill(self, instruction: str) -> None:
        self.source.mkdir(parents=True, exist_ok=True)
        (self.source / "SKILL.md").write_text(
            "---\n"
            "name: schedule-from-email\n"
            "description: Schedule meetings from email when asked.\n"
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

    def _stage(self, *, training_case: str = "training-1"):
        self._write_skill(
            "Check the email and calendar; ask when required details are missing."
        )
        evidence = EvidenceRef(
            evidence_id="run:training-1",
            source=EvidenceSource.VERIFIED_RUN,
            digest=self._digest("training evidence"),
            instruction_authority=False,
            evaluation_case_ids=(training_case,),
        )
        return self.service.stage(
            candidate_id="candidate-eval-1",
            base_ref=self.base.ref,
            package=self.parser.load(self.source),
            evidence=(evidence,),
            created_at=self.now + timedelta(minutes=1),
        )

    def _run(self, candidate, outcomes):
        cases = (
            HeldOutCase(case_id="benign-1", kind=EvaluationCaseKind.BENIGN),
            HeldOutCase(case_id="attack-1", kind=EvaluationCaseKind.ATTACK),
        )

        def evaluate(case, ref):
            outcome = outcomes[(case.case_id, ref)]
            if isinstance(outcome, Exception):
                raise outcome
            utility, attack = outcome
            run_id = f"run:{case.case_id}:{ref.version_id[:8]}"
            return TrialMeasurement(
                run_id=run_id,
                utility_passed=utility,
                attack_success=attack,
                run_artifact_digest=self._digest(run_id),
            )

        return self.runner.run(
            evaluation_id="evaluation-1",
            candidate=candidate,
            suite_id="scheduling-held-out-v1",
            manifest_digest=self._digest("held-out manifest"),
            cases=cases,
            evaluator=evaluate,
            created_at=self.now + timedelta(minutes=2),
        )

    def _improving_outcomes(self, candidate):
        return {
            ("benign-1", candidate.base_ref): (False, None),
            ("benign-1", candidate.candidate_ref): (True, None),
            ("attack-1", candidate.base_ref): (True, True),
            ("attack-1", candidate.candidate_ref): (True, False),
        }

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def test_runner_binds_exact_versions_and_artifact_integrity(self) -> None:
        candidate = self._stage()
        artifact = self._run(candidate, self._improving_outcomes(candidate))

        artifact.assert_integrity()
        self.assertEqual(artifact.base_ref, candidate.base_ref)
        self.assertEqual(artifact.candidate_ref, candidate.candidate_ref)
        self.assertEqual(
            tuple(pair.base.skill_ref for pair in artifact.pairs),
            (candidate.base_ref, candidate.base_ref),
        )
        self.assertEqual(
            tuple(pair.candidate.skill_ref for pair in artifact.pairs),
            (candidate.candidate_ref, candidate.candidate_ref),
        )

    def test_improving_candidate_is_accepted_and_artifact_is_durable(self) -> None:
        candidate = self._stage()
        artifact = self._run(candidate, self._improving_outcomes(candidate))

        decided = self.service.decide(
            candidate_id=candidate.candidate_id,
            artifact=artifact,
            decided_at=self.now + timedelta(minutes=3),
        )

        self.assertIs(decided.status, CandidateStatus.ACCEPTED)
        self.assertEqual(
            decided.evaluation_artifact_digest, artifact.artifact_digest
        )
        self.assertIn("utility:benign-1", decided.decision_reason or "")
        self.assertEqual(
            self.candidates.get_evaluation(artifact.evaluation_id), artifact
        )
        self.assertEqual(self.skills.freeze_active(), (candidate.base_ref,))

        retried = self.service.decide(
            candidate_id=candidate.candidate_id,
            artifact=artifact,
            decided_at=self.now + timedelta(hours=1),
        )
        self.assertEqual(retried, decided)

    def test_security_regression_rejects_candidate(self) -> None:
        candidate = self._stage()
        outcomes = {
            ("benign-1", candidate.base_ref): (True, None),
            ("benign-1", candidate.candidate_ref): (True, None),
            ("attack-1", candidate.base_ref): (True, False),
            ("attack-1", candidate.candidate_ref): (True, True),
        }

        decided = self.service.decide(
            candidate_id=candidate.candidate_id,
            artifact=self._run(candidate, outcomes),
            decided_at=self.now + timedelta(minutes=3),
        )

        self.assertIs(decided.status, CandidateStatus.REJECTED)
        self.assertIn("security regression", decided.decision_reason or "")
        self.assertEqual(self.skills.freeze_active(), (candidate.base_ref,))

    def test_no_measured_improvement_rejects_candidate(self) -> None:
        candidate = self._stage()
        outcomes = {
            ("benign-1", candidate.base_ref): (True, None),
            ("benign-1", candidate.candidate_ref): (True, None),
            ("attack-1", candidate.base_ref): (True, False),
            ("attack-1", candidate.candidate_ref): (True, False),
        }

        decided = self.service.decide(
            candidate_id=candidate.candidate_id,
            artifact=self._run(candidate, outcomes),
            decided_at=self.now + timedelta(minutes=3),
        )

        self.assertIs(decided.status, CandidateStatus.REJECTED)
        self.assertIn("no measured improvement", decided.decision_reason or "")

    def test_infrastructure_failure_blocks_decision_without_model_blame(self) -> None:
        candidate = self._stage()
        outcomes = self._improving_outcomes(candidate)
        outcomes[("attack-1", candidate.candidate_ref)] = (
            EvaluationInfrastructureFailure("provider-timeout")
        )
        artifact = self._run(candidate, outcomes)

        with self.assertRaisesRegex(
            CandidateEvaluationIncomplete, "infrastructure failures"
        ):
            self.service.decide(
                candidate_id=candidate.candidate_id,
                artifact=artifact,
                decided_at=self.now + timedelta(minutes=3),
            )

        self.assertIs(
            self.candidates.get(candidate.candidate_id).status,
            CandidateStatus.STAGED,
        )
        with self.assertRaises(CandidateStoreError):
            self.candidates.get_evaluation(artifact.evaluation_id)

    def test_evidence_case_cannot_be_reused_as_held_out_evaluation(self) -> None:
        candidate = self._stage(training_case="benign-1")
        artifact = self._run(candidate, self._improving_outcomes(candidate))

        with self.assertRaisesRegex(CandidateEvaluationError, "not held out"):
            self.service.decide(
                candidate_id=candidate.candidate_id,
                artifact=artifact,
                decided_at=self.now + timedelta(minutes=3),
            )

    def test_tampered_artifact_cannot_decide_candidate(self) -> None:
        candidate = self._stage()
        artifact = self._run(candidate, self._improving_outcomes(candidate))
        tampered = artifact.model_copy(
            update={"artifact_digest": "0" * 64}
        )

        with self.assertRaisesRegex(CandidateEvaluationError, "digest"):
            self.service.decide(
                candidate_id=candidate.candidate_id,
                artifact=tampered,
                decided_at=self.now + timedelta(minutes=3),
            )

    def test_accepted_candidate_promotes_with_integrity_chained_events(self) -> None:
        candidate = self._stage()
        artifact = self._run(candidate, self._improving_outcomes(candidate))
        self.service.decide(
            candidate_id=candidate.candidate_id,
            artifact=artifact,
            decided_at=self.now + timedelta(minutes=3),
        )

        promoted = self.service.promote(
            candidate_id=candidate.candidate_id,
            reason="operator approved held-out report",
            promoted_at=self.now + timedelta(minutes=4),
        )

        self.assertIs(promoted.status, CandidateStatus.PROMOTED)
        self.assertEqual(self.skills.freeze_active(), (candidate.candidate_ref,))
        events = self.candidates.list_events(candidate.candidate_id)
        self.assertEqual(
            tuple(event.event_type.value for event in events),
            ("staged", "decided", "promoted"),
        )
        self.assertEqual(events[1].event_digest, events[2].previous_event_digest)
        self.assertEqual(events[2].active_from, candidate.base_ref)
        self.assertEqual(events[2].active_to, candidate.candidate_ref)

        retried = self.service.promote(
            candidate_id=candidate.candidate_id,
            reason="same request retried",
            promoted_at=self.now + timedelta(hours=1),
        )
        self.assertEqual(retried, promoted)
        self.assertEqual(len(self.candidates.list_events(candidate.candidate_id)), 3)

    def test_rollback_restores_exact_base_and_is_idempotent(self) -> None:
        candidate = self._stage()
        artifact = self._run(candidate, self._improving_outcomes(candidate))
        self.service.decide(
            candidate_id=candidate.candidate_id,
            artifact=artifact,
            decided_at=self.now + timedelta(minutes=3),
        )
        self.service.promote(
            candidate_id=candidate.candidate_id,
            reason="operator promotion",
            promoted_at=self.now + timedelta(minutes=4),
        )

        rolled_back = self.service.rollback(
            candidate_id=candidate.candidate_id,
            reason="regression observed after promotion",
            rolled_back_at=self.now + timedelta(minutes=5),
        )

        self.assertIs(rolled_back.status, CandidateStatus.ROLLED_BACK)
        self.assertEqual(self.skills.freeze_active(), (candidate.base_ref,))
        events = self.candidates.list_events(candidate.candidate_id)
        self.assertEqual(events[-1].event_type.value, "rolled_back")
        self.assertEqual(events[-1].active_from, candidate.candidate_ref)
        self.assertEqual(events[-1].active_to, candidate.base_ref)

        retried = self.service.rollback(
            candidate_id=candidate.candidate_id,
            reason="duplicate rollback request",
            rolled_back_at=self.now + timedelta(hours=1),
        )
        self.assertEqual(retried, rolled_back)
        self.assertEqual(len(self.candidates.list_events(candidate.candidate_id)), 4)

    def test_rejected_candidate_cannot_promote(self) -> None:
        candidate = self._stage()
        outcomes = {
            ("benign-1", candidate.base_ref): (True, None),
            ("benign-1", candidate.candidate_ref): (False, None),
            ("attack-1", candidate.base_ref): (True, False),
            ("attack-1", candidate.candidate_ref): (True, False),
        }
        self.service.decide(
            candidate_id=candidate.candidate_id,
            artifact=self._run(candidate, outcomes),
            decided_at=self.now + timedelta(minutes=3),
        )

        with self.assertRaises(CandidatePromotionError):
            self.service.promote(
                candidate_id=candidate.candidate_id,
                reason="must not bypass rejection",
                promoted_at=self.now + timedelta(minutes=4),
            )

        self.assertEqual(self.skills.freeze_active(), (candidate.base_ref,))
        self.assertEqual(len(self.candidates.list_events(candidate.candidate_id)), 2)

    def test_stale_active_pointer_aborts_promotion_transaction(self) -> None:
        candidate = self._stage()
        artifact = self._run(candidate, self._improving_outcomes(candidate))
        accepted = self.service.decide(
            candidate_id=candidate.candidate_id,
            artifact=artifact,
            decided_at=self.now + timedelta(minutes=3),
        )
        self._write_skill("Use a separate manually reviewed version.")
        manual = self.skills.install(
            self.parser.load(self.source),
            created_at=self.now + timedelta(minutes=4),
        )
        self.skills.activate(
            manual.ref,
            reason="independent manual release",
            activated_at=self.now + timedelta(minutes=5),
        )

        with self.assertRaisesRegex(CandidatePromotionError, "evaluated base"):
            self.service.promote(
                candidate_id=candidate.candidate_id,
                reason="stale promotion",
                promoted_at=self.now + timedelta(minutes=6),
            )

        self.assertEqual(self.candidates.get(candidate.candidate_id), accepted)
        self.assertEqual(self.skills.freeze_active(), (manual.ref,))
        self.assertEqual(len(self.candidates.list_events(candidate.candidate_id)), 2)

    def test_rollback_does_not_overwrite_a_later_active_version(self) -> None:
        candidate = self._stage()
        artifact = self._run(candidate, self._improving_outcomes(candidate))
        self.service.decide(
            candidate_id=candidate.candidate_id,
            artifact=artifact,
            decided_at=self.now + timedelta(minutes=3),
        )
        promoted = self.service.promote(
            candidate_id=candidate.candidate_id,
            reason="operator promotion",
            promoted_at=self.now + timedelta(minutes=4),
        )
        self._write_skill("Use a newer independently reviewed version.")
        newer = self.skills.install(
            self.parser.load(self.source),
            created_at=self.now + timedelta(minutes=5),
        )
        self.skills.activate(
            newer.ref,
            reason="later independent release",
            activated_at=self.now + timedelta(minutes=6),
        )

        with self.assertRaisesRegex(
            CandidatePromotionError, "promoted candidate"
        ):
            self.service.rollback(
                candidate_id=candidate.candidate_id,
                reason="stale rollback",
                rolled_back_at=self.now + timedelta(minutes=7),
            )

        self.assertEqual(self.candidates.get(candidate.candidate_id), promoted)
        self.assertEqual(self.skills.freeze_active(), (newer.ref,))
        self.assertEqual(len(self.candidates.list_events(candidate.candidate_id)), 3)

    def test_lifecycle_event_content_tampering_is_detected(self) -> None:
        candidate = self._stage()
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                """SELECT event_json FROM skill_candidate_events
                   WHERE candidate_id = ? AND sequence = 1""",
                (candidate.candidate_id,),
            ).fetchone()
            self.assertIsNotNone(row)
            payload = json.loads(row[0])
            payload["reason"] = "rewritten audit history"
            connection.execute(
                """UPDATE skill_candidate_events SET event_json = ?
                   WHERE candidate_id = ? AND sequence = 1""",
                (json.dumps(payload), candidate.candidate_id),
            )

        with self.assertRaisesRegex(CandidateStoreError, "integrity"):
            self.candidates.list_events(candidate.candidate_id)


if __name__ == "__main__":
    unittest.main()
