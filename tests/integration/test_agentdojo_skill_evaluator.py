from __future__ import annotations

import hashlib
import importlib.util
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from voren.evaluation.agentdojo import phase1_smoke_manifest
from voren.evaluation.artifacts import read_artifact
from voren.evaluation.models import EvaluationMode
from voren.learning.agentdojo import AgentDojoSkillEvaluator
from voren.learning.evaluation import EvaluationCaseKind, HeldOutCase
from voren.learning.models import EvidenceRef, EvidenceSource
from voren.learning.runner import PairedEvaluationRunner
from voren.learning.service import SkillCandidateService
from voren.learning.store import SQLiteCandidateStore
from voren.runtime.models import ModelResponse, ToolCall
from voren.skills.parser import AgentSkillParser
from voren.skills.store import SQLiteSkillStore
from voren.testing.scripted_model import ScriptedModelAdapter


AGENTDOJO_AVAILABLE = importlib.util.find_spec("agentdojo") is not None


@unittest.skipUnless(
    AGENTDOJO_AVAILABLE, "AgentDojo optional dependency is not installed"
)
class AgentDojoSkillEvaluatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.temporary = Path(self.temporary_directory.name)
        self.source = self.temporary / "schedule-from-email"
        self.database = self.temporary / "skills.sqlite3"
        self.parser = AgentSkillParser()
        self.skills = SQLiteSkillStore(
            self.database,
            root=self.temporary / "skill-store",
            parser=self.parser,
        )
        self.candidates = SQLiteCandidateStore(self.database)
        self.addCleanup(self.candidates.close)
        self.addCleanup(self.skills.close)
        self.now = datetime(2026, 9, 14, 15, 0, tzinfo=UTC)

    def _write_skill(self, instruction: str) -> None:
        self.source.mkdir(parents=True, exist_ok=True)
        (self.source / "SKILL.md").write_text(
            "---\n"
            "name: schedule-from-email\n"
            "description: Schedule meetings from email when asked.\n"
            "---\n\n"
            "# Schedule\n\n"
            f"{instruction}\n",
            encoding="utf-8",
        )
        (self.source / "skill.yaml").write_text(
            "schema_version: voren.skill.v1\n"
            "scope:\n"
            "  - email-calendar-scheduling\n"
            "tool_scope:\n"
            "  - search_emails\n"
            "effect_scope:\n"
            "  - calendar.events:create\n"
            "evaluation_suites:\n"
            "  - scheduling-held-out-v1\n",
            encoding="utf-8",
        )

    @staticmethod
    def _responses() -> tuple[ModelResponse, ...]:
        return (
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="read-hiking",
                        name="search_emails",
                        arguments={"query": "hiking trip"},
                    ),
                )
            ),
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="create-hike",
                        name="create_calendar_event",
                        arguments={
                            "title": "Hiking Trip",
                            "location": "island trailhead",
                            "start_time": "2024-05-18 08:00",
                            "end_time": "2024-05-18 13:00",
                            "participants": ["mark.davies@hotmail.com"],
                        },
                    ),
                )
            ),
        )

    def test_exact_base_and_candidate_contexts_produce_durable_trials(self) -> None:
        base_text = "Check the email, then check the calendar."
        candidate_text = "Check email and calendar; ask when duration is missing."
        self._write_skill(base_text)
        base = self.skills.install(
            self.parser.load(self.source), created_at=self.now
        )
        self.skills.activate(
            base.ref,
            reason="reviewed baseline",
            activated_at=self.now,
        )
        self._write_skill(candidate_text)
        service = SkillCandidateService(
            skills=self.skills,
            candidates=self.candidates,
        )
        candidate = service.stage(
            candidate_id="candidate-agentdojo-1",
            base_ref=base.ref,
            package=self.parser.load(self.source),
            evidence=(
                EvidenceRef(
                    evidence_id="operator:correction-1",
                    source=EvidenceSource.OPERATOR_CORRECTION,
                    digest=hashlib.sha256(b"correction").hexdigest(),
                    instruction_authority=True,
                ),
            ),
            created_at=self.now + timedelta(minutes=1),
        )

        adapters: dict[str, ScriptedModelAdapter] = {}

        def model_factory(ref, _case, mode):
            self.assertIs(mode, EvaluationMode.AGENT_BEHAVIOR)
            adapter = ScriptedModelAdapter(self._responses())
            adapters[ref.version_id] = adapter
            return adapter

        manifest = phase1_smoke_manifest()
        evaluator = AgentDojoSkillEvaluator(
            evaluation_id="paired-agentdojo-1",
            skills=self.skills,
            manifest=manifest,
            model_factory=model_factory,
            database_directory=self.temporary / "trial-databases",
            artifact_directory=self.temporary / "trial-artifacts",
            provider="scripted",
            model="scripted-model",
            code_revision="integration-test",
            code_dirty=False,
            sampling={"temperature": 0},
            clock=lambda: self.now + timedelta(minutes=2),
        )
        artifact = PairedEvaluationRunner().run(
            evaluation_id="paired-agentdojo-1",
            candidate=candidate,
            suite_id="scheduling-held-out-v1",
            manifest_digest=manifest.calculated_digest(),
            cases=(
                HeldOutCase(
                    case_id="benign_user_18",
                    kind=EvaluationCaseKind.BENIGN,
                ),
            ),
            evaluator=evaluator,
            created_at=self.now + timedelta(minutes=3),
        )

        artifact.assert_integrity()
        pair = artifact.pairs[0]
        self.assertTrue(pair.base.measurement.utility_passed)
        self.assertTrue(pair.candidate.measurement.utility_passed)
        files = tuple((self.temporary / "trial-artifacts").glob("*.json"))
        self.assertEqual(len(files), 2)
        underlying = tuple(read_artifact(path) for path in files)
        self.assertEqual(
            {item.artifact_digest for item in underlying},
            {
                pair.base.measurement.run_artifact_digest,
                pair.candidate.measurement.run_artifact_digest,
            },
        )

        base_request = adapters[base.ref.version_id].requests[0][0]
        candidate_request = adapters[candidate.candidate_ref.version_id].requests[0][0]
        self.assertIn(base_text, base_request[0].content or "")
        self.assertNotIn(candidate_text, base_request[0].content or "")
        self.assertIn(candidate_text, candidate_request[0].content or "")
        self.assertNotIn(base_text, candidate_request[0].content or "")
        for underlying_artifact in underlying:
            context_event = next(
                event
                for event in underlying_artifact.trials[0].events
                if event.event_type == "skill_context.assembled"
            )
            self.assertEqual(len(context_event.payload["skill_versions"]), 1)


if __name__ == "__main__":
    unittest.main()
