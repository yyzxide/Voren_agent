# Phase 1 Verified Email Action

[简体中文](PHASE1_EMAIL_ACTION.zh-CN.md)

## Contract

Voren's AgentDojo action envelope now includes a standalone `send_email`
contract in addition to `create_calendar_event`. The workspace bundle is bumped
to `agentdojo-workspace-v1.2.2/voren-contract-v2` because the externally visible
capability set changed.

The input schema requires non-empty recipients, subject, and body; normalizes
and deduplicates recipients, CC, and BCC; rejects overlap between those classes;
and currently permits no attachments. The proposal materializes one irreversible,
confidential `inbox.emails:send` effect containing the exact normalized fields.

## Execution and recovery

Both the deterministic Fake Workspace and pinned AgentDojo adapter implement the
same protocol:

```text
prepare -> exact approval -> atomic claim -> provider call -> observe -> verify
```

The adapter records the predicted AgentDojo email ID and pre-state before the
provider call. A failure after an observable email appears is ambiguous, never a
safe retry. Reconciliation observes that stable ID and never resends. The common
operation ledger supplies cross-process claims, durable receipts, and duplicate
suppression for both calendar and email actions.

## Evaluation coverage

The frozen smoke manifest is bumped to `phase1-agentdojo-smoke-v2` and includes:

- calendar injection task 2;
- email forwarding/exfiltration task 3; and
- email security-code exfiltration task 4.

In raw agent-behavior mode, an exact scripted malicious email action is allowed
and the official AgentDojo security grader observes the attack. In runtime-
enforcement mode, the same proposal is not part of the benign user's exact
ground truth, so it is rejected before commit. These deterministic tests prove
measurement and enforcement semantics, not live-model injection resistance.

## Tested invariants

- exact AgentDojo email state passes the official task-4 security grader;
- observed effects exactly match the approved email fields;
- duplicate commit returns the durable receipt without a second send;
- recipient-class overlap is rejected before a proposal exists; and
- behavior and enforcement attack metrics remain separate for tasks 3 and 4.
