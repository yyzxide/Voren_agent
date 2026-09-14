from __future__ import annotations

import base64
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests.test_google_workspace_connector import FakeGoogleTransport
from voren.adapters.google_workspace import (
    GoogleWorkspaceConfig,
    GoogleWorkspaceConnector,
)
from voren.cli import build_parser, run_google
from voren.runtime.models import ModelResponse, ToolCall
from voren.testing.scripted_model import ScriptedModelAdapter


class GoogleWorkspaceCLITest(unittest.TestCase):
    TRANSCRIPT_KEY = base64.urlsafe_b64encode(b"g" * 32).decode("ascii")

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database = Path(self.temporary_directory.name) / "google.sqlite3"
        self.transport = FakeGoogleTransport()
        self.connector = GoogleWorkspaceConnector(
            GoogleWorkspaceConfig(
                access_token="test-token",
                account_email="sid@example.com",
                time_zone="Asia/Shanghai",
            ),
            transport=self.transport,
        )

    def args(self):
        return build_parser().parse_args(
            [
                "google",
                "Prepare an email draft.",
                "--model",
                "scripted-model",
                "--database",
                str(self.database),
            ]
        )

    def test_parser_exposes_no_token_or_automatic_approval_argument(self) -> None:
        args = self.args()

        self.assertFalse(hasattr(args, "access_token"))
        self.assertFalse(hasattr(args, "auto_approve"))
        self.assertFalse(hasattr(args, "yes"))

    def test_google_draft_runs_through_loop_approval_and_receipt(self) -> None:
        model = ScriptedModelAdapter(
            (
                ModelResponse(
                    tool_calls=(
                        ToolCall(
                            call_id="draft-action",
                            name="create_email_draft",
                            arguments={
                                "recipients": ["alice@example.com"],
                                "subject": "Project update",
                                "body": "The review is ready.",
                            },
                        ),
                    )
                ),
            )
        )
        output: list[str] = []

        exit_code = run_google(
            self.args(),
            model=model,
            connector=self.connector,
            transcript_key=self.TRANSCRIPT_KEY,
            approval_reader=lambda _: "yes",
            output=output.append,
        )

        self.assertEqual(exit_code, 0)
        self.assertIn("receipt: verified", output)
        self.assertTrue(any(line.startswith("run id: ") for line in output))
        posts = [call for call in self.transport.calls if call[0] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertTrue(posts[0][1].endswith("/drafts"))
        with sqlite3.connect(self.database) as connection:
            config_json, status = connection.execute(
                "SELECT config_json, status FROM runs"
            ).fetchone()
        self.assertIn('"world_adapter":"google_workspace_rest_v1"', config_json)
        self.assertIn('"google_connector_boundary":"injected_connector"', config_json)
        self.assertIn('"model_adapter_boundary":"injected_model"', config_json)
        self.assertEqual(status, "completed")

    def test_rejected_google_action_never_dispatches_http_write(self) -> None:
        model = ScriptedModelAdapter(
            (
                ModelResponse(
                    tool_calls=(
                        ToolCall(
                            call_id="calendar-action",
                            name="create_private_calendar_event",
                            arguments={
                                "title": "Private hold",
                                "start_time": "2026-09-15T09:00:00+08:00",
                                "end_time": "2026-09-15T10:00:00+08:00",
                            },
                        ),
                    )
                ),
            )
        )

        exit_code = run_google(
            self.args(),
            model=model,
            connector=self.connector,
            transcript_key=self.TRANSCRIPT_KEY,
            approval_reader=lambda _: "no",
            output=lambda _: None,
        )

        self.assertEqual(exit_code, 2)
        self.assertFalse(any(call[0] == "POST" for call in self.transport.calls))


if __name__ == "__main__":
    unittest.main()
