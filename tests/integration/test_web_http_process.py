from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path

from voren.knowledge.models import KnowledgeDocument, KnowledgeSourceKind
from voren.knowledge.store import SQLiteKnowledgeStore
from voren.skills.parser import AgentSkillParser
from voren.skills.store import SQLiteSkillStore


WEB_PROCESS_AVAILABLE = all(
    importlib.util.find_spec(package) is not None
    for package in ("agentdojo", "fastapi", "uvicorn")
)


@unittest.skipUnless(
    WEB_PROCESS_AVAILABLE,
    "web process dependencies are unavailable",
)
class VorenWebHTTPProcessTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database = Path(self.temporary_directory.name) / "web.sqlite3"
        self.skill_store_root = Path(self.temporary_directory.name) / "skills"
        parser = AgentSkillParser()
        skill_store = SQLiteSkillStore(
            self.database,
            root=self.skill_store_root,
            parser=parser,
        )
        source = Path(__file__).parents[2] / "skills" / "schedule-from-email"
        version = skill_store.install(parser.load(source))
        skill_store.activate(version.ref, reason="reviewed process-boundary test")
        skill_store.close()
        self.skill_ref = version.ref
        knowledge_store = SQLiteKnowledgeStore(self.database)
        document = KnowledgeDocument.create(
            document_id="meeting:process-boundary",
            title="Process-boundary review",
            source_uri="meeting://process-boundary",
            source_kind=KnowledgeSourceKind.MEETING_NOTE,
            content="Voren 的进程边界评审定于周四下午。",
            created_at=datetime(2026, 9, 14, 12, 0, tzinfo=UTC),
        )
        knowledge_store.install(document)
        knowledge_store.activate(document.ref, reason="reviewed HTTP fixture")
        knowledge_store.close()
        self.knowledge_ref = document.ref
        self.port = self._unused_loopback_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.process: subprocess.Popen[str] | None = None
        self._start_server()
        self.addCleanup(self._stop_server)

    def test_cold_start_exact_effect_approval_and_restart_recovery(self) -> None:
        page = self._request("/")
        self.assertIn("Voren Agent", page)
        self.assertIn("开始执行", page)

        health = self._request_json("/api/health")
        self.assertEqual(health["mode"], "demo")
        self.assertEqual(health["workspace"], "agentdojo")
        self.assertTrue(health["workspace_configured"])
        self.assertFalse(health["live_model_configured"])
        self.assertEqual(health["skill_routing_mode"], "auto")
        self.assertEqual(health["active_skill_count"], 1)
        self.assertEqual(health["knowledge_database"], str(self.database))

        knowledge = self._request_json(
            "/api/runs",
            method="POST",
            payload={
                "client_request_id": f"knowledge:http-e2e:{uuid.uuid4()}",
                "request": "进程边界评审安排在什么时候？",
            },
        )
        self.assertEqual(knowledge["status"], "completed")
        self.assertIn("周四下午", knowledge["final_text"])
        self.assertIn("meeting://process-boundary", knowledge["final_text"])
        self.assertIn(self.knowledge_ref.version_id, knowledge["final_text"])

        pending = self._request_json(
            "/api/runs",
            method="POST",
            payload={
                "client_request_id": f"http-e2e:{uuid.uuid4()}",
                "request": "根据徒步邮件安排日程，并在执行前让我确认。",
            },
        )
        self.assertEqual(pending["status"], "waiting_approval")
        self.assertEqual(len(pending["proposal"]["effects"]), 2)
        self.assertEqual(
            pending["skill_routing"]["selected_versions"],
            [self.skill_ref.model_dump(mode="json")],
        )

        decision = {
            "decision_id": f"decision:http-e2e:{uuid.uuid4()}",
            "proposal_digest": pending["proposal"]["digest"],
            "approved": True,
        }
        completed = self._request_json(
            f"/api/runs/{pending['run_id']}/decision",
            method="POST",
            payload=decision,
        )
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["receipt"]["status"], "verified")
        self.assertTrue(completed["receipt"]["verification"]["passed"])

        stream = self._request(
            f"/api/runs/{pending['run_id']}/events/stream"
        )
        self.assertIn("event: action.receipt", stream)
        self.assertIn("event: run.completed", stream)

        operation_id = completed["receipt"]["operation_id"]
        self._stop_server()
        self._start_server()

        recovered = self._request_json(f"/api/runs/{pending['run_id']}")
        self.assertEqual(recovered["status"], "completed")
        self.assertEqual(recovered["receipt"]["operation_id"], operation_id)
        self.assertTrue(recovered["receipt"]["verification"]["passed"])

    @staticmethod
    def _unused_loopback_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])

    def _start_server(self) -> None:
        if self.process is not None:
            self.fail("test server is already running")
        environment = os.environ.copy()
        environment.update(
            {
                "VOREN_WEB_DATABASE": str(self.database),
                "VOREN_KNOWLEDGE_DATABASE": str(self.database),
                "VOREN_SKILL_DATABASE": str(self.database),
                "VOREN_SKILL_STORE": str(self.skill_store_root),
                "VOREN_WEB_HOST": "127.0.0.1",
                "VOREN_WEB_MODE": "demo",
                "VOREN_WEB_PORT": str(self.port),
                "VOREN_WEB_WORKSPACE": "agentdojo",
            }
        )
        self.process = subprocess.Popen(
            [sys.executable, "-m", "voren.web.app"],
            cwd=Path(__file__).resolve().parents[2],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.monotonic() + 15
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.fail(
                    "Voren Web exited during startup:\n"
                    + self._server_output()
                )
            try:
                self._request_json("/api/health", timeout=0.5)
                return
            except (OSError, TimeoutError, urllib.error.URLError) as error:
                last_error = error
                time.sleep(0.05)
        self.fail(
            f"Voren Web did not become ready: {last_error}\n"
            + self._server_output()
        )

    def _stop_server(self) -> None:
        process = self.process
        if process is None:
            return
        self.process = None
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if process.stdout is not None:
            process.stdout.close()

    def _server_output(self) -> str:
        if self.process is None or self.process.stdout is None:
            return ""
        if self.process.poll() is None:
            return "server is still running"
        return self.process.stdout.read()

    def _request_json(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, object] | None = None,
        timeout: float = 10,
    ) -> dict[str, object]:
        return json.loads(
            self._request(
                path,
                method=method,
                payload=payload,
                timeout=timeout,
            )
        )

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, object] | None = None,
        timeout: float = 10,
    ) -> str:
        body = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode()
