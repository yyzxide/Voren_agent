# Product Definition

[简体中文](PRODUCT.zh-CN.md)

## 1. Product thesis

Voren is a single-operator action agent for email and calendar workflows. It
executes tasks through controlled tools and converts selected, verified
experience into versioned procedural skills.

The product is deliberately narrower than a general personal assistant. Its
primary artifact is a trustworthy learning loop around real side effects:

```text
request -> action -> observed outcome -> candidate learning -> evaluation -> promotion
```

## 2. Problem statement

An LLM can often complete a one-off email or scheduling task, but a persistent
agent introduces harder problems:

- email and other tool results are untrusted data that may contain
  instructions;
- sending a message or changing a calendar has external consequences;
- retries can duplicate irreversible actions;
- a user correction may be a durable preference, a reusable procedure, or only
  a one-off fact;
- a skill produced from one successful trace may regress other tasks; and
- a human-readable skill diff does not prove that behavior improved.

Voren treats these as runtime and evaluation problems, not prompt-writing
problems.

## 3. Primary user

The first user is one trusted operator using Voren on their own behalf. The
initial system is not multi-tenant and does not assume mutually untrusted human
users.

## 4. Golden workflow

The reference scenario is:

> Find Alice's email about the project review. Compare the candidate times in
> the thread with my calendar, prepare a 30-minute meeting this week, show me
> the exact reply and event first, then send the reply and create the event
> after I confirm. In future, prefer 30-minute meetings with Alice and avoid my
> lunch break.

This scenario is intentionally chosen to exercise:

- multi-step tool use across email and calendar;
- temporal and participant reasoning;
- separation of external data from trusted intent;
- exact-effect approval;
- idempotent mutation and postcondition verification;
- profile-memory versus procedural-skill classification; and
- later reuse of validated experience.

## 5. Product behavior

### 5.1 Execute a task

Voren accepts a natural-language request, assembles relevant profile and skill
context, and runs a bounded model/tool loop. The model may propose tool calls,
but it cannot invoke external adapters directly.

### 5.2 Control external effects

Every tool declares its effect class, preconditions, approval behavior,
idempotency strategy, and verifier. External mutations pass through:

```text
prepare -> authorize -> commit -> verify
```

Approval is attached to the exact normalized action arguments. A material
change invalidates the approval.

### 5.3 Record evidence

Each run emits an append-only event stream covering model turns, selected
skills, proposed actions, policy decisions, approvals, tool observations,
state verification, failures, and final outcome.

### 5.4 Learn conservatively

A completed run does not automatically become a skill. A learning router first
chooses one of:

- no durable write;
- profile or preference candidate;
- episode retention; or
- procedural-skill candidate.

Candidates retain links to their source evidence and remain inactive until
their applicable evaluation gate passes.

## 6. Trust model

The initial trust classes are:

| Source | Default trust | May authorize actions | May directly update durable learning |
| --- | --- | --- | --- |
| System policy | trusted | yes | policy only |
| Direct operator request | trusted intent | yes, within stated scope | explicit preferences only |
| Explicit operator correction | trusted evidence | yes | candidate evidence |
| Model output | untrusted proposal | no | no |
| Email, document, or tool result | untrusted data | no | no |
| Deterministic verifier | trusted evidence | no | candidate evidence |

Untrusted data may influence task data, such as a meeting date found in an
email, but it must not expand authority or become a persistent instruction.

## 7. Initial requirements

### Functional

- Run a bounded tool-calling loop with cancellation and deterministic stop
  reasons.
- Support AgentDojo email and calendar tools through an adapter.
- Pause and resume a run for an exact-effect approval.
- Prevent duplicate external mutations across retry and ambiguous timeout.
- Verify postconditions after every mutation.
- Persist a reconstructable run trace with trust provenance.
- Load a versioned skill through progressive disclosure.
- Create an inactive skill candidate from selected evidence.
- Evaluate active and candidate versions on the same held-out cases.
- Promote and roll back an exact immutable skill version.

### Quality

- Policy and verification must be deterministic code, not prompt-only rules.
- Secrets and sensitive tool payloads must be redacted from ordinary logs.
- Model, prompt, tool schema, skill, dataset, and evaluator versions must be
  recorded for reproducible experiments.
- Core domain logic must be testable without a live model or network.

## 8. Non-goals for the first release

- multi-agent planning, delegation, or review;
- mission DAGs, distributed leases, or worktree orchestration;
- a long-running omnichannel gateway;
- autonomous cron or heartbeat behavior;
- a general plugin marketplace;
- browser or desktop GUI automation;
- a general-purpose vector-memory platform;
- multiple real email/calendar providers; and
- automatic live promotion based only on LLM judgment or one successful run.

## 9. Product-level completion criteria

The first portfolio-ready release should demonstrate that:

1. the golden workflow completes end to end in a controlled environment;
2. no external mutation occurs without the configured authorization;
3. injected instructions in email content cannot authorize unrelated actions;
4. retry and recovery do not duplicate sent messages or calendar events;
5. the final external state is checked rather than assumed from tool success;
6. a bad candidate skill is rejected by a repeatable gate;
7. a promoted skill can be traced to evidence and rolled back; and
8. an evaluation report compares fixed baselines without invented claims.
