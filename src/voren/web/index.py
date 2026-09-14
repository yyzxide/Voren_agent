"""Durable idempotency and browser-visible result snapshots."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from voren.web.models import RunView


class WebRequestConflictError(RuntimeError):
    pass


class WebRequestInProgressError(RuntimeError):
    pass


class WebRunNotFoundError(RuntimeError):
    pass


class SQLiteWebRunIndex:
    """Use a fresh connection per call so sync HTTP workers can change threads."""

    def __init__(self, database: Path) -> None:
        self._database = database
        database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS web_run_requests (
                    client_request_id TEXT PRIMARY KEY,
                    request_digest TEXT NOT NULL,
                    run_id TEXT NOT NULL UNIQUE,
                    response_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def reserve(
        self,
        *,
        client_request_id: str,
        request_digest: str,
        run_id: str,
        now: datetime,
    ) -> tuple[str, RunView | None, bool]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT request_digest, run_id, response_json
                FROM web_run_requests
                WHERE client_request_id = ?
                """,
                (client_request_id,),
            ).fetchone()
            if row is not None:
                if row["request_digest"] != request_digest:
                    raise WebRequestConflictError(
                        "client_request_id is already bound to another request"
                    )
                view = (
                    None
                    if row["response_json"] is None
                    else RunView.model_validate_json(row["response_json"])
                )
                return row["run_id"], view, False
            connection.execute(
                """
                INSERT INTO web_run_requests (
                    client_request_id, request_digest, run_id, response_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, NULL, ?, ?)
                """,
                (
                    client_request_id,
                    request_digest,
                    run_id,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            return run_id, None, True

    def save(self, view: RunView) -> None:
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE web_run_requests
                SET response_json = ?, updated_at = ?
                WHERE client_request_id = ? AND run_id = ?
                """,
                (
                    view.model_dump_json(),
                    view.updated_at.isoformat(),
                    view.client_request_id,
                    view.run_id,
                ),
            )
            if updated.rowcount != 1:
                raise WebRunNotFoundError(f"unknown web run {view.run_id!r}")

    def abandon(self, *, client_request_id: str, run_id: str) -> None:
        """Release a reservation only if no observable result was persisted."""

        with self._connect() as connection:
            connection.execute(
                """
                DELETE FROM web_run_requests
                WHERE client_request_id = ? AND run_id = ? AND response_json IS NULL
                """,
                (client_request_id, run_id),
            )

    def get(self, run_id: str) -> RunView:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT response_json FROM web_run_requests WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            raise WebRunNotFoundError(f"unknown web run {run_id!r}")
        if row["response_json"] is None:
            raise WebRequestInProgressError(f"web run {run_id!r} is in progress")
        return RunView.model_validate_json(row["response_json"])

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self._database), timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection
