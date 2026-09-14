"""Atomic JSON I/O for integrity-bound Skill candidate evaluations."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from voren.learning.evaluation import CandidateEvaluationArtifact


def write_candidate_evaluation(
    path: Path, artifact: CandidateEvaluationArtifact
) -> None:
    artifact.assert_integrity()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            json.dump(
                artifact.model_dump(mode="json"),
                temporary,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def read_candidate_evaluation(path: Path) -> CandidateEvaluationArtifact:
    artifact = CandidateEvaluationArtifact.model_validate_json(
        path.read_text(encoding="utf-8")
    )
    artifact.assert_integrity()
    return artifact
