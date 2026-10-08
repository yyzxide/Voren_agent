#!/usr/bin/env python3
"""Evaluate a controlled retrieval fixture; default modes make no API calls."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from voren.evaluation.artifacts import detect_source_revision
from voren.knowledge.embeddings import EmbeddingError
from voren.knowledge.evaluation import (
    evaluate_retrieval,
    load_retrieval_dataset,
    write_retrieval_evaluation,
)
from voren.knowledge.store import KnowledgeStoreError


def main() -> int:
    repository = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=repository / "examples" / "knowledge-retrieval" / "controlled-bilingual.json")
    parser.add_argument("--mode", action="append", choices=("lexical", "bm25", "dense", "hybrid"), help="repeat for multiple modes; defaults to lexical and bm25")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--output", type=Path, default=Path(".voren/artifacts/knowledge-retrieval.json"))
    parser.add_argument("--allow-embedding-api", action="store_true", help="explicitly allow dense/hybrid requests to the configured VOREN_EMBEDDING_* endpoint; may incur cost")
    args = parser.parse_args()
    modes = tuple(args.mode or ("lexical", "bm25"))
    uses_embeddings = bool({"dense", "hybrid"}.intersection(modes))
    if uses_embeddings and not args.allow_embedding_api:
        parser.error("dense/hybrid require --allow-embedding-api and explicit VOREN_EMBEDDING_* configuration")
    if args.allow_embedding_api and not uses_embeddings:
        parser.error("--allow-embedding-api only applies to dense/hybrid evaluation")
    try:
        # Importing/configuring the external provider happens only after opt-in.
        embedder = None
        if uses_embeddings:
            from voren.knowledge.embeddings import embedding_provider_from_env
            embedder = embedding_provider_from_env()
        dataset = load_retrieval_dataset(args.dataset)
        revision = detect_source_revision(repository)
        artifact = evaluate_retrieval(
            dataset,
            modes=modes,
            k=args.k,
            embedder=embedder,
            code_revision=revision.revision,
            code_dirty=revision.dirty,
            dataset_file_digest=hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        )
        write_retrieval_evaluation(args.output, artifact)
    except (OSError, ValueError, EmbeddingError, KnowledgeStoreError) as error:
        # Embedding errors expose sanitized local messages, never response bodies.
        parser.exit(2, f"retrieval evaluation failed: {error}\n")
    print(json.dumps({
        "artifact": str(args.output.resolve()),
        "artifact_digest": artifact.artifact_digest,
        "dataset_id": artifact.dataset_id,
        "dataset_digest": artifact.dataset_digest,
        "ranking_digest": artifact.ranking_digest,
        "synthetic": artifact.synthetic,
        "k": artifact.k,
        "case_count": artifact.case_count,
        "active_document_count": artifact.active_document_count,
        "embedding_api_allowed": args.allow_embedding_api,
        "embedding_usage": (
            {stage: usage.model_dump(mode="json") for stage, usage in artifact.embedding_usage.items()}
            if artifact.embedding_usage is not None else None
        ),
        "evaluations": [
            {"mode": evaluation.mode, **evaluation.summary.model_dump(mode="json")}
            for evaluation in artifact.evaluations
        ],
        "limitations": artifact.limitations,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
