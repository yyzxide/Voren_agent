# Architecture

[简体中文](ARCHITECTURE.zh-CN.md)

## 1. Design principles

1. **The model proposes; deterministic code authorizes and executes.**
2. **External success is measured from resulting state, not fluent output.**
3. **Observations are data, not authority.**
4. **Experience becomes a candidate before it becomes behavior.**
5. **Every promoted behavior is bound to evidence, versions, and evaluation.**
6. **Build one complete vertical slice before generalizing the platform.**

## 2. System context

```text
                                  +----------------------+
                                  | Evaluation Runner    |
                                  | utility + security   |
                                  +----------+-----------+
                                             |
                                             v
+----------+     +---------------+    +------+-------+    +----------------+
| Operator |<--->| CLI / API     |<-->| Run Manager  |<-->| Agent Runtime  |
+----------+     +---------------+    +------+-------+    +-------+--------+
                                             |                    |
                                             |                    v
                                      +------+-------+    +-------+--------+
                                      | Event Store  |    | Action Gateway |
                                      +------+-------+    +-------+--------+
                                             |                    |
                         +-------------------+                    v
                         v                               +--------+---------+
                +--------+---------+                     | World Adapters   |
                | Learning Control |                     | AgentDojo first  |
                +--------+---------+                     +------------------+
                         |
                         v
                +--------+---------+
                | Memory / Skills  |
                +------------------+
```

The first implementation may run these components in one process. The
boundaries are logical contracts, not a requirement for microservices.

## 3. Runtime flow

1. `RunManager` creates an immutable run configuration containing model,
   prompt, policy, tool-schema, and active-skill versions.
2. `ContextAssembler` loads the operator request, trusted profile entries, and
   the metadata of available skills.
3. `AgentRuntime` requests the next model response.
4. Text output may finish the run. Tool calls become `ActionProposal` objects.
5. `ActionGateway` validates schema, provenance, effect policy, and
   preconditions.
6. If approval is needed, the run pauses with an `ApprovalRequest` bound to the
   normalized action digest.
7. On authorization, the adapter commits the action under an operation key.
8. A verifier reads the resulting world state and records an `ActionReceipt`.
9. The observation returns to the model if more steps are allowed.
10. The terminal outcome is recorded and may later be considered by the
    learning pipeline.

## 4. Core components

### 4.1 Run Manager

Owns lifecycle and durable state transitions:

```text
created -> running -> waiting_approval -> running -> completed
                   \-> cancelled      \-> failed
```

It is responsible for pause/resume, cancellation, budgets, and recovery after
process interruption. A resumed run keeps the exact configuration frozen at
creation time.

### 4.2 Agent Runtime

The runtime is intentionally small:

- provider-neutral model request/response types;
- bounded tool-use loop;
- structured stop reasons;
- cancellation and timeout propagation;
- context-budget accounting; and
- event emission at every model and tool boundary.

The first version should not depend on a graph framework. Framework integration
can be added later through an adapter if it serves a demonstrated need.

### 4.3 Action Gateway

An `ActionDefinition` contains at least:

```text
name
input_schema
effect_manifest
aggregate_risk
required_capabilities
approval_policy
idempotency_strategy
precondition_checker
postcondition_verifier
redaction_policy
```

An effect manifest is a list because one tool call may touch several resources.
Each effect declares at least:

```text
resource
kind
target_template
cardinality
reversibility
sensitivity
verifier
```

Initial effect kinds and policies are:

| Kind | Example | Initial policy |
| --- | --- | --- |
| `pure_read` | search email, inspect calendar | execute if in task scope |
| `stateful_read` | fetch unread email and mark it read | treat as a declared mutation |
| `local_draft` | prepare reply or event proposal | no external effect |
| `create` / `update` | create or reschedule an event | exact-effect approval |
| `send` | send email or event invitation | preview and exact-effect approval |
| `delete` | delete email or cancel event | explicit approval plus verifier |
| `compensate` | cancel a just-created event | new explicit action, not silent rollback |

For example, AgentDojo's `create_calendar_event` materializes two effects:

```text
calendar.events : create one event
inbox.emails    : send one invitation email
```

The proposal digest binds both effects. An approval for only the calendar
change is insufficient authorization for the email send.

The policy may become contextual later, but all external mutations require
approval in the first vertical slice.

The first version does not treat an automatically inferred natural-language
plan as a security boundary. Its `AuthorityEnvelope` is intentionally narrow:

- the deployment configuration decides which read tools are visible;
- an exact operator approval grants a one-use capability for one normalized
  external mutation; and
- no model-generated plan can broaden either rule.

Later versions may ask the operator to approve a structured multi-action plan,
but the approved structure, not the model's internal interpretation, becomes
the authority envelope.

### 4.4 Operation ledger and verification

Every mutation receives a stable `operation_id`. Before retrying an ambiguous
failure, Voren checks the ledger and the external postcondition. It must not
assume that a timeout means the remote action failed.

An `ActionReceipt` records:

- proposal and approval digests;
- adapter and tool version;
- start and completion timestamps;
- precondition result;
- normalized result or failure;
- expected and observed state change;
- verifier result; and
- compensation availability.

