"""SQLite persistence for non-active Skill candidates."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from voren.learning.models import SkillCandidate


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
