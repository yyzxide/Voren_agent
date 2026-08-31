from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from voren.cli import build_parser, run_agentdojo, run_agentdojo_evaluation
from voren.evaluation.artifacts import read_artifact
from voren.evaluation.models import EvaluationMode
from voren.runtime.models import ModelResponse, ToolCall
from voren.testing.scripted_model import ScriptedModelAdapter


AGENTDOJO_AVAILABLE = importlib.util.find_spec("agentdojo") is not None


@unittest.skipUnless(AGENTDOJO_AVAILABLE, "AgentDojo optional dependency is not installed")
class AgentDojoCLITest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database = Path(self.temporary_directory.name) / "nested" / "voren.sqlite3"

    def args(self) -> Namespace:
        return Namespace(
            request="Create the hiking event with Mark.",
            model="test-model",
            base_url=None,
            database=self.database,
            timeout_seconds=10.0,
            max_output_tokens=1_000,
            max_model_steps=4,
            max_tool_calls=4,
        )

    @staticmethod
    def action_response() -> ModelResponse:
        return ModelResponse(
            tool_calls=(
                ToolCall(
                    call_id="cli-action-1",
                    name="create_calendar_event",
                    arguments={
                        "title": "Hiking Trip",
                        "start_time": "2024-05-18 08:00",
                        "end_time": "2024-05-18 13:00",
                        "participants": ["mark.davies@hotmail.com"],
                        "location": "island trailhead",
                    },
                ),
            )
        )

    def test_cli_has_no_automatic_approval_option(self) -> None:
        parsed = build_parser().parse_args(
            ["agentdojo", "Read my email.", "--model", "test-model"]
        )

        self.assertFalse(hasattr(parsed, "yes"))
        self.assertFalse(hasattr(parsed, "auto_approve"))

    def test_operator_approval_commits_and_verifies(self) -> None:
        output: list[str] = []
        model = ScriptedModelAdapter((self.action_response(),))

        exit_code = run_agentdojo(
            self.args(),
            model=model,
            approval_reader=lambda _: "yes",
            output=output.append,
        )

        self.assertEqual(exit_code, 0)
        self.assertTrue(any(line == "receipt: verified" for line in output))
        self.assertTrue(any("Hiking Trip" in line for line in output))
        with sqlite3.connect(self.database) as connection:
            run_status = connection.execute("SELECT status FROM runs").fetchone()[0]
            operation = connection.execute(
                "SELECT status, receipt_json FROM operations"
            ).fetchone()
        self.assertEqual(run_status, "completed")
        self.assertEqual(operation[0], "verified")
        self.assertIsNotNone(operation[1])

    def test_operator_rejection_cancels_without_commit_receipt(self) -> None:
        output: list[str] = []
        model = ScriptedModelAdapter((self.action_response(),))

        exit_code = run_agentdojo(
            self.args(),
            model=model,
            approval_reader=lambda _: "no",
            output=output.append,
        )

        self.assertEqual(exit_code, 2)
        self.assertTrue(any("no external commit" in line for line in output))
        with sqlite3.connect(self.database) as connection:
            run_status = connection.execute("SELECT status FROM runs").fetchone()[0]
            operation = connection.execute(
                "SELECT status, receipt_json FROM operations"
            ).fetchone()
        self.assertEqual(run_status, "cancelled")
        self.assertEqual(operation[0], "prepared")
        self.assertIsNone(operation[1])

    def test_evaluation_cli_writes_integrity_checked_artifact(self) -> None:
        artifact_path = (
            Path(self.temporary_directory.name) / "artifacts" / "eval.json"
        )
        args = build_parser().parse_args(
            [
                "eval-agentdojo",
                "--case",
                "benign_user_18",
                "--mode",
                "runtime_enforcement",
                "--model",
                "scripted-model",
                "--output",
                str(artifact_path),
                "--database",
                str(self.database),
                "--experiment-id",
                "cli-evaluation",
            ]
        )
        output: list[str] = []

        exit_code = run_agentdojo_evaluation(
            args,
            model_factory=lambda _case, _mode: ScriptedModelAdapter(
                (self.action_response(),)
            ),
            output=output.append,
        )

        self.assertEqual(exit_code, 0)
        artifact = read_artifact(artifact_path)
        self.assertEqual(len(artifact.trials), 1)
        self.assertEqual(
            artifact.trials[0].mode, EvaluationMode.RUNTIME_ENFORCEMENT
        )
        self.assertTrue(artifact.trials[0].utility_passed)
        self.assertTrue(artifact.trials[0].receipt_verified)
        self.assertTrue(any(line.startswith("artifact digest:") for line in output))


if __name__ == "__main__":
    unittest.main()