The verifier compares the materialized effect manifest with the actual state
delta. A missing declared effect and an unexpected extra effect are both
failures. Adapter contract tests perform the same comparison against controlled
fixtures so an upstream implementation change cannot silently alter Voren's
authority boundary.

### 4.5 Event store

The event stream is append-only. Initial event families include:

- run lifecycle;
- model request and response metadata;
- skill discovery and activation;
- action proposal;
- policy and approval decision;
- tool invocation and observation;
- verification and state delta;
- memory or skill candidate creation; and
- evaluation and promotion decision.

Large or sensitive payloads should be stored as redacted, content-addressed
artifacts rather than copied into every event.

### 4.6 Memory system

Voren separates four stores:

| Store | Purpose | Mutation policy |
| --- | --- | --- |
| Working context | current run state | ephemeral |
| Profile | durable facts and preferences | explicit user evidence or reviewed candidate |
| Episodes | immutable execution evidence | append-only, retention-controlled |
| Skills | reusable procedures | immutable versions and gated active pointer |

No vector database is required initially. Structured metadata and simple text
search are sufficient until retrieval quality becomes a measured bottleneck.

### 4.7 Skill representation

Each skill version is a directory containing:

```text
skill-name/
  SKILL.md       # Agent Skills-compatible human-readable procedure
  skill.yaml     # Voren execution and evaluation contract
  references/    # optional supporting material
  scripts/       # optional deterministic helpers
```

`skill.yaml` may declare scope, allowed tools, preconditions, risk limits,
verifier references, parent version, source evidence, and required evaluation
suites. Prose inside `SKILL.md` cannot grant permissions.

Skill lifecycle:

```text
draft -> candidate -> evaluating -> active -> superseded -> archived
             |             |
             +-> rejected  +-> rejected
```

Promotion atomically changes the active version pointer. Rollback selects an
already evaluated immutable version; it does not rewrite history.

### 4.8 Learning control

The learning path runs outside the foreground action loop:

1. select eligible episodes;
2. discard untrusted instructions while retaining task data;
3. classify the durable learning type;
4. generate a bounded candidate edit;
5. validate representation and capability changes;
6. evaluate candidate versus active version;
7. request review when policy requires it; and
8. promote, reject, or retain for more evidence.

A content edit and a capability expansion are different changes. Adding a new
tool, network destination, secret, or effect class requires the stronger gate.

The foreground agent has no write access to the active skill store. The
learning worker may write only to the candidate namespace, and the promotion
component may change only the active version pointer after a valid evaluation
decision. This separation limits what a poisoned foreground trace can mutate
directly, while evaluation remains necessary because a generated candidate can
still contain harmful content.

### 4.9 Evaluation runner

The runner freezes model, prompt, tools, active skill, candidate skill, dataset,
randomness controls, and evaluator versions. It executes paired baseline and
candidate trials and emits machine-readable results plus a human-readable
report.

## 5. Provenance and authorization

Every message, artifact, field, and evidence reference carries a source class.
The initial policy enforces:

- only trusted operator intent can establish the task's authority envelope;
- tool output may fill data parameters but cannot expand that envelope;
- the model cannot convert an observation into an approval;
- untrusted content cannot directly update profile or skill stores;
- skill prose cannot override action policy; and
- verifier success does not retroactively authorize an unauthorized action.

This is a practical information-flow rule, not a claim of formal noninterference.
Tagging a whole email as untrusted also does not automatically distinguish a
malicious instruction from legitimate task data inside that email. Voren uses
provenance to restrict authority and durable writes, then relies on adversarial
evaluation to measure the remaining model-level failure risk.

## 6. Failure semantics

The design must distinguish:

- model failure before proposal;
- rejected or expired approval;
- precondition failure before side effect;
- confirmed adapter failure before side effect;
- ambiguous commit outcome;
- successful commit with failed verification; and
- successful action followed by later workflow failure.

Only confirmed pre-commit failures are safe to retry blindly. Ambiguous outcomes
require external-state inspection. Irreversible success followed by workflow
failure requires human-visible partial-completion reporting, not a false atomic
rollback claim.

## 7. Initial technology choices

- Python 3.12;
- `uv` for environment and dependency management;
- Pydantic v2 for domain and tool contracts;
- `asyncio` for cancellation-aware runtime operations;
- SQLite for the first durable event, operation, and version ledgers;
- pytest for unit, state-machine, fault-injection, and integration tests;
- one OpenAI-compatible model adapter first; and
- AgentDojo pinned behind a Voren-owned adapter.

FastAPI and a browser UI are deferred until the CLI vertical slice is stable.

## 8. Proposed package boundaries

```text
src/voren/
  domain/       # immutable contracts and enums
  runtime/      # run manager, loop, context, budgets
  actions/      # definitions, policy, approval, receipts, verification
  adapters/     # model and world adapters
  memory/       # profile and episode stores
  skills/       # discovery, loading, versions, lifecycle
  learning/     # routing, candidate creation, promotion
  evaluation/   # trials, graders, reports
  interfaces/   # CLI first, API later
```

These boundaries are provisional. The first vertical slice should test them
before the package tree is fully scaffolded.
