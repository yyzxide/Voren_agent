# Voren Agent

[简体中文](README.zh-CN.md)

The [2026-09-14 portfolio audit](docs/reviews/2026-09-14-PORTFOLIO-AUDIT.zh-CN.md)
records the audit baseline and revised delivery order. Subsequent slices added
atomic operation claims and observation-only reconciliation for the external-
commit-before-receipt crash window. Scripted tests still must not be read as
live-model quality evidence.

> Status: Phase 1 in progress. The safe-action core, durable approval pause,
> provenance-labelled AgentDojo reads, bounded loop, Responses API adapter, and
> interactive CLI are implemented. A reproducible agent-behavior/runtime-
> enforcement evaluation harness with integrity-bound artifacts is also
> implemented. Provider and evaluation contracts are tested without a
> credential, and model-token usage flows through runtime and evaluation
> artifacts. Background Responses polling, deadline/operator cancellation, and
> provider-confirmation evidence are implemented. Encrypted mid-loop transcript
> recovery now preserves context and budgets across process restarts. A recorded
> live-model run and production connectors do not exist. Phase 2 has started
> with an Agent Skills-compatible, content-addressed static skill store, exact
> active-version snapshots, and bounded version-pinned instructions wired into
> the agent loop. Phase 3 now has evidence-gated inactive candidates, paired
> held-out evaluation, transactional decisions, atomic promotion/rollback, an
> integrity-chained audit, and an exact-version AgentDojo Skill evaluator. A
> public learning CLI and recorded live comparison are not yet complete.

Voren is a planned single-agent assistant for email and calendar work. Its
engineering focus is not broad personal-assistant coverage, but a narrower
question:

> How can an action-taking agent learn from experience without promoting
> accidental success, unsafe instructions, or regressions into durable skills?

The intended system combines:

- a bounded, observable agent runtime;
- typed external actions with approval, idempotency, and outcome verification;
- separate profile, episode, and procedural-skill memory;
- candidate-only skill learning from provenance-aware execution evidence; and
- utility and security evaluation before a skill version can be promoted.

The first executable environment will be the AgentDojo `workspace` suite so
email and calendar behavior can be tested against deterministic state and
prompt-injection cases before any real account is connected.

## Initial scope

The first vertical slice is one end-to-end workflow:

1. find a relevant email thread;
2. inspect calendar availability;
3. prepare an email reply and calendar event;
4. bind approval to the exact proposed effects;
5. execute each external mutation once; and
6. verify the resulting world state.

Multi-agent orchestration, messaging gateways, proactive scheduling, plugin
marketplaces, GUI automation, and multiple production connectors are outside
the first release.

## Design documents

- [Product definition](docs/PRODUCT.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Evaluation strategy](docs/EVALUATION.md)
- [Roadmap and source-reading plan](docs/ROADMAP.md)
- [AgentDojo workspace spike](docs/research/AGENTDOJO_SPIKE.md)
- [Phase 1 action-core implementation](docs/implementation/PHASE1_ACTION_CORE.md)
- [Phase 1 durable run lifecycle](docs/implementation/PHASE1_RUN_LIFECYCLE.md)
- [Phase 1 provenance-aware agent loop](docs/implementation/PHASE1_AGENT_LOOP.md)
- [Phase 1 Responses adapter and CLI](docs/implementation/PHASE1_MODEL_ADAPTER_CLI.md)
- [Phase 1 dual-mode AgentDojo evaluation](docs/implementation/PHASE1_EVALUATION_HARNESS.md)
- [Phase 1 model-usage accounting](docs/implementation/PHASE1_USAGE_ACCOUNTING.md)
- [Phase 1 provider cancellation](docs/implementation/PHASE1_PROVIDER_CANCELLATION.md)
- [Phase 1 encrypted transcript recovery](docs/implementation/PHASE1_TRANSCRIPT_RECOVERY.md)
- [Phase 2 static skill store](docs/implementation/PHASE2_STATIC_SKILL_STORE.md)
- [Phase 2 version-pinned skill context](docs/implementation/PHASE2_SKILL_CONTEXT.md)
- [Phase 3 candidate staging](docs/implementation/PHASE3_CANDIDATE_STAGING.md)
- [Phase 3 paired evaluation](docs/implementation/PHASE3_PAIRED_EVALUATION.md)
- [Phase 3 promotion and rollback](docs/implementation/PHASE3_PROMOTION_ROLLBACK.md)
- [Phase 3 AgentDojo Skill evaluator](docs/implementation/PHASE3_AGENTDOJO_SKILL_EVALUATOR.md)
- [Phase 3 Skill lifecycle CLI](docs/implementation/PHASE3_SKILL_CLI.md)

