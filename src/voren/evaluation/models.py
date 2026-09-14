"""Immutable contracts for reproducible Voren evaluation artifacts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from voren.runtime.models import CancellationReason, RuntimeUsage


EVALUATION_SCHEMA_VERSION = "voren-evaluation/v4"
LEGACY_EVALUATION_SCHEMA_VERSION = "voren-evaluation/v3"


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvaluationMode(StrEnum):
    """The boundary whose behavior a trial is intended to measure."""

    AGENT_BEHAVIOR = "agent_behavior"
    RUNTIME_ENFORCEMENT = "runtime_enforcement"


class ApprovalOutcome(StrEnum):
    NOT_REQUIRED = "not_required"
    APPROVED = "approved"
    REJECTED = "rejected"


class EvaluationCase(FrozenModel):
    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    user_task_id: str = Field(pattern=r"^user_task_[0-9]+$")
    injection_task_id: str | None = Field(
        default=None, pattern=r"^injection_task_[0-9]+$"
    )
    injection_vector: str | None = None
    modes: tuple[EvaluationMode, ...]

    @model_validator(mode="after")
    def injection_and_modes_are_consistent(self) -> Self:
        if (self.injection_task_id is None) != (self.injection_vector is None):
            raise ValueError(
                "injection_task_id and injection_vector must be set together"
            )
        if not self.modes or len(self.modes) != len(set(self.modes)):
            raise ValueError("modes must be non-empty and unique")
        return self


class EvaluationManifest(FrozenModel):
    manifest_id: str = Field(min_length=1)
    dataset: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    attack_template_version: str = Field(min_length=1)
    cases: tuple[EvaluationCase, ...]

    @model_validator(mode="after")
    def case_ids_are_unique(self) -> Self:
        case_ids = [case.case_id for case in self.cases]
        if not self.cases or len(case_ids) != len(set(case_ids)):
            raise ValueError("manifest case IDs must be non-empty and unique")
        return self

    def calculated_digest(self) -> str:
        return _digest(self.model_dump(mode="json"))


class EvaluationSelection(FrozenModel):
    """One exact manifest case/mode pair requested for an experiment."""

    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    mode: EvaluationMode


class ExperimentConfig(FrozenModel):
    experiment_id: str = Field(min_length=1)
    created_at: datetime
    code_revision: str = Field(min_length=1)
    code_dirty: bool
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    manifest_id: str = Field(min_length=1)
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    system_prompt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_schema_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    attack_template_version: str = Field(min_length=1)
    attack_template_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_trials: tuple[EvaluationSelection, ...] = ()
    sampling: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def selected_trial_pairs_are_unique(self) -> Self:
        keys = tuple((item.case_id, item.mode) for item in self.selected_trials)
        if len(keys) != len(set(keys)):
            raise ValueError("selected trial case/mode pairs must be unique")
        return self


class NormalizedRunEvent(FrozenModel):
    sequence: int = Field(gt=0)
    event_type: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)


class TrialResult(FrozenModel):
    trial_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    mode: EvaluationMode
    user_task_id: str = Field(min_length=1)
    injection_task_id: str | None = None
    injection_vector: str | None = None
    run_id: str = Field(min_length=1)
    run_status: str = Field(min_length=1)
    utility_passed: bool
    attack_success: bool | None = None
    approval_outcome: ApprovalOutcome
    approval_policy_version: str = Field(min_length=1)
    proposal_digest: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    receipt_verified: bool | None = None
    model_steps: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    model_usage: RuntimeUsage = Field(default_factory=RuntimeUsage)
    error_code: str | None = None
    error_detail_code: str | None = None
    cancellation_reason: CancellationReason | None = None
    cancellation_confirmed: bool | None = None
    final_output_digest: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    pre_state_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    post_state_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    events: tuple[NormalizedRunEvent, ...]

    @model_validator(mode="after")
    def attack_metric_matches_case(self) -> Self:
        attacked = self.injection_task_id is not None
        if attacked != (self.injection_vector is not None):
            raise ValueError("injection task and vector must be recorded together")
        if attacked != (self.attack_success is not None):
            raise ValueError("attack_success must be recorded only for attacked cases")
        if self.model_usage.model_requests != self.model_steps:
            raise ValueError("trial model usage must match model_steps")
        if self.cancellation_reason is None:
            if self.cancellation_confirmed is not None:
                raise ValueError(
                    "cancellation confirmation requires a cancellation reason"
                )
        elif self.run_status != "cancelled":
            raise ValueError("model cancellation requires cancelled run status")
        return self


class ModeSummary(FrozenModel):
    mode: EvaluationMode
    total_trials: int = Field(ge=0)
    utility_passed: int = Field(ge=0)
    utility_rate: float | None = Field(default=None, ge=0, le=1)
    attacked_trials: int = Field(ge=0)
    attack_successes: int = Field(ge=0)
    attack_success_rate: float | None = Field(default=None, ge=0, le=1)
    approvals: int = Field(ge=0)
    rejections: int = Field(ge=0)
    cancelled_runs: int = Field(ge=0)
    model_usage: RuntimeUsage = Field(default_factory=RuntimeUsage)


class ExperimentArtifact(FrozenModel):
    schema_version: str = EVALUATION_SCHEMA_VERSION
    config: ExperimentConfig
    trials: tuple[TrialResult, ...]
    summaries: tuple[ModeSummary, ...]
    artifact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def trials_match_frozen_selection(self) -> Self:
        trial_ids = tuple(trial.trial_id for trial in self.trials)
        if not trial_ids or len(trial_ids) != len(set(trial_ids)):
            raise ValueError("trial IDs must be non-empty and unique")
        if self.schema_version == EVALUATION_SCHEMA_VERSION:
            if not self.config.selected_trials:
                raise ValueError("v4 artifacts require frozen selected trials")
            actual = tuple(
                EvaluationSelection(case_id=trial.case_id, mode=trial.mode)
                for trial in self.trials
            )
            if actual != self.config.selected_trials:
                raise ValueError(
                    "artifact trials do not match the frozen trial selection"
                )
        elif self.schema_version != LEGACY_EVALUATION_SCHEMA_VERSION:
            raise ValueError("unsupported evaluation artifact schema")
        return self

    @classmethod
    def create(
        cls,
        *,
        config: ExperimentConfig,
        trials: tuple[TrialResult, ...],
    ) -> Self:
        summaries = summarize_trials(trials)
        unsigned = {
            "schema_version": EVALUATION_SCHEMA_VERSION,
            "config": config.model_dump(mode="json"),
            "trials": [trial.model_dump(mode="json") for trial in trials],
            "summaries": [summary.model_dump(mode="json") for summary in summaries],
        }
        return cls(
            **unsigned,
            artifact_digest=_digest(unsigned),
        )

    def calculated_digest(self) -> str:
        unsigned = self.model_dump(mode="json", exclude={"artifact_digest"})
        if (
            self.schema_version == LEGACY_EVALUATION_SCHEMA_VERSION
            and not self.config.selected_trials
        ):
            unsigned["config"].pop("selected_trials", None)
        return _digest(unsigned)

    def assert_integrity(self) -> None:
        if self.artifact_digest != self.calculated_digest():
            raise ValueError("evaluation artifact digest does not match its content")


def summarize_trials(trials: tuple[TrialResult, ...]) -> tuple[ModeSummary, ...]:
    summaries: list[ModeSummary] = []
    for mode in EvaluationMode:
        selected = tuple(trial for trial in trials if trial.mode is mode)
        if not selected:
            continue
        attacked = tuple(
            trial for trial in selected if trial.attack_success is not None
        )
        utility_passed = sum(trial.utility_passed for trial in selected)
        attack_successes = sum(trial.attack_success is True for trial in attacked)
        summaries.append(
            ModeSummary(
                mode=mode,
                total_trials=len(selected),
                utility_passed=utility_passed,
                utility_rate=utility_passed / len(selected),
                attacked_trials=len(attacked),
                attack_successes=attack_successes,
                attack_success_rate=(
                    attack_successes / len(attacked) if attacked else None
                ),
                approvals=sum(
                    trial.approval_outcome is ApprovalOutcome.APPROVED
                    for trial in selected
                ),
                rejections=sum(
                    trial.approval_outcome is ApprovalOutcome.REJECTED
                    for trial in selected
                ),
                cancelled_runs=sum(
                    trial.run_status == "cancelled" for trial in selected
                ),
                model_usage=aggregate_runtime_usage(
                    tuple(trial.model_usage for trial in selected)
                ),
            )
        )
    return tuple(summaries)


def aggregate_runtime_usage(usages: tuple[RuntimeUsage, ...]) -> RuntimeUsage:
    return RuntimeUsage(
        model_requests=sum(usage.model_requests for usage in usages),
        reported_model_requests=sum(
            usage.reported_model_requests for usage in usages
        ),
        input_tokens=sum(usage.input_tokens for usage in usages),
        cached_input_tokens=sum(usage.cached_input_tokens for usage in usages),
        cache_write_input_tokens=sum(
            usage.cache_write_input_tokens for usage in usages
        ),
        output_tokens=sum(usage.output_tokens for usage in usages),
        reasoning_output_tokens=sum(
            usage.reasoning_output_tokens for usage in usages
        ),
        total_tokens=sum(usage.total_tokens for usage in usages),
    )


def digest_json(value: Any) -> str:
    """Public canonical SHA-256 helper used for frozen evaluation inputs."""

    return _digest(value)


def _digest(value: Any) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
