from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from voren.learning.ablation import LearningAblationArtifact


class LearningAblationTest(unittest.TestCase):
    def test_demo_compares_direct_and_gated_policies_without_credentials(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(repository / "scripts" / "demo_learning_ablation.py"),
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
            self.assertFalse(summary["live_model_evidence"])
            self.assertEqual(summary["direct_reflection_activation_count"], 2)
            self.assertEqual(summary["gated_promotion_eligible_count"], 1)
            self.assertEqual(summary["unsafe_activations_avoided"], 1)
            self.assertEqual(summary["beneficial_candidates_retained"], 1)
            self.assertEqual(summary["unsafe_candidate_status"], "rejected")
            self.assertIn("security regression", summary["unsafe_candidate_reason"])

            artifact_path = Path(summary["artifact"])
            artifact = LearningAblationArtifact.model_validate_json(
                artifact_path.read_text(encoding="utf-8")
            )
            artifact.assert_integrity()
            report = Path(summary["report"]).read_text(encoding="utf-8")
            self.assertIn("Direct-reflection candidates activated: 2", report)
            self.assertIn("Unsafe activations avoided by the gate: 1", report)
            self.assertIn("counterfactual policy baseline", report)

    def test_tampered_ablation_artifact_fails_integrity_check(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(repository / "scripts" / "demo_learning_ablation.py"),
                    "--output-root",
                    temporary,
                ],
                cwd=repository,
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            summary = json.loads(completed.stdout)
            payload = json.loads(Path(summary["artifact"]).read_text(encoding="utf-8"))
            payload["unsafe_activations_avoided"] = 0
            with self.assertRaisesRegex(ValueError, "summary"):
                LearningAblationArtifact.model_validate(payload)
