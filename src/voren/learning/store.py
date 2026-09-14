"""SQLite persistence for non-active Skill candidates."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from voren.learning.evaluation import CandidateEvaluationArtifact
from voren.learning.models import CandidateStatus, SkillCandidate
from voren.learning.policy import CandidateDecision


class CandidateStoreError(RuntimeError):
    pass


class SQLiteCandidateStore:
    def __init__(self, database: str | Path) -> None:
        self._connection = sqlite3.connect(str(database))
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
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
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def stage(self, candidate: SkillCandidate) -> SkillCandidate:
        record_json = candidate.model_dump_json()
        with self._connection:
            self._connection.execute(
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

    @staticmethod
    def _candidate_from_row(row: sqlite3.Row) -> SkillCandidate:
        candidate = SkillCandidate.model_validate_json(row["record_json"])
        if candidate.status.value != row["status"]:
            raise CandidateStoreError(
                "candidate status column disagrees with its record"
            )
        return candidate
