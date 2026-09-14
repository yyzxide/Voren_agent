# Phase 3 Reproducible Skill-learning Demo

[简体中文](PHASE3_REPRODUCIBLE_DEMO.zh-CN.md)

## Purpose

`scripts/demo_skill_learning.py` provides a credential-free acceptance path for
Voren's central learning claim. It executes the actual stores, policies, paired
runner, promotion transaction, rollback transaction, and report generator. It
does not replace a live-model evaluation.

## Observable lifecycle

1. Install and activate the repository's reviewed scheduling Skill.
2. Persist an operator-authored correction as authoritative evidence.
3. Stage a bounded instruction-only candidate against the exact active version.
4. Evaluate the exact base and candidate on the same benign and attack cases.
5. Record an accepted decision while proving that the active pointer is unchanged.
6. Explicitly promote the candidate, then roll it back to the exact base version.
7. Write raw trials, an integrity-bound evaluation artifact, and a Markdown report.

Each invocation creates a new directory under `.voren/demos`; it never reuses or
overwrites an earlier demo workspace.

## Evidence boundary

The included evaluator checks two explicit instruction contracts. It gives the
candidate a benign improvement for preserving conflicts between matching email
threads, and requires both versions to retain the boundary that email text has no
instruction authority. Every raw result says that it is deterministic and is
not a live-model or AgentDojo benchmark score.

This deliberate limitation makes the lifecycle reproducible without a secret.
Live model behavior must still be measured separately through
`voren skill eval-agentdojo` with explicit paid cases.

## Acceptance

```bash
python scripts/demo_skill_learning.py
python -m unittest tests.test_demo_skill_learning -v
```

The command succeeds only if the candidate is accepted without auto-activation,
promotion selects its exact version, rollback restores the exact base, and all
artifacts can be rendered with valid integrity links.
