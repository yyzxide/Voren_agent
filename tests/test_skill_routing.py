from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from voren.skills.context import SkillContextAssembler, SkillContextMode
from voren.skills.models import AgentSkillMetadata, SkillPackage
from voren.skills.parser import AgentSkillParser
from voren.skills.routing import SkillRouter, SkillRoutingError, SkillRoutingMode
from voren.skills.store import SQLiteSkillStore


class SkillRoutingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        temporary = Path(self.temporary_directory.name)
        self.store = SQLiteSkillStore(
            temporary / "skills.sqlite3",
            root=temporary / "objects",
        )
        self.addCleanup(self.store.close)
        self.parser = AgentSkillParser()
        self.repository_skill = (
            Path(__file__).resolve().parents[1]
            / "skills"
            / "schedule-from-email"
        )
        self.version = self.store.install(self.parser.load(self.repository_skill))
        self.store.activate(self.version.ref, reason="reviewed routing baseline")
        self.router = SkillRouter(self.store)
        self.agentdojo_tools = (
            "search_emails",
            "get_day_calendar_events",
            "search_contacts_by_name",
            "create_calendar_event",
            "send_email",
        )

    def test_auto_route_selects_unique_compatible_skill_from_operator_request(self) -> None:
        decision = self.router.auto(
            request="请从徒步邮件中提取信息并安排日程。",
            available_tools=self.agentdojo_tools,
        )
        snapshot = SkillContextAssembler(self.store).from_frozen(
            decision.selected_versions
        )

        self.assertEqual(decision.mode, SkillRoutingMode.AUTO)
        self.assertEqual(decision.selected_versions, (self.version.ref,))
        self.assertIn("日程", decision.matches[0].matched_keywords)
        self.assertEqual(snapshot.mode, SkillContextMode.STATIC_SKILL)
        self.assertIn("approval boundary", snapshot.rendered_instructions)
        self.assertNotIn("徒步邮件", decision.model_dump_json())

    def test_auto_route_does_not_load_skill_for_unrelated_request(self) -> None:
        decision = self.router.auto(
            request="你好，请概括一下你能做什么。",
            available_tools=self.agentdojo_tools,
        )

        self.assertEqual(decision.selected_versions, ())
        self.assertEqual(decision.matches, ())

    def test_auto_route_filters_contract_incompatible_with_workspace(self) -> None:
        decision = self.router.auto(
            request="Schedule a meeting from the latest email.",
            available_tools=("search_emails", "create_email_draft"),
        )

        self.assertEqual(decision.selected_versions, ())
        self.assertEqual(len(decision.incompatible_skills), 1)
        self.assertIn(
            "create_calendar_event",
            decision.incompatible_skills[0].missing_tools,
        )

    def test_explicit_route_rejects_incompatible_skill_before_instruction_load(self) -> None:
        with self.assertRaisesRegex(
            SkillRoutingError, "unavailable workspace tools"
        ):
            self.router.explicit(
                request="Draft a reply.",
                available_tools=("search_emails", "create_email_draft"),
                names=("schedule-from-email",),
            )

    def test_equal_best_matches_fail_closed_as_ambiguous(self) -> None:
        package = self.parser.load(self.repository_skill)
        metadata = package.metadata.model_copy(
            update={
                "name": "meeting-helper",
            }
        )
        files = tuple(
            item.model_copy(
                update={
                    "content": item.content.replace(
                        b"name: schedule-from-email", b"name: meeting-helper"
                    ),
                    "digest": hashlib.sha256(
                        item.content.replace(
                            b"name: schedule-from-email", b"name: meeting-helper"
                        )
                    ).hexdigest(),
                }
            )
            if item.relative_path == "SKILL.md"
            else item
            for item in package.files
        )
        alternative = SkillPackage.build(
            metadata=AgentSkillMetadata.model_validate(metadata),
            instructions=package.instructions,
            contract=package.contract,
            files=files,
        )
        version = self.store.install(alternative)
        self.store.activate(version.ref, reason="reviewed ambiguous alternative")

        decision = self.router.auto(
            request="Schedule a meeting.",
            available_tools=self.agentdojo_tools,
        )

        self.assertEqual(decision.selected_versions, ())
        self.assertEqual(
            decision.ambiguous_skills,
            ("meeting-helper", "schedule-from-email"),
        )


if __name__ == "__main__":
    unittest.main()
