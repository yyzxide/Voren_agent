from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from voren.actions.gateway import ActionGateway
from voren.actions.ledger import SQLiteOperationLedger
from voren.adapters.fake_workspace import FakeWorkspaceAdapter
from voren.adapters.workspace_contracts import (
    WORKSPACE_CONTRACT_VERSION,
    create_calendar_event_definition,
)
from voren.observations.read_tools import READ_TOOL_DEFINITIONS
from voren.runs.manager import RunManager
from voren.runs.models import RunConfig, RunEventType
from voren.runs.store import SQLiteRunStore
from voren.runtime.agent_loop import AgentLoop, SYSTEM_INSTRUCTION
from voren.runtime.models import MessageRole, ModelResponse, RuntimeResultStatus
from voren.runtime.tools import external_action_tool
from voren.skills.context import (
    SkillContextAssembler,
    SkillContextMode,
    SkillContextSnapshot,
)
from voren.skills.parser import AgentSkillParser
from voren.skills.store import SQLiteSkillStore
from voren.testing.scripted_model import ScriptedModelAdapter


class UnusedReadAdapter:
    definitions = READ_TOOL_DEFINITIONS

    def execute(self, *, tool_call_id: str, tool_name: str, arguments: dict):
        raise AssertionError("read tool should not be called")


class SkillContextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        temporary = Path(self.temporary_directory.name)
        self.database = temporary / "voren.sqlite3"
        self.skill_source = temporary / "sources" / "schedule-from-email"
        self.skill_source.mkdir(parents=True)
        self.skill_store = SQLiteSkillStore(
            self.database,
            root=temporary / "skill-store",
        )
        self.addCleanup(self.skill_store.close)
        self.parser = AgentSkillParser()

    def write_skill(
        self,
        body: str,
        *,
        tool_scope: tuple[str, ...] = (
            "search_emails",
            "get_day_calendar_events",
            "create_calendar_event",
        ),
    ) -> None:
        (self.skill_source / "SKILL.md").write_text(
            "---\n"
            "name: schedule-from-email\n"
            "description: Schedule an event from an operator-selected email.\n"
            "metadata:\n"
            "  stage: static-reference\n"
            "---\n\n"
            f"{body}\n",
            encoding="utf-8",
        )
        tool_lines = "".join(f"  - {name}\n" for name in tool_scope)
        (self.skill_source / "skill.yaml").write_text(
            "schema_version: voren.skill.v1\n"
            "scope:\n"
            "  - email-calendar-scheduling\n"
            "tool_scope:\n"
            f"{tool_lines}"
            "effect_scope:\n"
            "  - calendar.events:create\n"
            "evaluation_suites:\n"
            "  - agentdojo-workspace-user-task-18\n",
            encoding="utf-8",
        )

    def install_and_activate(self, body: str, *, reason: str = "reviewed static"):
        self.write_skill(body)
        version = self.skill_store.install(
            self.parser.load(self.skill_source),
            created_at=datetime(2026, 9, 2, 8, 0, tzinfo=UTC),
        )
        self.skill_store.activate(version.ref, reason=reason)
        return version

    @staticmethod
    def config(snapshot: SkillContextSnapshot) -> RunConfig:
        return RunConfig(
            workflow="email_to_calendar",
            world_adapter="deterministic_workspace",
            policy_version="provenance-and-approval-v1",
            action_contract_versions=(WORKSPACE_CONTRACT_VERSION,),
            skill_versions=snapshot.skill_versions,
            metadata={"skill_mode": snapshot.mode.value},
        )

    def open_runtime(self, snapshot: SkillContextSnapshot, responses):
        ledger = SQLiteOperationLedger(self.database)
        run_store = SQLiteRunStore(self.database)
        self.addCleanup(ledger.close)
        self.addCleanup(run_store.close)
        world = FakeWorkspaceAdapter()
        definition = create_calendar_event_definition()
        manager = RunManager(
            store=run_store,
            operation_ledger=ledger,
            action_gateway=ActionGateway(
                definitions=(definition,),
                adapter=world,
                ledger=ledger,
            ),
            run_id_factory=lambda: "run-skill-context-1",
            event_id_factory=self._event_id,
            clock=lambda: datetime(2026, 9, 2, 8, 0, tzinfo=UTC),
        )
        model = ScriptedModelAdapter(tuple(responses))
        loop = AgentLoop(
            model=model,
            read_tools=UnusedReadAdapter(),
            action_tools=(
                external_action_tool(
                    definition,
                    description="Propose a calendar event for approval.",
                ),
            ),
            run_manager=manager,
            skill_context=snapshot,
        )
        return loop, model, run_store

    _event_number = 0

    @classmethod
    def _event_id(cls) -> str:
        cls._event_number += 1
        return f"event-skill-context-{cls._event_number}"

    def test_no_skill_is_an_explicit_reproducible_baseline(self) -> None:
        snapshot = SkillContextAssembler(self.skill_store).no_skill()
        loop, model, run_store = self.open_runtime(
            snapshot, (ModelResponse(text="No matching email was requested."),)
        )

        result = loop.run(user_request="What can you do?", config=self.config(snapshot))

        self.assertEqual(result.status, RuntimeResultStatus.COMPLETED)
        system_message = model.requests[0][0][0]
        self.assertEqual(system_message.role, MessageRole.SYSTEM)
        self.assertEqual(system_message.content, SYSTEM_INSTRUCTION)
        event = next(
            item
            for item in run_store.list_events(result.run_id)
            if item.event_type is RunEventType.SKILL_CONTEXT_ASSEMBLED
        )
        self.assertEqual(event.payload["mode"], SkillContextMode.NO_SKILL.value)
        self.assertEqual(event.payload["skill_versions"], [])
        self.assertEqual(event.payload["trust"], "none")

    def test_static_skill_enters_context_without_entering_audit_prose(self) -> None:
        body = "CHECK CALENDAR BEFORE PROPOSING THE REVIEWED EVENT"
        version = self.install_and_activate(body)
        snapshot = SkillContextAssembler(self.skill_store).static_skill(
            ("schedule-from-email",)
        )
        loop, model, run_store = self.open_runtime(
            snapshot, (ModelResponse(text="I need an email to schedule from."),)
        )

        result = loop.run(
            user_request="Schedule the selected email.", config=self.config(snapshot)
        )

        system_content = model.requests[0][0][0].content
        self.assertIn(body, system_content)
        self.assertIn(version.ref.version_id, system_content)
        self.assertIn("do not grant tools or effects", system_content)
        event = next(
            item
            for item in run_store.list_events(result.run_id)
            if item.event_type is RunEventType.SKILL_CONTEXT_ASSEMBLED
        )
        self.assertEqual(event.payload["mode"], "static_skill")
        self.assertEqual(event.payload["context_digest"], snapshot.context_digest)
        self.assertEqual(event.payload["trust"], "configured_static")
        self.assertEqual(event.payload["skill_versions"], [version.ref.model_dump()])
        self.assertNotIn(body, json.dumps(event.payload))

    def test_snapshot_survives_active_pointer_change(self) -> None:
        first = self.install_and_activate("FIRST REVIEWED PROCEDURE")
        assembler = SkillContextAssembler(self.skill_store)
        frozen = assembler.static_skill(("schedule-from-email",))

        self.write_skill("SECOND REVIEWED PROCEDURE")
        second = self.skill_store.install(self.parser.load(self.skill_source))
        self.skill_store.activate(second.ref, reason="reviewed replacement")
        restored = assembler.from_frozen(frozen.skill_versions)

        self.assertEqual(frozen.skill_versions, (first.ref,))
        self.assertEqual(restored.context_digest, frozen.context_digest)
        self.assertIn("FIRST REVIEWED PROCEDURE", restored.rendered_instructions)
        self.assertNotIn("SECOND REVIEWED PROCEDURE", restored.rendered_instructions)

    def test_run_config_mismatch_is_rejected_before_a_run_is_created(self) -> None:
        self.install_and_activate("REVIEWED PROCEDURE")
        snapshot = SkillContextAssembler(self.skill_store).static_skill(
            ("schedule-from-email",)
        )
        loop, model, _ = self.open_runtime(
            snapshot, (ModelResponse(text="unused"),)
        )

        with self.assertRaisesRegex(ValueError, "do not match"):
            loop.run(
                user_request="Schedule it.",
                config=self.config(SkillContextSnapshot.no_skill()),
            )

        self.assertEqual(model.requests, [])
        with sqlite3.connect(self.database) as connection:
            count = connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        self.assertEqual(count, 0)

    def test_skill_tool_scope_cannot_expand_runtime_capabilities(self) -> None:
        self.write_skill("UNSAFE TOOL REQUEST", tool_scope=("delete_all_emails",))
        version = self.skill_store.install(self.parser.load(self.skill_source))
        self.skill_store.activate(version.ref, reason="test invalid capability")
        snapshot = SkillContextAssembler(self.skill_store).static_skill(
            ("schedule-from-email",)
        )

        with self.assertRaisesRegex(ValueError, "unavailable tools"):
            self.open_runtime(snapshot, (ModelResponse(text="unused"),))

    def test_context_size_limit_is_enforced_after_verified_load(self) -> None:
        self.install_and_activate("x" * 1_000)

        with self.assertRaisesRegex(ValueError, "max_instruction_bytes"):
            SkillContextAssembler(
                self.skill_store, max_instruction_bytes=100
            ).static_skill(("schedule-from-email",))


if __name__ == "__main__":
    unittest.main()
