from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from voren.knowledge.evaluation import (
    ExpectedEvidence,
    FixtureDocument,
    RetrievalCase,
    RetrievalDataset,
    evaluate_retrieval,
    load_retrieval_dataset,
    measure_case,
    read_retrieval_evaluation,
    summarize_cases,
    write_retrieval_evaluation,
)
from voren.knowledge.models import KnowledgeSearchHit


class RetrievalEvaluationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repository = Path(__file__).resolve().parents[1]
        cls.dataset_path = cls.repository / "examples" / "knowledge-retrieval" / "controlled-bilingual.json"

    def document(self, document_id: str, content: str):
        return FixtureDocument(document_id=document_id, title=document_id, content=content).as_document()

    def hit(self, document, *, snippet: str | None = None, **changes) -> KnowledgeSearchHit:
        values = {
            "ref": document.ref,
            "title": document.title,
            "source_uri": document.source_uri,
            "source_kind": document.source_kind,
            "content_digest": document.content_digest,
            "snippet": snippet or document.content,
            "score": 1,
        }
        values.update(changes)
        return KnowledgeSearchHit(**values)

    def test_document_recall_deduplicates_chunks_and_evidence_requires_actual_text(self) -> None:
        first = self.document("note:first", "Introduction. The recovery owner is Rowan.")
        second = self.document("note:second", "The calendar hold has no attendees.")
        unrelated = self.document("note:other", "A training session is on Monday.")
        case = RetrievalCase(
            case_id="multi",
            category="multiple-sources",
            query="recovery owner calendar attendees",
            expected_document_ids=("note:first", "note:second"),
            expected_evidence=(ExpectedEvidence(document_id="note:first", text="The recovery owner is Rowan."),),
        )
        result = measure_case(
            case,
            (self.hit(unrelated), self.hit(first, snippet="Introduction."), self.hit(first, snippet="Introduction.")),
            active_documents={d.ref.document_id: d for d in (first, second, unrelated)},
            k=3,
            latency_ms=1,
        )
        self.assertEqual(result.document_recall_at_k, 0.5)
        self.assertEqual(result.reciprocal_rank, 0.5)
        self.assertFalse(result.evidence_hit)
        self.assertIsNone(result.no_answer_false_positive)

    def test_no_answer_false_positives_have_their_own_denominator(self) -> None:
        document = self.document("note:first", "Monday meeting")
        active = {document.ref.document_id: document}
        answer = RetrievalCase(case_id="answer", category="positive", query="Monday", expected_document_ids=("note:first",))
        absent = RetrievalCase(case_id="absent", category="negative", query="unknown", expected_document_ids=())
        noisy = RetrievalCase(case_id="noisy", category="negative", query="Monday phone", expected_document_ids=())
        cases = (
            measure_case(answer, (self.hit(document),), active_documents=active, k=1, latency_ms=1),
            measure_case(absent, (), active_documents=active, k=1, latency_ms=2),
            measure_case(noisy, (self.hit(document),), active_documents=active, k=1, latency_ms=3),
        )
        summary = summarize_cases(cases)
        self.assertEqual(summary.answer_case_count, 1)
        self.assertEqual(summary.no_answer_case_count, 2)
        self.assertEqual(summary.recall_at_k, 1)
        self.assertEqual(summary.mrr, 1)
        self.assertEqual(summary.no_answer_false_positive_rate, 0.5)
        self.assertIsNone(summary.evidence_hit_rate)

    def test_inactive_source_version_is_not_credited_as_retrieval_or_evidence(self) -> None:
        old = self.document("note:updated", "The launch is Wednesday.")
        active = self.document("note:updated", "The launch is Thursday.")
        case = RetrievalCase(
            case_id="updated", category="correction", query="launch",
            expected_document_ids=("note:updated",),
            expected_evidence=(ExpectedEvidence(document_id="note:updated", text="The launch is Thursday."),),
        )
        result = measure_case(case, (self.hit(old),), active_documents={active.ref.document_id: active}, k=1, latency_ms=0)
        self.assertFalse(result.results[0].citation_valid)
        self.assertIn("source version differs from the active fixture version", result.results[0].citation_errors)
        self.assertEqual(result.document_recall_at_k, 0)
        self.assertEqual(result.reciprocal_rank, 0)
        self.assertFalse(result.evidence_hit)

    def test_fabricated_snippet_cannot_make_an_evidence_hit(self) -> None:
        document = self.document("note:first", "The release is Monday. No phone number is recorded.")
        case = RetrievalCase(case_id="phone", category="positive", query="release", expected_document_ids=("note:first",), expected_evidence=(ExpectedEvidence(document_id="note:first", text="The release is Monday."),))
        result = measure_case(case, (self.hit(document, snippet="The release is Monday. Phone: 123456."),), active_documents={document.ref.document_id: document}, k=1, latency_ms=0)
        self.assertFalse(result.results[0].citation_valid)
        self.assertFalse(result.evidence_hit)

    def test_empty_ellipsis_snippet_and_forged_chunk_identity_are_not_valid_citations(self) -> None:
        document = self.document("note:first", "The release is Monday.")
        case = RetrievalCase(case_id="release", category="positive", query="release", expected_document_ids=("note:first",))
        hits = (
            self.hit(document, snippet="……"),
            # Bypass the hit validator to exercise the evaluator's own source
            # check; normal structured hits also reject this forged identity.
            self.hit(document).model_copy(update={
                "retrieval_method": "bm25",
                "chunk_id": "f" * 64,
                "chunk_start": 0,
                "chunk_end": len(document.content),
                "chunk_digest": hashlib.sha256(document.content.encode("utf-8")).hexdigest(),
            }),
        )
        result = measure_case(case, hits[:1], active_documents={document.ref.document_id: document}, k=2, latency_ms=0)
        self.assertFalse(result.results[0].citation_valid)
        self.assertEqual(result.document_recall_at_k, 0)
        with self.assertRaisesRegex(ValidationError, "chunk ID"):
            measure_case(case, hits[1:], active_documents={document.ref.document_id: document}, k=2, latency_ms=0)

    def test_fixture_labels_reject_inactive_docs_and_nonexistent_evidence(self) -> None:
        fixture = load_retrieval_dataset(self.dataset_path).model_dump(mode="json")
        fixture["cases"][0]["expected_document_ids"] = ["meeting:inactive"]
        fixture["cases"][0]["expected_evidence"] = []
        with self.assertRaisesRegex(ValidationError, "active fixture versions"):
            RetrievalDataset.model_validate(fixture)
        fixture = load_retrieval_dataset(self.dataset_path).model_dump(mode="json")
        fixture["cases"][0]["expected_evidence"][0]["text"] = "This quotation is fabricated."
        with self.assertRaisesRegex(ValidationError, "active source content"):
            RetrievalDataset.model_validate(fixture)

    def test_default_evaluation_ignores_a_supplied_embedding_provider_and_has_repeatable_ranks(self) -> None:
        class NeverCalledEmbedder:
            fingerprint = "unused-provider"

            def embed(self, texts):
                raise AssertionError("default evaluation must not call an embedding provider")

        dataset = load_retrieval_dataset(self.dataset_path)
        first = evaluate_retrieval(dataset, k=3, embedder=NeverCalledEmbedder())
        second = evaluate_retrieval(dataset, k=3)
        first.assert_integrity()
        self.assertEqual(first.case_count, 28)
        self.assertEqual(first.active_document_count, 20)
        self.assertEqual(first.document_version_count, 22)
        self.assertEqual(first.ranking_digest, second.ranking_digest)
        self.assertEqual(first.dataset_digest, second.dataset_digest)
        self.assertTrue(first.synthetic)
        self.assertIsNone(first.embedding_fingerprint)
        for evaluation in first.evaluations:
            self.assertEqual(evaluation.summary.citation_version_correctness, 1)
            self.assertEqual(evaluation.summary.answer_case_count, 22)
            self.assertEqual(evaluation.summary.no_answer_case_count, 6)
        baseline_tail = next(case for case in first.evaluations[0].cases if case.case_id == "en-long-tail")
        self.assertEqual(baseline_tail.document_recall_at_k, 1)
        self.assertFalse(baseline_tail.evidence_hit)
        chunked_tail = next(case for case in first.evaluations[1].cases if case.case_id == "en-long-tail")
        self.assertTrue(chunked_tail.evidence_hit)

    def test_explicit_dense_evaluation_indexes_only_the_fixture_with_a_test_double(self) -> None:
        class ConstantTestEmbedder:
            fingerprint = "constant-test-double-not-semantic-evidence"

            def __init__(self):
                self.calls = 0
                self.requests_attempted = 0
                self.prompt_tokens = 0
                self.usage_complete = True

            def embed(self, texts):
                self.calls += 1
                self.requests_attempted += 1
                self.prompt_tokens += len(texts)
                return tuple((1.0, 0.0) for _ in texts)

        embedder = ConstantTestEmbedder()
        artifact = evaluate_retrieval(load_retrieval_dataset(self.dataset_path), modes=("dense", "hybrid"), embedder=embedder, k=2)
        self.assertGreater(embedder.calls, 1)
        self.assertEqual(artifact.embedding_fingerprint, embedder.fingerprint)
        self.assertGreater(artifact.embedding_usage["corpus_indexing"].requests_attempted, 0)
        for mode in ("dense", "hybrid"):
            self.assertEqual(artifact.embedding_usage[f"queries:{mode}"].requests_attempted, 28)
            self.assertEqual(artifact.embedding_usage[f"queries:{mode}"].prompt_tokens, 28)
        self.assertTrue(all(mode.summary.citation_version_correctness == 1 for mode in artifact.evaluations))
        dense = artifact.evaluations[0].summary
        self.assertEqual(dense.no_answer_false_positive_rate, 1)
        with self.assertRaisesRegex(ValueError, "explicit embedding provider"):
            evaluate_retrieval(load_retrieval_dataset(self.dataset_path), modes=("dense",))

    def test_artifact_roundtrip_and_tampered_metric_rejection(self) -> None:
        artifact = evaluate_retrieval(load_retrieval_dataset(self.dataset_path), modes=("bm25",), k=3)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "nested" / "retrieval.json"
            write_retrieval_evaluation(path, artifact)
            self.assertEqual(read_retrieval_evaluation(path), artifact)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["evaluations"][0]["summary"]["mrr"] = 0.123
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "artifact digest"):
                read_retrieval_evaluation(path)
        mode = artifact.evaluations[0]
        changed_case = mode.cases[0].model_copy(update={"document_recall_at_k": 0})
        changed_cases = (changed_case, *mode.cases[1:])
        changed_mode = mode.model_copy(update={"cases": changed_cases, "summary": summarize_cases(changed_cases)})
        changed_artifact = artifact.model_copy(update={"evaluations": (changed_mode,)})
        changed_artifact = changed_artifact.model_copy(update={"artifact_digest": changed_artifact.calculated_digest()})
        with self.assertRaisesRegex(ValueError, "case metrics"):
            changed_artifact.assert_integrity()

    def test_script_defaults_to_local_modes_and_requires_explicit_api_opt_in(self) -> None:
        environment = dict(os.environ)
        environment.update({"VOREN_EMBEDDING_ENDPOINT": "https://invalid.example/embeddings", "VOREN_EMBEDDING_MODEL": "never-called", "VOREN_EMBEDDING_API_KEY": "fixture-placeholder"})
        script = self.repository / "scripts" / "evaluate_knowledge_retrieval.py"
        with tempfile.TemporaryDirectory() as temporary:
            completed = subprocess.run([sys.executable, str(script), "--output", str(Path(temporary) / "result.json")], cwd=self.repository, env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            summary = json.loads(completed.stdout)
            self.assertEqual([mode["mode"] for mode in summary["evaluations"]], ["lexical", "bm25"])
            self.assertFalse(summary["embedding_api_allowed"])
            blocked = subprocess.run([sys.executable, str(script), "--mode", "dense"], cwd=self.repository, env=environment, capture_output=True, text=True, timeout=10)
            self.assertEqual(blocked.returncode, 2)
            self.assertIn("--allow-embedding-api", blocked.stderr)


if __name__ == "__main__":
    unittest.main()
