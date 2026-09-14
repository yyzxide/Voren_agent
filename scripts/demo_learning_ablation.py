#!/usr/bin/env python3
"""Compare counterfactual direct reflection with Voren's actual learning gate."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from demo_skill_learning import _digest, _write_trial, run_demo
from voren.learning.ablation import compare_learning_policies, write_learning_ablation
from voren.learning.artifacts import write_candidate_evaluation
from voren.learning.evaluation import EvaluationCaseKind, HeldOutCase
from voren.learning.evidence import DurableLearningRouter
from voren.learning.models import CandidateStatus
from voren.learning.runner import PairedEvaluationRunner
from voren.learning.service import SkillCandidateService
from voren.learning.store import SQLiteCandidateStore
from voren.skills.parser import AgentSkillParser
from voren.skills.store import SQLiteSkillStore


def run_ablation(*, repository: Path, output_root: Path) -> dict[str, object]:
    lifecycle = run_demo(repository=repository, output_root=output_root)
    workspace = Path(str(lifecycle["workspace"]))
    database = workspace / "voren.sqlite3"
    trials = workspace / "ablation-trials"
    trials.mkdir()
    parser = AgentSkillParser()
    skills = SQLiteSkillStore(database, root=workspace / "skill-store", parser=parser)
    candidates = SQLiteCandidateStore(database)
    now = datetime.now(UTC)
    try:
        base_ref = skills.freeze_active(("schedule-from-email",))[0]
        evidence = DurableLearningRouter(
            evidence_store=candidates
        ).record_operator_correction(
            evidence_id="operator:demo-unsafe-email-authority",
            correction=(
                repository / "examples" / "skill-learning" / "unsafe-correction.txt"
            ).read_text(encoding="utf-8"),
            operator_ref="operator:ablation-fixture",
            created_at=now,
        )
        service = SkillCandidateService(skills=skills, candidates=candidates)
        unsafe = service.stage(
            candidate_id="candidate:demo-unsafe-email-authority",
            base_ref=base_ref,
            package=parser.load(
                repository
                / "examples"
                / "skill-learning"
                / "unsafe-candidate"
                / "schedule-from-email"
            ),
            evidence=(evidence,),
            created_at=now + timedelta(seconds=1),
        )
        cases = (
            HeldOutCase(
                case_id="heldout-conflicting-threads",
                kind=EvaluationCaseKind.BENIGN,
            ),
            HeldOutCase(
                case_id="heldout-email-injection",
                kind=EvaluationCaseKind.ATTACK,
            ),
        )

        def evaluate(case, ref):
            return _write_trial(
                directory=trials,
                case=case,
                skill_ref=ref,
                instructions=skills.load(ref).instructions,
            )

        evaluation = PairedEvaluationRunner().run(
            evaluation_id="evaluation:demo-unsafe-email-authority",
            candidate=unsafe,
            suite_id="agentdojo-workspace-user-task-18",
            manifest_digest=_digest(
                {
                    "kind": "deterministic_instruction_contract",
                    "cases": [case.model_dump(mode="json") for case in cases],
                }
            ),
            cases=cases,
            evaluator=evaluate,
            created_at=now + timedelta(seconds=2),
        )
        unsafe_evaluation_path = workspace / "unsafe-candidate-evaluation.json"
        write_candidate_evaluation(unsafe_evaluation_path, evaluation)
        rejected = service.decide(
            candidate_id=unsafe.candidate_id,
            artifact=evaluation,
            decided_at=now + timedelta(seconds=3),
        )
        if rejected.status is not CandidateStatus.REJECTED:
            raise RuntimeError("unsafe candidate was not rejected")
        if "security regression" not in (rejected.decision_reason or ""):
            raise RuntimeError("unsafe candidate was rejected for the wrong reason")
        if skills.freeze_active(("schedule-from-email",))[0] != base_ref:
            raise RuntimeError("rejected candidate changed the active Skill")

        helpful = candidates.get("candidate:demo-conflicting-threads")
        helpful_evaluation = candidates.get_evaluation(
            "evaluation:demo-conflicting-threads"
        )
        artifact = compare_learning_policies(
            experiment_id="direct-reflection-vs-voren-gated-demo",
            created_at=now + timedelta(seconds=4),
            candidates=(helpful, rejected),
            evaluations=(helpful_evaluation, evaluation),
        )
        artifact_path = workspace / "learning-ablation.json"
        report_path = workspace / "learning-ablation.md"
        file_digest, report_digest = write_learning_ablation(
            artifact_path=artifact_path,
            report_path=report_path,
            artifact=artifact,
        )
        return {
            "demo_kind": "deterministic_learning_policy_ablation",
            "live_model_evidence": False,
            "direct_reflection_activation_count": (
                artifact.direct_reflection_activation_count
            ),
            "gated_promotion_eligible_count": (
                artifact.gated_promotion_eligible_count
            ),
            "unsafe_activations_avoided": artifact.unsafe_activations_avoided,
            "beneficial_candidates_retained": (
                artifact.beneficial_candidates_retained
            ),
            "unsafe_candidate_status": rejected.status.value,
            "unsafe_candidate_reason": rejected.decision_reason,
            "active_version": base_ref.model_dump(mode="json"),
            "artifact": str(artifact_path),
            "artifact_digest": artifact.artifact_digest,
            "artifact_file_digest": file_digest,
            "report": str(report_path),
            "report_digest": report_digest,
            "workspace": str(workspace),
        }
    finally:
        candidates.close()
        skills.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Voren's credential-free learning-policy ablation."
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(".voren/demos"),
        help="parent directory for a new, non-overwriting demo workspace",
    )
    arguments = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    summary = run_ablation(
        repository=repository,
        output_root=arguments.output_root.resolve(),
    )
    print(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
