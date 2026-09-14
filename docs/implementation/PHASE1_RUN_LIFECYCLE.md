# Phase 1 Durable Run Lifecycle

[简体中文](PHASE1_RUN_LIFECYCLE.zh-CN.md)

## Delivered scope

This slice adds a durable control plane around the existing action protocol:

```text
created -> running -> waiting_approval -> completed
                                  |\----> failed
                                  |\----> cancelled
                                  \-----> needs_reconciliation -> completed
```

`RunConfig`, pending proposal identity, state version, and the terminal receipt
status are stored in SQLite. A run paused before approval can close all database
connections, rebuild the runtime objects, and resume the same proposal.

## Persistence model

The implementation uses the same SQLite database file for three distinct logical
stores:

- `operations`: proposal, approval, operation state, and receipt;
- `runs`: frozen config, lifecycle state, pending operation, and optimistic
  state version; and
- `run_events`: append-only ordered lifecycle evidence.

Run configuration carries a SHA-256 digest. Loading a run recomputes the digest
and rejects corrupted or unexpectedly changed configuration.

## Pause and resume protocol

1. `RunManager.start_new_run` persists `run.created` and `run.started`.
2. `propose_action` asks `ActionGateway` to normalize the action and then stores
   its operation ID and exact proposal digest.
3. The run moves to `waiting_approval`; no adapter commit has occurred.
4. After restart, `resume_with_approval` reloads the proposal from the operation
   ledger, validates the decision, and commits through the same gateway.
5. The receipt maps to a run terminal state:
   - `verified` -> `completed`;
   - known pre-commit `failed` -> `failed`;
   - `ambiguous` or `verification_failed` -> `needs_reconciliation`.
6. If a receipt was persisted but the process stopped before updating the run,
   `recover_pending_receipt` completes the event chain without committing again.
7. If the process stopped after the external commit but before receipt storage,
   the gateway re-observes by stable operation ID. Exact evidence produces a
   recovered receipt; inconclusive evidence persists `ambiguous`, retains the
   pending operation, and never redispatches it.
8. `reconcile_pending_action` can re-observe later and advance a
   `needs_reconciliation` run to `completed` once exact effects are visible.

A rejected approval cancels this single-action run without calling the adapter.
An invalid or stale approval is audited while the run remains paused for a new
valid decision.

## Append-only event rules

The lifecycle-only path contains:

```text
run.created
run.started
action.proposed
run.waiting_approval
approval.accepted | approval.rejected | approval.invalid
action.receipt
action.reconciled
run.completed | run.failed | run.cancelled | run.needs_reconciliation
```

Events receive a SQLite sequence and a per-run deduplication key. Reusing a key
with byte-equivalent canonical JSON is an idempotent replay. Reusing it with a
different event type or payload is rejected instead of silently losing audit
information. The store exposes no update or delete event operation.

Proposal and receipt events contain digests, effect classifications,
verification results, and external references, but do not duplicate action
arguments or effect attributes. The operation ledger still needs the full
proposal to resume execution; encryption and retention for that primary copy
remain a later storage-policy decision.

## Source map

| Concern | File |
| --- | --- |
| Run/config/event models | `src/voren/runs/models.py` |
| SQLite run and event store | `src/voren/runs/store.py` |
| Pause/resume orchestration | `src/voren/runs/manager.py` |
| Operation approval recovery | `src/voren/actions/ledger.py` |
| Lifecycle and crash-boundary tests | `tests/test_run_lifecycle.py` |
| Restart demonstration | `scripts/demo_run_lifecycle.py` |

## Run it

```bash
python scripts/demo_run_lifecycle.py
```

For the deterministic demonstration used by tests and CI:

```bash
python scripts/demo_run_lifecycle.py --simulate-approval
```

The demo deliberately closes and reopens the SQLite-backed components while
the run is waiting for approval.

## Current evidence

Lifecycle tests cover:

- ordered happy-path events;
- resume after reopening both SQLite stores;
- operator rejection without external effects;
- invalid approval audit while remaining paused;
- ambiguous commit mapped to `needs_reconciliation`;
- idempotent resume after approval was already persisted;
- durable receipt recovery after a simulated process crash; and
- read-only recovery when external effects exist before receipt persistence;
- an inconclusive result remaining ambiguous without redispatch;
- a second reconciliation after external state becomes visible; and
- idempotent event replay plus conflicting dedupe-key rejection.

## Remaining boundary

This slice implements the reconciliation protocol for the commit/receipt crash
window, but production guarantees still depend on the adapter. It must
rediscover the remote operation through a stable operation ID, provider
idempotency key, or attributable external postcondition. Without that query
capability, Voren remains `ambiguous` and requires human handling; it does not
claim end-to-end exactly-once delivery.

The deterministic Fake Workspace tests prove that the control flow never
redispatches automatically. They do not replace persistence and consistency
validation against a real email or calendar provider.

## Suggested reading order

1. `runs/models.py`: identify the lifecycle states and immutable evidence.
2. `runs/store.py`: follow one state update and its events inside a transaction.
3. `runs/manager.py`: trace proposal, approval, receipt, and terminal mapping.
4. `test_run_lifecycle.py`: use each failure case to challenge the invariants.

You should be able to explain why `needs_reconciliation` is not called `failed`,
why the proposal digest is stored twice, and why reconciliation may observe but
must not redispatch.
