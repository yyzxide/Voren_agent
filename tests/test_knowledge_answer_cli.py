from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from voren.cli import build_parser, run_knowledge_ask
from voren.knowledge.models import KnowledgeDocument, KnowledgeSourceKind
from voren.knowledge.store import SQLiteKnowledgeStore
from voren.runtime.models import ModelResponse, ToolCall
from voren.testing.scripted_model import ScriptedModelAdapter


class KnowledgeAnswerCLITest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.database = self.directory / "knowledge.sqlite3"
        self.draft = self.directory / "proposal.json"
        self.document = KnowledgeDocument.create(
            document_id="meeting:atlas", title="Atlas checklist",
            source_uri="fixture://atlas", source_kind=KnowledgeSourceKind.MEETING_NOTE,
            content="Mira owns the Atlas checklist.", created_at=datetime(2026, 10, 8, tzinfo=UTC),
        )
        store = SQLiteKnowledgeStore(self.database)
        try:
            store.install(self.document)
            store.activate(self.document.ref, reason="CLI fixture")
            hit = store.search("Atlas checklist")[0]
        finally:
            store.close()
        self.proposal = {
            "status": "answer", "claims": [{
                "text": "Mira owns the checklist.", "citations": [{
                    "hit_id": hit.chunk_id, "quote": self.document.content,
                    "start": 0, "end": len(self.document.content),
                }],
            }],
        }

    def args(self, *options: str):
        return build_parser().parse_args([
            "knowledge", "ask", "Atlas checklist", "--database", str(self.database), *options,
        ])

    def test_parser_requires_one_explicit_proposal_source(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.args()
            with self.assertRaises(SystemExit):
                self.args("--draft", str(self.draft), "--allow-model-api")

    def test_offline_draft_returns_source_verified_json_without_provider(self) -> None:
        self.draft.write_text(json.dumps(self.proposal), encoding="utf-8")
        output = []
        with patch("voren.cli._resolve_run_model", side_effect=AssertionError("unexpected model provider")), \
             patch("voren.cli.embedding_provider_from_env", side_effect=AssertionError("unexpected embedding provider")):
            code = run_knowledge_ask(self.args("--draft", str(self.draft)), output=output.append)
        result = json.loads(output[0])
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["citation_integrity"], "verified")
        self.assertEqual(result["semantic_support"], "unverified")
        self.assertEqual(result["claims"][0]["citations"][0]["ref"], self.document.ref.model_dump())
        self.assertEqual(result["usage"]["requests_attempted"], 1)

    def test_injected_model_obeys_single_call_and_rejects_tool_proposal(self) -> None:
        model = ScriptedModelAdapter((ModelResponse(tool_calls=(
            ToolCall(call_id="forbidden", name="send_email", arguments={}),
        )),))
        output = []
        code = run_knowledge_ask(self.args("--allow-model-api"), model=model, output=output.append)
        result = json.loads(output[0])
        self.assertEqual(code, 2)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["error_code"], "unexpected_model_tool_calls")
        self.assertEqual(result["claims"], [])
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(model.requests[0][1], ())

    def test_abstention_is_success_and_malformed_proposal_is_failure(self) -> None:
        for proposal, expected_code, expected_status in (
            ({"status": "abstain", "claims": [], "reason": "No sufficient evidence."}, 0, "abstained"),
            ({"status": "answer", "claims": []}, 2, "rejected"),
        ):
            with self.subTest(expected_status=expected_status):
                self.draft.write_text(json.dumps(proposal), encoding="utf-8")
                output = []
                self.assertEqual(run_knowledge_ask(self.args("--draft", str(self.draft)), output=output.append), expected_code)
                self.assertEqual(json.loads(output[0])["status"], expected_status)

    def test_dense_draft_requires_separate_embedding_opt_in(self) -> None:
        with patch("voren.cli.embedding_provider_from_env") as provider:
            with self.assertRaisesRegex(ValueError, "requires --allow-embedding-api"):
                run_knowledge_ask(self.args("--draft", str(self.draft), "--mode", "dense"))
            provider.assert_not_called()
        with self.assertRaisesRegex(ValueError, "requires dense or hybrid"):
            run_knowledge_ask(self.args("--draft", str(self.draft), "--allow-embedding-api"))

    def test_oversized_or_unreadable_draft_does_not_initialize_model(self) -> None:
        for content in (b"x" * 64_001, b"\xff"):
            with self.subTest(size=len(content)):
                self.draft.write_bytes(content)
                with patch("voren.cli._resolve_run_model") as provider:
                    with self.assertRaises(ValueError):
                        run_knowledge_ask(self.args("--draft", str(self.draft)))
                    provider.assert_not_called()


if __name__ == "__main__":
    unittest.main()
