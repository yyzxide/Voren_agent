from __future__ import annotations

import importlib.util
import base64
import sqlite3
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from voren.cli import build_parser, run_agentdojo, run_agentdojo_evaluation
from voren.evaluation.artifacts import read_artifact
from voren.evaluation.models import EvaluationMode
from voren.runs.models import RunConfig
from voren.runtime.cancellation import CancellationToken
from voren.runtime.models import ModelResponse, ToolCall
from voren.skills.parser import AgentSkillParser
from voren.skills.store import SQLiteSkillStore
from voren.testing.scripted_model import ScriptedModelAdapter


AGENTDOJO_AVAILABLE = importlib.util.find_spec("agentdojo") is not None


@unittest.skipUnless(AGENTDOJO_AVAILABLE, "AgentDojo optional dependency is not installed")
class AgentDojoCLITest(unittest.TestCase):
    TRANSCRIPT_KEY = base64.urlsafe_b64encode(b"t" * 32).decode("ascii")

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
        self.assertIsNone(parsed.skill)
        self.assertFalse(parsed.no_skill)

        with self.assertRaises(SystemExit):
            build_parser().parse_args(
                [
                    "agentdojo",
                    "Read my email.",
                    "--model",
                    "test-model",
                    "--skill",
                    "schedule-from-email",
                    "--no-skill",
                ]
            )

    def test_operator_approval_commits_and_verifies(self) -> None:
        output: list[str] = []
        model = ScriptedModelAdapter((self.action_response(),))

        exit_code = run_agentdojo(
            self.args(),
            model=model,
            transcript_key=self.TRANSCRIPT_KEY,
            approval_reader=lambda _: "yes",
            output=output.append,
        )

        self.assertEqual(exit_code, 0)
        self.assertTrue(
            any(
                line.startswith("model usage:") and "complete=false" in line
                for line in output
            )
        )
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

    def test_runtime_auto_routes_exact_active_skill_and_records_decision(self) -> None:
        skill_store_root = Path(self.temporary_directory.name) / "skills"
        self.database.parent.mkdir(parents=True, exist_ok=True)
        parser = AgentSkillParser()
        store = SQLiteSkillStore(
            self.database,
            root=skill_store_root,
            parser=parser,
        )
        try:
            source = Path(__file__).parents[2] / "skills" / "schedule-from-email"
            version = store.install(parser.load(source))
            store.activate(version.ref, reason="reviewed runtime route")
        finally:
            store.close()
        args = self.args()
        args.skill_store = skill_store_root
        model = ScriptedModelAdapter((self.action_response(),))
        output: list[str] = []

        exit_code = run_agentdojo(
            args,
            model=model,
            transcript_key=self.TRANSCRIPT_KEY,
            approval_reader=lambda _: "yes",
            output=output.append,
        )

        self.assertEqual(exit_code, 0)
        self.assertIn(
            "# Activated procedural skills",
            model.requests[0][0][0].content,
        )
        self.assertTrue(
            any(
                line.startswith("skill routing: auto; selected=schedule-from-email@")
                for line in output
            )
        )
        with sqlite3.connect(self.database) as connection:
            config_json = connection.execute(
                "SELECT config_json FROM runs"
            ).fetchone()[0]
        config = RunConfig.model_validate_json(config_json)
        self.assertEqual(config.skill_versions, (version.ref,))
        self.assertEqual(config.metadata["skill_routing"]["mode"], "auto")
        self.assertEqual(
            config.metadata["skill_routing"]["selected_versions"],
            [version.ref.model_dump(mode="json")],
        )

    def test_operator_rejection_cancels_without_commit_receipt(self) -> None:
        output: list[str] = []
        model = ScriptedModelAdapter((self.action_response(),))

        exit_code = run_agentdojo(
            self.args(),
            model=model,
            transcript_key=self.TRANSCRIPT_KEY,
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

    def test_pre_requested_runtime_cancellation_returns_130(self) -> None:
        output: list[str] = []
        cancellation = CancellationToken()
        cancellation.cancel()

        exit_code = run_agentdojo(
            self.args(),
            model=ScriptedModelAdapter(()),
            cancellation=cancellation,
            transcript_key=self.TRANSCRIPT_KEY,
            output=output.append,
        )

        self.assertEqual(exit_code, 130)
        self.assertTrue(any("run cancelled: operator" in line for line in output))
        with sqlite3.connect(self.database) as connection:
            run_status = connection.execute("SELECT status FROM runs").fetchone()[0]
        self.assertEqual(run_status, "cancelled")

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
        self.assertFalse(artifact.trials[0].model_usage.complete)
        self.assertTrue(any("usage_reported=0/1" in line for line in output))
        self.assertTrue(any(line.startswith("artifact digest:") for line in output))

    def test_evaluation_cli_freezes_exact_active_skill_context(self) -> None:
        temporary = Path(self.temporary_directory.name)
        artifact_path = temporary / "artifacts" / "static-skill-eval.json"
        skill_store_root = temporary / "skills"
        self.database.parent.mkdir(parents=True, exist_ok=True)
        parser = AgentSkillParser()
        store = SQLiteSkillStore(
            self.database,
            root=skill_store_root,
            parser=parser,
        )
        try:
            source = Path(__file__).parents[2] / "skills" / "schedule-from-email"
            version = store.install(parser.load(source))
            store.activate(version.ref, reason="reviewed test baseline")
        finally:
            store.close()

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
                "--skill-store",
                str(skill_store_root),
                "--skill",
                "schedule-from-email",
                "--experiment-id",
                "cli-static-skill-evaluation",
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
        context = artifact.config.sampling["skill_context"]
        self.assertEqual(context["mode"], "static_skill")
        self.assertEqual(
            context["skill_versions"],
            [version.ref.model_dump(mode="json")],
        )
        self.assertGreater(context["instruction_bytes"], 0)
        self.assertTrue(
            any(line.startswith("skill context: static_skill") for line in output)
        )


if __name__ == "__main__":
    unittest.main()
