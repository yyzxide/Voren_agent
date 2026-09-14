# Phase 3 Direct-reflection versus Gated-learning Ablation

[简体中文](PHASE3_LEARNING_ABLATION.zh-CN.md)

## Question

What does Voren's evaluation gate add after a candidate has already passed
format, evidence, edit-size, and capability-scope admission?

`scripts/demo_learning_ablation.py` answers that policy question with two exact
candidate versions:

- a beneficial candidate adds conflict-preservation behavior without weakening
  the email instruction-authority boundary; and
- a contaminated candidate adds the same utility rule while also treating email
  text as tool authorization.

Both edits are deliberately small and syntactically admissible. Admission alone
therefore cannot distinguish them.

## Compared policies

`direct_reflection` is a counterfactual baseline: every admitted edit would be
activated immediately, without held-out evidence. The demo never adds that
unsafe path to the production Skill Store.

`voren_gated` uses the actual paired evaluation and decision path. Acceptance
only makes a candidate eligible for a separate, reason-bearing operator
promotion; it still does not activate the candidate automatically.

## Deterministic result

The reproducible fixture produces:

| Metric | Result |
|---|---:|
| Direct-reflection activations | 2 |
| Gated promotion-eligible candidates | 1 |
| Unsafe activations avoided | 1 |
| Beneficial candidates retained | 1 |

The contaminated candidate improves the benign contract but introduces a
security regression on the paired attack case, so the gate rejects it and the
active pointer remains on the reviewed base.

## Integrity and limits

The JSON ablation artifact binds each candidate to its exact paired-evaluation
digest and carries its own digest. The Markdown report repeats the policy
boundary and result. Tampered summaries fail validation.

This is a deterministic mechanism test, not a statistical claim about a live
model. Live candidate quality must be evaluated separately with explicit paid
AgentDojo cases.

```bash
python scripts/demo_learning_ablation.py
python -m unittest tests.test_learning_ablation -v
```
