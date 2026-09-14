# Phase 3 Candidate Decision Report

[简体中文](PHASE3_CANDIDATE_REPORT.zh-CN.md)

## Purpose

`voren skill report` turns the durable candidate records into a reviewable
Markdown artifact. It is deterministic for the same database state and can be
checked into a portfolio beside the machine-readable JSON artifacts.

The report contains:

- current status and exact base/candidate/diff/evaluation digests;
- Evidence IDs, source references, media types, digests, and payload sizes;
- every paired held-out base/candidate utility and attack result;
- the bounded instruction Diff as an indented code block;
- the integrity-chained lifecycle with exact event digests; and
- explicit interpretation and threat-model limits.

Evidence payloads are never included. This prevents operator-correction text or
a captured Run snapshot from leaking into a report or CI log. The Skill Diff is
included intentionally because reviewing the proposed procedural change is the
report's purpose.

## Integrity behavior

The renderer first revalidates every Evidence and Evaluation Artifact and checks
their candidate binding. Lifecycle events have already passed sequence, digest-
chain, transition, and final-state validation in the store. The report is written
with an atomic replace and its SHA-256 digest is printed by the CLI.

The Markdown digest is not a signature. A published portfolio should keep the
referenced JSON artifacts and source revision alongside the report when making
measured claims.
