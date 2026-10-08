from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from pydantic import ValidationError

from voren.knowledge.answers import (
    AnswerCitation,
    KnowledgeAnswerDraft,
    KnowledgeAnswerService,
    MAX_PROPOSAL_BYTES,
)
from voren.knowledge.embeddings import HTTPEmbeddingProvider
from voren.knowledge.models import KnowledgeDocument, KnowledgeSourceKind
from voren.knowledge.store import SQLiteKnowledgeStore
from voren.runtime.cancellation import CancellationToken, ModelRequestCancelled
from voren.runtime.models import CancellationReason, ModelResponse, ModelUsage, ToolCall
from voren.testing.scripted_model import ScriptedModelAdapter


class KnowledgeAnswersTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.store = SQLiteKnowledgeStore(Path(self.temporary_directory.name) / "knowledge.sqlite3")
        self.addCleanup(self.store.close)
        self.document = self.make_document(
            "meeting:atlas", "The Atlas release is Friday at 09:30. Mira owns the checklist.",
        )
        self.install_active(self.document)
        self.question = "Atlas release"

    def make_document(self, document_id: str, content: str) -> KnowledgeDocument:
        return KnowledgeDocument.create(
            document_id=document_id, title="Reviewed release note",
            source_uri=f"meeting://{document_id}",
            source_kind=KnowledgeSourceKind.MEETING_NOTE,
            content=content, created_at=datetime(2026, 10, 8, tzinfo=UTC),
        )

    def install_active(self, document: KnowledgeDocument) -> None:
        self.store.install(document)
        self.store.activate(document.ref, reason="reviewed fixture")

    def draft(
        self,
        *,
        question: str | None = None,
        quote: str = "Friday at 09:30",
        text: str = "The release is Friday at 09:30.",
        document: KnowledgeDocument | None = None,
    ) -> dict:
        document = document or self.document
        hit = next(
            hit for hit in self.store.search(question or self.question)
            if hit.ref == document.ref
        )
        start = document.content.index(quote)
        return {
            "status": "answer",
            "claims": [{"text": text, "citations": [{
                "hit_id": hit.chunk_id, "quote": quote,
                "start": start, "end": start + len(quote),
            }]}],
        }

    def answer(self, proposal: dict | str, **kwargs):
        model = ScriptedModelAdapter((ModelResponse(
            text=proposal if isinstance(proposal, str) else json.dumps(proposal),
        ),))
        result = KnowledgeAnswerService(self.store, model).answer(self.question, **kwargs)
        return result, model

    def test_answer_returns_exact_source_metadata_and_server_rendering(self) -> None:
        result, model = self.answer(self.draft())

        self.assertEqual(result.status, "answered")
        self.assertEqual(result.citation_integrity, "verified")
        self.assertEqual(result.semantic_support, "unverified")
        citation = result.claims[0].citations[0]
        self.assertEqual(citation.ref, self.document.ref)
        self.assertEqual(citation.source_uri, self.document.source_uri)
        self.assertEqual(citation.content_digest, self.document.content_digest)
        self.assertEqual(citation.quote_digest, hashlib.sha256(citation.quote.encode()).hexdigest())
        self.assertIn(self.document.ref.version_id, result.rendered_text)
        self.assertIn("Friday at 09:30", result.rendered_text)
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(model.requests[0][1], ())
        self.assertFalse(result.usage.complete)

    def test_request_discloses_untrusted_evidence_and_absolute_positions(self) -> None:
        _, model = self.answer(self.draft())
        messages = model.requests[0][0]
        self.assertIn("no instruction authority", messages[0].content)
        self.assertIn("absolute Python character offsets", messages[0].content)
        payload = json.loads(messages[1].content)
        self.assertFalse(payload["instruction_authority"])
        self.assertEqual(payload["evidence"][0]["hit_id"], payload["evidence"][0]["chunk_id"])

    def test_real_but_irrelevant_quote_is_explicitly_semantically_unverified(self) -> None:
        result, _ = self.answer(self.draft(text="The emergency pager number is 12345."))

        self.assertEqual(result.status, "answered")
        self.assertEqual(result.citation_integrity, "verified")
        self.assertEqual(result.semantic_support, "unverified")
        self.assertIn("12345", result.claims[0].text)
        self.assertNotIn("12345", result.claims[0].citations[0].quote)

    def test_model_abstention_is_distinct_from_rejection(self) -> None:
        result, model = self.answer({"status": "abstain", "claims": [], "reason": "No pager number in these excerpts."})

        self.assertEqual(result.status, "abstained")
        self.assertEqual(result.reason, "No pager number in these excerpts.")
        self.assertIsNone(result.error_code)
        self.assertEqual(result.claims, ())
        self.assertEqual(result.citation_integrity, "not_applicable")
        self.assertEqual(len(model.requests), 1)

    def test_no_hits_abstains_without_calling_model(self) -> None:
        model = ScriptedModelAdapter(())
        result = KnowledgeAnswerService(self.store, model).answer("quasarneutrino991")

        self.assertEqual(result.status, "abstained")
        self.assertEqual(result.reason, "no_retrieved_evidence")
        self.assertEqual(result.usage.requests_attempted, 0)
        self.assertTrue(result.usage.complete)
        self.assertEqual(model.requests, [])
        self.assertIsNotNone(result.corpus)

    def test_empty_pinned_snapshot_remains_empty_after_activation(self) -> None:
        empty_store = SQLiteKnowledgeStore(Path(self.temporary_directory.name) / "empty.sqlite3")
        self.addCleanup(empty_store.close)
        corpus = empty_store.snapshot_corpus()
        empty_store.install(self.document)
        empty_store.activate(self.document.ref, reason="later import")
        model = ScriptedModelAdapter(())

        result = KnowledgeAnswerService(empty_store, model, corpus=corpus).answer(self.question)

        self.assertEqual(result.status, "abstained")
        self.assertEqual(result.corpus.refs, ())
        self.assertEqual(model.requests, [])

    def test_pinned_versions_survive_source_activation(self) -> None:
        draft = self.draft()
        corpus = self.store.snapshot_corpus()
        revised = self.make_document("meeting:atlas", "The Atlas release is Monday at 14:00.")
        self.install_active(revised)
        model = ScriptedModelAdapter((ModelResponse(text=json.dumps(draft)),))

        result = KnowledgeAnswerService(self.store, model, corpus=corpus).answer(self.question)

        self.assertEqual(result.status, "answered")
        self.assertEqual(result.corpus, corpus)
        self.assertEqual(result.claims[0].citations[0].ref, self.document.ref)
        self.assertNotEqual(self.store.get_active("meeting:atlas").ref, result.hits[0].ref)

    def test_corpus_is_frozen_before_model_and_recaptured_on_next_request(self) -> None:
        draft = self.draft()
        revised = self.make_document("meeting:atlas", "The Atlas release is Monday at 14:00.")
        store = self.store

        class ActivatingModel:
            def complete(self, **kwargs):
                store.install(revised)
                store.activate(revised.ref, reason="during model request")
                return ModelResponse(text=json.dumps(draft))

        service = KnowledgeAnswerService(self.store, ActivatingModel())
        first = service.answer(self.question)
        second = service.answer(self.question)

        self.assertEqual(first.status, "answered")
        self.assertEqual(first.corpus.refs, (self.document.ref,))
        self.assertEqual(second.corpus.refs, (revised.ref,))
        self.assertEqual(second.status, "rejected")
        self.assertEqual(second.error_code, "citation_outside_retrieval")

    def test_long_document_quotes_use_absolute_not_snippet_relative_offsets(self) -> None:
        document = self.make_document(
            "meeting:long", "Routine office status.\n" * 60 + "Zephyr recovery owner is Rowan.",
        )
        self.install_active(document)
        question = "Zephyr recovery owner Rowan"
        draft = self.draft(question=question, quote="Rowan", document=document)
        model = ScriptedModelAdapter((ModelResponse(text=json.dumps(draft)),))

        result = KnowledgeAnswerService(self.store, model).answer(question)

        self.assertEqual(result.status, "answered")
        self.assertGreater(result.hits[0].chunk_start, 600)
        self.assertEqual(result.claims[0].citations[0].start, document.content.index("Rowan"))

    def test_chinese_quote_offsets_are_characters_not_utf8_bytes(self) -> None:
        document = self.make_document("meeting:cn", "会议决定周五下午三点，负责人是林舟。")
        self.install_active(document)
        draft = self.draft(question="会议负责人", quote="林舟", document=document)
        model = ScriptedModelAdapter((ModelResponse(text=json.dumps(draft)),))

        result = KnowledgeAnswerService(self.store, model).answer("会议负责人")

        citation = result.claims[0].citations[0]
        self.assertEqual(result.status, "answered")
        self.assertEqual(citation.start, document.content.index("林舟"))
        self.assertEqual(citation.end - citation.start, 2)

    def test_citation_outside_retrieval_window_is_rejected(self) -> None:
        draft = self.draft()
        draft["claims"][0]["citations"][0]["hit_id"] = "0" * 64
        result, _ = self.answer(draft)

        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.error_code, "citation_outside_retrieval")
        self.assertEqual(result.claims, ())

    def test_quote_mismatch_with_valid_length_is_rejected(self) -> None:
        draft = self.draft()
        draft["claims"][0]["citations"][0]["quote"] = "Monday at 14:00"
        result, _ = self.answer(draft)

        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.error_code, "citation_quote_mismatch")

    def test_quote_in_document_but_outside_selected_chunk_is_rejected(self) -> None:
        document = self.make_document(
            "meeting:long", "First section owner is Alder.\n" + "Routine office status.\n" * 60 + "Zephyr recovery owner is Rowan.",
        )
        self.install_active(document)
        question = "Zephyr recovery owner Rowan"
        draft = self.draft(question=question, quote="Alder", document=document)
        model = ScriptedModelAdapter((ModelResponse(text=json.dumps(draft)),))

        result = KnowledgeAnswerService(self.store, model).answer(question)

        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.error_code, "citation_outside_chunk")

    def test_schema_rejects_uncited_claims_extra_metadata_and_invalid_positions(self) -> None:
        variants = [
            {"status": "answer", "claims": []},
            {"status": "answer", "claims": [{"text": "Friday", "citations": []}]},
            {"status": "abstain", "claims": self.draft()["claims"]},
            {**self.draft(), "source_uri": "https://invented.example"},
        ]
        for bad_start in (True, "24", -1):
            draft = self.draft()
            draft["claims"][0]["citations"][0]["start"] = bad_start
            variants.append(draft)
        draft = self.draft()
        draft["claims"][0]["citations"][0]["quote"] = ""
        variants.append(draft)
        draft = self.draft()
        draft["claims"][0]["text"] = " "
        variants.append(draft)
        draft = self.draft()
        draft["claims"][0]["citations"][0]["source_uri"] = "https://invented.example"
        variants.append(draft)

        for proposal in variants:
            with self.subTest(proposal=proposal):
                result, model = self.answer(proposal)
                self.assertEqual(result.status, "rejected")
                self.assertEqual(result.error_code, "invalid_answer_proposal")
                self.assertEqual(len(model.requests), 1)

    def test_duplicate_json_keys_and_markdown_are_rejected(self) -> None:
        for proposal in (
            '{"status":"answer","status":"abstain","claims":[]}',
            '```json\n{"status":"abstain","claims":[]}\n```',
            '{"status":"abstain","claims":[],"reason":{"x":1,"x":2}}',
            "not json",
            "\ud800",
        ):
            with self.subTest(proposal=repr(proposal)):
                result, _ = self.answer(proposal)
                self.assertEqual(result.status, "rejected")
                self.assertEqual(result.error_code, "invalid_answer_proposal")

    def test_nested_draft_models_are_revalidated(self) -> None:
        draft = KnowledgeAnswerDraft.model_validate(self.draft())
        citation = draft.claims[0].citations[0].model_copy(update={"quote": "forged"})
        claim = draft.claims[0].model_copy(update={"citations": (citation,)})
        invalid = draft.model_copy(update={"claims": (claim,)})

        with self.assertRaises(ValidationError):
            KnowledgeAnswerDraft.model_validate(invalid)
        with self.assertRaises(ValidationError):
            AnswerCitation.model_validate(citation)

    def test_model_tool_call_is_rejected_and_never_executed(self) -> None:
        model = ScriptedModelAdapter((ModelResponse(tool_calls=(ToolCall(
            call_id="invented", name="send_email", arguments={},
        ),)),))

        result = KnowledgeAnswerService(self.store, model).answer(self.question)

        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.error_code, "unexpected_model_tool_calls")
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(model.requests[0][1], ())

    def test_proposal_budget_rejects_without_retry(self) -> None:
        result, model = self.answer("x" * (MAX_PROPOSAL_BYTES + 1))

        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.error_code, "proposal_budget_exceeded")
        self.assertEqual(result.usage.requests_attempted, 1)
        self.assertEqual(len(model.requests), 1)

    def test_evidence_budget_prevents_model_request(self) -> None:
        with patch("voren.knowledge.answers.MAX_EVIDENCE_BYTES", 100):
            result, model = self.answer(self.draft())

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "evidence_budget_exceeded")
        self.assertEqual(result.usage.requests_attempted, 0)
        self.assertEqual(model.requests, [])

    def test_usage_is_reported_even_for_rejected_proposal(self) -> None:
        model = ScriptedModelAdapter((ModelResponse(
            text="not json", usage=ModelUsage(input_tokens=30, output_tokens=5, total_tokens=35),
        ),))

        result = KnowledgeAnswerService(self.store, model).answer(self.question)

        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.usage.requests_attempted, 1)
        self.assertEqual(result.usage.reported_model_requests, 1)
        self.assertEqual(result.usage.total_tokens, 35)
        self.assertTrue(result.usage.complete)

    def test_model_failure_is_sanitized_and_not_retried(self) -> None:
        class FailingModel:
            def __init__(self):
                self.calls = 0

            def complete(self, **kwargs):
                self.calls += 1
                raise RuntimeError("private endpoint credential")

        model = FailingModel()
        result = KnowledgeAnswerService(self.store, model).answer(self.question)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "model_request_failed")
        self.assertEqual(result.usage.requests_attempted, 1)
        self.assertEqual(model.calls, 1)
        self.assertNotIn("credential", result.model_dump_json())

    def test_precancelled_request_does_not_retrieve_or_generate(self) -> None:
        token = CancellationToken()
        token.cancel()
        model = ScriptedModelAdapter(())
        with patch.object(self.store, "snapshot_corpus", side_effect=AssertionError("must not retrieve")):
            result = KnowledgeAnswerService(self.store, model).answer(self.question, cancellation=token)

        self.assertEqual(result.status, "cancelled")
        self.assertEqual(result.usage.requests_attempted, 0)
        self.assertEqual(model.requests, [])

    def test_cancelled_provider_usage_is_preserved(self) -> None:
        class CancellingModel:
            def complete(self, **kwargs):
                raise ModelRequestCancelled(
                    reason=CancellationReason.OPERATOR, provider_confirmed=True,
                    usage=ModelUsage(input_tokens=3, output_tokens=2, total_tokens=5),
                )

        result = KnowledgeAnswerService(self.store, CancellingModel()).answer(self.question)

        self.assertEqual(result.status, "cancelled")
        self.assertEqual(result.usage.total_tokens, 5)
        self.assertTrue(result.usage.complete)

    def test_cancellation_after_completed_model_suppresses_answer(self) -> None:
        token = CancellationToken()
        draft = self.draft()

        class CancellingModel:
            def complete(self, **kwargs):
                token.cancel()
                return ModelResponse(text=json.dumps(draft))

        result = KnowledgeAnswerService(self.store, CancellingModel()).answer(self.question, cancellation=token)

        self.assertEqual(result.status, "cancelled")
        self.assertEqual(result.claims, ())
        self.assertEqual(result.usage.requests_attempted, 1)

    def test_missing_frozen_source_fails_before_model(self) -> None:
        corpus = self.store.snapshot_corpus()
        model = ScriptedModelAdapter(())
        self.store._connection.execute("DELETE FROM active_knowledge_versions")
        self.store._connection.execute("DELETE FROM knowledge_versions")
        self.store._connection.commit()

        result = KnowledgeAnswerService(self.store, model, corpus=corpus).answer(self.question)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "knowledge_retrieval_failed")
        self.assertEqual(model.requests, [])

    def test_internally_consistent_forged_hit_is_checked_against_actual_source(self) -> None:
        hit = self.store.search(self.question)[0]
        forged_snippet = "X" * len(hit.snippet)
        chunk_digest = hashlib.sha256(forged_snippet.encode()).hexdigest()
        identity = json.dumps({
            "version_id": hit.ref.version_id, "start": hit.chunk_start,
            "end": hit.chunk_end, "content_digest": chunk_digest,
        }, sort_keys=True, separators=(",", ":"))
        forged = hit.model_copy(update={
            "snippet": forged_snippet, "chunk_digest": chunk_digest,
            "chunk_id": hashlib.sha256(identity.encode()).hexdigest(),
        })
        model = ScriptedModelAdapter(())
        with patch.object(self.store, "search", return_value=(forged,)):
            result = KnowledgeAnswerService(self.store, model).answer(self.question)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "invalid_retrieval_hit")
        self.assertEqual(result.hits, ())
        self.assertEqual(model.requests, [])

    def test_question_limit_and_legacy_mode_are_validated(self) -> None:
        model = ScriptedModelAdapter(())
        service = KnowledgeAnswerService(self.store, model)
        for question in ("", " ", "x" * 501, "\ud800", None):
            with self.subTest(question=repr(question)), self.assertRaises(ValueError):
                service.answer(question)
        for limit in (0, 21, True, "5"):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                service.answer(self.question, limit=limit)
        with self.assertRaises(ValueError):
            KnowledgeAnswerService(self.store, model, mode="lexical")
        self.assertEqual(model.requests, [])

    def embedding_provider(self, **kwargs) -> HTTPEmbeddingProvider:
        return HTTPEmbeddingProvider(
            endpoint="http://127.0.0.1:12345/v1/embeddings", model="test-model", **kwargs,
        )

    def embedding_transport(self, *, tokens: int | None = 7):
        opener = MagicMock()

        def open_response(request, **kwargs):
            count = len(json.loads(request.data)["input"])
            payload = {
                "model": "test-model",
                "data": [{"index": index, "embedding": [1.0, 0.0]} for index in range(count)],
            }
            if tokens is not None:
                payload["usage"] = {"prompt_tokens": tokens}
            response = Mock()
            response.read.return_value = json.dumps(payload).encode()
            manager = MagicMock()
            manager.__enter__.return_value = response
            return manager

        opener.open.side_effect = open_response
        return patch("voren.knowledge.embeddings.build_opener", return_value=opener), opener

    def test_query_embedding_usage_excludes_previous_indexing_and_model_usage(self) -> None:
        for mode in ("dense", "hybrid"):
            with self.subTest(mode=mode):
                provider = self.embedding_provider()
                transport, opener = self.embedding_transport()
                draft = self.draft()
                model = ScriptedModelAdapter((ModelResponse(
                    text=json.dumps(draft), usage=ModelUsage(input_tokens=3, output_tokens=2, total_tokens=5),
                ),))
                with transport:
                    self.store.index_embeddings(provider)
                    # Each subtest explicitly spends an earlier unrelated request,
                    # even if the persistent fixture index already exists.
                    provider.embed(("earlier unrelated indexing request",))
                    before_attempts = provider.requests_attempted
                    before_tokens = provider.prompt_tokens
                    result = KnowledgeAnswerService(self.store, model, mode=mode, embedder=provider).answer(self.question)

                self.assertEqual(result.status, "answered")
                self.assertEqual(result.embedding_usage.requests_attempted, 1)
                self.assertEqual(result.embedding_usage.prompt_tokens, 7)
                self.assertTrue(result.embedding_usage.usage_complete)
                self.assertEqual(provider.requests_attempted - before_attempts, 1)
                self.assertEqual(provider.prompt_tokens - before_tokens, 7)
                self.assertGreater(opener.open.call_count, 1)
                self.assertEqual(result.usage.requests_attempted, 1)
                self.assertEqual(result.usage.total_tokens, 5)
                self.assertEqual(json.loads(result.model_dump_json())["embedding_usage"]["prompt_tokens"], 7)

    def test_embedding_timeout_records_attempt_before_failed_answer(self) -> None:
        provider = self.embedding_provider()
        transport, _ = self.embedding_transport()
        with transport:
            self.store.index_embeddings(provider)
        opener = Mock()
        opener.open.side_effect = TimeoutError("private endpoint text")
        model = ScriptedModelAdapter(())

        with patch("voren.knowledge.embeddings.build_opener", return_value=opener):
            result = KnowledgeAnswerService(self.store, model, mode="dense", embedder=provider).answer(self.question)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.embedding_usage.requests_attempted, 1)
        self.assertEqual(result.embedding_usage.prompt_tokens, 0)
        self.assertFalse(result.embedding_usage.usage_complete)
        self.assertEqual(result.usage.requests_attempted, 0)
        self.assertEqual(model.requests, [])
        self.assertNotIn("private endpoint", result.model_dump_json())

    def test_embedding_budget_blocked_query_records_no_new_attempt(self) -> None:
        provider = self.embedding_provider(max_requests=1)
        transport, _ = self.embedding_transport()
        with transport:
            self.store.index_embeddings(provider)
        # Earlier incomplete usage must not contaminate a stage that sends
        # no new requests at all.
        provider.usage_complete = False
        model = ScriptedModelAdapter(())
        with patch("voren.knowledge.embeddings.build_opener") as opener:
            result = KnowledgeAnswerService(self.store, model, mode="dense", embedder=provider).answer(self.question)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.embedding_usage.requests_attempted, 0)
        self.assertEqual(result.embedding_usage.prompt_tokens, 0)
        self.assertTrue(result.embedding_usage.usage_complete)
        self.assertEqual(result.usage.requests_attempted, 0)
        opener.assert_not_called()

    def test_unmetered_custom_embedding_provider_reports_unknown(self) -> None:
        class UnmeteredProvider:
            fingerprint = "f" * 64

            def embed(self, texts):
                return tuple((1.0, 0.0) for _ in texts)

        provider = UnmeteredProvider()
        self.store.index_embeddings(provider)
        model = ScriptedModelAdapter((ModelResponse(text=json.dumps(self.draft())),))
        result = KnowledgeAnswerService(self.store, model, mode="dense", embedder=provider).answer(self.question)

        self.assertEqual(result.status, "answered")
        self.assertIsNone(result.embedding_usage.requests_attempted)
        self.assertIsNone(result.embedding_usage.prompt_tokens)
        self.assertFalse(result.embedding_usage.usage_complete)
        dumped = json.loads(result.model_dump_json())
        self.assertIsNone(dumped["embedding_usage"]["prompt_tokens"])

    def test_bm25_records_no_embedding_usage_even_with_previously_used_provider(self) -> None:
        provider = self.embedding_provider()
        provider.requests_attempted = 12
        provider.prompt_tokens = 150
        provider.usage_complete = False
        model = ScriptedModelAdapter((ModelResponse(text=json.dumps(self.draft())),))
        with patch("voren.knowledge.embeddings.build_opener") as opener:
            result = KnowledgeAnswerService(self.store, model, embedder=provider).answer(self.question)

        self.assertEqual(result.status, "answered")
        self.assertEqual(result.embedding_usage.requests_attempted, 0)
        self.assertEqual(result.embedding_usage.prompt_tokens, 0)
        self.assertTrue(result.embedding_usage.usage_complete)
        opener.assert_not_called()

    def test_missing_embedding_token_report_is_not_measured_zero(self) -> None:
        provider = self.embedding_provider()
        index_transport, _ = self.embedding_transport()
        with index_transport:
            self.store.index_embeddings(provider)
        query_transport, _ = self.embedding_transport(tokens=None)
        model = ScriptedModelAdapter((ModelResponse(text=json.dumps(self.draft())),))

        with query_transport:
            result = KnowledgeAnswerService(self.store, model, mode="dense", embedder=provider).answer(self.question)

        self.assertEqual(result.status, "answered")
        self.assertEqual(result.embedding_usage.requests_attempted, 1)
        self.assertEqual(result.embedding_usage.prompt_tokens, 0)
        self.assertFalse(result.embedding_usage.usage_complete)


if __name__ == "__main__":
    unittest.main()
