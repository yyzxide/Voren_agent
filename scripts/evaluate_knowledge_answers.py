#!/usr/bin/env python3
"""Prepare frozen BM25 evidence, then replay raw answer proposals offline.

    python scripts/evaluate_knowledge_answers.py prepare \
        --dataset docs/fixtures/knowledge-answers.json \
        --output .voren/artifacts/answers-prepared.json \
        --proposals-output .voren/artifacts/answers-proposals.json \
        --model-inputs-output .voren/artifacts/answers-model-inputs.json

Replace each template proposal_text with original model/manual JSON text, declare
origin honestly, and preserve corpus/window digests. The labelled prepared bundle
must not be given to a model under evaluation; model-inputs exports only questions,
evidence, opaque case_ref values, binding digests and the proposal schema. The
case_id-to-case_ref mapping remains in the local labelled preparation bundle.
Use exact quotes and absolute
character offsets from hits. Empty text is an invalid proposal,
not a default abstention. No provider is contacted by either command.

    python scripts/evaluate_knowledge_answers.py evaluate \
        --prepared .voren/artifacts/answers-prepared.json \
        --proposals .voren/artifacts/answers-proposals.json \
        --output .voren/artifacts/answers-evaluation.json

    python scripts/evaluate_knowledge_answers.py verify \
        .voren/artifacts/answers-evaluation.json

Answer/abstain decision rates compare supplied labels. They do not establish
semantic accuracy. Optional human support labels require an identified reviewer.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from voren.evaluation.artifacts import detect_source_revision
from voren.knowledge.answer_evaluation import (
    evaluate_answers, load_answer_dataset, prepare_answers, proposal_template,
    read_answer_evaluation, read_answer_proposals, read_prepared_answers,
    write_answer_evaluation, write_answer_model_inputs, write_answer_proposals, write_prepared_answers,
)
from voren.knowledge.store import KnowledgeStoreError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subcommands = parser.add_subparsers(dest="command", required=True)
    prepare = subcommands.add_parser("prepare", help="Export source-bound evidence and an empty proposal template.")
    prepare.add_argument("--dataset", type=Path, required=True)
    prepare.add_argument("--limit", type=int, default=5)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--proposals-output", type=Path)
    prepare.add_argument("--model-inputs-output", type=Path, help="Export local evidence without answerability or human review labels.")
    evaluate = subcommands.add_parser("evaluate", help="Replay supplied original text; no model or embedding API.")
    evaluate.add_argument("--prepared", type=Path, required=True)
    evaluate.add_argument("--proposals", type=Path, required=True)
    evaluate.add_argument("--output", type=Path, required=True)
    verify = subcommands.add_parser("verify", help="Rebuild windows and replay an artifact to verify its results.")
    verify.add_argument("artifact", type=Path)
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    revision = detect_source_revision(repository)
    try:
        if args.command == "prepare":
            outputs = [path.resolve() for path in (args.output, args.proposals_output, args.model_inputs_output) if path is not None]
            if len(set(outputs)) != len(outputs):
                raise ValueError("prepared evidence, model inputs and proposal template need different output paths")
            if args.dataset.resolve() in outputs:
                raise ValueError("output paths must not overwrite the source dataset")
            prepared = prepare_answers(load_answer_dataset(args.dataset), limit=args.limit,
                                       code_revision=revision.revision, code_dirty=revision.dirty)
            write_prepared_answers(args.output, prepared)
            if args.proposals_output is not None:
                write_answer_proposals(args.proposals_output, proposal_template(prepared))
            if args.model_inputs_output is not None:
                write_answer_model_inputs(args.model_inputs_output, prepared)
            print(json.dumps({
                "prepared": str(args.output.resolve()), "case_count": len(prepared.cases),
                "dataset_digest": prepared.dataset_digest, "corpus_digest": prepared.corpus.digest,
                "artifact_digest": prepared.artifact_digest, "synthetic": prepared.dataset.synthetic,
                "external_calls": False,
                "proposals_template": str(args.proposals_output.resolve()) if args.proposals_output else None,
                "model_inputs": str(args.model_inputs_output.resolve()) if args.model_inputs_output else None,
                "note": "Blank proposal text is invalid until original output is supplied. Decision labels do not prove semantic correctness.",
            }, ensure_ascii=False, indent=2))
        else:
            if args.command == "evaluate":
                if args.output.resolve() in {args.prepared.resolve(), args.proposals.resolve()}:
                    raise ValueError("evaluation output must not overwrite its inputs")
                artifact = evaluate_answers(read_prepared_answers(args.prepared), read_answer_proposals(args.proposals),
                                            code_revision=revision.revision, code_dirty=revision.dirty)
                write_answer_evaluation(args.output, artifact)
                path = args.output
            else:
                artifact = read_answer_evaluation(args.artifact)
                path = args.artifact
            print(json.dumps({
                "artifact": str(path.resolve()), "artifact_digest": artifact.artifact_digest,
                "decision_digest": artifact.decision_digest, "origin": artifact.proposals.origin,
                "synthetic": artifact.prepared.dataset.synthetic, "external_calls": False,
                "summary": artifact.summary.model_dump(mode="json"),
                "metric_definitions": artifact.metric_definitions, "limitations": artifact.limitations,
            }, ensure_ascii=False, indent=2))
    except (OSError, ValueError, KnowledgeStoreError, RecursionError):
        # Raw proposal text and imported source documents can contain secrets;
        # parser errors never print Pydantic input values or source text.
        parser.exit(2, "answer evaluation failed: input validation, evidence binding or replay integrity check failed\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
