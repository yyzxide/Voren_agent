# Phase 1 Provenance-aware Agent Loop

[简体中文](PHASE1_AGENT_LOOP.zh-CN.md)

## Delivered scope

This slice connects Voren's durable run manager and safe-action gateway with a
provider-neutral, bounded model/tool loop:

```text
operator request
      |
      v
model response --pure read call--> labelled ToolObservation --+
      ^                                                       |
      +-------------------------------------------------------+
      |
      +--external action call--> ActionProposal --> waiting_approval
                                                     |
                                              operator decision
                                                     |
                                           commit -> observe -> verify
```

The credential-free test and demo model is scripted. It exercises the same
runtime contracts a real provider adapter will use, but it is not evidence of
LLM planning quality.

## Observation and authority contract

Every returned item has field-level provenance metadata:

```text
trust
source
source_ref
retrieved_by
retrieved_at
instruction_authority
```

Email bodies, calendar fields, and contacts are labelled
`external_untrusted` with `instruction_authority=false`. Their content remains
available as task data; labelling does not delete a malicious string or pretend
the model cannot see it. The system instruction and structured tool message
both state that such fields cannot issue commands.

This is an authority boundary, not a formal guarantee that an LLM will never be
influenced by prompt injection. Runtime enforcement still matters:

- only deployment-allowlisted tools are visible;
- the first read adapter exposes only `search_emails`,
  `get_day_calendar_events`, and `search_contacts_by_name`;
- AgentDojo reads execute against a deep environment copy, so an upstream
  "read" cannot mutate the live world;
- `get_unread_emails` is excluded because AgentDojo marks messages as read;
- an external action must be the only call in its model response;
- an external action creates a proposal and immediately pauses the run; and
- only a digest-bound operator approval can reach `ActionGateway.commit`.

## Bounded loop

`RuntimeLimits` enforces four independent budgets:

| Budget | Default | Failure behavior |
| --- | ---: | --- |
| Model steps | 8 | fail the run with `max_model_steps` |
| Total tool calls | 12 | reject the next call before execution |
| Repeated identical call | 2 | stop a same-name, same-arguments loop |
| One observation | 64,000 bytes | do not add it to model context |

Tool-call IDs must also be unique within a run. Unknown tools and mixed
read/action batches fail closed. A read-only final answer completes the run; a
mutation request returns `waiting_approval` and the exact `ActionProposal`.

## Event and data-retention boundary

The loop adds these event types:

```text
model.requested
model.responded
tool.called
tool.observed
runtime.limit_reached
```

Events store step numbers, counts, names, trust labels, sizes, and SHA-256
digests. Raw operator requests, model text, email bodies, and complete action
arguments are not copied into the event stream. The full proposal remains in
the operation ledger because approval recovery requires it. An action-proposal
event also links to the digests of preceding observations, preserving causal
evidence lineage without granting those observations authority. Approval binds
the exact effects; evidence lineage records why they were proposed.

This is minimization, not encryption. A later storage-policy slice still needs
artifact encryption, retention, and explicit redaction rules.

## Source map

| Concern | File |
| --- | --- |
| Provenance and observation models | `src/voren/observations/models.py` |
| Read-tool contracts | `src/voren/observations/read_tools.py` |
| Snapshot-enforced AgentDojo reads | `src/voren/adapters/agentdojo_reads.py` |
| Provider-neutral messages and limits | `src/voren/runtime/models.py` |
| Model protocol | `src/voren/runtime/ports.py` |
| Action contract to model-tool schema | `src/voren/runtime/tools.py` |
| Bounded loop and action pause | `src/voren/runtime/agent_loop.py` |
| Deterministic model adapter | `src/voren/testing/scripted_model.py` |
| Runtime boundary events | `src/voren/runs/manager.py` |
| Loop safety tests | `tests/test_agent_loop.py` |
| Provenance invariant tests | `tests/test_observation_provenance.py` |
| AgentDojo read-contract tests | `tests/integration/test_agentdojo_read_adapter.py` |
| Full credential-free demonstration | `scripts/demo_agent_loop.py` |

## Run it

```bash
python -m pip install -e '.[agentdojo]'
python -m unittest discover -s tests -v
python scripts/demo_agent_loop.py
```

The demo performs two AgentDojo reads, pauses on a two-effect calendar
proposal, prints that no commit has occurred, applies a deterministic operator
approval, verifies the receipt, and runs AgentDojo's official `user_task_18`
utility grader.

## Current evidence

Eight loop tests cover:

- read-to-action pause without external mutation;
- structured untrusted provenance in the model message;
- event payload minimization;
- read-only completion;
- repeated-call, total-call, step, and observation-size limits;
- rejection of a mixed read/action batch; and
- rejection of an unknown tool.

Three AgentDojo read-adapter tests cover malicious email content, calendar
provenance, snapshot purity, and exclusion of the stateful unread-email tool.
Two model-level tests reject untrusted instruction authority and tampered
observation digests. Together with the previous action and lifecycle suites,
Voren had 36 passing tests at this slice's checkpoint. The later
[Responses Model Adapter and CLI](PHASE1_MODEL_ADAPTER_CLI.md) brought its
checkpoint to 47 tests; the later
[dual-mode AgentDojo evaluation](PHASE1_EVALUATION_HARNESS.md) brings the current
complete suite to 54 tests.

## Remaining Phase 1 boundary

At this slice's checkpoint the provider-neutral contract had only a
`ScriptedModelAdapter`. The later Responses/CLI slice adds an API-capable
adapter; a recorded live golden run and injection evaluation still remain.

The loop transcript is currently in process. The run and approval pause are
durable, but a process death during a sequence of reads cannot yet resume the
exact model transcript. Real-provider timeouts, cancellation, token accounting,
and context compaction also remain unimplemented.

## Suggested reading order

1. `observations/models.py`: identify which facts are data and which fields can
   never claim authority.
2. `agentdojo_reads.py`: verify that read purity comes from snapshot execution,
   not naming convention.
3. `runtime/models.py` and `runtime/ports.py`: inspect the provider boundary.
4. `runtime/agent_loop.py`: follow budgets, read handling, and the hard action
   pause.
5. `test_agent_loop.py`: use each adversarial case to challenge those claims.

You should be able to explain why provenance tags and approval solve different
problems, why `get_unread_emails` is not a pure read, and why the scripted demo
proves orchestration but not model intelligence.
