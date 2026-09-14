# Roadmap and Source-Reading Plan

[简体中文](ROADMAP.zh-CN.md)

## 1. Delivery strategy

Voren will be built as a sequence of executable vertical slices. A phase is
complete only when its observable acceptance criteria pass; creating interfaces
or database tables alone is not completion.

## 2. Phase 0: environment and design spike

Status: **completed on 2026-08-30** for the environment and tool-contract
scope. See [AgentDojo Workspace Spike](research/AGENTDOJO_SPIKE.md).

Goals:

- run a pinned AgentDojo workspace task unchanged;
- inspect the email/calendar state and grading model;
- confirm dependency, license, and model-adapter constraints;
- record the first architecture decisions; and
- turn the golden workflow into concrete test fixtures.

Exit criteria:

- one baseline AgentDojo run can be reproduced;
- the exact enabled tools and state verifier are documented; and
- no production credentials are required.

Recorded outcome:

- AgentDojo distribution `0.1.35`, commit `a75aba7631d3ca5fb7ab938965c97ead2f9ff84b`;
- benchmark `v1.2.2`, suite `workspace`;
- golden case `user_task_18`, with `user_task_17` and `user_task_20` as controls;
- security smoke cases `injection_task_2`, `injection_task_3`, and
  `injection_task_4`;
- multi-resource effect manifests replace scalar read/write labels; and
- the upstream aggregate self-check is recorded as failing for known,
  decomposed reasons rather than represented as a green baseline.

## 3. Phase 1: safe-action vertical slice

Status: **in progress**. The action core, durable approval lifecycle, and
scripted provenance-aware loop are complete; see
[Phase 1 Action Core](implementation/PHASE1_ACTION_CORE.md),
[Phase 1 Run Lifecycle](implementation/PHASE1_RUN_LIFECYCLE.md), and
[Phase 1 Agent Loop](implementation/PHASE1_AGENT_LOOP.md). The API-capable
provider and CLI are described in
[Phase 1 Model Adapter and CLI](implementation/PHASE1_MODEL_ADAPTER_CLI.md).

Implement only what the golden workflow needs:

- CLI request and run lifecycle;
- one model adapter;
- bounded tool-calling loop;
- AgentDojo email/calendar adapter;
- append-only trace;
- local action proposals;
- exact-effect approval pause/resume;
- operation ledger; and
- postcondition verification.

Completed in the action-core sub-slice:

- typed multi-resource effect manifests and proposals;
- exact digest-bound approval with expiration checks;
- SQLite operation ledger and durable receipts;
- state-based exact-effect verification;
- duplicate-commit suppression and ambiguous-commit handling; and
- deterministic Fake Workspace faults and tests;
- pinned AgentDojo workspace adapter;
- official `user_task_18` utility-grader integration;
- durable run pause/resume and receipt recovery;
- append-only, ordered, deduplicated lifecycle events;
- provider-neutral model messages, tool calls, and definitions;
- bounded model steps, total calls, repeated calls, and observation size;
- provenance-labelled AgentDojo email, calendar, and contact reads;
- snapshot-enforced read purity and exclusion of stateful unread-email reads;
- a hard proposal-and-pause boundary for every external action;
- a credential-free AgentDojo `user_task_18` loop demonstration;
- an HTTPS Responses API adapter with provider-output replay;
- API-key and endpoint handling that excludes secrets from traces;
- an installed `voren agentdojo` command;
- terminal-only exact-effect approval with no auto-approval option;
- a frozen AgentDojo evaluation manifest for cases, modes, and provider config;
- a dual-mode runner separating agent behavior from runtime enforcement;
- JSON artifacts with integrity digests, per-mode metrics, and normalized traces;
- an explicit-case, explicit-mode `voren eval-agentdojo` command;
- Responses input/output/cached/cache-write/reasoning token parsing;
- completeness-aware run usage in events, CLI output, and evaluation artifacts;
- background Responses polling with explicit deadlines;
- operator/deadline cancellation propagated to the provider cancel endpoint;
- cancellation reason and provider-confirmation evidence in runs and artifacts.
- AES-256-GCM transcript checkpoints bound to the frozen run config, with
  retained budgets and process-restart recovery at safe model-request boundaries.

