from __future__ import annotations

import importlib.util
import socket
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path


WEB_AVAILABLE = (
    importlib.util.find_spec("fastapi") is not None
    and importlib.util.find_spec("mcp") is not None
)


def web_runtime_available() -> bool:
    left = None
    right = None
    try:
        left, right = socket.socketpair()
        left.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1)
    except OSError:
        return False
    finally:
        if left is not None:
            left.close()
        if right is not None:
            right.close()
    return True


WEB_RUNTIME_AVAILABLE = web_runtime_available()

if WEB_AVAILABLE:
    from fastapi.testclient import TestClient

    from tests.test_google_workspace_connector import FakeGoogleTransport
    from voren.adapters.google_workspace import (
        GoogleWorkspaceConfig,
        GoogleWorkspaceConnector,
    )
    from voren.knowledge.models import KnowledgeDocument, KnowledgeSourceKind
    from voren.knowledge.store import SQLiteKnowledgeStore
    from voren.providers.openai_responses import ModelConfigurationError
    from voren.runtime.models import ModelResponse, ToolCall
    from voren.skills.parser import AgentSkillParser
    from voren.skills.routing import SkillRoutingMode
    from voren.skills.store import SQLiteSkillStore
    from voren.testing.scripted_model import ScriptedModelAdapter
    from voren.web.app import create_app
    from voren.web.demo_model import DemoWorkspaceModel
    from voren.web.service import (
        VorenWebService,
        create_google_web_workspace,
    )


