from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from voren.adapters.workspace_contracts import WORKSPACE_CONTRACT_VERSION
from voren.runs.models import RunConfig
from voren.skills.parser import AgentSkillParser, SkillFormatError
from voren.skills.store import SQLiteSkillStore


class SkillStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        temporary = Path(self.temporary_directory.name)
        self.sources = temporary / "sources"
        self.sources.mkdir()
        self.database = temporary / "skills.sqlite3"
        self.store_root = temporary / "store"
        self.parser = AgentSkillParser()
        self.store = SQLiteSkillStore(
            self.database, root=self.store_root, parser=self.parser
        )
        self.addCleanup(self.store.close)
        self.now = datetime(2026, 9, 1, 14, 0, tzinfo=UTC)

    def write_skill(
        self,
        *,
        name: str = "schedule-from-email",
        body: str = "# Schedule\n\nCheck email, then check the calendar.",
        allowed_tools: str | None = None,
    ) -> Path:
        directory = self.sources / name
        directory.mkdir(parents=True, exist_ok=True)
        hint = (
            f"allowed-tools: {allowed_tools}\n" if allowed_tools is not None else ""
        )
        (directory / "SKILL.md").write_text(
            "---\n"
            f"name: {name}\n"
            "description: Schedule meetings from email when the operator asks.\n"
            f"{hint}"
            "metadata:\n"
            "  author: sid\n"
            "---\n\n"
            f"{body}\n",
            encoding="utf-8",
        )
        (directory / "skill.yaml").write_text(
            "schema_version: voren.skill.v1\n"
            "scope:\n"
            "  - email-calendar-scheduling\n"
            "tool_scope:\n"
            "  - search_emails\n"
            "  - get_day_calendar_events\n"
            "effect_scope:\n"
            "  - calendar.events:create\n"
            "evaluation_suites:\n"
            "  - agentdojo-user-task-18\n",
            encoding="utf-8",
        )
        references = directory / "references"
        references.mkdir(exist_ok=True)
        (references / "edge-cases.md").write_text(
            "Ask when the duration is missing.\n", encoding="utf-8"
        )
        return directory

    @staticmethod
    def run_config(*, skill_versions=()) -> RunConfig:
        return RunConfig(
            workflow="email_to_calendar",
            world_adapter="deterministic_workspace",
            policy_version="provenance-and-approval-v1",
            action_contract_versions=(WORKSPACE_CONTRACT_VERSION,),
            skill_versions=skill_versions,
        )

    def test_discovery_exposes_metadata_without_instruction_body(self) -> None:
        self.write_skill(body="SECRET PROCEDURE BODY")

        discovered = self.parser.discover(self.sources)

        self.assertEqual(len(discovered), 1)
        self.assertEqual(discovered[0].name, "schedule-from-email")
        self.assertIn("Schedule meetings", discovered[0].description)
        self.assertFalse(hasattr(discovered[0], "instructions"))
        self.assertNotIn("SECRET PROCEDURE BODY", discovered[0].model_dump_json())

    def test_install_activate_freeze_and_progressive_load(self) -> None:
        source = self.write_skill()
        package = self.parser.load(source)

        version = self.store.install(package, created_at=self.now)

        self.assertEqual(self.store.discover_active(), ())
        summary = self.store.activate(
            version.ref,
            reason="manually reviewed static reference",
            activated_at=self.now,
        )
        frozen = self.store.freeze_active()
        loaded = self.store.load(frozen[0])

        self.assertEqual(summary.ref, version.ref)
        self.assertEqual(frozen, (version.ref,))
        self.assertIn("Check email", loaded.instructions)
        self.assertEqual(loaded.resources, ("references/edge-cases.md",))
        resource = self.store.load_resource(
            frozen[0], "references/edge-cases.md"
        )
        self.assertIn(b"duration is missing", resource.content)
        self.assertEqual(
            loaded.version.contract.tool_scope,
            ("search_emails", "get_day_calendar_events"),
        )
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                "SELECT metadata_json, contract_json FROM skill_versions"
            ).fetchone()
        persisted_metadata = "".join(row)
        self.assertNotIn("Check email, then check the calendar", persisted_metadata)

    def test_version_switch_does_not_mutate_frozen_run_or_old_package(self) -> None:
        source = self.write_skill(body="# V1\n\nUse the first reviewed procedure.")
        version_one = self.store.install(self.parser.load(source), created_at=self.now)
        self.store.activate(version_one.ref, reason="initial static version")
        run_one = self.run_config(skill_versions=self.store.freeze_active())

        (source / "SKILL.md").write_text(
            (source / "SKILL.md")
            .read_text(encoding="utf-8")
            .replace("Use the first reviewed procedure.", "Use the revised procedure."),
            encoding="utf-8",
        )
        version_two = self.store.install(
            self.parser.load(source), created_at=self.now + timedelta(minutes=1)
        )
        self.store.activate(version_two.ref, reason="manual revision")
        run_two = self.run_config(skill_versions=self.store.freeze_active())

        self.assertNotEqual(version_one.ref, version_two.ref)
        self.assertNotEqual(run_one.calculated_digest(), run_two.calculated_digest())
        self.assertIn("first reviewed", self.store.load(version_one.ref).instructions)
        self.assertIn("revised procedure", self.store.load(version_two.ref).instructions)
        self.assertEqual(run_one.skill_versions, (version_one.ref,))

    def test_reinstall_is_idempotent_and_keeps_original_created_time(self) -> None:
        package = self.parser.load(self.write_skill())
        first = self.store.install(package, created_at=self.now)
        second = self.store.install(
            package, created_at=self.now + timedelta(days=1)
        )

        self.assertEqual(first, second)
        self.assertEqual(second.created_at, self.now)

    def test_active_pointer_and_package_survive_store_reopen(self) -> None:
        package = self.parser.load(self.write_skill())
        version = self.store.install(package, created_at=self.now)
        self.store.activate(version.ref, reason="durable static activation")
        self.store.close()

        reopened = SQLiteSkillStore(
            self.database, root=self.store_root, parser=self.parser
        )
        try:
            self.assertEqual(reopened.freeze_active(), (version.ref,))
            self.assertIn("Check email", reopened.load(version.ref).instructions)
        finally:
            reopened.close()

    def test_installed_package_tampering_is_detected_before_context_load(self) -> None:
        package = self.parser.load(self.write_skill())
        version = self.store.install(package, created_at=self.now)
        installed_skill = self.store_root / version.package_path / "SKILL.md"
        installed_skill.write_text(
            installed_skill.read_text(encoding="utf-8") + "\nTampered.\n",
            encoding="utf-8",
        )

        with self.assertRaises(SkillFormatError):
            self.store.load(version.ref)

    def test_frontmatter_allowed_tools_is_only_a_hint_not_runtime_authority(self) -> None:
        package = self.parser.load(
            self.write_skill(allowed_tools="DeleteAllEmails SendEmail")
        )
        version = self.store.install(package, created_at=self.now)
        self.store.activate(version.ref, reason="reviewed tool scope sidecar")
        loaded = self.store.load(version.ref)

        self.assertEqual(
            loaded.version.metadata.allowed_tools_hint,
            "DeleteAllEmails SendEmail",
        )
        self.assertNotIn("DeleteAllEmails", loaded.version.contract.tool_scope)
        self.assertNotIn("SendEmail", loaded.version.contract.tool_scope)

    def test_name_mismatch_and_symlink_resources_are_rejected(self) -> None:
        source = self.write_skill(name="schedule-from-email")
        skill_markdown = source / "SKILL.md"
        skill_markdown.write_text(
            skill_markdown.read_text(encoding="utf-8").replace(
                "name: schedule-from-email", "name: other-skill"
            ),
            encoding="utf-8",
        )
        with self.assertRaises(SkillFormatError):
            self.parser.load(source)

        skill_markdown.write_text(
            skill_markdown.read_text(encoding="utf-8").replace(
                "name: other-skill", "name: schedule-from-email"
            ),
            encoding="utf-8",
        )
        (source / "references" / "escape").symlink_to(Path("/tmp"))
        with self.assertRaises(SkillFormatError):
            self.parser.load(source)

    def test_empty_context_snapshots_keep_phase1_run_digest_compatible(self) -> None:
        config = self.run_config()
        legacy_payload = config.model_dump(mode="json")
        legacy_payload.pop("skill_versions")
        legacy_payload.pop("memory_versions")
        canonical = json.dumps(
            legacy_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

        self.assertEqual(
            config.calculated_digest(),
            hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )

    def test_repository_scheduling_skill_conforms_to_parser(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        package = self.parser.load(
            repository_root / "skills" / "schedule-from-email"
        )

        self.assertEqual(package.metadata.name, "schedule-from-email")
        self.assertIn("approval boundary", package.instructions)
        self.assertIn("get_day_calendar_events", package.contract.tool_scope)
        self.assertNotIn("search_calendar_events", package.contract.tool_scope)
        self.assertIn("agentdojo-workspace-user-task-18", package.contract.evaluation_suites)


if __name__ == "__main__":
    unittest.main()
