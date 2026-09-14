# Phase 3 Durable Learning Router

[简体中文](PHASE3_DURABLE_LEARNING_ROUTER.zh-CN.md)

## Why references were insufficient

Candidate staging originally accepted a caller-constructed evidence ID and
digest. That proved the candidate record was bound to *some claimed digest*, but
not that a corresponding immutable evidence object existed. The public path now
uses an integrity-checked SQLite Evidence Store and resolves IDs before staging.

## Authorizing evidence routes

There are two explicit routes:

- an operator correction stores its UTF-8 text, operator reference, content
  digest, creation time, and instruction-authority bit in one typed artifact;
- a verified Run stores a canonical snapshot of the validated Run record and
  event stream. It never receives instruction authority.

Model reflection and external observation are not accepted as standalone
authorizing artifacts. They may inform a human or a later candidate author, but
cannot directly cross the durable-learning boundary.

## Verified Run admission

A Run is eligible only if it is completed and ends in `run.completed`. For every
proposed operation, the router requires matching sets of proposal, accepted
approval, and final receipt events. Operation ID, proposal digest, and approval
ID must agree; the final receipt must be committed, `verified`, and pass exact-
effect verification. An ambiguous receipt followed by a verified reconciliation
is eligible because the final observation wins without resending the effect.

If an external-untrusted observation claims instruction authority, admission
fails. In normal runtime construction this is already prevented by the
provenance model; the router repeats the check at the durable boundary as
defense in depth.

## Integrity and holdout isolation

Evidence artifacts bind their payload media type, source reference, case IDs,
payload digest, and complete artifact digest. IDs are idempotent only for the
exact same artifact. Candidate staging through the CLI loads these artifacts and
compares the generated `EvidenceRef` byte-for-byte with the stored reference.

Evaluation Case IDs used to create Run evidence travel into the reference, so
the paired policy can reject their reuse as held-out cases.

## Threat-model boundary

The Run event store is local SQLite, not a remote attested log. The router takes
and integrity-binds the event snapshot it observes; it cannot prove that a
privileged database administrator did not rewrite history before selection.
