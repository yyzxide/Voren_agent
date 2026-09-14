"""Concrete AgentDojo evaluator for exact Skill-version pairs."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from voren.evaluation.agentdojo import (
    AgentDojoEvaluationRunner,
    evaluation_input_digests,
)
from voren.evaluation.artifacts import write_artifact
from voren.evaluation.models import (
    EvaluationCase,
    EvaluationManifest,
    EvaluationMode,
    EvaluationSelection,
    ExperimentConfig,
)
from voren.learning.evaluation import (
    EvaluationCaseKind,
    HeldOutCase,
    TrialMeasurement,
)
from voren.learning.runner import EvaluationInfrastructureFailure
from voren.runtime.models import CancellationReason, RuntimeLimits
from voren.runtime.ports import ModelAdapter
from voren.skills.context import SkillContextAssembler
from voren.skills.models import SkillVersionRef
from voren.skills.store import SQLiteSkillStore


SkillModelFactory = Callable[
    [SkillVersionRef, EvaluationCase, EvaluationMode], ModelAdapter
]


class AgentDojoSkillEvaluator:
    """Evaluate one held-out case with one exact frozen Skill version."""

    def __init__(
        self,
        *,
        evaluation_id: str,
        skills: SQLiteSkillStore,
        manifest: EvaluationManifest,
        model_factory: SkillModelFactory,
        database_directory: Path,
        artifact_directory: Path,
        provider: str,
        model: str,
        endpoint: str,
        code_revision: str,
        code_dirty: bool,
        sampling: dict[str, object] | None = None,
        limits: RuntimeLimits | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not evaluation_id.strip():
            raise ValueError("evaluation_id must be non-empty")
        self._evaluation_id = evaluation_id
        self._skills = skills
        self._manifest = manifest
        self._cases = {case.case_id: case for case in manifest.cases}
        self._model_factory = model_factory
        self._database_directory = database_directory
        self._artifact_directory = artifact_directory
        self._provider = provider
        self._model = model
        self._endpoint = endpoint
        self._code_revision = code_revision
        self._code_dirty = code_dirty
        self._sampling = sampling or {}
        self._limits = limits
        self._clock = clock or (lambda: datetime.now(UTC))

    def __call__(
        self, held_out: HeldOutCase, ref: SkillVersionRef
    ) -> TrialMeasurement:
        try:
            case = self._cases[held_out.case_id]
        except KeyError as error:
            raise ValueError(
                f"held-out case {held_out.case_id!r} is absent from the manifest"
            ) from error
        expected_kind = (
            EvaluationCaseKind.ATTACK
            if case.injection_task_id is not None
            else EvaluationCaseKind.BENIGN
        )
        if held_out.kind is not expected_kind:
            raise ValueError("held-out case kind disagrees with AgentDojo manifest")
        if EvaluationMode.AGENT_BEHAVIOR not in case.modes:
            raise ValueError(
                "Skill learning requires raw agent_behavior mode for every case"
            )

        context = SkillContextAssembler(self._skills).from_frozen((ref,))
        prompt_digest, tool_digest, attack_digest = evaluation_input_digests(
            context
        )
        ref_short = ref.version_id[:16]
        trial_key = f"{self._evaluation_id}:{case.case_id}:{ref_short}"
        created_at = self._clock()
        config = ExperimentConfig(
            experiment_id=trial_key,
            created_at=created_at,
            code_revision=self._code_revision,
            code_dirty=self._code_dirty,
            provider=self._provider,
            model=self._model,
            endpoint=self._endpoint,
            manifest_id=self._manifest.manifest_id,
            manifest_digest=self._manifest.calculated_digest(),
            system_prompt_digest=prompt_digest,
            tool_schema_digest=tool_digest,
            dataset=self._manifest.dataset,
            dataset_version=self._manifest.dataset_version,
            attack_template_version=self._manifest.attack_template_version,
            attack_template_digest=attack_digest,
            selected_trials=(
                EvaluationSelection(
                    case_id=case.case_id,
                    mode=EvaluationMode.AGENT_BEHAVIOR,
                ),
            ),
            sampling=self._sampling,
        )
        database = self._database_directory / f"{_safe_name(trial_key)}.sqlite3"
        runner = AgentDojoEvaluationRunner(
            model_factory=lambda selected, mode: self._model_factory(
                ref, selected, mode
            ),
            database=database,
            limits=self._limits,
            clock=self._clock,
            skill_context=context,
        )
        artifact = runner.run(
            config=config,
            manifest=self._manifest,
            selections=((case, EvaluationMode.AGENT_BEHAVIOR),),
        )
        artifact_path = self._artifact_directory / (
            f"{_safe_name(trial_key)}-{artifact.artifact_digest}.json"
        )
        write_artifact(artifact_path, artifact)
        trial = artifact.trials[0]
        if trial.error_code in {"model_adapter_failed", "read_adapter_failed"}:
            raise EvaluationInfrastructureFailure(
                trial.error_detail_code or trial.error_code
            )
        if trial.cancellation_reason in {
            CancellationReason.DEADLINE,
            CancellationReason.PROVIDER,
        }:
            raise EvaluationInfrastructureFailure(
                f"cancelled:{trial.cancellation_reason.value}"
            )
        return TrialMeasurement(
            run_id=trial.run_id,
            utility_passed=trial.utility_passed,
            attack_success=trial.attack_success,
            run_artifact_digest=artifact.artifact_digest,
        )


def _safe_name(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in {"-", "_"} else "-"
        for character in value
    )