Still pending in Phase 1: one recorded live-model golden run and a live
prompt-injection artifact from the current runner. Coverage currently
includes only `injection_task_2`, which falls inside the implemented calendar
action contract; tasks 3/4 require a `send_email` action that does not exist yet.
Contract tests prove translation and orchestration, not live model planning or
injection resistance.

Exit criteria:

- the golden workflow completes end to end;
- tool results alone cannot trigger an unrelated mutation;
- all external writes have a matching approval and receipt; and
- a post-commit timeout does not duplicate the action.

## 4. Phase 2: memory and static skills

Status: **in progress**. Agent Skills-compatible parsing, progressive loading
APIs, content-addressed immutable versions, an atomic active pointer, exact
RunConfig skill references, and the first hand-written scheduling skill are
implemented. Explicit `no_skill`/`static_skill` runtime contexts now freeze and
load exact versions, enforce context size and tool compatibility, and emit a
metadata-only audit event. See [Phase 2 Static Skill Store](implementation/PHASE2_STATIC_SKILL_STORE.md)
and [Version-Pinned Skill Context](implementation/PHASE2_SKILL_CONTEXT.md).

Profile/Episode stores, semantic skill routing, and paired `no_skill`/
`static_skill` evaluation artifacts remain pending.

Implement:

- profile, episode, and skill stores;
- Agent Skills-compatible discovery;
- progressive skill loading;
- immutable skill versions and active pointer;
- trust provenance in context and evidence; and
- one hand-written scheduling skill.

Exit criteria:

- runs freeze exact memory and skill versions;
- preference data is not confused with procedural instructions;
- untrusted email content cannot directly update durable stores; and
- `no_skill`, `static_skill`, and optional `oracle_skill` trials are reproducible.

## 5. Phase 3: candidate learning and promotion

Status: **in progress**. Evidence eligibility, an admission policy limited to
bounded `SKILL.md` instruction edits, and SQLite persistence of inactive
candidates are complete. See
[Phase 3 Candidate Staging](implementation/PHASE3_CANDIDATE_STAGING.md).
An integrity-bound paired held-out runner and deterministic acceptance policy
are also complete; see
[Phase 3 Paired Evaluation](implementation/PHASE3_PAIRED_EVALUATION.md).
Atomic compare-and-swap promotion/rollback and an integrity-chained lifecycle
audit are complete; see
[Phase 3 Promotion and Rollback](implementation/PHASE3_PROMOTION_ROLLBACK.md).
The paired contract is now connected to the exact version-pinned AgentDojo
runtime and durable underlying artifacts; see
[Phase 3 AgentDojo Skill Evaluator](implementation/PHASE3_AGENTDOJO_SKILL_EVALUATOR.md).
The explicit install/stage/decide/inspect/promote/rollback CLI is complete; see
[Phase 3 Skill CLI](implementation/PHASE3_SKILL_CLI.md). It now also exposes an
explicit paid `skill eval-agentdojo` Artifact-generation command. The durable-
learning router and integrity-bound Evidence Store are complete; see
[Phase 3 Durable Learning Router](implementation/PHASE3_DURABLE_LEARNING_ROUTER.md).
A deterministic Markdown report now combines exact Evidence metadata, Skill
Diff, paired base/candidate results, decision, and lifecycle audit; see
[Phase 3 Candidate Report](implementation/PHASE3_CANDIDATE_REPORT.md).
A direct-reflection versus gated-learning ablation remains pending.

Implement:

- evidence eligibility rules;
- durable-learning router;
- bounded skill edits;
- candidate lifecycle;
- paired evaluation runner;
- promotion policy and decision report; and
- rollback.

Exit criteria:

- selected traces create only inactive candidates;
- a known harmful candidate is rejected;
- an accepted candidate is bound to exact evidence and evaluation artifacts;
- direct-reflection and gated-learning baselines can be compared; and
- rollback restores the prior behavior under the same tests.

## 6. Phase 4: security and portfolio report

Implement:

- AgentDojo workspace injection runs;
- memory/skill contamination tests;
- separate agent-behavior and runtime-enforcement evaluation modes;
- security-versus-utility report;
- targeted ablations;
- trace and skill-diff inspection UI or generated report; and
- documented limitations and threat-model boundaries.

