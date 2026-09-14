from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from voren.adapters.google_smoke import (
    GoogleSmokeArtifact,
    collect_google_smoke_run,
    read_google_smoke_artifact,
    write_google_smoke_artifact,
)
from voren.runs.models import (
    RunConfig,
    RunEvent,
    RunEventType,
    RunRecord,
    RunStatus,
)


class GoogleSmokeArtifactTest(unittest.TestCase):
    def test_complete_suite_is_redacted_and_integrity_checked(self) -> None:
        email_run, email_events = self._run(
            ordinal=1,
            read_tool="search_emails",
            action_name="create_email_draft",
            resource="gmail.drafts",
        )
        calendar_run, calendar_events = self._run(
            ordinal=2,
            read_tool="get_day_calendar_events",
            action_name="create_private_calendar_event",
            resource="google.calendar.events",
        )

        artifact = GoogleSmokeArtifact.create(
            runs=(
                collect_google_smoke_run(email_run, email_events),
                collect_google_smoke_run(calendar_run, calendar_events),
            ),
            created_at=datetime(2026, 9, 14, 8, 0, tzinfo=UTC),
        )
        artifact.assert_integrity()

        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "google-smoke.json"
            write_google_smoke_artifact(output, artifact)
            raw = output.read_text(encoding="utf-8")
            restored = read_google_smoke_artifact(output)

        self.assertEqual(restored, artifact)
        self.assertEqual(
            set(artifact.coverage.observed_read_tools),
            {"search_emails", "get_day_calendar_events"},
        )
        self.assertEqual(
            set(artifact.coverage.observed_actions),
            {"create_email_draft", "create_private_calendar_event"},
        )
        self.assertNotIn("alice@example.com", raw)
        self.assertNotIn("private mailbox content", raw)
        self.assertNotIn("google-provider-object-id", raw)
        self.assertNotIn("test-oauth-token", raw)
        self.assertIn("https://api.deepseek.com/responses", raw)
        self.assertIn("deepseek-flash", raw)

    def test_injected_connector_run_cannot_be_exported_as_live(self) -> None:
        run, events = self._run(
            ordinal=1,
            read_tool="search_emails",
            action_name="create_email_draft",
            resource="gmail.drafts",
            connector_boundary="injected_connector",
        )

        with self.assertRaisesRegex(ValueError, "live Google boundary"):
            collect_google_smoke_run(run, events)

    def test_incomplete_suite_cannot_be_signed(self) -> None:
        run, events = self._run(
            ordinal=1,
            read_tool="search_emails",
            action_name="create_email_draft",
            resource="gmail.drafts",
        )

        with self.assertRaisesRegex(ValueError, "required read"):
            GoogleSmokeArtifact.create(
                runs=(collect_google_smoke_run(run, events),)
            )

    def test_tampered_artifact_is_rejected(self) -> None:
        email_run, email_events = self._run(
            ordinal=1,
            read_tool="search_emails",
            action_name="create_email_draft",
            resource="gmail.drafts",
        )
        calendar_run, calendar_events = self._run(
            ordinal=2,
            read_tool="get_day_calendar_events",
            action_name="create_private_calendar_event",
            resource="google.calendar.events",
        )
        artifact = GoogleSmokeArtifact.create(
            runs=(
                collect_google_smoke_run(email_run, email_events),
                collect_google_smoke_run(calendar_run, calendar_events),
            )
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "google-smoke.json"
            write_google_smoke_artifact(output, artifact)
            payload = json.loads(output.read_text(encoding="utf-8"))
            payload["runs"][0]["provider"] = "tampered-provider"
            output.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaises((ValidationError, ValueError)):
                read_google_smoke_artifact(output)

    @staticmethod
    def _run(
        *,
        ordinal: int,
        read_tool: str,
        action_name: str,
        resource: str,
        connector_boundary: str = "environment_google_rest",
    ) -> tuple[RunRecord, tuple[RunEvent, ...]]:
        created_at = datetime(2026, 9, 14, 7, ordinal, tzinfo=UTC)
        config = RunConfig(
            workflow="google_email_calendar",
            world_adapter="google_workspace_rest_v1",
            policy_version="provenance-and-exact-effects-v1",
            action_contract_versions=("google-workspace-rest-v1/voren-contract-v1",),
            metadata={
                "model": "deepseek-v4-flash",
                "provider": "deepseek",
                "provider_endpoint": "https://api.deepseek.com/responses",
                "code_revision": "a" * 40,
                "code_dirty": False,
                "google_connector_boundary": connector_boundary,
                "model_adapter_boundary": "environment_responses_api",
            },
        )
        run = RunRecord(
            run_id=f"google-smoke-run-{ordinal}",
            status=RunStatus.COMPLETED,
            config=config,
            config_digest=config.calculated_digest(),
            version=4,
            created_at=created_at,
            updated_at=created_at + timedelta(seconds=5),
        )
        proposal_digest = f"{ordinal}" * 64
        observation_digest = f"{ordinal + 2}" * 64
        base_payload = {
            "private_test_material": {
                "account": "alice@example.com",
                "content": "private mailbox content",
                "access_token": "test-oauth-token",
            }
        }
        event_data = (
            (RunEventType.RUN_CREATED, {}),
            (RunEventType.RUN_STARTED, {}),
            (
                RunEventType.MODEL_RESPONDED,
                {
                    "returned_model": "deepseek-flash",
                    "usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 20,
                        "cache_write_input_tokens": 0,
                        "output_tokens": 10,
                        "reasoning_output_tokens": 0,
                        "total_tokens": 110,
                    },
                },
            ),
            (
                RunEventType.TOOL_OBSERVED,
                {
                    "tool_name": read_tool,
                    "status": "succeeded",
                    "observation_digest": observation_digest,
                    **base_payload,
                },
            ),
            (
                RunEventType.ACTION_PROPOSED,
                {
                    "action_name": action_name,
                    "action_version": (
                        "google-workspace-rest-v1/voren-contract-v1"
                    ),
                    "proposal_digest": proposal_digest,
                    "evidence_digests": [observation_digest],
                    "effects": [
                        {
                            "resource": resource,
                            "kind": "create",
                            "private_content": "private mailbox content",
                        }
                    ],
                },
            ),
            (RunEventType.APPROVAL_ACCEPTED, {"approved": True}),
            (
                RunEventType.ACTION_RECEIPT,
                {
                    "status": "verified",
                    "committed": True,
                    "recovered_after_ambiguous_commit": False,
                    "verification": {"passed": True},
                    "external_references": [
                        {"external_reference": "google-provider-object-id"}
                    ],
                },
            ),
            (RunEventType.RUN_COMPLETED, {"receipt_status": "verified"}),
        )
        events = tuple(
            RunEvent(
                sequence=index,
                event_id=f"event-{ordinal}-{index}",
                run_id=run.run_id,
                dedupe_key=f"event:{index}",
                event_type=event_type,
                payload=payload,
                occurred_at=created_at + timedelta(seconds=index),
            )
            for index, (event_type, payload) in enumerate(event_data, start=1)
        )
        return run, events


if __name__ == "__main__":
    unittest.main()
