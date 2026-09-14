# Phase 3 Promotion and Rollback

[简体中文](PHASE3_PROMOTION_ROLLBACK.zh-CN.md)

## Lifecycle boundary

Evaluation and activation are deliberately separate:

```text
staged -> accepted -> promoted -> rolled_back
       \-> rejected
```

Only an `accepted` candidate can be promoted. A rejected or merely staged
candidate cannot reach the active pointer. Promotion is an explicit operator or
release-controller action with a non-empty reason; passing an evaluation alone
never activates learned instructions.

## Atomic pointer changes

Promotion acquires an immediate SQLite write transaction and checks that the
current active version is the exact evaluated base. In the same transaction it:

1. conditionally changes `active_skills` from base to candidate;
2. conditionally changes candidate status from `accepted` to `promoted`; and
3. appends a lifecycle event containing both exact references.

If any comparison fails, all three changes roll back. This prevents a delayed
promotion from overwriting a newer release and prevents two candidates evaluated
against the same base from both winning.

Rollback uses the symmetric rule: it restores the exact base only while the
active pointer is still the exact promoted candidate. It will not overwrite a
later independent release. Exact retry after a successful promotion or rollback
is idempotent and adds no duplicate event.

## Integrity-chained audit

Candidate staging, evaluation decision, promotion, and rollback append typed
events. Each event contains the prior event digest and a SHA-256 digest of its
own canonical content. Reads verify contiguous sequence numbers, digest links,
status transitions, typed event invariants, and agreement with the current
candidate record. Evaluation, pointer, candidate, and event records are updated
in the same transaction at their respective decision boundary.

This chain is tamper-evident, not a defense against a database administrator who
can rewrite the entire database and all digests. An external append-only sink or
signature would be required for that stronger threat model.
