from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.integration.test_web_app import WEB_AVAILABLE

if WEB_AVAILABLE:
    from fastapi.testclient import TestClient
    from voren.web.app import create_app
    from voren.web.service import VorenWebService


@unittest.skipUnless(WEB_AVAILABLE, "web dependencies are unavailable")
class WebKnowledgeAppTest(unittest.TestCase):
    def test_real_app_mounts_knowledge_routes_without_online_factory_on_empty_corpus(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "runtime.sqlite3"

            def forbidden_model():
                raise AssertionError("an empty corpus must not configure a model")

            service = VorenWebService(database=database, model_factory=forbidden_model, mode="demo")
            client = TestClient(create_app(service=service, knowledge_model_factory=forbidden_model))
            search = client.post("/api/knowledge/search", json={"question": "审批回执"})
            self.assertEqual(search.status_code, 200)
            self.assertEqual(search.json()["hits"], [])
            self.assertEqual(search.json()["corpus"]["refs"], [])
            ask = client.post("/api/knowledge/ask", json={
                "question": "审批回执", "corpus": search.json()["corpus"], "allow_model_api": True,
            })
            self.assertEqual(ask.status_code, 200)
            self.assertEqual(ask.json()["status"], "abstained")
            self.assertEqual(ask.json()["usage"]["requests_attempted"], 0)


if __name__ == "__main__":
    unittest.main()
