# Voren Agent

[简体中文](README.zh-CN.md)

> Status: Phase 1 in progress. The safe-action core, durable approval pause,
> provenance-labelled AgentDojo reads, bounded loop, Responses API adapter, and
> interactive CLI are implemented. Provider contracts are tested without a
> credential; a recorded live-model run and production connectors do not exist
> yet.

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

To exercise the API-capable adapter, set `OPENAI_API_KEY`, explicitly choose a
model supported by the endpoint, and run:

```bash
voren agentdojo --model 'your-model-id' \
  'Create an event for the hiking trip with Mark based on my emails.'
```

The command still operates only on AgentDojo. It prints every proposed effect
and requires an interactive exact-effect approval before commit. There is no
automatic-approval flag.
