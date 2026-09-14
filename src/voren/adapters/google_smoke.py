"""Redacted, integrity-checked evidence for a live Google Workspace smoke suite."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from voren.runs.models import RunEvent, RunEventType, RunRecord, RunStatus
from voren.runtime.models import RuntimeUsage


GOOGLE_SMOKE_SCHEMA_VERSION = "voren-google-workspace-smoke/v1"
GOOGLE_LIVE_CONNECTOR_BOUNDARY = "environment_google_rest"
GOOGLE_LIVE_MODEL_BOUNDARY = "environment_responses_api"
REQUIRED_GOOGLE_READ_TOOLS = frozenset(
    {"search_emails", "get_day_calendar_events"}
)
REQUIRED_GOOGLE_ACTIONS = frozenset(
    {"create_email_draft", "create_private_calendar_event"}
)
GOOGLE_SMOKE_ACTION_CONTRACTS = {
    "create_email_draft": ("search_emails", "gmail.drafts", "create"),
    "create_private_calendar_event": (
        "get_day_calendar_events",
        "google.calendar.events",
        "create",
    ),
}


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GoogleSmokeActionEvidence(FrozenModel):
    """Non-content metadata proving one approved exact-effect boundary."""

    action_name: str = Field(min_length=1)
    action_version: str = Field(min_length=1)
    proposal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    grounded_observation_count: int = Field(ge=1)
    effect_count: int = Field(ge=1)
    effect_resources: tuple[str, ...]
    effect_kinds: tuple[str, ...]
    approved: bool
    receipt_status: str = Field(min_length=1)
    committed: bool
    verification_passed: bool
    recovered_after_ambiguous_commit: bool


class GoogleSmokeRunEvidence(FrozenModel):
    """A deliberately redacted projection of one durable Agent run."""

    run_id: str = Field(min_length=1)
    run_status: str = Field(min_length=1)
    run_config_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    started_at: datetime
    completed_at: datetime
    code_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    code_dirty: bool
    provider: str = Field(min_length=1)
    requested_model: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    response_models: tuple[str | None, ...]
    successful_read_tools: tuple[str, ...]
    model_usage: RuntimeUsage
    action: GoogleSmokeActionEvidence | None = None
    event_stream_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class GoogleSmokeCoverage(FrozenModel):
    required_read_tools: tuple[str, ...]
    observed_read_tools: tuple[str, ...]
    required_actions: tuple[str, ...]
    observed_actions: tuple[str, ...]
    all_runs_completed: bool
    all_actions_grounded: bool
    all_actions_approved_and_verified: bool


class GoogleSmokeArtifact(FrozenModel):
    """A passing live-suite artifact; invalid or incomplete suites are rejected."""

    schema_version: str = GOOGLE_SMOKE_SCHEMA_VERSION
    created_at: datetime
    runs: tuple[GoogleSmokeRunEvidence, ...]
    coverage: GoogleSmokeCoverage
    artifact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def suite_is_complete_and_live(self) -> Self:
        if self.schema_version != GOOGLE_SMOKE_SCHEMA_VERSION:
            raise ValueError("unsupported Google smoke artifact schema")
        run_ids = tuple(run.run_id for run in self.runs)
        if not run_ids or len(run_ids) != len(set(run_ids)):
            raise ValueError("Google smoke run IDs must be non-empty and unique")
        if not self.coverage.all_runs_completed:
            raise ValueError("every Google smoke run must complete")
        if not self.coverage.all_actions_grounded:
            raise ValueError("every Google smoke action must be grounded in a read")
        if not self.coverage.all_actions_approved_and_verified:
            raise ValueError("every Google smoke action must be approved and verified")
        if not REQUIRED_GOOGLE_READ_TOOLS.issubset(
            self.coverage.observed_read_tools
        ):
            raise ValueError("Google smoke suite does not cover every required read")
        if not REQUIRED_GOOGLE_ACTIONS.issubset(self.coverage.observed_actions):
            raise ValueError("Google smoke suite does not cover every required action")
        revisions = {(run.code_revision, run.code_dirty) for run in self.runs}
        if len(revisions) != 1 or next(iter(revisions))[1]:
            raise ValueError("Google smoke runs must use one clean source revision")
        boundaries = {
            (run.provider, run.requested_model, run.endpoint) for run in self.runs
        }
        if len(boundaries) != 1:
            raise ValueError("Google smoke runs must use one exact model boundary")
        return self

    @classmethod
    def create(
        cls,
        *,
        runs: tuple[GoogleSmokeRunEvidence, ...],
        created_at: datetime | None = None,
    ) -> Self:
        reads = tuple(
            sorted({tool for run in runs for tool in run.successful_read_tools})
        )
        actions = tuple(
            sorted(run.action.action_name for run in runs if run.action is not None)
        )
        action_evidence = tuple(run.action for run in runs if run.action is not None)
        coverage = GoogleSmokeCoverage(
            required_read_tools=tuple(sorted(REQUIRED_GOOGLE_READ_TOOLS)),
            observed_read_tools=reads,
            required_actions=tuple(sorted(REQUIRED_GOOGLE_ACTIONS)),
            observed_actions=actions,
            all_runs_completed=all(
                run.run_status == RunStatus.COMPLETED.value for run in runs
            ),
            all_actions_grounded=bool(action_evidence)
            and all(action.grounded_observation_count > 0 for action in action_evidence),
            all_actions_approved_and_verified=bool(action_evidence)
            and all(
                action.approved
                and action.committed
                and action.receipt_status == "verified"
                and action.verification_passed
                for action in action_evidence
            ),
        )
        unsigned = cls(
            schema_version=GOOGLE_SMOKE_SCHEMA_VERSION,
            created_at=created_at or datetime.now(UTC),
            runs=runs,
            coverage=coverage,
            artifact_digest="0" * 64,
        )
        return unsigned.model_copy(
            update={"artifact_digest": unsigned.calculated_digest()}
        )

    def calculated_digest(self) -> str:
        return _digest(
            self.model_dump(mode="json", exclude={"artifact_digest"})
        )

    def assert_integrity(self) -> None:
        if self.artifact_digest != self.calculated_digest():
            raise ValueError("Google smoke artifact digest does not match its content")


def collect_google_smoke_run(
    run: RunRecord,
    events: tuple[RunEvent, ...],
) -> GoogleSmokeRunEvidence:
    """Project a durable live run into redacted evidence and reject test doubles."""

    metadata = run.config.metadata
    if run.config.workflow != "google_email_calendar":
        raise ValueError(f"run {run.run_id!r} is not a Google workspace run")
    if run.config.world_adapter != "google_workspace_rest_v1":
        raise ValueError(f"run {run.run_id!r} has the wrong Google adapter")
    if metadata.get("google_connector_boundary") != GOOGLE_LIVE_CONNECTOR_BOUNDARY:
        raise ValueError(f"run {run.run_id!r} did not use the live Google boundary")
    if metadata.get("model_adapter_boundary") != GOOGLE_LIVE_MODEL_BOUNDARY:
        raise ValueError(f"run {run.run_id!r} did not use the live model boundary")

    successful_reads = tuple(
        sorted(
            {
                str(event.payload["tool_name"])
                for event in events
                if event.event_type is RunEventType.TOOL_OBSERVED
                and event.payload.get("status") == "succeeded"
                and event.payload.get("tool_name") in REQUIRED_GOOGLE_READ_TOOLS
            }
        )
    )
    response_events = tuple(
        event for event in events if event.event_type is RunEventType.MODEL_RESPONDED
    )
    usage = _sum_usage(response_events)
    action = _collect_action(events)
    terminal = events[-1] if events else None
    if terminal is None or terminal.event_type is not RunEventType.RUN_COMPLETED:
        raise ValueError(f"run {run.run_id!r} has no completed terminal event")

    event_stream = [
        {
            "sequence": event.sequence,
            "event_type": event.event_type.value,
            "payload": event.payload,
            "occurred_at": event.occurred_at.isoformat(),
        }
        for event in events
    ]
    return GoogleSmokeRunEvidence(
        run_id=run.run_id,
        run_status=run.status.value,
        run_config_digest=run.config_digest,
        started_at=run.created_at,
        completed_at=run.updated_at,
        code_revision=str(metadata.get("code_revision", "")),
        code_dirty=bool(metadata.get("code_dirty", True)),
        provider=str(metadata.get("provider", "")),
        requested_model=str(metadata.get("model", "")),
        endpoint=str(metadata.get("provider_endpoint", "")),
        response_models=tuple(
            event.payload.get("returned_model") for event in response_events
        ),
        successful_read_tools=successful_reads,
        model_usage=usage,
        action=action,
        event_stream_digest=_digest(event_stream),
    )


def write_google_smoke_artifact(
    path: Path,
    artifact: GoogleSmokeArtifact,
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


def read_google_smoke_artifact(path: Path) -> GoogleSmokeArtifact:
    artifact = GoogleSmokeArtifact.model_validate_json(
        path.read_text(encoding="utf-8")
    )
    artifact.assert_integrity()
    return artifact


def _collect_action(
    events: tuple[RunEvent, ...],
) -> GoogleSmokeActionEvidence | None:
    proposed = tuple(
        event for event in events if event.event_type is RunEventType.ACTION_PROPOSED
    )
    if not proposed:
        return None
    if len(proposed) != 1:
        raise ValueError("Google smoke runs must propose at most one external action")
    proposal = proposed[0].payload
    if proposal.get("action_name") not in REQUIRED_GOOGLE_ACTIONS:
        raise ValueError("Google smoke run proposed an unsupported external action")
    approvals = tuple(
        event for event in events if event.event_type is RunEventType.APPROVAL_ACCEPTED
    )
    receipts = tuple(
        event for event in events if event.event_type is RunEventType.ACTION_RECEIPT
    )
    if len(approvals) != 1 or len(receipts) != 1:
        raise ValueError("Google smoke action lacks one approval and one receipt")
    approval = approvals[0].payload
    receipt = receipts[0].payload
    effects = proposal.get("effects")
    evidence_digests = proposal.get("evidence_digests")
    if not isinstance(effects, list) or not isinstance(evidence_digests, list):
        raise ValueError("Google smoke proposal evidence is malformed")
    action_name = str(proposal["action_name"])
    required_read, expected_resource, expected_kind = (
        GOOGLE_SMOKE_ACTION_CONTRACTS[action_name]
    )
    if len(effects) != 1 or (
        effects[0].get("resource"), effects[0].get("kind")
    ) != (expected_resource, expected_kind):
        raise ValueError("Google smoke proposal is outside the safe effect contract")
    successful_observation_digests = {
        str(event.payload.get("observation_digest"))
        for event in events
        if event.event_type is RunEventType.TOOL_OBSERVED
        and event.payload.get("tool_name") == required_read
        and event.payload.get("status") == "succeeded"
    }
    grounded_digests = {
        str(digest)
        for digest in evidence_digests
        if digest in successful_observation_digests
    }
    if not grounded_digests:
        raise ValueError("Google smoke action is not grounded in its required read")
    verification = receipt.get("verification")
    if not isinstance(verification, dict):
        raise ValueError("Google smoke receipt verification is malformed")
    proposal_digest = str(proposal["proposal_digest"])
    if (
        approval.get("proposal_digest") != proposal_digest
        or receipt.get("proposal_digest") != proposal_digest
    ):
        raise ValueError("Google smoke approval/receipt digest chain is broken")
    return GoogleSmokeActionEvidence(
        action_name=action_name,
        action_version=str(proposal["action_version"]),
        proposal_digest=proposal_digest,
        grounded_observation_count=len(grounded_digests),
        effect_count=1,
        effect_resources=(expected_resource,),
        effect_kinds=(expected_kind,),
        approved=approval.get("approved") is True,
        receipt_status=str(receipt.get("status", "")),
        committed=receipt.get("committed") is True,
        verification_passed=verification.get("passed") is True,
        recovered_after_ambiguous_commit=(
            receipt.get("recovered_after_ambiguous_commit") is True
        ),
    )


def _sum_usage(events: tuple[RunEvent, ...]) -> RuntimeUsage:
    totals = {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
        "total_tokens": 0,
    }
    reported = 0
    for event in events:
        raw_usage = event.payload.get("usage")
        if not isinstance(raw_usage, dict):
            continue
        reported += 1
        for field in totals:
            value = raw_usage.get(field, 0)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                totals[field] += value
    return RuntimeUsage(
        **totals,
        model_requests=len(events),
        reported_model_requests=reported,
    )


def _digest(value: Any) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
