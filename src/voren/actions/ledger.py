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
        approval_json = approval.model_dump_json()
        if self._transition(
            operation_id,
            expected_statuses=("prepared",),
            status="authorized",
            approval_json=approval_json,
        ):
            return

        row = self._get_row(operation_id)
        status = str(row["status"])
        if status == "authorized" and row["approval_json"] == approval_json:
            return
        raise InvalidOperationStateError(
            f"cannot authorize operation {operation_id!r} from state {status!r}"
        )

    def mark_committing(self, operation_id: str) -> None:
        if self._transition(
            operation_id,
            expected_statuses=("authorized",),
            status="committing",
        ):
            return
        status = self.get_status(operation_id)
        raise InvalidOperationStateError(
            f"cannot commit operation {operation_id!r} from state {status!r}"
        )

    def mark_ambiguous(self, operation_id: str) -> None:
        if self._transition(
            operation_id,
            expected_statuses=("committing",),
            status="ambiguous",
        ):
            return
        status = self.get_status(operation_id)
        raise InvalidOperationStateError(
            f"cannot mark operation {operation_id!r} ambiguous from state {status!r}"
        )

    def store_receipt(self, receipt: ActionReceipt) -> None:
        row = self._get_row(receipt.operation_id)
        proposal = ActionProposal.model_validate_json(row["proposal_json"])
        approval_json = row["approval_json"]
        approval = (
            None
            if approval_json is None
            else ApprovalDecision.model_validate_json(approval_json)
        )
        if receipt.proposal_digest != proposal.digest:
            raise InvalidOperationStateError(
                f"receipt for operation {receipt.operation_id!r} has the wrong proposal digest"
            )
        if approval is None or receipt.approval_id != approval.approval_id:
            raise InvalidOperationStateError(
                f"receipt for operation {receipt.operation_id!r} has the wrong approval"
            )

        receipt_json = receipt.model_dump_json()
        if row["receipt_json"] == receipt_json:
            return
        if row["receipt_json"] is not None:
            raise InvalidOperationStateError(
                f"operation {receipt.operation_id!r} already has a different receipt"
            )
        if self._transition(
            receipt.operation_id,
            expected_statuses=("committing", "ambiguous"),
            status=receipt.status.value,
            receipt_json=receipt_json,
            require_empty_receipt=True,
        ):
            return

        current = self._get_row(receipt.operation_id)
        if current["receipt_json"] == receipt_json:
            return
        status = str(current["status"])
        if current["receipt_json"] is not None:
            raise InvalidOperationStateError(
                f"operation {receipt.operation_id!r} already has a different receipt"
            )
        raise InvalidOperationStateError(
            f"cannot store receipt for operation {receipt.operation_id!r} from state {status!r}"
        )

    def _get_row(self, operation_id: str) -> sqlite3.Row:
        row = self._connection.execute(
            "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        if row is None:
            raise InvalidOperationStateError(f"unknown operation_id {operation_id!r}")
        return row

    def _transition(
        self,
        operation_id: str,
        *,
        expected_statuses: tuple[str, ...],
        status: str,
        require_empty_receipt: bool = False,
        **fields: str,
    ) -> bool:
        if not expected_statuses:
            raise ValueError("expected_statuses must not be empty")
        assignments = ["status = ?", "updated_at = ?"]
        values: list[str] = [status, datetime.now(UTC).isoformat()]
        for name, value in fields.items():
            if name not in {"approval_json", "receipt_json"}:
                raise ValueError(f"unsupported ledger field {name!r}")
            assignments.append(f"{name} = ?")
            values.append(value)
        placeholders = ", ".join("?" for _ in expected_statuses)
        values.append(operation_id)
        values.extend(expected_statuses)
        receipt_guard = " AND receipt_json IS NULL" if require_empty_receipt else ""
        with self._connection:
            cursor = self._connection.execute(
                f"""UPDATE operations
                    SET {', '.join(assignments)}
                    WHERE operation_id = ?
                      AND status IN ({placeholders}){receipt_guard}""",  # noqa: S608
                values,
            )
        return cursor.rowcount == 1
