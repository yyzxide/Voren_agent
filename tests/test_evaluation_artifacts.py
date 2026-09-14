from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from voren.evaluation.artifacts import read_artifact, write_artifact
from voren.evaluation.models import (
    ApprovalOutcome,
    EvaluationCase,
    EvaluationMode,
    EvaluationSelection,
    ExperimentArtifact,
    ExperimentConfig,
    TrialResult,
    digest_json,
    summarize_trials,
)
from voren.runtime.models import RuntimeUsage


class EvaluationArtifactTest(unittest.TestCase):
    @staticmethod
    def config() -> ExperimentConfig:
        return ExperimentConfig(
            experiment_id="experiment-1",
            created_at=datetime(2026, 8, 31, tzinfo=UTC),
            code_revision="test-revision",
            code_dirty=False,
            provider="scripted",
            model="scripted-model",
            endpoint="https://scripted.example/responses",
            manifest_id="manifest-1",
            manifest_digest="1" * 64,
            system_prompt_digest="2" * 64,
            tool_schema_digest="3" * 64,
            dataset="workspace",
            dataset_version="v1.2.2",
            attack_template_version="attack-v1",
            attack_template_digest="4" * 64,
            selected_trials=(
                EvaluationSelection(
                    case_id="attacked-case",
                    mode=EvaluationMode.AGENT_BEHAVIOR,
                ),
            ),
            sampling={"temperature": 0},
        )

    @staticmethod
    def trial(
        *,
        trial_id: str,
        mode: EvaluationMode,
        utility: bool,
        attack: bool,
        outcome: ApprovalOutcome,
    ) -> TrialResult:
        return TrialResult(
            trial_id=trial_id,
            case_id="attacked-case",
            mode=mode,
            user_task_id="user_task_18",
            injection_task_id="injection_task_2",
            injection_vector="email_hiking_injection",
            run_id=f"run-{trial_id}",
            run_status="completed" if outcome is ApprovalOutcome.APPROVED else "cancelled",
            utility_passed=utility,
            attack_success=attack,
            approval_outcome=outcome,
            approval_policy_version="policy-v1",
            proposal_digest="5" * 64,
            receipt_verified=(outcome is ApprovalOutcome.APPROVED),
            model_steps=1,
            tool_calls=1,
            model_usage=RuntimeUsage(model_requests=1),
            pre_state_digest="6" * 64,
            post_state_digest="7" * 64,
            events=(),
        )

    def test_case_requires_injection_task_and_vector_together(self) -> None:
        with self.assertRaises(ValidationError):
            EvaluationCase(
                case_id="incomplete-injection",
                user_task_id="user_task_18",
                injection_task_id="injection_task_2",
                modes=(EvaluationMode.AGENT_BEHAVIOR,),
            )

    def test_summaries_never_mix_behavior_and_enforcement_metrics(self) -> None:
        behavior = self.trial(
            trial_id="behavior",
            mode=EvaluationMode.AGENT_BEHAVIOR,
            utility=False,
            attack=True,
            outcome=ApprovalOutcome.APPROVED,
        )
        enforcement = self.trial(
            trial_id="enforcement",
            mode=EvaluationMode.RUNTIME_ENFORCEMENT,
            utility=False,
            attack=False,
            outcome=ApprovalOutcome.REJECTED,
        )

        summaries = summarize_trials((behavior, enforcement))

        self.assertEqual(len(summaries), 2)
        by_mode = {summary.mode: summary for summary in summaries}
        self.assertEqual(
            by_mode[EvaluationMode.AGENT_BEHAVIOR].attack_success_rate, 1.0
        )
        self.assertEqual(
            by_mode[EvaluationMode.RUNTIME_ENFORCEMENT].attack_success_rate, 0.0
        )
        self.assertEqual(by_mode[EvaluationMode.AGENT_BEHAVIOR].approvals, 1)
        self.assertEqual(by_mode[EvaluationMode.RUNTIME_ENFORCEMENT].rejections, 1)
        self.assertEqual(by_mode[EvaluationMode.AGENT_BEHAVIOR].cancelled_runs, 0)
        self.assertEqual(
            by_mode[EvaluationMode.RUNTIME_ENFORCEMENT].cancelled_runs, 1
        )
        for summary in summaries:
            self.assertEqual(summary.model_usage.model_requests, 1)
            self.assertEqual(summary.model_usage.reported_model_requests, 0)
            self.assertFalse(summary.model_usage.complete)

    def test_artifact_round_trip_checks_integrity_digest(self) -> None:
        artifact = ExperimentArtifact.create(
            config=self.config(),
            trials=(
                self.trial(
                    trial_id="behavior",
                    mode=EvaluationMode.AGENT_BEHAVIOR,
                    utility=False,
                    attack=True,
                    outcome=ApprovalOutcome.APPROVED,
                ),
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "artifact.json"
            write_artifact(path, artifact)

            loaded = read_artifact(path)

            self.assertEqual(loaded, artifact)
            self.assertEqual(loaded.schema_version, "voren-evaluation/v5")
            self.assertIsNone(loaded.trials[0].cancellation_reason)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["trials"][0]["utility_passed"] = True
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                read_artifact(path)

    def test_artifact_rejects_trial_missing_from_frozen_selection(self) -> None:
        with self.assertRaises(ValueError):
            ExperimentArtifact.create(
                config=self.config(),
                trials=(
                    self.trial(
                        trial_id="enforcement",
                        mode=EvaluationMode.RUNTIME_ENFORCEMENT,
                        utility=False,
                        attack=False,
                        outcome=ApprovalOutcome.REJECTED,
                    ),
                ),
            )

    def test_legacy_v3_artifact_without_selection_still_validates(self) -> None:
        artifact = ExperimentArtifact.create(
            config=self.config(),
            trials=(
                self.trial(
                    trial_id="behavior",
                    mode=EvaluationMode.AGENT_BEHAVIOR,
                    utility=False,
                    attack=True,
                    outcome=ApprovalOutcome.APPROVED,
                ),
            ),
        )
        payload = artifact.model_dump(mode="json")
        payload["schema_version"] = "voren-evaluation/v3"
        payload["config"].pop("endpoint")
        payload["config"].pop("selected_trials")
        for trial in payload["trials"]:
            trial.pop("response_models")
        unsigned = {
            key: value for key, value in payload.items() if key != "artifact_digest"
        }
        payload["artifact_digest"] = digest_json(unsigned)

        legacy = ExperimentArtifact.model_validate(payload)

        legacy.assert_integrity()
        self.assertEqual(legacy.schema_version, "voren-evaluation/v3")
        self.assertEqual(legacy.config.selected_trials, ())

    def test_legacy_v4_artifact_without_provider_provenance_still_validates(
        self,
    ) -> None:
        artifact = ExperimentArtifact.create(
            config=self.config(),
            trials=(
                self.trial(
                    trial_id="behavior",
                    mode=EvaluationMode.AGENT_BEHAVIOR,
                    utility=False,
                    attack=True,
                    outcome=ApprovalOutcome.APPROVED,
                ),
            ),
        )
        payload = artifact.model_dump(mode="json")
        payload["schema_version"] = "voren-evaluation/v4"
        payload["config"].pop("endpoint")
        for trial in payload["trials"]:
            trial.pop("response_models")
        unsigned = {
            key: value for key, value in payload.items() if key != "artifact_digest"
        }
        payload["artifact_digest"] = digest_json(unsigned)

        legacy = ExperimentArtifact.model_validate(payload)

        legacy.assert_integrity()
        self.assertEqual(legacy.schema_version, "voren-evaluation/v4")
        self.assertIsNone(legacy.config.endpoint)
        self.assertEqual(legacy.trials[0].response_models, ())

    def test_v5_rejects_response_model_sequence_that_disagrees_with_events(
        self,
    ) -> None:
        trial = self.trial(
            trial_id="behavior",
            mode=EvaluationMode.AGENT_BEHAVIOR,
            utility=False,
            attack=True,
            outcome=ApprovalOutcome.APPROVED,
        )
        payload = trial.model_dump(mode="json")
        payload["response_models"] = ["returned-model"]

        with self.assertRaises(ValueError):
            ExperimentArtifact.create(
                config=self.config(),
                trials=(TrialResult.model_validate(payload),),
            )


if __name__ == "__main__":
    unittest.main()
