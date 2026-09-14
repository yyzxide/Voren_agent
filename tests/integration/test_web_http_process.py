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
from pathlib import Path


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
