from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from voren.learning.evidence import (
    DurableLearningRouter,
    LearningEvidenceRoutingError,
)
from voren.learning.models import EvidenceRef, EvidenceSource
from voren.learning.store import CandidateStoreError, SQLiteCandidateStore
from voren.runs.models import (
    NewRunEvent,
    RunConfig,
    RunEventType,
    RunRecord,
    RunStatus,
)
from voren.runs.store import SQLiteRunStore
from voren.skills.store import SQLiteSkillStore


class LearningEvidenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        temporary = Path(self.temporary_directory.name)
        self.database = temporary / "evidence.sqlite3"
        self.skills = SQLiteSkillStore(
            self.database, root=temporary / "skill-store"
        )
        self.candidates = SQLiteCandidateStore(self.database)
        self.runs = SQLiteRunStore(self.database)
        self.addCleanup(self.runs.close)
        self.addCleanup(self.candidates.close)
        self.addCleanup(self.skills.close)
        self.router = DurableLearningRouter(
            evidence_store=self.candidates,
            run_store=self.runs,
        )
        self.now = datetime(2026, 9, 14, 18, 0, tzinfo=UTC)

    def _event(
        self,
        run_id: str,
        event_type: RunEventType,
        payload: dict | None = None,
    ) -> NewRunEvent:
        return NewRunEvent(
            event_id=f"event:{run_id}:{event_type.value}:{id(payload)}",
            dedupe_key=f"{event_type.value}:{id(payload)}",
            event_type=event_type,
            payload=payload or {},
            occurred_at=self.now,
        )

    def _new_running(self, run_id: str) -> None:
        config = RunConfig(
            workflow="evidence-test",
            world_adapter="fake-world",
            policy_version="test-policy",
            action_contract_versions=(),
        )
        record = RunRecord(
            run_id=run_id,
            status=RunStatus.CREATED,
            config=config,
            config_digest=config.calculated_digest(),
            version=0,
            created_at=self.now,
            updated_at=self.now,
        )
        self.runs.create(
            record,
            self._event(run_id, RunEventType.RUN_CREATED),
        )
        self.runs.transition(
            run_id,
            expected_status=RunStatus.CREATED,
            new_status=RunStatus.RUNNING,
            pending_operation_id=None,
            pending_proposal_digest=None,
            last_receipt_status=None,
            updated_at=self.now + timedelta(seconds=1),
            events=(self._event(run_id, RunEventType.RUN_STARTED),),
        )

    def _complete_read_only(
        self,
        run_id: str,
        *,
        untrusted_authority: bool = False,
    ) -> None:
        self._new_running(run_id)
        self.runs.transition(
            run_id,
            expected_status=RunStatus.RUNNING,
            new_status=RunStatus.COMPLETED,
            pending_operation_id=None,
            pending_proposal_digest=None,
            last_receipt_status=None,
            updated_at=self.now + timedelta(seconds=2),
            events=(
                self._event(
                    run_id,
                    RunEventType.TOOL_OBSERVED,
                    {
                        "trust_levels": ["external_untrusted"],
                        "instruction_authority": untrusted_authority,
                    },
                ),
                self._event(run_id, RunEventType.RUN_COMPLETED),
            ),
        )

    def _complete_reconciled_action(self, run_id: str) -> None:
        self._new_running(run_id)
        operation_id = f"operation:{run_id}"
        proposal_digest = hashlib.sha256(operation_id.encode()).hexdigest()
        self.runs.transition(
            run_id,
            expected_status=RunStatus.RUNNING,
            new_status=RunStatus.WAITING_APPROVAL,
            pending_operation_id=operation_id,
            pending_proposal_digest=proposal_digest,
            last_receipt_status=None,
            updated_at=self.now + timedelta(seconds=2),
            events=(
                self._event(
                    run_id,
                    RunEventType.ACTION_PROPOSED,
                    {
                        "operation_id": operation_id,
                        "proposal_digest": proposal_digest,
                    },
                ),
            ),
        )
        approval_id = f"approval:{run_id}"
        self.runs.append_events(
            run_id,
            (
                self._event(
                    run_id,
                    RunEventType.APPROVAL_ACCEPTED,
                    {
                        "approval_id": approval_id,
                        "operation_id": operation_id,
                        "proposal_digest": proposal_digest,
                        "approved": True,
                    },
                ),
            ),
        )
        self.runs.transition(
            run_id,
            expected_status=RunStatus.WAITING_APPROVAL,
            new_status=RunStatus.NEEDS_RECONCILIATION,
            pending_operation_id=operation_id,
            pending_proposal_digest=proposal_digest,
            last_receipt_status="ambiguous",
            updated_at=self.now + timedelta(seconds=3),
            events=(
                self._event(
                    run_id,
                    RunEventType.ACTION_RECEIPT,
                    {
                        "operation_id": operation_id,
                        "proposal_digest": proposal_digest,
                        "approval_id": approval_id,
                        "status": "ambiguous",
                        "committed": False,
                        "verification": {"passed": False},
                    },
                ),
                self._event(run_id, RunEventType.RUN_NEEDS_RECONCILIATION),
            ),
        )
        self.runs.transition(
            run_id,
            expected_status=RunStatus.NEEDS_RECONCILIATION,
            new_status=RunStatus.COMPLETED,
            pending_operation_id=None,
            pending_proposal_digest=None,
            last_receipt_status="verified",
            updated_at=self.now + timedelta(seconds=4),
            events=(
                self._event(
                    run_id,
                    RunEventType.ACTION_RECONCILED,
                    {
                        "operation_id": operation_id,
                        "proposal_digest": proposal_digest,
                        "approval_id": approval_id,
                        "status": "verified",
                        "committed": True,
                        "verification": {"passed": True},
                    },
                ),
                self._event(run_id, RunEventType.RUN_COMPLETED),
            ),
        )

    def test_completed_verified_run_becomes_durable_evidence(self) -> None:
        self._complete_reconciled_action("run-verified")

        evidence = self.router.select_verified_run(
            evidence_id="run:evidence-1",
            run_id="run-verified",
            evaluation_case_ids=("training-case-1",),
            created_at=self.now + timedelta(minutes=1),
        )

        stored = self.candidates.get_evidence(evidence.evidence_id)
        stored.assert_integrity()
        self.assertEqual(stored.as_ref(), evidence)
        self.assertEqual(evidence.source, EvidenceSource.VERIFIED_RUN)
        self.assertFalse(evidence.instruction_authority)
        self.assertIn('"status":"completed"', stored.payload)

    def test_untrusted_observation_cannot_authorize_learning(self) -> None:
        self._complete_read_only("run-untrusted", untrusted_authority=True)

        with self.assertRaisesRegex(
            LearningEvidenceRoutingError, "untrusted observation"
        ):
            self.router.select_verified_run(
                evidence_id="run:untrusted",
                run_id="run-untrusted",
            )

    def test_unverified_action_receipt_cannot_be_routed(self) -> None:
        self._complete_reconciled_action("run-rewritten-receipt")
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                """SELECT payload_json FROM run_events
                   WHERE run_id = ? AND event_type = ?""",
                (
                    "run-rewritten-receipt",
                    RunEventType.ACTION_RECONCILED.value,
                ),
            ).fetchone()
            payload = json.loads(row[0])
            payload["verification"]["passed"] = False
            connection.execute(
                """UPDATE run_events SET payload_json = ?
                   WHERE run_id = ? AND event_type = ?""",
                (
                    json.dumps(payload),
                    "run-rewritten-receipt",
                    RunEventType.ACTION_RECONCILED.value,
                ),
            )

        with self.assertRaisesRegex(
            LearningEvidenceRoutingError, "incorrectly bound"
        ):
            self.router.select_verified_run(
                evidence_id="run:rewritten-receipt",
                run_id="run-rewritten-receipt",
            )

    def test_non_completed_run_cannot_be_routed(self) -> None:
        self._new_running("run-incomplete")

        with self.assertRaisesRegex(LearningEvidenceRoutingError, "completed"):
            self.router.select_verified_run(
                evidence_id="run:incomplete",
                run_id="run-incomplete",
            )

    def test_fabricated_reference_is_rejected_by_persisted_evidence_gate(self) -> None:
        fabricated = EvidenceRef(
            evidence_id="operator:not-stored",
            source=EvidenceSource.OPERATOR_CORRECTION,
            digest="0" * 64,
            instruction_authority=True,
        )

        with self.assertRaises(CandidateStoreError):
            self.candidates.require_evidence((fabricated,))

    def test_operator_correction_is_durable_and_idempotent(self) -> None:
        first = self.router.record_operator_correction(
            evidence_id="operator:correction-1",
            correction="Ask before guessing a meeting duration.",
            operator_ref="operator:sid",
            created_at=self.now,
        )
        repeated = self.router.record_operator_correction(
            evidence_id="operator:correction-1",
            correction="Ask before guessing a meeting duration.",
            operator_ref="operator:sid",
            created_at=self.now,
        )

        self.assertEqual(repeated, first)
        artifact = self.candidates.get_evidence(first.evidence_id)
        self.assertEqual(
            artifact.payload, "Ask before guessing a meeting duration."
        )
        self.assertTrue(first.instruction_authority)


if __name__ == "__main__":
    unittest.main()
