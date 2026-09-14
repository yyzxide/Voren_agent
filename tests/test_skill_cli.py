from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from voren.cli import build_parser
from voren.learning.artifacts import write_candidate_evaluation
from voren.learning.evaluation import (
    EvaluationCaseKind,
    HeldOutCase,
    TrialMeasurement,
)
from voren.learning.runner import PairedEvaluationRunner
from voren.learning.store import SQLiteCandidateStore
from voren.skills.store import SQLiteSkillStore


class SkillCLITest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        temporary = Path(self.temporary_directory.name)
        self.database = temporary / "state" / "voren.sqlite3"
        self.objects = temporary / "objects"
        self.base_path = temporary / "base" / "schedule-from-email"
        self.candidate_path = temporary / "candidate" / "schedule-from-email"
        self.correction_path = temporary / "operator-correction.txt"
        self.artifact_path = temporary / "candidate-evaluation.json"
        self.report_path = temporary / "candidate-report.md"
        self.parser = build_parser()
        self._write_skill(
            self.base_path,
            "Check the email, then check the calendar.",
        )
        self._write_skill(
            self.candidate_path,
            "Check email and calendar; ask when duration is missing.",
        )
        self.correction_path.write_text(
            "Ask the operator when a required meeting duration is missing.\n",
            encoding="utf-8",
        )

    @staticmethod
    def _write_skill(path: Path, instruction: str) -> None:
        path.mkdir(parents=True, exist_ok=True)
        (path / "SKILL.md").write_text(
            "---\n"
            "name: schedule-from-email\n"
            "description: Schedule meetings from email when asked.\n"
            "---\n\n"
            "# Schedule\n\n"
            f"{instruction}\n",
            encoding="utf-8",
        )
        (path / "skill.yaml").write_text(
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

    def _run(self, arguments: list[str]) -> tuple[int, list[str]]:
        args = self.parser.parse_args(arguments)
        output: list[str] = []
        return args.handler(args, output=output.append), output

    def _storage_args(self) -> list[str]:
        return [
            "--database",
            str(self.database),
            "--skill-store",
            str(self.objects),
        ]

    def test_public_cli_runs_explicit_candidate_lifecycle(self) -> None:
        exit_code, installed = self._run(
            [
                "skill",
                "install",
                str(self.base_path),
                "--activate",
                "--reason",
                "reviewed baseline",
                *self._storage_args(),
            ]
        )
        self.assertEqual(exit_code, 0)
        self.assertTrue(any(line.startswith("active:") for line in installed))

        exit_code, evidence_output = self._run(
            [
                "skill",
                "evidence-correction",
                str(self.correction_path),
                "--evidence-id",
                "operator:cli-correction",
                "--operator",
                "operator:local-test",
                *self._storage_args(),
            ]
        )
        self.assertEqual(exit_code, 0)
        self.assertIn("source: operator_correction", evidence_output)

        exit_code, staged = self._run(
            [
                "skill",
                "stage",
                str(self.candidate_path),
                "--candidate-id",
                "candidate-cli-1",
                "--base",
                "schedule-from-email",
                "--evidence-id",
                "operator:cli-correction",
                *self._storage_args(),
            ]
        )
        self.assertEqual(exit_code, 0)
        self.assertIn("status: staged", staged)

        skills = SQLiteSkillStore(self.database, root=self.objects)
        candidates = SQLiteCandidateStore(self.database)
        try:
            candidate = candidates.get("candidate-cli-1")
        finally:
            candidates.close()
            skills.close()
        cases = (
            HeldOutCase(case_id="benign-1", kind=EvaluationCaseKind.BENIGN),
            HeldOutCase(case_id="attack-1", kind=EvaluationCaseKind.ATTACK),
        )
        outcomes = {
            ("benign-1", candidate.base_ref): (False, None),
            ("benign-1", candidate.candidate_ref): (True, None),
            ("attack-1", candidate.base_ref): (True, True),
            ("attack-1", candidate.candidate_ref): (True, False),
        }

        def evaluator(case, ref):
            utility, attack = outcomes[(case.case_id, ref)]
            run_id = f"run:{case.case_id}:{ref.version_id[:8]}"
            return TrialMeasurement(
                run_id=run_id,
                utility_passed=utility,
                attack_success=attack,
                run_artifact_digest=hashlib.sha256(run_id.encode()).hexdigest(),
            )

        artifact = PairedEvaluationRunner().run(
            evaluation_id="evaluation-cli-1",
            candidate=candidate,
            suite_id="scheduling-held-out-v1",
            manifest_digest=hashlib.sha256(b"manifest").hexdigest(),
            cases=cases,
            evaluator=evaluator,
            created_at=datetime(2026, 9, 14, tzinfo=UTC),
        )
        write_candidate_evaluation(self.artifact_path, artifact)

        exit_code, decision = self._run(
            [
                "skill",
                "decide",
                "--candidate-id",
                candidate.candidate_id,
                "--artifact",
                str(self.artifact_path),
                *self._storage_args(),
            ]
        )
        self.assertEqual(exit_code, 0)
        self.assertIn("decision: accepted", decision)
        check = SQLiteSkillStore(self.database, root=self.objects)
        try:
            self.assertEqual(check.freeze_active(), (candidate.base_ref,))
        finally:
            check.close()

        exit_code, promoted = self._run(
            [
                "skill",
                "promote",
                "--candidate-id",
                candidate.candidate_id,
                "--reason",
                "reviewed candidate evaluation",
                *self._storage_args(),
            ]
        )
        self.assertEqual(exit_code, 0)
        self.assertIn("status: promoted", promoted)

        exit_code, inspected = self._run(
            [
                "skill",
                "inspect",
                "--candidate-id",
                candidate.candidate_id,
                *self._storage_args(),
            ]
        )
        self.assertEqual(exit_code, 0)
        report = json.loads(inspected[0])
        self.assertEqual(report["candidate"]["status"], "promoted")
        self.assertEqual(len(report["evidence"]), 1)
        self.assertEqual(report["evidence"][0]["payload_media_type"], "text/plain")
        self.assertNotIn("payload", report["evidence"][0])
        self.assertGreater(report["evidence"][0]["payload_bytes"], 0)
        self.assertEqual(len(report["evaluations"]), 1)
        self.assertEqual(
            [event["event_type"] for event in report["events"]],
            ["staged", "decided", "promoted"],
        )

        exit_code, report_output = self._run(
            [
                "skill",
                "report",
                "--candidate-id",
                candidate.candidate_id,
                "--output",
                str(self.report_path),
                *self._storage_args(),
            ]
        )
        self.assertEqual(exit_code, 0)
        self.assertTrue(any(line.startswith("report digest:") for line in report_output))
        markdown = self.report_path.read_text(encoding="utf-8")
        self.assertIn("## Paired held-out results", markdown)
        self.assertIn("utility:benign-1", markdown)
        self.assertIn("ask when duration is missing", markdown)
        self.assertNotIn(
            "Ask the operator when a required meeting duration is missing.",
            markdown,
        )
        self.assertIn("scripted or deterministic trials", markdown)

        exit_code, rolled_back = self._run(
            [
                "skill",
                "rollback",
                "--candidate-id",
                candidate.candidate_id,
                "--reason",
                "exercise rollback path",
                *self._storage_args(),
            ]
        )
        self.assertEqual(exit_code, 0)
        self.assertIn("status: rolled_back", rolled_back)
        check = SQLiteSkillStore(self.database, root=self.objects)
        try:
            self.assertEqual(check.freeze_active(), (candidate.base_ref,))
        finally:
            check.close()


if __name__ == "__main__":
    unittest.main()
