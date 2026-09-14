"""SQLite persistence for non-active Skill candidates."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from voren.learning.evaluation import CandidateEvaluationArtifact
from voren.learning.evidence import LearningEvidenceArtifact
from voren.learning.lifecycle import CandidateEventType, CandidateLifecycleEvent
from voren.learning.models import CandidateStatus, EvidenceRef, SkillCandidate
from voren.learning.policy import CandidateDecision
from voren.skills.models import SkillVersionRef


class CandidateStoreError(RuntimeError):
    pass


class CandidatePromotionError(CandidateStoreError):
    pass


class SQLiteCandidateStore:
    def __init__(self, database: str | Path) -> None:
        self._connection = sqlite3.connect(str(database))
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS learning_evidence (
                   evidence_id TEXT PRIMARY KEY,
                   source TEXT NOT NULL,
                   artifact_digest TEXT NOT NULL UNIQUE,
                   artifact_json TEXT NOT NULL,
                   created_at TEXT NOT NULL
               )"""
        )
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS skill_candidates (
                   candidate_id TEXT PRIMARY KEY,
                   skill_name TEXT NOT NULL,
                   base_version_id TEXT NOT NULL,
                   candidate_version_id TEXT NOT NULL,
                   status TEXT NOT NULL,
                   record_json TEXT NOT NULL,
                   created_at TEXT NOT NULL,
                   updated_at TEXT NOT NULL,
                   FOREIGN KEY(skill_name, base_version_id)
                       REFERENCES skill_versions(skill_name, version_id),
                   FOREIGN KEY(skill_name, candidate_version_id)
                       REFERENCES skill_versions(skill_name, version_id)
               )"""
        )
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS skill_candidate_evaluations (
                   evaluation_id TEXT PRIMARY KEY,
                   candidate_id TEXT NOT NULL,
                   artifact_digest TEXT NOT NULL UNIQUE,
                   artifact_json TEXT NOT NULL,
                   created_at TEXT NOT NULL,
                   FOREIGN KEY(candidate_id)
                       REFERENCES skill_candidates(candidate_id)
               )"""
        )
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS skill_candidate_events (
                   candidate_id TEXT NOT NULL,
                   sequence INTEGER NOT NULL,
                   event_digest TEXT NOT NULL UNIQUE,
                   event_json TEXT NOT NULL,
                   occurred_at TEXT NOT NULL,
                   PRIMARY KEY(candidate_id, sequence),
                   FOREIGN KEY(candidate_id)
                       REFERENCES skill_candidates(candidate_id)
               )"""
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def record_evidence(
        self, artifact: LearningEvidenceArtifact
    ) -> LearningEvidenceArtifact:
        artifact.assert_integrity()
        with self._connection:
            self._connection.execute(
                """INSERT INTO learning_evidence (
                       evidence_id, source, artifact_digest,
                       artifact_json, created_at
                   ) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(evidence_id) DO NOTHING""",
                (
                    artifact.evidence_id,
                    artifact.source.value,
                    artifact.artifact_digest,
                    artifact.model_dump_json(),
                    artifact.created_at.isoformat(),
                ),
            )
        stored = self.get_evidence(artifact.evidence_id)
        if stored != artifact:
            raise CandidateStoreError(
                f"evidence ID {artifact.evidence_id!r} is already bound "
                "to another artifact"
            )
        return stored

    def get_evidence(self, evidence_id: str) -> LearningEvidenceArtifact:
        row = self._connection.execute(
            """SELECT source, artifact_digest, artifact_json
               FROM learning_evidence WHERE evidence_id = ?""",
            (evidence_id,),
        ).fetchone()
        if row is None:
            raise CandidateStoreError(f"unknown evidence ID {evidence_id!r}")
        try:
            artifact = LearningEvidenceArtifact.model_validate_json(
                row["artifact_json"]
            )
            artifact.assert_integrity()
        except ValueError as error:
            raise CandidateStoreError(
                "learning evidence failed integrity validation"
            ) from error
        if (
            artifact.source.value != row["source"]
            or artifact.artifact_digest != row["artifact_digest"]
        ):
            raise CandidateStoreError(
                "learning evidence columns disagree with its artifact"
            )
        return artifact

    def require_evidence(self, evidence: tuple[EvidenceRef, ...]) -> None:
        for expected in evidence:
            stored = self.get_evidence(expected.evidence_id).as_ref()
            if stored != expected:
                raise CandidateStoreError(
                    f"evidence reference {expected.evidence_id!r} does not "
                    "match its durable artifact"
                )

    def stage(self, candidate: SkillCandidate) -> SkillCandidate:
        record_json = candidate.model_dump_json()
        with self._connection:
            cursor = self._connection.execute(
                """INSERT INTO skill_candidates (
                       candidate_id, skill_name, base_version_id,
                       candidate_version_id, status, record_json,
                       created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(candidate_id) DO NOTHING""",
                (
                    candidate.candidate_id,
                    candidate.base_ref.name,
                    candidate.base_ref.version_id,
                    candidate.candidate_ref.version_id,
                    candidate.status.value,
                    record_json,
                    candidate.created_at.isoformat(),
                    candidate.updated_at.isoformat(),
                ),
            )
            if cursor.rowcount == 1:
                self._append_event(
                    candidate_id=candidate.candidate_id,
                    event_type=CandidateEventType.STAGED,
                    from_status=None,
                    to_status=CandidateStatus.STAGED,
                    reason="candidate admitted by bounded policy",
                    occurred_at=candidate.created_at,
                )
        stored = self.get(candidate.candidate_id)
        if stored != candidate:
            raise CandidateStoreError(
                f"candidate ID {candidate.candidate_id!r} is already bound to another record"
            )
        return stored

    def get(self, candidate_id: str) -> SkillCandidate:
        row = self._connection.execute(
            "SELECT status, record_json FROM skill_candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise CandidateStoreError(f"unknown candidate ID {candidate_id!r}")
        candidate = SkillCandidate.model_validate_json(row["record_json"])
        if candidate.status.value != row["status"]:
            raise CandidateStoreError("candidate status column disagrees with its record")
        return candidate

    def list_for_skill(self, skill_name: str) -> tuple[SkillCandidate, ...]:
        rows = self._connection.execute(
            """SELECT candidate_id FROM skill_candidates
               WHERE skill_name = ? ORDER BY created_at, candidate_id""",
            (skill_name,),
        ).fetchall()
        return tuple(self.get(str(row["candidate_id"])) for row in rows)

    def record_decision(
        self,
        *,
        candidate_id: str,
        artifact: CandidateEvaluationArtifact,
        decision: CandidateDecision,
        decided_at: datetime,
    ) -> SkillCandidate:
        artifact.assert_integrity()
        if decision.status not in {
            CandidateStatus.ACCEPTED,
            CandidateStatus.REJECTED,
        }:
            raise CandidateStoreError("candidate decision must accept or reject")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                """SELECT status, record_json FROM skill_candidates
                   WHERE candidate_id = ?""",
                (candidate_id,),
            ).fetchone()
            if row is None:
                raise CandidateStoreError(
                    f"unknown candidate ID {candidate_id!r}"
                )
            current = self._candidate_from_row(row)
            if (
                current.status is decision.status
                and current.evaluation_artifact_digest
                == artifact.artifact_digest
                and current.decision_reason == decision.reason
            ):
                self._connection.commit()
                return current
            if current.status is not CandidateStatus.STAGED:
                raise CandidateStoreError(
                    f"candidate in {current.status.value!r} cannot be decided"
                )
            if artifact.candidate_id != candidate_id:
                raise CandidateStoreError("evaluation belongs to another candidate")
            if (
                artifact.base_ref != current.base_ref
                or artifact.candidate_ref != current.candidate_ref
            ):
                raise CandidateStoreError(
                    "evaluation versions do not match the stored candidate"
                )

            updated = SkillCandidate.model_validate(
                {
                    **current.model_dump(mode="python"),
                    "status": decision.status,
                    "updated_at": decided_at,
                    "decision_reason": decision.reason,
                    "evaluation_artifact_digest": artifact.artifact_digest,
                }
            )
            self._connection.execute(
                """INSERT INTO skill_candidate_evaluations (
                       evaluation_id, candidate_id, artifact_digest,
                       artifact_json, created_at
                   ) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(evaluation_id) DO NOTHING""",
                (
                    artifact.evaluation_id,
                    candidate_id,
                    artifact.artifact_digest,
                    artifact.model_dump_json(),
                    artifact.created_at.isoformat(),
                ),
            )
            stored_artifact = self._load_evaluation_row(
                artifact.evaluation_id
            )
            if stored_artifact != artifact:
                raise CandidateStoreError(
                    "evaluation ID is already bound to another artifact"
                )
            cursor = self._connection.execute(
                """UPDATE skill_candidates
                   SET status = ?, record_json = ?, updated_at = ?
                   WHERE candidate_id = ? AND status = ?""",
                (
                    updated.status.value,
                    updated.model_dump_json(),
                    updated.updated_at.isoformat(),
                    candidate_id,
                    CandidateStatus.STAGED.value,
                ),
            )
            if cursor.rowcount != 1:
                raise CandidateStoreError("candidate decision lost a state race")
            self._append_event(
                candidate_id=candidate_id,
                event_type=CandidateEventType.DECIDED,
                from_status=CandidateStatus.STAGED,
                to_status=updated.status,
                reason=decision.reason,
                occurred_at=decided_at,
                evaluation_artifact_digest=artifact.artifact_digest,
            )
            self._connection.commit()
            return updated
        except Exception:
            self._connection.rollback()
            raise

    def get_evaluation(
        self, evaluation_id: str
    ) -> CandidateEvaluationArtifact:
        artifact = self._load_evaluation_row(evaluation_id)
        artifact.assert_integrity()
        return artifact

    def list_evaluations(
        self, candidate_id: str
    ) -> tuple[CandidateEvaluationArtifact, ...]:
        rows = self._connection.execute(
            """SELECT evaluation_id FROM skill_candidate_evaluations
               WHERE candidate_id = ? ORDER BY created_at, evaluation_id""",
            (candidate_id,),
        ).fetchall()
        return tuple(
            self.get_evaluation(str(row["evaluation_id"])) for row in rows
        )

    def promote(
        self,
        *,
        candidate_id: str,
        reason: str,
        promoted_at: datetime,
    ) -> SkillCandidate:
        if not reason.strip():
            raise CandidatePromotionError("promotion reason must be non-empty")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            current = self._get_candidate_in_transaction(candidate_id)
            active_ref = self._get_active_ref(current.base_ref.name)
            if (
                current.status is CandidateStatus.PROMOTED
                and active_ref == current.candidate_ref
            ):
                self._connection.commit()
                return current
            if current.status is not CandidateStatus.ACCEPTED:
                raise CandidatePromotionError(
                    f"candidate in {current.status.value!r} cannot be promoted"
                )
            if active_ref != current.base_ref:
                raise CandidatePromotionError(
                    "active Skill no longer matches the evaluated base"
                )
            updated = self._candidate_with_status(
                current,
                status=CandidateStatus.PROMOTED,
                updated_at=promoted_at,
            )
            pointer = self._connection.execute(
                """UPDATE active_skills
                   SET version_id = ?, activated_at = ?, activation_reason = ?
                   WHERE skill_name = ? AND version_id = ?""",
                (
                    current.candidate_ref.version_id,
                    promoted_at.isoformat(),
                    reason,
                    current.base_ref.name,
                    current.base_ref.version_id,
                ),
            )
            if pointer.rowcount != 1:
                raise CandidatePromotionError("promotion lost the active-pointer race")
            self._update_candidate_status(
                current=current,
                updated=updated,
                expected=CandidateStatus.ACCEPTED,
            )
            self._append_event(
                candidate_id=candidate_id,
                event_type=CandidateEventType.PROMOTED,
                from_status=CandidateStatus.ACCEPTED,
                to_status=CandidateStatus.PROMOTED,
                reason=reason,
                occurred_at=promoted_at,
                evaluation_artifact_digest=current.evaluation_artifact_digest,
                active_from=current.base_ref,
                active_to=current.candidate_ref,
            )
            self._connection.commit()
            return updated
        except Exception:
            self._connection.rollback()
            raise

    def rollback(
        self,
        *,
        candidate_id: str,
        reason: str,
        rolled_back_at: datetime,
    ) -> SkillCandidate:
        if not reason.strip():
            raise CandidatePromotionError("rollback reason must be non-empty")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            current = self._get_candidate_in_transaction(candidate_id)
            active_ref = self._get_active_ref(current.base_ref.name)
            if (
                current.status is CandidateStatus.ROLLED_BACK
                and active_ref == current.base_ref
            ):
                self._connection.commit()
                return current
            if current.status is not CandidateStatus.PROMOTED:
                raise CandidatePromotionError(
                    f"candidate in {current.status.value!r} cannot be rolled back"
                )
            if active_ref != current.candidate_ref:
                raise CandidatePromotionError(
                    "active Skill no longer matches the promoted candidate"
                )
            updated = self._candidate_with_status(
                current,
                status=CandidateStatus.ROLLED_BACK,
                updated_at=rolled_back_at,
            )
            pointer = self._connection.execute(
                """UPDATE active_skills
                   SET version_id = ?, activated_at = ?, activation_reason = ?
                   WHERE skill_name = ? AND version_id = ?""",
                (
                    current.base_ref.version_id,
                    rolled_back_at.isoformat(),
                    reason,
                    current.candidate_ref.name,
                    current.candidate_ref.version_id,
                ),
            )
            if pointer.rowcount != 1:
                raise CandidatePromotionError("rollback lost the active-pointer race")
            self._update_candidate_status(
                current=current,
                updated=updated,
                expected=CandidateStatus.PROMOTED,
            )
            self._append_event(
                candidate_id=candidate_id,
                event_type=CandidateEventType.ROLLED_BACK,
                from_status=CandidateStatus.PROMOTED,
                to_status=CandidateStatus.ROLLED_BACK,
                reason=reason,
                occurred_at=rolled_back_at,
                evaluation_artifact_digest=current.evaluation_artifact_digest,
                active_from=current.candidate_ref,
                active_to=current.base_ref,
            )
            self._connection.commit()
            return updated
        except Exception:
            self._connection.rollback()
            raise

    def list_events(
        self, candidate_id: str
    ) -> tuple[CandidateLifecycleEvent, ...]:
        rows = self._connection.execute(
            """SELECT sequence, event_digest, event_json
               FROM skill_candidate_events
               WHERE candidate_id = ? ORDER BY sequence""",
            (candidate_id,),
        ).fetchall()
        events = tuple(self._event_from_row(row) for row in rows)
        previous: CandidateLifecycleEvent | None = None
        for event in events:
            if event.sequence != (1 if previous is None else previous.sequence + 1):
                raise CandidateStoreError("candidate event sequence is not contiguous")
            if event.previous_event_digest != (
                None if previous is None else previous.event_digest
            ):
                raise CandidateStoreError("candidate event digest chain is broken")
            if previous is not None and event.from_status is not previous.to_status:
                raise CandidateStoreError("candidate event status chain is broken")
            previous = event
        if events:
            current = self.get(candidate_id)
            if events[-1].to_status is not current.status:
                raise CandidateStoreError(
                    "candidate record disagrees with its lifecycle events"
                )
        return events

    def _load_evaluation_row(
        self, evaluation_id: str
    ) -> CandidateEvaluationArtifact:
        row = self._connection.execute(
            """SELECT artifact_digest, artifact_json
               FROM skill_candidate_evaluations WHERE evaluation_id = ?""",
            (evaluation_id,),
        ).fetchone()
        if row is None:
            raise CandidateStoreError(
                f"unknown candidate evaluation ID {evaluation_id!r}"
            )
        artifact = CandidateEvaluationArtifact.model_validate_json(
            row["artifact_json"]
        )
        if artifact.artifact_digest != row["artifact_digest"]:
            raise CandidateStoreError(
                "evaluation digest column disagrees with its artifact"
            )
        return artifact

    def _get_candidate_in_transaction(
        self, candidate_id: str
    ) -> SkillCandidate:
        row = self._connection.execute(
            """SELECT status, record_json FROM skill_candidates
               WHERE candidate_id = ?""",
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise CandidateStoreError(f"unknown candidate ID {candidate_id!r}")
        return self._candidate_from_row(row)

    def _get_active_ref(self, skill_name: str) -> SkillVersionRef:
        row = self._connection.execute(
            """SELECT v.skill_name, v.version_id, v.content_digest
               FROM active_skills AS a
               JOIN skill_versions AS v
                 ON v.skill_name = a.skill_name AND v.version_id = a.version_id
               WHERE a.skill_name = ?""",
            (skill_name,),
        ).fetchone()
        if row is None:
            raise CandidatePromotionError(f"Skill {skill_name!r} is not active")
        return SkillVersionRef(
            name=row["skill_name"],
            version_id=row["version_id"],
            content_digest=row["content_digest"],
        )

    @staticmethod
    def _candidate_with_status(
        current: SkillCandidate,
        *,
        status: CandidateStatus,
        updated_at: datetime,
    ) -> SkillCandidate:
        return SkillCandidate.model_validate(
            {
                **current.model_dump(mode="python"),
                "status": status,
                "updated_at": updated_at,
            }
        )

    def _update_candidate_status(
        self,
        *,
        current: SkillCandidate,
        updated: SkillCandidate,
        expected: CandidateStatus,
    ) -> None:
        cursor = self._connection.execute(
            """UPDATE skill_candidates
               SET status = ?, record_json = ?, updated_at = ?
               WHERE candidate_id = ? AND status = ?""",
            (
                updated.status.value,
                updated.model_dump_json(),
                updated.updated_at.isoformat(),
                current.candidate_id,
                expected.value,
            ),
        )
        if cursor.rowcount != 1:
            raise CandidatePromotionError("candidate status transition lost a race")

    def _append_event(
        self,
        *,
        candidate_id: str,
        event_type: CandidateEventType,
        from_status: CandidateStatus | None,
        to_status: CandidateStatus,
        reason: str,
        occurred_at: datetime,
        evaluation_artifact_digest: str | None = None,
        active_from: SkillVersionRef | None = None,
        active_to: SkillVersionRef | None = None,
    ) -> CandidateLifecycleEvent:
        row = self._connection.execute(
            """SELECT sequence, event_digest FROM skill_candidate_events
               WHERE candidate_id = ? ORDER BY sequence DESC LIMIT 1""",
            (candidate_id,),
        ).fetchone()
        sequence = 1 if row is None else int(row["sequence"]) + 1
        previous_digest = None if row is None else str(row["event_digest"])
        event = CandidateLifecycleEvent.create(
            candidate_id=candidate_id,
            sequence=sequence,
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            reason=reason,
            occurred_at=occurred_at,
            evaluation_artifact_digest=evaluation_artifact_digest,
            active_from=active_from,
            active_to=active_to,
            previous_event_digest=previous_digest,
        )
        self._connection.execute(
            """INSERT INTO skill_candidate_events (
                   candidate_id, sequence, event_digest, event_json, occurred_at
               ) VALUES (?, ?, ?, ?, ?)""",
            (
                candidate_id,
                event.sequence,
                event.event_digest,
                event.model_dump_json(),
                event.occurred_at.isoformat(),
            ),
        )
        return event

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> CandidateLifecycleEvent:
        try:
            event = CandidateLifecycleEvent.model_validate_json(row["event_json"])
            event.assert_integrity()
        except ValueError as error:
            raise CandidateStoreError(
                "candidate lifecycle event failed integrity validation"
            ) from error
        if (
            event.sequence != row["sequence"]
            or event.event_digest != row["event_digest"]
        ):
            raise CandidateStoreError(
                "candidate event columns disagree with the event record"
            )
        return event

    @staticmethod
    def _candidate_from_row(row: sqlite3.Row) -> SkillCandidate:
        candidate = SkillCandidate.model_validate_json(row["record_json"])
        if candidate.status.value != row["status"]:
            raise CandidateStoreError(
                "candidate status column disagrees with its record"
            )
        return candidate
