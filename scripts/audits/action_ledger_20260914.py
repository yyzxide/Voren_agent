"""Read-only source audit: use temporary ledgers and a fake external world."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
import json

from voren.actions.gateway import ActionGateway
from voren.actions.ledger import SQLiteOperationLedger
from voren.actions.models import ApprovalDecision
from voren.adapters.fake_workspace import FakeWorkspaceAdapter
from voren.adapters.workspace_contracts import create_calendar_event_definition

NOW = datetime.now(UTC)
ARGS = {"title": "Audit-only meeting", "start_time": "2026-09-15 10:00", "end_time": "2026-09-15 11:00"}

def gateway(ledger, adapter):
    return ActionGateway(definitions=(create_calendar_event_definition(),), adapter=adapter, ledger=ledger, clock=lambda: NOW)

def seed(ledger, adapter):
    g = gateway(ledger, adapter)
    proposal = g.prepare("create_calendar_event", ARGS)
    approval = ApprovalDecision.for_proposal(proposal, approval_id="audit-approval", decided_by="audit-operator", decided_at=NOW, ttl=timedelta(minutes=30))
    return g, g.authorize(proposal, approval)

with TemporaryDirectory(prefix="voren-audit-race-") as tmp:
    path = Path(tmp) / "race.sqlite3"
    ledger = SQLiteOperationLedger(path)
    _, action = seed(ledger, FakeWorkspaceAdapter())
    ledger.close()
    barrier = Barrier(2)
    class InterleavedLedger(SQLiteOperationLedger):
        def _update(self, operation_id, *, status, **fields):
            if status == "committing":
                barrier.wait(timeout=10)
            return super()._update(operation_id, status=status, **fields)
    def claim(_):
        worker = InterleavedLedger(path)
        try:
            worker.mark_committing(action.proposal.operation_id)
            return "claimed"
        finally:
            worker.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, range(2)))
    print(json.dumps({"case": "two_connections_claim_one_operation", "results": claims, "expected_successful_claims": 1, "actual_successful_claims": claims.count("claimed")}))

with TemporaryDirectory(prefix="voren-audit-crash-") as tmp:
    path = Path(tmp) / "crash.sqlite3"
    adapter = FakeWorkspaceAdapter()
    ledger = SQLiteOperationLedger(path)
    g, action = seed(ledger, adapter)
    def fail_receipt(_):
        raise RuntimeError("simulated crash after external effects, before receipt persistence")
    ledger.store_receipt = fail_receipt
    try:
        g.commit(action)
    except RuntimeError:
        pass
    ledger.close()
    reopened = SQLiteOperationLedger(path)
    recovery = None
    try:
        gateway(reopened, adapter).commit(action)
    except Exception as error:
        recovery = type(error).__name__
    print(json.dumps({"case": "external_commit_before_receipt_crash", "external_events": len(adapter.events), "ledger_status": reopened.get_status(action.proposal.operation_id), "receipt_exists": reopened.get_receipt(action.proposal.operation_id) is not None, "retry_error": recovery}))
    reopened.close()
