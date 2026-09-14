# Phase 3 Skill Lifecycle CLI

[简体中文](PHASE3_SKILL_CLI.zh-CN.md)

## Public operations

The `voren skill` command exposes the lifecycle without weakening its state
boundaries:

- `install` parses and content-addresses a package; optional activation is only
  for a manually managed baseline and requires a reason;
- `stage` resolves the named active base, validates one explicit evidence
  reference, applies bounded admission, and leaves the candidate inactive;
- `decide` integrity-loads a paired evaluation Artifact and atomically records
  an accepted or rejected decision;
- `inspect` emits the candidate, complete evaluation artifacts, and verified
  lifecycle chain as machine-readable JSON;
- `promote` performs the exact-base compare-and-swap; and
- `rollback` performs the exact-candidate compare-and-swap.

The database and immutable object root are explicit options on every command,
with `.voren/` defaults. Commands close both SQLite connections on success and
failure. Validation and store errors are rendered as normal CLI usage errors
rather than raw tracebacks.

## Safety properties

There is no `--auto-promote` option. A successful `decide` prints the decision
and exits while the base remains active. Promotion and rollback each require a
separate command and non-empty reason. Operator-correction evidence must carry
the explicit instruction-authority flag; other evidence sources cannot claim it.

A rejected decision returns exit code 3 so automation can distinguish a valid
negative policy decision from malformed input or infrastructure failure.

## Current boundary

This slice consumes an existing Candidate Evaluation Artifact. The next CLI
slice invokes the exact-version AgentDojo evaluator to produce one, with paid
model cases selected explicitly.