@unittest.skipUnless(
    WEB_AVAILABLE and WEB_RUNTIME_AVAILABLE,
    "web dependencies or the local IPC required by TestClient are unavailable",
)
class VorenWebAppIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database = Path(self.temporary_directory.name) / "web.sqlite3"
        self.now = datetime(2026, 9, 14, 12, 30, tzinfo=UTC)
        self.service = self.create_service()
        self.client = TestClient(create_app(service=self.service))
        self.addCleanup(self.client.close)

    def create_service(self) -> VorenWebService:
        return VorenWebService(
            database=self.database,
            model_factory=DemoWorkspaceModel,
            mode="demo",
            clock=lambda: self.now,
        )

    def test_local_page_and_health_work_without_model_credentials(self) -> None:
        page = self.client.get("/")
        health = self.client.get("/api/health")
        css = self.client.get("/static/styles.css")

        self.assertEqual(page.status_code, 200)
        self.assertIn("Voren Agent", page.text)
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["mode"], "demo")
        self.assertEqual(health.json()["workspace"], "agentdojo")
        self.assertTrue(health.json()["workspace_configured"])
        self.assertFalse(health.json()["live_model_configured"])
        self.assertEqual(health.json()["skill_routing_mode"], "auto")
        self.assertEqual(health.json()["active_skill_count"], 0)
        self.assertEqual(health.json()["skill_database"], str(self.database))
        self.assertIn("font: 16px/1.6", css.text)
        javascript = self.client.get("/static/app.js")
        self.assertEqual(javascript.status_code, 200)
        self.assertIn("localStorage.setItem", javascript.text)
        self.assertIn("state.decision.decision_id", javascript.text)

    def test_greeting_returns_an_agent_response_without_an_api_key(self) -> None:
        response = self.client.post(
            "/api/runs",
            json={
                "client_request_id": "request:greeting-001",
                "request": "你好",
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "completed")
        self.assertIn("确定性演示", body["final_text"])
        self.assertIsNone(body["proposal"])

        stream = self.client.get(f"/api/runs/{body['run_id']}/events/stream")
        self.assertEqual(stream.status_code, 200)
        self.assertIn("event: run.completed", stream.text)

    def test_demo_search_returns_source_bound_knowledge(self) -> None:
        store = SQLiteKnowledgeStore(self.database)
        document = KnowledgeDocument.create(
            document_id="meeting:web-demo",
            title="Web demo review",
            source_uri="meeting://web-demo",
            source_kind=KnowledgeSourceKind.MEETING_NOTE,
            content="Voren 的网页演示评审安排在周五下午。",
            created_at=self.now,
        )
        store.install(document)
        store.activate(document.ref, reason="operator reviewed web fixture")
        store.close()

        response = self.client.post(
            "/api/runs",
            json={
                "client_request_id": "request:knowledge-001",
                "request": "网页演示评审安排在什么时候？",
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "completed")
        self.assertIn("周五下午", body["final_text"])
        self.assertIn("meeting://web-demo", body["final_text"])
        self.assertIn(document.ref.version_id, body["final_text"])

    def test_task_runs_immediately_then_requires_only_exact_effect_decision(self) -> None:
        version = self._activate_repository_skill()
        payload = {
            "client_request_id": "request:schedule-001",
            "request": "根据徒步邮件安排日程，并在执行前让我确认。",
        }
        response = self.client.post("/api/runs", json=payload)

        self.assertEqual(response.status_code, 200)
        pending = response.json()
        self.assertEqual(pending["status"], "waiting_approval")
        self.assertEqual(len(pending["proposal"]["effects"]), 2)
        self.assertEqual(
            pending["skill_routing"]["selected_versions"],
            [version.ref.model_dump(mode="json")],
        )
        self.assertEqual(pending["skill_routing"]["mode"], "auto")

        repeated = self.client.post("/api/runs", json=payload)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.json()["run_id"], pending["run_id"])

        mismatch = self.client.post(
            f"/api/runs/{pending['run_id']}/decision",
            json={
                "decision_id": "decision:schedule-001",
                "proposal_digest": "0" * 64,
                "approved": True,
            },
        )
        self.assertEqual(mismatch.status_code, 409)

        decision = {
            "decision_id": "decision:schedule-001",
            "proposal_digest": pending["proposal"]["digest"],
            "approved": True,
        }
        approved = self.client.post(
            f"/api/runs/{pending['run_id']}/decision",
            json=decision,
        )

        self.assertEqual(approved.status_code, 200)
        completed = approved.json()
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["receipt"]["status"], "verified")
        self.assertTrue(completed["receipt"]["verification"]["passed"])

        same_decision = self.client.post(
            f"/api/runs/{pending['run_id']}/decision",
            json=decision,
        )
        self.assertEqual(same_decision.status_code, 200)
        self.assertEqual(
            same_decision.json()["receipt"]["operation_id"],
            completed["receipt"]["operation_id"],
        )

        events = self.client.get(
            f"/api/runs/{pending['run_id']}/events"
        ).json()["events"]
        event_types = [event["event_type"] for event in events]
        self.assertIn("approval.accepted", event_types)
        self.assertIn("action.receipt", event_types)
        self.assertIn("run.completed", event_types)
        skill_event = next(
            event
            for event in events
            if event["event_type"] == "skill_context.assembled"
        )
        self.assertEqual(skill_event["payload"]["mode"], "static_skill")
        self.assertEqual(
            skill_event["payload"]["skill_versions"],
            [version.ref.model_dump(mode="json")],
        )

    def test_rejection_is_terminal_and_dispatches_no_action(self) -> None:
        pending = self.client.post(
            "/api/runs",
            json={
                "client_request_id": "request:reject-001",
                "request": "安排徒步日程",
            },
        ).json()

        rejected = self.client.post(
            f"/api/runs/{pending['run_id']}/decision",
            json={
                "decision_id": "decision:reject-001",
                "proposal_digest": pending["proposal"]["digest"],
                "approved": False,
            },
        )

        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(rejected.json()["status"], "cancelled")
        self.assertIsNone(rejected.json()["receipt"])
        self.assertFalse(rejected.json()["decision_approved"])

    def test_web_skill_routing_can_be_explicitly_disabled(self) -> None:
        self._activate_repository_skill()
        disabled_service = VorenWebService(
            database=self.database,
            model_factory=DemoWorkspaceModel,
            mode="demo",
            skill_database=self.service.skill_database,
            skill_store_root=self.service.skill_store_root,
            skill_routing_mode=SkillRoutingMode.DISABLED,
            clock=lambda: self.now,
        )
        disabled = TestClient(create_app(service=disabled_service))
        self.addCleanup(disabled.close)

        response = disabled.post(
            "/api/runs",
            json={
                "client_request_id": "request:no-skill-web-001",
                "request": "根据徒步邮件安排日程。",
            },
        )

        self.assertEqual(response.status_code, 200)
        route = response.json()["skill_routing"]
        self.assertEqual(route["mode"], "disabled")
        self.assertEqual(route["selected_versions"], [])

    def test_lost_decision_response_recovers_durable_receipt_without_resend(self) -> None:
        pending = self.client.post(
            "/api/runs",
            json={
                "client_request_id": "request:lost-response-001",
                "request": "安排徒步日程",
            },
        ).json()
        decision = {
            "decision_id": "decision:lost-response-001",
            "proposal_digest": pending["proposal"]["digest"],
            "approved": True,
        }
        original_save = self.service._index.save
        failed_once = False

        def fail_after_commit(view):
            nonlocal failed_once
            if view.decision_id is not None and not failed_once:
                failed_once = True
                raise RuntimeError("simulated lost HTTP response")
            original_save(view)

        self.service._index.save = fail_after_commit
        with self.assertRaises(RuntimeError):
            self.service.decide(pending["run_id"], self._decision(decision))

        recovered = self.service.decide(
            pending["run_id"], self._decision(decision)
        )

        self.assertEqual(recovered.status, "completed")
        self.assertTrue(recovered.receipt.verification.passed)
        events = self.service.events(pending["run_id"])
        self.assertEqual(
            sum(event.event_type.value == "action.receipt" for event in events),
            1,
        )

    def test_same_client_request_id_cannot_be_rebound(self) -> None:
        first = {
            "client_request_id": "request:conflict-001",
            "request": "你好",
        }
        self.assertEqual(self.client.post("/api/runs", json=first).status_code, 200)

        conflict = self.client.post(
            "/api/runs",
            json={**first, "request": "另一个任务"},
        )

        self.assertEqual(conflict.status_code, 409)

    def test_restart_marks_in_memory_pending_workspace_as_unrecoverable(self) -> None:
        pending = self.client.post(
            "/api/runs",
            json={
                "client_request_id": "request:restart-001",
                "request": "安排徒步日程",
            },
        ).json()
        restarted = TestClient(create_app(service=self.create_service()))
        self.addCleanup(restarted.close)

        restored = restarted.get(f"/api/runs/{pending['run_id']}")

        self.assertEqual(restored.status_code, 200)
        self.assertTrue(restored.json()["recovery_required"])
        decision = restarted.post(
            f"/api/runs/{pending['run_id']}/decision",
            json={
                "decision_id": "decision:restart-001",
                "proposal_digest": pending["proposal"]["digest"],
                "approved": True,
            },
        )
        self.assertEqual(decision.status_code, 409)
        self.assertIn("no action was dispatched", decision.json()["detail"])

    def test_model_configuration_failure_releases_request_reservation(self) -> None:
        def fail_model():
            raise ModelConfigurationError("test model is not configured")

        unavailable = TestClient(
            create_app(
                service=VorenWebService(
                    database=self.database,
                    model_factory=fail_model,
                    mode="live",
                    clock=lambda: self.now,
                )
            )
        )
        self.addCleanup(unavailable.close)
        payload = {
            "client_request_id": "request:unconfigured-001",
            "request": "你好",
        }

        first = unavailable.post("/api/runs", json=payload)
        retried = unavailable.post("/api/runs", json=payload)

        self.assertEqual(first.status_code, 503)
        self.assertEqual(retried.status_code, 503)

    def test_google_web_approval_survives_process_restart(self) -> None:
        self._activate_repository_skill()
        transport = FakeGoogleTransport()
        config = GoogleWorkspaceConfig(
            access_token="test-token",
            account_email="sid@example.com",
            time_zone="Asia/Shanghai",
        )

        def workspace_factory():
            return create_google_web_workspace(
                connector=GoogleWorkspaceConnector(
                    config,
                    transport=transport,
                    clock=lambda: self.now,
                )
            )

        def model_factory():
            return ScriptedModelAdapter(
                (
                    ModelResponse(
                        tool_calls=(
                            ToolCall(
                                call_id="google-calendar-action",
                                name="create_private_calendar_event",
                                arguments={
                                    "title": "Web recovery review",
                                    "start_time": "2026-09-15T09:00:00+08:00",
                                    "end_time": "2026-09-15T10:00:00+08:00",
                                },
                            ),
                        )
                    ),
                )
            )

        def service():
            return VorenWebService(
                database=self.database,
                model_factory=model_factory,
                mode="live",
                workspace_factory=workspace_factory,
                workspace_name="google",
                workspace_recoverable=True,
                clock=lambda: self.now,
            )

        original = TestClient(create_app(service=service()))
        self.addCleanup(original.close)
        pending = original.post(
            "/api/runs",
            json={
                "client_request_id": "request:google-restart-001",
                "request": "创建一个私人日历占位。",
            },
        ).json()
        self.assertEqual(pending["workspace"], "google")
        self.assertEqual(pending["status"], "waiting_approval")
        self.assertEqual(pending["skill_routing"]["selected_versions"], [])
        self.assertEqual(
            pending["skill_routing"]["incompatible_skills"][0]["ref"]["name"],
            "schedule-from-email",
        )

        restarted = TestClient(create_app(service=service()))
        self.addCleanup(restarted.close)
        restored = restarted.get(f"/api/runs/{pending['run_id']}").json()
        self.assertFalse(restored["recovery_required"])
        approved = restarted.post(
            f"/api/runs/{pending['run_id']}/decision",
            json={
                "decision_id": "decision:google-restart-001",
                "proposal_digest": pending["proposal"]["digest"],
                "approved": True,
            },
        )

        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.json()["status"], "completed")
        self.assertTrue(approved.json()["receipt"]["verification"]["passed"])
        self.assertEqual(
            sum(call[0] == "POST" for call in transport.calls),
            1,
        )

    def test_google_workspace_cannot_be_mislabeled_as_demo_mode(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires VOREN_WEB_MODE=live"):
            create_app(mode="demo", workspace="google")

    @staticmethod
    def _decision(payload):
        from voren.web.models import DecideRunRequest

        return DecideRunRequest.model_validate(payload)

    def _activate_repository_skill(self):
        parser = AgentSkillParser()
        store = SQLiteSkillStore(
            self.service.skill_database,
            root=self.service.skill_store_root,
            parser=parser,
        )
        try:
            source = Path(__file__).parents[2] / "skills" / "schedule-from-email"
            version = store.install(parser.load(source))
            store.activate(version.ref, reason="reviewed Web routing test")
            return version
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
