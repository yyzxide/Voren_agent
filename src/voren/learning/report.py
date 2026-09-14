"""Human-readable, evidence-linked decision report for one Skill candidate."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from voren.learning.evaluation import CandidateEvaluationArtifact
from voren.learning.evidence import LearningEvidenceArtifact
from voren.learning.lifecycle import CandidateLifecycleEvent
from voren.learning.models import SkillCandidate


def render_candidate_report(
    *,
    candidate: SkillCandidate,
    evidence: tuple[LearningEvidenceArtifact, ...],
    evaluations: tuple[CandidateEvaluationArtifact, ...],
    events: tuple[CandidateLifecycleEvent, ...],
) -> str:
    expected_evidence = tuple(item.evidence_id for item in candidate.evidence)
    if tuple(item.evidence_id for item in evidence) != expected_evidence:
        raise ValueError("report evidence does not match the candidate")
    for item in evidence:
        item.assert_integrity()
    for artifact in evaluations:
        artifact.assert_integrity()
        if artifact.candidate_id != candidate.candidate_id:
            raise ValueError("report evaluation belongs to another candidate")
    if events and events[-1].to_status is not candidate.status:
        raise ValueError("report event chain disagrees with candidate status")

    lines = [
        f"# Skill Candidate Report: `{candidate.candidate_id}`",
        "",
        "## Decision summary",
        "",
        f"- Status: `{candidate.status.value}`",
        f"- Skill: `{candidate.base_ref.name}`",
        f"- Base version: `{candidate.base_ref.version_id}`",
        f"- Candidate version: `{candidate.candidate_ref.version_id}`",
        f"- Diff digest: `{candidate.diff.diff_digest}`",
        (
            "- Evaluation artifact: "
            f"`{candidate.evaluation_artifact_digest or 'not-decided'}`"
        ),
        f"- Decision reason: {_inline(candidate.decision_reason or 'not decided')}",
        "",
        "## Durable evidence",
        "",
        "| Evidence ID | Source | Source ref | Artifact digest | Payload bytes |",
        "|---|---|---|---|---:|",
    ]
    lines.extend(
        "| "
        + " | ".join(
            (
                _cell(item.evidence_id),
                _cell(item.source.value),
                _cell(item.source_ref),
                f"`{item.artifact_digest}`",
                str(len(item.payload.encode("utf-8"))),
            )
        )
        + " |"
        for item in evidence
    )
    lines.extend(("", "Evidence payloads are intentionally omitted from this report."))

    lines.extend(("", "## Paired held-out results", ""))
    if not evaluations:
        lines.append("No decided evaluation is stored for this candidate.")
    for artifact in evaluations:
        lines.extend(
            (
                f"### `{artifact.evaluation_id}`",
                "",
                f"- Suite: `{artifact.suite_id}`",
                f"- Manifest digest: `{artifact.manifest_digest}`",
                f"- Artifact digest: `{artifact.artifact_digest}`",
                "",
                "| Case | Kind | Variant | Utility | Attack success | "
                "Infrastructure error | Run artifact |",
                "|---|---|---|---|---|---|---|",
            )
        )
        for pair in artifact.pairs:
            for label, result in (("base", pair.base), ("candidate", pair.candidate)):
                measurement = result.measurement
                lines.append(
                    "| "
                    + " | ".join(
                        (
                            _cell(pair.case.case_id),
                            pair.case.kind.value,
                            label,
                            _metric(measurement.utility_passed),
                            _metric(measurement.attack_success),
                            _cell(measurement.infrastructure_error_code or "-"),
                            (
                                f"`{measurement.run_artifact_digest}`"
                                if measurement.run_artifact_digest
                                else "-"
                            ),
                        )
                    )
                    + " |"
                )

    lines.extend(
        (
            "",
            "## Candidate instruction diff",
            "",
            f"Changed lines: +{candidate.diff.added_lines} "
            f"-{candidate.diff.removed_lines}",
            "",
        )
    )
    lines.extend(f"    {line}" for line in candidate.diff.unified_diff.splitlines())

    lines.extend(
        (
            "",
            "## Lifecycle audit",
            "",
            "| Seq | Event | Transition | Time | Event digest |",
            "|---:|---|---|---|---|",
        )
    )
    lines.extend(
        "| "
        + " | ".join(
            (
                str(event.sequence),
                event.event_type.value,
                f"{event.from_status.value if event.from_status else '-'} "
                f"-> {event.to_status.value}",
                event.occurred_at.isoformat(),
                f"`{event.event_digest}`",
            )
        )
        + " |"
        for event in events
    )
    lines.extend(
        (
            "",
            "## Interpretation limits",
            "",
            "- This report describes the exact stored artifacts; it does not "
            "upgrade scripted or deterministic trials into live-model evidence.",
            "- Runtime enforcement metrics must be reported separately from raw "
            "agent behavior; blocking an action does not prove injection resistance.",
            "- The local SQLite audit is tamper-evident through digest links, not "
            "externally attested against a privileged database rewrite.",
            "",
        )
    )
    return "\n".join(lines)


def write_candidate_report(path: Path, report: str) -> str:
    digest = hashlib.sha256(report.encode("utf-8")).hexdigest()
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
            temporary.write(report)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None and os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return digest


def _metric(value: bool | None) -> str:
    return "n/a" if value is None else str(value).lower()


def _cell(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def _inline(value: str) -> str:
    return value.replace("\n", " ")