## Current executable slice

```bash
python -m pip install -e '.[agentdojo]'
python -m unittest discover -s tests -v
python scripts/demo_agent_loop.py
```

The agent-loop demo performs provenance-labelled email/calendar reads, pauses
before an external action, prints the exact effects, then applies a scripted
operator approval and verifies the resulting state with AgentDojo's official
`user_task_18` utility grader. It uses a deterministic scripted model and does
not connect to a real email or calendar account. The separate lifecycle demo
also closes and rebuilds the SQLite-backed runtime while approval is pending.

The Skill lifecycle is separately available through explicit subcommands:

```bash
voren skill install skills/schedule-from-email --activate \
  --reason 'reviewed baseline'
voren skill stage path/to/candidate --candidate-id candidate-001 \
  --base schedule-from-email --evidence-id operator:correction-001 \
  --evidence-source operator_correction --evidence-digest "$EVIDENCE_SHA256" \
  --instruction-authority
voren skill eval-agentdojo --candidate-id candidate-001 \
  --case benign_user_18 --case attacked_user_18_injection_2 \
  --suite scheduling-held-out-v1 --model 'your-model-id' \
  --output .voren/artifacts/candidate-001.json
voren skill decide --candidate-id candidate-001 \
  --artifact .voren/artifacts/candidate-001.json
voren skill inspect --candidate-id candidate-001
voren skill promote --candidate-id candidate-001 \
  --reason 'reviewed held-out evaluation'
voren skill rollback --candidate-id candidate-001 \
  --reason 'post-promotion regression'
```

`decide` never activates a Skill. `promote` and `rollback` remain separate,
reason-bearing operations. `eval-agentdojo` requires explicit paid Cases and
always runs raw agent behavior for both exact versions. It only writes evidence;
the candidate remains staged until the separate `decide` command.

To exercise the API-capable adapter, set a credential, explicitly choose an
endpoint profile and model, and generate a local transcript key once. The
default OpenAI endpoint can run as follows:

```bash
export VOREN_TRANSCRIPT_KEY="$(python3 -c 'from voren.runtime.transcripts import SQLiteTranscriptStore; print(SQLiteTranscriptStore.generate_key())')"
voren agentdojo --model 'your-model-id' \
  'Create an event for the hiking trip with Mark based on my emails.'
```

DeepSeek Responses is a stateless foreground endpoint and requires its explicit
capability profile:

```bash
export DEEPSEEK_API_KEY='your-api-key'
voren agentdojo --provider-profile deepseek --model deepseek-v4-flash \
  'Summarize the hiking email.'
```

A custom `OPENAI_BASE_URL` likewise requires `--provider-profile` or
`VOREN_RESPONSES_PROFILE`; wire compatibility is not treated as identical
capability support.

The command still operates only on AgentDojo. It prints every proposed effect
and requires an interactive exact-effect approval before commit. There is no
automatic-approval flag.

To run an explicitly labelled evaluation and write a machine-readable artifact:

```bash
voren eval-agentdojo \
  --case benign_user_18 \
  --case attacked_user_18_injection_2 \
  --mode agent_behavior \
  --mode runtime_enforcement \
  --model 'your-model-id' \
  --output .voren/artifacts/phase1-eval.json
```

This command calls an online model and may incur cost, so both cases and modes
must be selected explicitly. The automated tests do not call a live model and
are not live-model quality or prompt-injection results. For a dated test result,
see the portfolio audit above.
