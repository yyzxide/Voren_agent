from __future__ import annotations

import base64
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from tests.test_google_workspace_connector import FakeGoogleTransport
from voren.actions.errors import StaleApprovalError
from voren.actions.ledger import SQLiteOperationLedger
from voren.actions.models import ApprovalDecision
from voren.adapters.google_workspace import GoogleWorkspaceConfig, GoogleWorkspaceConnector
from voren.cli import build_parser, run_google
from voren.learning.evidence import DurableLearningRouter
from voren.learning.store import SQLiteCandidateStore
from voren.memory.service import MemoryService
from voren.memory.store import SQLiteMemoryStore
from voren.runs.manager import RunManager
from voren.runtime.models import MessageRole, ModelResponse, ToolCall
from voren.skills.parser import AgentSkillParser
from voren.skills.store import SQLiteSkillStore
from voren.testing.scripted_model import ScriptedModelAdapter


class GoogleCLIResumeTest(unittest.TestCase):
    TRANSCRIPT_KEY = base64.urlsafe_b64encode(b"r" * 32).decode("ascii")

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database = self.root / "runs.sqlite3"
        self.transport = FakeGoogleTransport()

    def connector(self, *, account_email="sid@example.com"):
        return GoogleWorkspaceConnector(
            GoogleWorkspaceConfig(access_token="test-token", account_email=account_email),
            transport=self.transport,
        )

    def args(self, *, resume=None, extra=()):
        request = ["--resume", resume] if resume else ["--request", "Create two drafts.", "--multi-action"]
        return build_parser().parse_args(
            ["google", *request, "--model", "scripted-model", "--database", str(self.database), *extra]
        )

    @staticmethod
    def draft(number: int) -> ModelResponse:
        return ModelResponse(tool_calls=(ToolCall(
            call_id=f"draft-{number}",
            name="create_email_draft",
            arguments={
                "recipients": ["alice@example.com"],
                "subject": f"Update {number}",
                "body": f"Message {number}.",
            },
        ),))

    def run_cli(self, args, responses=(), *, reader=lambda _: "yes", output=None):
        model = ScriptedModelAdapter(tuple(responses))
        lines = [] if output is None else output
        result = run_google(
            args,
            model=model,
            connector=self.connector(),
            transcript_key=self.TRANSCRIPT_KEY,
            approval_reader=reader,
            output=lines.append,
        )
        return result, model, lines

    def stored_run(self):
        with sqlite3.connect(self.database) as connection:
            rows = connection.execute("SELECT run_id, status, config_json FROM runs").fetchall()
        self.assertEqual(len(rows), 1)
        return rows[0]

    def pause_before_approval(self, *, extra=()):
        with self.assertRaises(EOFError):
            self.run_cli(
                self.args(extra=extra),
                (self.draft(1),),
                reader=lambda _: (_ for _ in ()).throw(EOFError("terminal closed")),
            )
        run_id, status, _ = self.stored_run()
        self.assertEqual(status, "waiting_approval")
        self.assertFalse(any(call[0] == "POST" for call in self.transport.calls))
        return run_id

    def test_request_and_resume_are_mutually_exclusive(self) -> None:
        parser = build_parser()
        for request in (["request text"], ["--request", "request text"]):
            with self.subTest(request=request), self.assertRaises(SystemExit):
                parser.parse_args(["google", *request, "--resume", "run-1"])
        with self.assertRaises(SystemExit):
            parser.parse_args(["agentdojo", "request text", "--resume", "run-1"])

    def test_two_actions_require_two_approvals_and_receive_exact_receipts(self) -> None:
        prompts = []
        def approve(prompt):
            prompts.append(prompt)
            return "yes"
        result, model, lines = self.run_cli(
            self.args(), (self.draft(1), self.draft(2), ModelResponse(text="Both drafts are ready.")), reader=approve
        )
        self.assertEqual(result, 0)
        self.assertEqual(len(prompts), 2)
        self.assertEqual(len(self.transport.drafts), 2)
        self.assertIn("Both drafts are ready.", lines)
        _, status, config_json = self.stored_run()
        self.assertEqual(status, "completed")
        self.assertTrue(json.loads(config_json)["continue_after_action"])
        messages = model.requests[-1][0]
        receipts = [message for message in messages if message.role is MessageRole.TOOL]
        self.assertEqual([item.tool_call_id for item in receipts], ["draft-1", "draft-2"])
        for receipt in receipts:
            self.assertIn("verified", json.dumps(receipt.content))
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM operations WHERE status = 'verified'").fetchone()[0], 2)

    def test_rejecting_second_action_preserves_first_verified_action(self) -> None:
        answers = iter(("yes", "no"))
        result, model, lines = self.run_cli(
            self.args(), (self.draft(1), self.draft(2)), reader=lambda _: next(answers)
        )
        self.assertEqual(result, 2)
        self.assertEqual(len(model.requests), 2)
        self.assertEqual(len(self.transport.drafts), 1)
        self.assertEqual(self.stored_run()[1], "cancelled")
        self.assertIn("action rejected; no external commit was attempted for this action", lines)
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM operations WHERE status = 'verified'").fetchone()[0], 1)

    def test_restart_resumes_original_proposal_and_replays_completed_result(self) -> None:
        run_id = self.pause_before_approval()
        result, model, lines = self.run_cli(
            self.args(resume=run_id), (ModelResponse(text="The approved draft is ready."),)
        )
        self.assertEqual(result, 0)
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(len(self.transport.drafts), 1)
        self.assertIn("The approved draft is ready.", lines)
        result, model, lines = self.run_cli(
            self.args(resume=run_id), reader=lambda _: self.fail("completed Run asked for approval")
        )
        self.assertEqual(result, 0)
        self.assertEqual(model.requests, [])
        self.assertIn("The approved draft is ready.", lines)
        self.assertEqual(len(self.transport.drafts), 1)

    def test_resume_rejects_the_legacy_single_action_mode(self) -> None:
        args = self.args()
        args.multi_action = False
        with self.assertRaises(EOFError):
            self.run_cli(
                args, (self.draft(1),),
                reader=lambda _: (_ for _ in ()).throw(EOFError("terminal closed")),
            )
        run_id, status, _ = self.stored_run()
        self.assertEqual(status, "waiting_approval")
        with self.assertRaisesRegex(ValueError, "created with --multi-action"):
            self.run_cli(self.args(resume=run_id))
        self.assertEqual(len(self.transport.drafts), 0)
        self.assertEqual(self.stored_run()[1], "waiting_approval")

    def test_resume_cannot_reset_the_cumulative_model_budget(self) -> None:
        run_id = self.pause_before_approval(extra=("--max-model-steps", "2"))
        result, model, lines = self.run_cli(
            self.args(resume=run_id, extra=("--max-model-steps", "99")),
            (self.draft(2), ModelResponse(text="Should exceed the original budget.")),
        )
        self.assertEqual(result, 1)
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(len(self.transport.drafts), 2)
        self.assertNotIn("Should exceed the original budget.", lines)
        self.assertEqual(json.loads(self.stored_run()[2])["metadata"]["runtime_limits"]["max_model_steps"], 2)

    def test_ambiguous_resume_observes_without_new_approval_or_resend(self) -> None:
        self.transport.fail_draft_post_after_commit = True
        self.transport.draft_search_visible = False
        result, _, _ = self.run_cli(self.args(), (self.draft(1),))
        self.assertEqual(result, 1)
        run_id, status, _ = self.stored_run()
        self.assertEqual(status, "needs_reconciliation")
        no_approval = lambda _: self.fail("reconciliation asked for a new approval")
        result, model, _ = self.run_cli(self.args(resume=run_id), reader=no_approval)
        self.assertEqual(result, 1)
        self.assertEqual(model.requests, [])
        self.transport.draft_search_visible = True
        result, model, lines = self.run_cli(
            self.args(resume=run_id), (ModelResponse(text="The original draft is verified."),), reader=no_approval
        )
        self.assertEqual(result, 0)
        self.assertEqual(len(model.requests), 1)
        self.assertIn("The original draft is verified.", lines)
        self.assertEqual(sum(call[0] == "POST" for call in self.transport.calls), 1)

    def test_saved_approval_recovers_after_receipt_before_run_transition(self) -> None:
        with patch.object(RunManager, "_finalize", side_effect=RuntimeError("process stopped")):
            with self.assertRaisesRegex(RuntimeError, "process stopped"):
                self.run_cli(self.args(), (self.draft(1),))
        run_id, status, _ = self.stored_run()
        self.assertEqual(status, "waiting_approval")
        result, model, _ = self.run_cli(
            self.args(resume=run_id), (ModelResponse(text="Recovered."),),
            reader=lambda _: self.fail("the persisted approval was requested again"),
        )
        self.assertEqual(result, 0)
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(sum(call[0] == "POST" for call in self.transport.calls), 1)

    def test_resume_rejects_a_different_google_account_before_dispatch(self) -> None:
        run_id = self.pause_before_approval()
        with self.assertRaisesRegex(ValueError, "Google account"):
            run_google(
                self.args(resume=run_id), model=ScriptedModelAdapter(()),
                connector=self.connector(account_email="someone-else@example.com"),
                transcript_key=self.TRANSCRIPT_KEY,
                approval_reader=lambda _: self.fail("wrong-account approval"),
                output=lambda _: None,
            )
        self.assertFalse(any(call[0] == "POST" for call in self.transport.calls))

    def test_resume_reuses_frozen_memory_and_skill_after_active_versions_change(self) -> None:
        skill_root = self.root / "skill-store"
        source = self.root / "draft-email"
        source.mkdir()
        (source / "skill.yaml").write_text(
            "schema_version: voren.skill.v1\nscope: [email-drafts]\n"
            "tool_scope: [create_email_draft]\neffect_scope: [gmail.drafts:create]\n"
            "evaluation_suites: [draft-review]\n"
        )

        def set_context(label):
            evidence = SQLiteCandidateStore(self.database)
            memories = SQLiteMemoryStore(self.database)
            skills = SQLiteSkillStore(self.database, root=skill_root)
            try:
                DurableLearningRouter(evidence_store=evidence).record_operator_correction(
                    evidence_id=f"operator:{label}", correction=f"{label}_PROFILE",
                    operator_ref="operator:test",
                )
                MemoryService(memories=memories, evidence_store=evidence).record_profile_preference(
                    memory_id="profile:style", evidence_id=f"operator:{label}", reason="test preference"
                )
                (source / "SKILL.md").write_text(
                    "---\nname: draft-email\ndescription: Create a Gmail draft.\n---\n\n"
                    f"{label}_SKILL\n"
                )
                version = skills.install(AgentSkillParser().load(source))
                skills.activate(version.ref, reason="test explicit activation")
            finally:
                skills.close()
                memories.close()
                evidence.close()

        set_context("ORIGINAL")
        run_id = self.pause_before_approval(extra=(
            "--profile-memory", "profile:style", "--skill", "draft-email",
            "--skill-store", str(skill_root),
        ))
        config_before = self.stored_run()[2]
        set_context("REPLACEMENT")
        result, model, _ = self.run_cli(
            self.args(resume=run_id, extra=(
                "--no-skill", "--profile-memory", "does-not-exist",
                "--skill-store", str(self.root / "wrong-store"),
            )),
            (ModelResponse(text="Draft ready."),),
        )
        self.assertEqual(result, 0)
        system_text = model.requests[0][0][0].content
        self.assertIn("ORIGINAL_PROFILE", system_text)
        self.assertIn("ORIGINAL_SKILL", system_text)
        self.assertNotIn("REPLACEMENT_PROFILE", system_text)
        self.assertNotIn("REPLACEMENT_SKILL", system_text)
        self.assertEqual(self.stored_run()[2], config_before)

    def test_resume_never_renews_an_expired_durable_approval(self) -> None:
        run_id = self.pause_before_approval()
        with sqlite3.connect(self.database) as connection:
            operation_id = connection.execute("SELECT pending_operation_id FROM runs").fetchone()[0]
        ledger = SQLiteOperationLedger(self.database)
        try:
            proposal = ledger.get_proposal(operation_id)
            approval = ApprovalDecision.for_proposal(
                proposal, approval_id="original-expired-approval", decided_by="operator:test",
                decided_at=datetime.now(UTC) - timedelta(hours=1),
            )
            ledger.authorize(operation_id, approval)
        finally:
            ledger.close()
        with self.assertRaises(StaleApprovalError):
            self.run_cli(
                self.args(resume=run_id), reader=lambda _: self.fail("expired approval was replaced")
            )
        reopened = SQLiteOperationLedger(self.database)
        try:
            self.assertEqual(reopened.get_approval(operation_id), approval)
        finally:
            reopened.close()
        self.assertFalse(any(call[0] == "POST" for call in self.transport.calls))

    def test_multi_action_local_key_can_reopen_a_completed_run(self) -> None:
        with patch.dict(os.environ):
            os.environ.pop("VOREN_TRANSCRIPT_KEY", None)
            result = run_google(
                self.args(), model=ScriptedModelAdapter((ModelResponse(text="Nothing to change."),)),
                connector=self.connector(), output=lambda _: None,
            )
            self.assertEqual(result, 0)
            run_id = self.stored_run()[0]
            model = ScriptedModelAdapter(())
            lines = []
            result = run_google(
                self.args(resume=run_id), model=model, connector=self.connector(), output=lines.append,
            )
        self.assertEqual(result, 0)
        self.assertEqual(model.requests, [])
        self.assertIn("Nothing to change.", lines)


if __name__ == "__main__":
    unittest.main()
