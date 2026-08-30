"""SQLite-backed operation state for pause/resume and idempotent replay."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from voren.actions.errors import InvalidOperationStateError, InvalidProposalError
from voren.actions.models import ActionProposal, ActionReceipt, ApprovalDecision


class SQLiteOperationLedger:
    def __init__(self, path: str | Path) -> None:
        self._connection = sqlite3.connect(str(path))
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS operations (
                operation_id TEXT PRIMARY KEY,
                proposal_json TEXT NOT NULL,
                proposal_digest TEXT NOT NULL,
                status TEXT NOT NULL,
                approval_json TEXT,
                receipt_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def register(self, proposal: ActionProposal) -> None:
        now = datetime.now(UTC).isoformat()
        proposal_json = proposal.model_dump_json()
        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO operations (
                        operation_id, proposal_json, proposal_digest, status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, 'prepared', ?, ?)
                    """,
                    (proposal.operation_id, proposal_json, proposal.digest, now, now),
                )
        except sqlite3.IntegrityError as error:
            existing = self.get_proposal(proposal.operation_id)
            if existing != proposal:
                raise InvalidProposalError(
                    f"operation_id {proposal.operation_id!r} is already bound to another proposal"
                ) from error

    def get_proposal(self, operation_id: str) -> ActionProposal:
        row = self._get_row(operation_id)
        return ActionProposal.model_validate_json(row["proposal_json"])

    def get_status(self, operation_id: str) -> str:
        return str(self._get_row(operation_id)["status"])

    def get_receipt(self, operation_id: str) -> ActionReceipt | None:
        receipt_json = self._get_row(operation_id)["receipt_json"]
        if receipt_json is None:
            return None
        return ActionReceipt.model_validate_json(receipt_json)

    def get_approval(self, operation_id: str) -> ApprovalDecision | None:
        approval_json = self._get_row(operation_id)["approval_json"]
        if approval_json is None:
            return None
        return ApprovalDecision.model_validate_json(approval_json)

    def authorize(self, operation_id: str, approval: ApprovalDecision) -> None:
        row = self._get_row(operation_id)
        status = str(row["status"])
        approval_json = approval.model_dump_json()
        if status == "authorized" and row["approval_json"] == approval_json:
            return
        if status != "prepared":
            raise InvalidOperationStateError(
                f"cannot authorize operation {operation_id!r} from state {status!r}"
            )
        self._update(
            operation_id,
            status="authorized",
            approval_json=approval_json,
        )

    def mark_committing(self, operation_id: str) -> None:
        status = self.get_status(operation_id)
        if status != "authorized":
            raise InvalidOperationStateError(
                f"cannot commit operation {operation_id!r} from state {status!r}"
            )
        self._update(operation_id, status="committing")

    def mark_ambiguous(self, operation_id: str) -> None:
        status = self.get_status(operation_id)
        if status != "committing":
            raise InvalidOperationStateError(
                f"cannot mark operation {operation_id!r} ambiguous from state {status!r}"
            )
        self._update(operation_id, status="ambiguous")

    def store_receipt(self, receipt: ActionReceipt) -> None:
        self._get_row(receipt.operation_id)
        self._update(
            receipt.operation_id,
            status=receipt.status.value,
            receipt_json=receipt.model_dump_json(),
        )

    def _get_row(self, operation_id: str) -> sqlite3.Row:
        row = self._connection.execute(
            "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        if row is None:
            raise InvalidOperationStateError(f"unknown operation_id {operation_id!r}")
        return row

    def _update(self, operation_id: str, *, status: str, **fields: str) -> None:
        assignments = ["status = ?", "updated_at = ?"]
        values: list[str] = [status, datetime.now(UTC).isoformat()]
        for name, value in fields.items():
            if name not in {"approval_json", "receipt_json"}:
                raise ValueError(f"unsupported ledger field {name!r}")
            assignments.append(f"{name} = ?")
            values.append(value)
        values.append(operation_id)
        with self._connection:
            self._connection.execute(
                f"UPDATE operations SET {', '.join(assignments)} WHERE operation_id = ?",  # noqa: S608
                values,
            )
