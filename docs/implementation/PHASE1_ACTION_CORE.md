# Phase 1 Action Core

[简体中文](PHASE1_ACTION_CORE.zh-CN.md)

## Delivered scope

This slice implements the deterministic boundary between a proposed tool call
and external state. It does not yet include an LLM loop or a production
connector.

```text
validated input
      |
      v
EffectManifest -> ActionProposal -> ApprovalDecision
                                       |
                                       v
                              SQLite operation ledger
                                       |
                                       v
                                    commit
                                       |
                                       v
                              observe world state
                                       |
                                       v
                            verify -> ActionReceipt
```

Operation states currently used are:

```text
prepared -> authorized -> committing -> verified
                                  |\-> verification_failed
                                  |\-> ambiguous
                                  \-> failed
```

## Enforced invariants

1. Inputs are validated and normalized before effects are materialized.
2. A SHA-256 proposal digest binds the operation ID, action/version, normalized
   arguments, and every effect.
3. Approval must match both the operation ID and proposal digest and must not be
   expired.
4. The registered proposal is checked again immediately before commit. Nested
   JSON mutation after approval invalidates the digest.
5. A completed operation returns its stored receipt instead of calling the
   adapter again.
6. A timeout after commit triggers observation, not an automatic retry.
7. If an ambiguous timeout cannot be reconciled from state, the receipt remains
   `ambiguous`; repeating the call does not submit it again.
8. Verification fails on missing, mismatched, or unexpected effects.

## Golden action contract

The implemented `create_calendar_event` contract mirrors the AgentDojo spike.
One action proposal contains two effects:

- create `calendar.events/new_event`; and
- send `inbox.emails/new_sent_email` with the event invitation.

Both the Fake Workspace and pinned AgentDojo adapter commit and then
independently reconstruct observed effects from their state. The gateway
compares those observations with the approved manifest instead of trusting the
adapter's commit return value. The AgentDojo adapter additionally converts any
unrecognized workspace state delta into an unexpected effect.

## Source map

| Concern | File |
| --- | --- |
| Immutable wire models and exact-effect verifier | `src/voren/actions/models.py` |
| Prepare/authorize/commit/verify protocol | `src/voren/actions/gateway.py` |
| Durable operation state | `src/voren/actions/ledger.py` |
| Adapter protocol | `src/voren/actions/ports.py` |
| Shared email/calendar action contract | `src/voren/adapters/workspace_contracts.py` |
| Golden action plus deterministic world/faults | `src/voren/adapters/fake_workspace.py` |
| Pinned AgentDojo workspace adapter | `src/voren/adapters/agentdojo_workspace.py` |
| Fake and AgentDojo approval demos | `scripts/demo_safe_action.py`, `scripts/demo_agentdojo_action.py` |
| Contract and fault tests | `tests/test_action_gateway.py` |
| AgentDojo integration contract | `tests/integration/test_agentdojo_workspace_adapter.py` |

## Run it

For the full controlled-environment test suite:

```bash
python -m pip install -e '.[agentdojo]'
python -m unittest discover -s tests -v
python scripts/demo_agentdojo_action.py
```

For non-interactive local demonstrations only:

```bash
python scripts/demo_safe_action.py --simulate-approval
python scripts/demo_agentdojo_action.py --simulate-approval
```

The simulator is evaluation/demo infrastructure. It must not be used to claim
that real external actions received human approval.

## Current evidence

Eleven deterministic gateway tests cover:

- the calendar plus invitation-email manifest;
- approval bound to the wrong proposal;
- proposal mutation after digest creation;
- stale approval;
- normal verified commit;
- duplicate commit suppression;
- known failure before commit;
- timeout after commit with state-based recovery;
- unresolved ambiguous commit without retry;
- unexpected external effect detection; and
- SQLite receipt recovery after reopening the ledger.

Four AgentDojo integration tests additionally prove:

- the installed distribution and benchmark API match the research pin;
- the state committed through Voren passes AgentDojo's official
  `user_task_18` utility grader; and
- replaying the authorized action adds exactly one event and one email; and
- an unrelated AgentDojo state change is surfaced as an unexpected effect.

## Explicitly not complete

Phase 1 as a whole remains in progress. The next slice still needs:

- the bounded model/tool loop; and
- read-side email/calendar tools with provenance labels.

Durable pause/resume and append-only lifecycle events are implemented in
[Phase 1 Durable Run Lifecycle](PHASE1_RUN_LIFECYCLE.md).

Neither controlled workspace is presented as a production connector. The Fake
Workspace process state is not durable, and AgentDojo intentionally models an
in-memory benchmark rather than a real provider's delivery and OAuth behavior.
