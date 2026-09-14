# Phase 3 Paired Evaluation

[简体中文](PHASE3_PAIRED_EVALUATION.zh-CN.md)

## What is compared

The paired runner receives an already staged candidate and an explicit held-out
manifest. For every case it passes the exact base reference and then the exact
candidate reference to the evaluator. Each successful measurement must include
a run ID, utility result, and underlying run-artifact digest. Attack cases also
require an attack-success result.

The resulting immutable artifact binds:

- candidate ID and exact base/candidate content digests;
- the candidate's exact evidence IDs;
- declared evaluation-suite ID and manifest digest;
- every held-out case, kind, run, metric, and run-artifact digest; and
- a canonical SHA-256 digest over the complete typed artifact.

## Decision policy

The default policy requires both benign and attack coverage. The thresholds are
configurable for a concrete suite. Before comparing behavior, the policy checks
the artifact digest, exact candidate/evidence/version binding, suite declaration
in `skill.yaml`, and disjointness between evidence cases and held-out cases.

A candidate is rejected on any per-case utility regression, any new attack
success, or no measured improvement. It is accepted only when it is
non-regressing and improves utility or security on at least one held-out case.
The decision and exact evaluation artifact are persisted in one SQLite
transaction. Retrying the same decision is idempotent. Neither outcome changes
the active Skill pointer.

## Infrastructure failures

Provider timeouts and other explicitly classified infrastructure failures have
no utility or security metric. They block the decision and leave the candidate
staged, rather than being counted as model failure. Unexpected programming
exceptions are not swallowed by the runner.

## Boundary of this slice

The runner defines the orchestration and artifact contract; a concrete evaluator
still has to run the frozen runtime and produce each underlying run artifact.
The current deterministic tests prove binding, policy, persistence, and failure
semantics, not live-model quality. Promotion and rollback are implemented in the
next slice.
