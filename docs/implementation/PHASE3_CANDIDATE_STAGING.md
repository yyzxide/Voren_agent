# Phase 3 Candidate Staging

[简体中文](PHASE3_CANDIDATE_STAGING.zh-CN.md)

## Purpose

This slice creates an inactive, reviewable Skill candidate. It does not let a
model, tool result, or external document modify the active Skill.

## Admission boundary

A candidate must name the exact active base version and carry at least one
reference to either an operator correction or a verified run. Model reflection
and external observation may be supporting evidence, but cannot authorize a
candidate by themselves. Evidence references are immutable identifiers and
SHA-256 digests. The subsequent durable-router slice now persists and verifies
those exact evidence objects before the public CLI permits staging.

The first edit policy is intentionally narrow:

- package name, routing metadata, and `skill.yaml` contract stay identical;
- package files cannot be added or removed;
- only UTF-8 `SKILL.md` instruction content may change; and
- both changed-line count and serialized diff size are bounded.

The contract comparison prevents learned prose from adding tools, effects, or
evaluation suites. Runtime permissions continue to come from typed code and the
frozen contract, never from prose.

## Persistence and activation

The candidate package is installed as a content-addressed immutable version.
Its record binds the base and candidate digests, evidence references, and a
human-readable unified diff. Staging is idempotent for the same candidate ID and
exact record. A reused ID with different content fails closed.

Staging never changes `active_skills`. The current active pointer is checked at
admission time to reject an already-stale base. A concurrent pointer change does
not make staging dangerous because promotion is a separate operation; promotion
must perform the definitive compare-and-swap check.

## Verified behavior

Deterministic tests cover eligible and ineligible evidence, immutable contract
scope, edit-size bounds, stale bases, inactive staging, and process-restart
loading.

## Explicitly not implemented here

This slice does not claim that a candidate is good. It has no paired held-out
evaluation, acceptance decision, active-pointer promotion, or rollback. Those
operations are the next Phase 3 slices.
