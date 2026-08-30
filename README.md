# Voren Agent

[简体中文](README.zh-CN.md)

> Status: Phase 1 in progress. The safe-action core works in both a deterministic
> fake world and the pinned AgentDojo workspace; no model-connected or
> production-account agent exists yet.

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

## Current executable slice

```bash
python -m pip install -e '.[agentdojo]'
python -m unittest discover -s tests -v
python scripts/demo_agentdojo_action.py
```

The demo runs the approved action through AgentDojo and checks the resulting
state with its official `user_task_18` utility grader. It never connects to a
real email or calendar account.
