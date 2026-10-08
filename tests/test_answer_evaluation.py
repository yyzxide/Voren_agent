from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from voren.knowledge.answer_evaluation import (
    AnswerEvaluationArtifact, AnswerEvaluationDataset, AnswerProposal, AnswerProposals,
    HumanSupportReview, answer_model_inputs, evaluate_answers, load_answer_dataset,
    prepare_answers, proposal_template, read_answer_evaluation, read_answer_proposals,
    read_prepared_answers, write_answer_evaluation, write_answer_proposals, write_prepared_answers,
)


class AnswerEvaluationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repository = Path(__file__).resolve().parents[1]
        cls.dataset_path = cls.repository / "docs/fixtures/knowledge-answers.json"
        cls.dataset = load_answer_dataset(cls.dataset_path)
        cls.prepared = prepare_answers(cls.dataset, code_revision="test-source", code_dirty=True)

    def proposals(self, prepared=None, *, review=False) -> AnswerProposals:
        prepared = prepared or self.prepared
        owner = prepared.cases[0]
        hit = owner.hits[0]
        quote = "Mira owns the Atlas deployment checklist."
        citation = {"hit_id": hit.chunk_id, "quote": quote, "start": 0, "end": len(quote)}
        texts = {
            "owner": json.dumps({"status": "answer", "claims": [{"text": "Mira owns the checklist.", "citations": [citation]}]}),
            "pager": json.dumps({"status": "abstain", "claims": [], "reason": "The number is missing."}),
            "irrelevant-quote": json.dumps({"status": "answer", "claims": [{"text": "The emergency pager is 555-0199.", "citations": [citation]}]}),
            # Duplicate key is kept in the original text, not parsed/reencoded.
            "invalid-proposal": '{"status":"answer","status":"abstain","claims":[]}',
            "empty-window": "not-json-and-not-used",
        }
        rows = []
        for case in prepared.cases:
            human = HumanSupportReview()
            if review and case.case_id == "owner":
                human = HumanSupportReview(label="supported", reviewer="fixture-author")
            if review and case.case_id == "irrelevant-quote":
                human = HumanSupportReview(label="unsupported", reviewer="fixture-author")
            rows.append(AnswerProposal(
                case_id=case.case_id, corpus_digest=prepared.corpus.digest,
                retrieval_window_digest=case.retrieval_window_digest,
                proposal_text=texts[case.case_id], human_support=human,
            ))
        return AnswerProposals(origin="scripted", description="Authored protocol examples; not real-model quality evidence.", cases=tuple(rows))

    def redigest(self, artifact: AnswerEvaluationArtifact) -> AnswerEvaluationArtifact:
        return artifact.model_copy(update={"artifact_digest": artifact.calculated_digest()})

    def test_prepare_is_repeatable_and_keeps_exact_evidence(self) -> None:
        other = prepare_answers(self.dataset)
        self.prepared.assert_integrity()
        self.assertEqual(self.prepared.dataset_digest, other.dataset_digest)
        self.assertEqual(self.prepared.corpus, other.corpus)
        self.assertEqual(self.prepared.cases, other.cases)
        self.assertTrue(self.prepared.dataset.synthetic)
        self.assertEqual(self.prepared.settings.retrieval_mode, "bm25")
        self.assertFalse(self.prepared.cases[-1].hits)

    def test_model_input_export_has_evidence_and_no_truth_or_review_labels(self) -> None:
        values = self.dataset.model_dump(mode="json")
        private_ids = ("private-supported-answer", "private-missing-answer", "private-irrelevant-quote", "private-invalid-proposal", "private-empty-window")
        for case, identifier in zip(values["cases"], private_ids, strict=True):
            case["case_id"] = identifier
        prepared = prepare_answers(AnswerEvaluationDataset.model_validate(values))
        payload = answer_model_inputs(prepared)
        self.assertEqual(payload["prepared_artifact_digest"], prepared.artifact_digest)
        self.assertEqual(payload["cases"][0]["hits"][0]["hit_id"], prepared.cases[0].hits[0].chunk_id)
        self.assertEqual(payload["cases"][0]["retrieval_window_digest"], prepared.cases[0].retrieval_window_digest)
        self.assertEqual(payload["cases"][0]["case_ref"], prepared.cases[0].case_ref)
        self.assertEqual(len({case["case_ref"] for case in payload["cases"]}), len(prepared.cases))
        self.assertIn("claims", payload["proposal_schema"]["properties"])
        forbidden = {"case_id", "answerable", "human_support", "reviewer", "expected", "expected_status", "dataset", "documents"}
        def check(value):
            if isinstance(value, dict):
                self.assertFalse(set(value).intersection(forbidden))
                for nested in value.values():
                    check(nested)
            elif isinstance(value, list):
                for nested in value:
                    check(nested)
        check(payload)
        exported = json.dumps(payload)
        for identifier in private_ids:
            self.assertNotIn(identifier, exported)

    def test_protocol_states_and_human_review_have_separate_denominators(self) -> None:
        artifact = evaluate_answers(self.prepared, self.proposals(review=True))
        artifact.assert_integrity()
        summary = artifact.summary
        self.assertEqual(summary.status_counts, {"answered": 2, "abstained": 2, "rejected": 1, "failed": 0, "cancelled": 0})
        self.assertEqual(summary.no_answer_case_count, 4)
        self.assertEqual(summary.no_answer_abstained_count, 2)
        self.assertEqual(summary.no_answer_unresolved_count, 1)
        self.assertEqual(summary.no_answer_abstention_rate, .5)
        self.assertEqual(summary.reviewed_answer_count, 2)
        self.assertEqual(summary.human_supported_fraction, .5)
        unrelated = artifact.cases[2]
        self.assertEqual(unrelated.result.citation_integrity, "verified")
        self.assertEqual(unrelated.result.semantic_support, "unverified")
        self.assertEqual(unrelated.human_support.label, "unsupported")
        self.assertEqual(artifact.proposals.origin, "scripted")
        self.assertFalse(artifact.external_calls)

    def test_unreviewed_answers_do_not_enter_semantic_denominator(self) -> None:
        artifact = evaluate_answers(self.prepared, self.proposals())
        self.assertEqual(artifact.summary.reviewed_answer_count, 0)
        self.assertEqual(artifact.summary.unreviewed_answer_count, 2)
        self.assertIsNone(artifact.summary.human_supported_fraction)
        other = evaluate_answers(self.prepared, self.proposals())
        self.assertEqual(artifact.decision_digest, other.decision_digest)

    def test_duplicate_proposal_json_keys_are_preserved_and_rejected(self) -> None:
        artifact = evaluate_answers(self.prepared, self.proposals())
        raw = artifact.proposals.cases[3].proposal_text
        self.assertEqual(raw, '{"status":"answer","status":"abstain","claims":[]}')
        self.assertEqual(artifact.cases[3].result.status, "rejected")
        self.assertEqual(artifact.cases[3].result.error_code, "invalid_answer_proposal")
        self.assertEqual(artifact.cases[-1].result.status, "abstained")
        self.assertEqual(artifact.cases[-1].result.usage.requests_attempted, 0)

    def test_raw_utf8_byte_budget_failure_is_not_counted_as_abstention(self) -> None:
        original = self.proposals()
        oversized = original.cases[1].model_copy(update={"proposal_text": "文" * 22_000})
        changed = original.model_copy(update={"cases": (original.cases[0], oversized, *original.cases[2:])})
        artifact = evaluate_answers(self.prepared, changed)
        self.assertEqual(artifact.cases[1].result.status, "rejected")
        self.assertEqual(artifact.cases[1].result.error_code, "proposal_budget_exceeded")
        self.assertEqual(artifact.summary.no_answer_abstained_count, 1)
        self.assertEqual(artifact.proposals.cases[1].proposal_text, "文" * 22_000)

    def test_custom_unicode_sources_use_normalized_absolute_character_offsets(self) -> None:
        dataset = AnswerEvaluationDataset.model_validate({
            "dataset_id": "unicode-source", "description": "Authored Unicode offset test.", "synthetic": True,
            "documents": [{"document_id": "note:release", "title": "发布", "source_uri": "fixture://unicode",
                           "content": "  希德负责周五发布。联系电话没有记录。  "}],
            "cases": [{"case_id": "owner", "question": "发布 负责", "answerable": True}],
        })
        prepared = prepare_answers(dataset)
        quote = "希德负责周五发布。"
        text = json.dumps({"status": "answer", "claims": [{"text": "发布负责人是希德。", "citations": [{
            "hit_id": prepared.cases[0].hits[0].chunk_id, "quote": quote, "start": 0, "end": len(quote),
        }]}]}, ensure_ascii=False)
        proposals = AnswerProposals(origin="manual", description="Authored Unicode proposal.", cases=(AnswerProposal(
            case_id="owner", corpus_digest=prepared.corpus.digest,
            retrieval_window_digest=prepared.cases[0].retrieval_window_digest, proposal_text=text,
        ),))
        artifact = evaluate_answers(prepared, proposals)
        self.assertEqual(artifact.cases[0].result.status, "answered")
        self.assertEqual(artifact.cases[0].result.claims[0].citations[0].end, len(quote))
        self.assertEqual(artifact.summary.answerable_answer_rate, 1)
        self.assertIsNone(artifact.summary.no_answer_abstention_rate)
        self.assertIsNone(artifact.summary.human_supported_fraction)

    def test_blank_templates_are_invalid_and_do_not_manufacture_no_answer_success(self) -> None:
        artifact = evaluate_answers(self.prepared, proposal_template(self.prepared))
        self.assertEqual(artifact.summary.status_counts["rejected"], 4)
        self.assertEqual(artifact.summary.no_answer_abstention_rate, .25)
        self.assertEqual(artifact.summary.no_answer_unresolved_count, 3)

    def test_stale_corpus_and_stale_windows_fail_before_metrics(self) -> None:
        original = self.proposals()
        for field in ("corpus_digest", "retrieval_window_digest"):
            with self.subTest(field=field):
                row = original.cases[0].model_copy(update={field: "0" * 64})
                proposals = original.model_copy(update={"cases": (row, *original.cases[1:])})
                with self.assertRaisesRegex(ValueError, "digest differs"):
                    evaluate_answers(self.prepared, proposals)
        updated_data = self.dataset.model_dump(mode="json")
        updated_data["documents"][0]["content"] = "Nora now owns the Atlas deployment checklist."
        newer = prepare_answers(AnswerEvaluationDataset.model_validate(updated_data))
        with self.assertRaisesRegex(ValueError, "corpus digest differs"):
            evaluate_answers(newer, original)

    def test_missing_extra_and_duplicate_proposal_cases_are_input_errors(self) -> None:
        original = self.proposals()
        with self.assertRaisesRegex(ValueError, "case IDs exactly"):
            evaluate_answers(self.prepared, original.model_copy(update={"cases": original.cases[:-1]}))
        duplicate = original.model_dump(mode="json")
        duplicate["cases"].append(duplicate["cases"][0])
        with self.assertRaisesRegex(ValidationError, "case IDs must be unique"):
            AnswerProposals.model_validate(duplicate)
        extra = original.model_dump(mode="json")
        extra["cases"][0]["case_id"] = "unexpected"
        with self.assertRaisesRegex(ValueError, "case IDs exactly"):
            evaluate_answers(self.prepared, AnswerProposals.model_validate(extra))

    def test_human_reviews_require_identity_and_answered_protocol_status(self) -> None:
        for values in ({"label": "supported"}, {"label": "unsupported", "reviewer": " "},
                       {"label": "unreviewed", "reviewer": "Someone"}):
            with self.subTest(values=values):
                with self.assertRaises(ValidationError):
                    HumanSupportReview(**values)
        proposals = self.proposals()
        changed = proposals.cases[1].model_copy(update={"human_support": HumanSupportReview(label="supported", reviewer="author")})
        with self.assertRaisesRegex(ValueError, "require an answered"):
            evaluate_answers(self.prepared, proposals.model_copy(update={"cases": (proposals.cases[0], changed, *proposals.cases[2:])}))

    def test_forged_prepared_window_fails_even_after_recomputing_hash(self) -> None:
        changed_case = self.prepared.cases[0].model_copy(update={"retrieval_window_digest": "0" * 64})
        changed = self.prepared.model_copy(update={"cases": (changed_case, *self.prepared.cases[1:])})
        changed = changed.model_copy(update={"artifact_digest": changed.calculated_digest()})
        with self.assertRaisesRegex(ValueError, "retrieval windows differ"):
            changed.assert_integrity()

    def test_tampered_stats_and_results_fail_replay_after_recomputing_hash(self) -> None:
        artifact = evaluate_answers(self.prepared, self.proposals())
        changed = artifact.model_copy(update={"summary": artifact.summary.model_copy(update={"no_answer_abstention_rate": 1.0})})
        with self.assertRaisesRegex(ValueError, "summary differs"):
            self.redigest(changed).assert_integrity()
        changed_case = artifact.cases[1].model_copy(update={"result": artifact.cases[0].result})
        changed = artifact.model_copy(update={"cases": (artifact.cases[0], changed_case, *artifact.cases[2:])})
        with self.assertRaisesRegex(ValueError, "original proposal replay"):
            self.redigest(changed).assert_integrity()

    def test_file_roundtrip_verifies_and_preserves_original_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            prepared_path, proposals_path, output = (directory / name for name in ("prepared.json", "proposals.json", "evaluation.json"))
            write_prepared_answers(prepared_path, self.prepared)
            write_answer_proposals(proposals_path, self.proposals())
            self.assertEqual(read_prepared_answers(prepared_path), self.prepared)
            proposals = read_answer_proposals(proposals_path)
            artifact = evaluate_answers(self.prepared, proposals)
            write_answer_evaluation(output, artifact)
            self.assertEqual(read_answer_evaluation(output), artifact)
            data = json.loads(output.read_text())
            data["summary"]["status_counts"]["rejected"] = 0
            output.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "artifact digest"):
                read_answer_evaluation(output)

    def test_outer_duplicate_keys_and_coerced_answerability_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "proposals.json"
            path.write_text('{"origin":"manual","origin":"external_model"}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate answer evaluation JSON key"):
                read_answer_proposals(path)
        data = self.dataset.model_dump(mode="json")
        data["cases"][0]["answerable"] = "true"
        with self.assertRaises(ValidationError):
            AnswerEvaluationDataset.model_validate(data)

    def test_cli_prepare_evaluate_verify_works_without_provider_configuration(self) -> None:
        script = self.repository / "scripts/evaluate_knowledge_answers.py"
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            prepared_path, proposals_path, output = (directory / name for name in ("prepared.json", "proposals.json", "evaluation.json"))
            model_inputs = directory / "model-inputs.json"
            command = [sys.executable, str(script)]
            prepared_result = subprocess.run([*command, "prepare", "--dataset", str(self.dataset_path), "--output", str(prepared_path),
                                              "--proposals-output", str(proposals_path), "--model-inputs-output", str(model_inputs)],
                                             cwd=self.repository, capture_output=True, text=True, timeout=30)
            self.assertEqual(prepared_result.returncode, 0, prepared_result.stderr)
            self.assertFalse(json.loads(prepared_result.stdout)["external_calls"])
            self.assertNotIn("answerable", model_inputs.read_text(encoding="utf-8"))
            prepared = read_prepared_answers(prepared_path)
            write_answer_proposals(proposals_path, self.proposals(prepared))
            evaluated = subprocess.run([*command, "evaluate", "--prepared", str(prepared_path), "--proposals", str(proposals_path),
                                        "--output", str(output)], cwd=self.repository, capture_output=True, text=True, timeout=30)
            self.assertEqual(evaluated.returncode, 0, evaluated.stderr)
            self.assertEqual(json.loads(evaluated.stdout)["summary"]["status_counts"]["rejected"], 1)
            verified = subprocess.run([*command, "verify", str(output)], cwd=self.repository, capture_output=True, text=True, timeout=30)
            self.assertEqual(verified.returncode, 0, verified.stderr)


if __name__ == "__main__":
    unittest.main()
