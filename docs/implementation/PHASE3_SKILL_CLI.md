# Phase 3 Skill Lifecycle CLI

[简体中文](PHASE3_SKILL_CLI.zh-CN.md)

## Public operations

The `voren skill` command exposes the lifecycle without weakening its state
boundaries:

- `install` parses and content-addresses a package; optional activation is only
  for a manually managed baseline and requires a reason;
- `evidence-correction` stores explicit operator-authored text, while
  `evidence-run` admits only a semantically verified completed Run;
- `stage` resolves the named active base and already-persisted Evidence IDs,
  applies bounded admission, and leaves the candidate inactive;
- `eval-agentdojo` requires explicit paid cases, runs raw behavior against both
  exact versions, and writes the paired Artifact plus every underlying Artifact;
- `decide` integrity-loads a paired evaluation Artifact and atomically records
  an accepted or rejected decision;
- `inspect` emits the candidate, complete evaluation artifacts, and verified
  lifecycle chain as machine-readable JSON. Evidence payloads are redacted by
  default and require `--include-evidence-payload`;
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
instruction authority inside its typed durable artifact; other evidence sources
cannot claim it. `stage` does not accept a caller-supplied source, authority bit,
or digest.

A rejected decision returns exit code 3 so automation can distinguish a valid
negative policy decision from malformed input or infrastructure failure.

## Current boundary

The CLI now produces and consumes Candidate Evaluation Artifacts. It does not
author the candidate edit itself or automatically route arbitrary traces into
durable learning. Only the explicit correction and verified-Run routers create
authorizing evidence for the bounded staging command.
