from __future__ import annotations

import hashlib
import importlib.util
import json
import unittest
from pathlib import Path


class KnowledgeAnswerDemoTest(unittest.TestCase):
    def test_demo_preserves_protocol_failures_and_semantic_counterexample(self) -> None:
        path = Path(__file__).resolve().parents[1] / "scripts" / "demo_knowledge_answers.py"
        spec = importlib.util.spec_from_file_location("knowledge_answer_demo", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        artifact = module.run_demo()
        self.assertEqual(artifact["scenario_count"], 10)
        self.assertEqual(artifact["passed_count"], 10)
        self.assertFalse(artifact["external_calls"])
        cases = {row["case_id"]: row for row in artifact["cases"]}
        self.assertEqual(cases["empty-window"]["result"]["usage"]["requests_attempted"], 0)
        for case_id in ("unknown-hit", "forged-quote", "wrong-span", "uncited-claim", "unexpected-tool-call"):
            self.assertEqual(cases[case_id]["result"]["status"], "rejected")
            self.assertEqual(cases[case_id]["result"]["claims"], [])
        unrelated = cases["real-quote-unrelated-claim"]["result"]
        self.assertEqual(unrelated["status"], "answered")
        self.assertEqual(unrelated["citation_integrity"], "verified")
        self.assertEqual(unrelated["semantic_support"], "unverified")
        original = cases["supported-answer"]["result"]
        frozen = cases["frozen-source-version"]["result"]
        self.assertEqual(original["corpus"], frozen["corpus"])
        self.assertEqual(original["claims"], frozen["claims"])
        digest = artifact.pop("artifact_digest")
        canonical = json.dumps(artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.assertEqual(digest, hashlib.sha256(canonical.encode()).hexdigest())


if __name__ == "__main__":
    unittest.main()