Exit criteria:

- benign utility and attack success are reported separately;
- raw AgentDojo attack success is not hidden behind an always-deny policy;
- provenance and promotion gates are tested rather than described only;
- all public claims link to reproducible artifacts; and
- the project has an interview-ready architecture and reading guide.

## 7. Phase 5: one real connector

Only after the controlled system is stable, add one provider family, most
likely Gmail plus Google Calendar, with conservative defaults.

Initial production behavior should prefer read and draft operations. Sending,
deleting, or changing external state remains explicitly authorized. The
AgentDojo path stays as the regression environment.

Multiple providers, background inbox polling, channel integrations, and
proactive automation remain later work.

## 8. Source-reading order

Source study is problem-directed. Each note should record the mechanism,
assumptions, adopted idea, rejected idea, and the Voren test it influences.

### 1. AgentDojo workspace and pipeline

Read first because it defines Voren's initial world and evaluation contract:

- workspace email/calendar tool definitions;
- normal user tasks and state graders;
- injection tasks and security graders;
- pipeline and tool-execution loop; and
- benchmark runner and result format.

Adopt: state-based utility/security evaluation and untrusted-tool-output tests.

Reject: coupling Voren's runtime directly to an unstable benchmark API.

### 2. Pi agent core

Read the smallest agent runtime, provider abstraction, state, event, and
compaction surfaces.

Adopt: a readable bounded loop and typed state/events.

Reject: treating Pi's process permissions as an adequate action-security model.

### 3. Codex action boundary

Read targeted approval, tool orchestration, cancellation, and app-server event
contracts rather than the whole product.

Adopt: explicit pending items, exact approval context, policy before execution,
and resumable event-driven interaction.

Reject: copying a large coding-agent architecture or filesystem sandbox model
into API side effects without translation.

### 4. Agent Skills specification

Adopt: portable `SKILL.md` structure and progressive disclosure.

Extend: a Voren sidecar for effect scope, evidence, and evaluation. Prose never
grants runtime permissions.

### 5. Hermes Agent

Read background review, skill management, provenance, staging, and approval.

Adopt: practical skill-authoring lifecycle and human-readable diffs.

Reject: creating durable skills merely because a task was complex or used many
tools.

### 6. SkillOpt and SkillOpt-Sleep

Read scored rollout ingestion, bounded edits, validation selection, rejected
edit handling, and offline consolidation.

Adopt: candidate optimization against held-out evidence.

Translate: generic benchmark scores into Voren's external-state, side-effect,
and security gates.

### 7. OpenClaw

Read provenance, action receipts, skill proposals, exact revision binding, and
trust-boundary documentation.

Adopt: deterministic admission checks and evidence-linked lifecycle events.

Reject: gateway, channel, device-node, scheduler, and plugin-ecosystem scope.

### Optional references

- Letta, if memory organization becomes a measured limitation;
- DeepSeek Harness, if a second real adapter proves the need for stronger plugin
  boundaries;
- AppWorld, when broader daily-app outcome evaluation is needed; and
- OpenHands or Goose only for a concrete missing mechanism, not general study.

## 9. First implementation decisions

The following defaults keep the first slice coherent:

- language: Python 3.12;
- interface: CLI;
- model support: one OpenAI-compatible adapter behind a local protocol;
- world: AgentDojo `0.1.35` / benchmark `v1.2.2` workspace adapter;
- persistence: SQLite plus content-addressed artifacts;
- mutation policy: approval for every external write;
- skill activation: one explicitly selected static skill first;
- learning: offline candidate generation only; and
- UI, real OAuth, vector retrieval, and general plugins: deferred.

These are reversible defaults, not claims that the final system must remain
small forever.

## 10. Open design questions

The AgentDojo version and task subset are now resolved above. The remaining
questions are:

1. Which run and event fields require encrypted storage rather than redaction?
2. What is the smallest approval UI that clearly presents multiple related
   effects?
3. Should the initial scheduling preference be a profile rule, a skill input,
   or both with explicit precedence?
4. What minimum paired sample and uncertainty rule should move a candidate from
   `needs_evidence` to `active`?
