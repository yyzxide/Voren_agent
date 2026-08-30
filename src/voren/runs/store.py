"""SQLite run state plus an append-only, deduplicated event stream."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from voren.actions.errors import InvalidOperationStateError
from voren.runs.models import (
    NewRunEvent,
    RunConfig,
    RunEvent,
    RunRecord,
    RunStatus,
)


class SQLiteRunStore:
    def __init__(self, path: str | Path) -> None:
        self._connection = sqlite3.connect(str(path))
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                config_json TEXT NOT NULL,
                config_digest TEXT NOT NULL,
                pending_operation_id TEXT,
                pending_proposal_digest TEXT,
                last_receipt_status TEXT,
                version INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS run_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                run_id TEXT NOT NULL,
                dedupe_key TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                UNIQUE(run_id, dedupe_key),
                FOREIGN KEY(run_id) REFERENCES runs(run_id)
            );
            """
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def create(self, record: RunRecord, event: NewRunEvent) -> RunRecord:
        if record.status is not RunStatus.CREATED or record.version != 0:
            raise ValueError("a new run must start in created state at version zero")
        if record.config_digest != record.config.calculated_digest():
            raise ValueError("run config does not match config_digest")
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO runs (
                    run_id, status, config_json, config_digest,
                    pending_operation_id, pending_proposal_digest,
                    last_receipt_status, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.status.value,
                    record.config.model_dump_json(),
                    record.config_digest,
                    record.pending_operation_id,
                    record.pending_proposal_digest,
                    record.last_receipt_status,
                    record.version,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
            self._insert_events(record.run_id, (event,))
        return self.get_run(record.run_id)

    def get_run(self, run_id: str) -> RunRecord:
        row = self._connection.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise InvalidOperationStateError(f"unknown run_id {run_id!r}")
        config = RunConfig.model_validate_json(row["config_json"])
        record = RunRecord(
            run_id=row["run_id"],
            status=row["status"],
            config=config,
            config_digest=row["config_digest"],
            pending_operation_id=row["pending_operation_id"],
            pending_proposal_digest=row["pending_proposal_digest"],
            last_receipt_status=row["last_receipt_status"],
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        if record.config_digest != record.config.calculated_digest():
            raise InvalidOperationStateError(f"run {run_id!r} has a corrupted config")
        return record

    def transition(
        self,
        run_id: str,
        *,
        expected_status: RunStatus,
        new_status: RunStatus,
        pending_operation_id: str | None,
        pending_proposal_digest: str | None,
        last_receipt_status: str | None,
        updated_at,
        events: tuple[NewRunEvent, ...],
    ) -> RunRecord:
        current = self.get_run(run_id)
        if current.status is not expected_status:
            raise InvalidOperationStateError(
                f"cannot transition run {run_id!r} from {current.status.value!r}; "
                f"expected {expected_status.value!r}"
            )
        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE runs
                SET status = ?, pending_operation_id = ?,
                    pending_proposal_digest = ?, last_receipt_status = ?,
                    version = version + 1, updated_at = ?
                WHERE run_id = ? AND status = ? AND version = ?
                """,
                (
                    new_status.value,
                    pending_operation_id,
                    pending_proposal_digest,
                    last_receipt_status,
                    updated_at.isoformat(),
                    run_id,
                    expected_status.value,
                    current.version,
                ),
            )
            if cursor.rowcount != 1:
                raise InvalidOperationStateError(
                    f"concurrent transition detected for run {run_id!r}"
                )
            self._insert_events(run_id, events)
        return self.get_run(run_id)

    def append_events(
        self, run_id: str, events: tuple[NewRunEvent, ...]
    ) -> tuple[RunEvent, ...]:
        self.get_run(run_id)
        with self._connection:
            self._insert_events(run_id, events)
        return self.list_events(run_id)

    def list_events(self, run_id: str) -> tuple[RunEvent, ...]:
        self.get_run(run_id)
        rows = self._connection.execute(
            "SELECT * FROM run_events WHERE run_id = ? ORDER BY sequence", (run_id,)
        ).fetchall()
        return tuple(
            RunEvent(
                sequence=row["sequence"],
                event_id=row["event_id"],
                run_id=row["run_id"],
                dedupe_key=row["dedupe_key"],
                event_type=row["event_type"],
                payload=json.loads(row["payload_json"]),
                occurred_at=row["occurred_at"],
            )
            for row in rows
        )

    def _insert_events(self, run_id: str, events: tuple[NewRunEvent, ...]) -> None:
        for event in events:
            payload_json = json.dumps(
                event.payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            cursor = self._connection.execute(
                """
                INSERT INTO run_events (
                    event_id, run_id, dedupe_key, event_type, payload_json, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, dedupe_key) DO NOTHING
                """,
                (
                    event.event_id,
                    run_id,
                    event.dedupe_key,
                    event.event_type.value,
                    payload_json,
                    event.occurred_at.isoformat(),
                ),
            )
            if cursor.rowcount == 0:
                existing = self._connection.execute(
                    """
                    SELECT event_type, payload_json
                    FROM run_events
                    WHERE run_id = ? AND dedupe_key = ?
                    """,
                    (run_id, event.dedupe_key),
                ).fetchone()
                if (
                    existing is None
                    or existing["event_type"] != event.event_type.value
                    or existing["payload_json"] != payload_json
                ):
                    raise InvalidOperationStateError(
                        f"event dedupe key {event.dedupe_key!r} was reused "
                        "with different content"
                    )
