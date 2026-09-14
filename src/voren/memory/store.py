"""SQLite persistence for separately typed profile and episode memory."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from voren.memory.models import MemoryKind, MemoryRecord, MemoryRef


class MemoryStoreError(ValueError):
    pass


class SQLiteMemoryStore:
    def __init__(self, database: str | Path) -> None:
        self._connection = sqlite3.connect(str(database))
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS memory_records (
                kind TEXT NOT NULL,
                memory_id TEXT NOT NULL,
                version_id TEXT NOT NULL,
                content_digest TEXT NOT NULL,
                content TEXT NOT NULL,
                evidence_id TEXT NOT NULL,
                evidence_digest TEXT NOT NULL,
                evidence_source TEXT NOT NULL,
                instruction_authority INTEGER NOT NULL CHECK (
                    instruction_authority = 0
                ),
                created_at TEXT NOT NULL,
                PRIMARY KEY(kind, memory_id, version_id)
            );

            CREATE TABLE IF NOT EXISTS active_profile_preferences (
                kind TEXT NOT NULL DEFAULT 'profile_preference' CHECK (
                    kind = 'profile_preference'
                ),
                memory_id TEXT PRIMARY KEY,
                version_id TEXT NOT NULL,
                activated_at TEXT NOT NULL,
                activation_reason TEXT NOT NULL,
                FOREIGN KEY(
                    kind, memory_id, version_id
                ) REFERENCES memory_records(kind, memory_id, version_id)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS one_episode_per_identity
                ON memory_records(memory_id)
                WHERE kind = 'episode_summary';
            """
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def record_profile(
        self,
        record: MemoryRecord,
        *,
        reason: str,
        activated_at: datetime | None = None,
    ) -> MemoryRecord:
        if record.ref.kind is not MemoryKind.PROFILE_PREFERENCE:
            raise MemoryStoreError("record_profile requires a profile preference")
        if not reason.strip():
            raise MemoryStoreError("profile activation requires a reason")
        timestamp = activated_at or datetime.now(UTC)
        with self._connection:
            self._insert(record)
            self._connection.execute(
                """
                INSERT INTO active_profile_preferences (
                    kind, memory_id, version_id, activated_at, activation_reason
                ) VALUES ('profile_preference', ?, ?, ?, ?)
                ON CONFLICT(memory_id) DO UPDATE SET
                    version_id = excluded.version_id,
                    activated_at = excluded.activated_at,
                    activation_reason = excluded.activation_reason
                """,
                (
                    record.ref.memory_id,
                    record.ref.version_id,
                    timestamp.isoformat(),
                    reason,
                ),
            )
        return self.get(record.ref)

    def record_episode(self, record: MemoryRecord) -> MemoryRecord:
        if record.ref.kind is not MemoryKind.EPISODE_SUMMARY:
            raise MemoryStoreError("record_episode requires an episode summary")
        existing = self._connection.execute(
            """
            SELECT version_id FROM memory_records
            WHERE kind = ? AND memory_id = ?
            """,
            (record.ref.kind.value, record.ref.memory_id),
        ).fetchone()
        if existing is not None and existing["version_id"] != record.ref.version_id:
            raise MemoryStoreError("episode identity is immutable")
        with self._connection:
            self._insert(record)
        return self.get(record.ref)

    def freeze(
        self,
        *,
        profile_ids: tuple[str, ...] | None = None,
        episode_ids: tuple[str, ...] = (),
    ) -> tuple[MemoryRef, ...]:
        if profile_ids is not None and len(profile_ids) != len(set(profile_ids)):
            raise MemoryStoreError("profile IDs must be unique")
        if len(episode_ids) != len(set(episode_ids)):
            raise MemoryStoreError("episode IDs must be unique")
        active_rows = self._connection.execute(
            """
            SELECT r.* FROM active_profile_preferences AS a
            JOIN memory_records AS r
              ON r.kind = 'profile_preference'
             AND r.memory_id = a.memory_id
             AND r.version_id = a.version_id
            ORDER BY r.memory_id
            """
        ).fetchall()
        active = {
            row["memory_id"]: self._record_from_row(row).ref for row in active_rows
        }
        selected_profiles = (
            tuple(sorted(active)) if profile_ids is None else profile_ids
        )
        missing_profiles = [item for item in selected_profiles if item not in active]
        if missing_profiles:
            raise MemoryStoreError(
                f"profile preferences are not active: {missing_profiles}"
            )
        refs = [active[item] for item in selected_profiles]
        for episode_id in episode_ids:
            row = self._connection.execute(
                """
                SELECT * FROM memory_records
                WHERE kind = 'episode_summary' AND memory_id = ?
                """,
                (episode_id,),
            ).fetchone()
            if row is None:
                raise MemoryStoreError(f"unknown episode: {episode_id!r}")
            refs.append(self._record_from_row(row).ref)
        return tuple(refs)

    def get(self, ref: MemoryRef) -> MemoryRecord:
        row = self._connection.execute(
            """
            SELECT * FROM memory_records
            WHERE kind = ? AND memory_id = ? AND version_id = ?
            """,
            (ref.kind.value, ref.memory_id, ref.version_id),
        ).fetchone()
        if row is None:
            raise MemoryStoreError(
                f"unknown memory version {ref.kind.value}:{ref.memory_id}@{ref.version_id}"
            )
        record = self._record_from_row(row)
        if record.ref != ref:
            raise MemoryStoreError("memory reference digest does not match the store")
        return record

    def _insert(self, record: MemoryRecord) -> None:
        record = MemoryRecord.model_validate(record)
        self._connection.execute(
            """
            INSERT INTO memory_records (
                kind, memory_id, version_id, content_digest, content,
                evidence_id, evidence_digest, evidence_source,
                instruction_authority, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(kind, memory_id, version_id) DO NOTHING
            """,
            (
                record.ref.kind.value,
                record.ref.memory_id,
                record.ref.version_id,
                record.ref.content_digest,
                record.content,
                record.evidence_id,
                record.evidence_digest,
                record.evidence_source.value,
                int(record.instruction_authority),
                record.created_at.isoformat(),
            ),
        )
        if self.get(record.ref) != record:
            raise MemoryStoreError("stored memory version is inconsistent")

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord.model_validate(
            {
                "ref": {
                    "kind": row["kind"],
                    "memory_id": row["memory_id"],
                    "version_id": row["version_id"],
                    "content_digest": row["content_digest"],
                },
                "content": row["content"],
                "evidence_id": row["evidence_id"],
                "evidence_digest": row["evidence_digest"],
                "evidence_source": row["evidence_source"],
                "instruction_authority": bool(row["instruction_authority"]),
                "created_at": row["created_at"],
            }
        )
