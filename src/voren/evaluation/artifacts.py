"""Durable JSON artifacts and local source-revision detection."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from voren.evaluation.models import ExperimentArtifact


@dataclass(frozen=True, slots=True)
class SourceRevision:
    revision: str
    dirty: bool


def detect_source_revision(repository: Path) -> SourceRevision:
    """Return a Git revision without failing an installed/non-Git runtime."""

    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return SourceRevision(revision="unavailable", dirty=False)
    if len(revision) != 40:
        return SourceRevision(revision="unavailable", dirty=bool(status.strip()))
    return SourceRevision(revision=revision, dirty=bool(status.strip()))


def write_artifact(path: Path, artifact: ExperimentArtifact) -> None:
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


def read_artifact(path: Path) -> ExperimentArtifact:
    artifact = ExperimentArtifact.model_validate_json(path.read_text(encoding="utf-8"))
    artifact.assert_integrity()
    return artifact
