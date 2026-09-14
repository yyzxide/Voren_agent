from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class SkillLearningDemoTest(unittest.TestCase):
    def test_demo_runs_complete_lifecycle_without_credentials(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(repository / "scripts" / "demo_skill_learning.py"),
                    "--output-root",
                    temporary,
                ],
                cwd=repository,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            summary = json.loads(completed.stdout)
            self.assertEqual(summary["candidate_status"], "rolled_back")
            self.assertFalse(summary["live_model_evidence"])
            self.assertEqual(len(summary["trial_artifacts"]), 4)
            self.assertTrue(Path(summary["evaluation_artifact"]).is_file())
            report_path = Path(summary["report"])
            self.assertTrue(report_path.is_file())
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("Status: `rolled_back`", report)
            self.assertIn("heldout-conflicting-threads", report)
            self.assertIn("staged -> accepted", report)
            self.assertIn("promoted -> rolled_back", report)
            self.assertIn("scripted or deterministic trials", report)
