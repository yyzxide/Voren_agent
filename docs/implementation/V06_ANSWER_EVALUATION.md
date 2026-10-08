# 0.6.0: offline answer evaluation and original-proposal replay

[简体中文](V06_ANSWER_EVALUATION.zh-CN.md)

2026-10-08. Scripted answer demos test protocol behavior, not real model judgement.
This slice prepares custom document/question evidence, imports original output,
replays it offline and preserves independent human support labels. It makes no
model or embedding requests and does not automatically grade semantic quality.

```bash
python scripts/evaluate_knowledge_answers.py prepare \
  --dataset docs/fixtures/knowledge-answers.json \
  --output .voren/artifacts/answers-prepared.json \
  --proposals-output .voren/artifacts/answers-proposals.json \
  --model-inputs-output .voren/artifacts/answers-model-inputs.json
```

Prepared evidence contains supplied answerability labels and must not be sent
to a model under evaluation. Model inputs exclude labels/reviews and replace
semantic case IDs with opaque case references; the mapping remains local.
Fill original `proposal_text` into the blank template, honestly declare
`external_model`, `manual` or `scripted` origin, and keep corpus/window digests.
Blank text is invalid, not a default abstention.

```bash
python scripts/evaluate_knowledge_answers.py evaluate \
  --prepared .voren/artifacts/answers-prepared.json \
  --proposals .voren/artifacts/answers-proposals.json \
  --output .voren/artifacts/answers-evaluation.json
python scripts/evaluate_knowledge_answers.py verify \
  .voren/artifacts/answers-evaluation.json
```

Missing/duplicate cases and stale source/window bindings fail as input errors.
Answered, abstained, rejected, failed and cancelled are counted separately.
Rejected no-answer proposals remain unresolved, not correct abstentions. Empty
windows record zero model calls. Decision rates compare supplied answerability
labels and are distinct from semantic accuracy or retrieval false positives.

Optional supported/unsupported/unreviewed labels require an identified reviewer
for reviewed answers; only reviewed answered cases enter their denominator.
These are supplied annotations, not reviewer authentication, and service results
keep semantic support unverified. Verification rebuilds sources/windows and
replays raw text to check statuses and summaries, rejecting recomputed checksums
with inconsistent metrics. Checksums/replay do not prove provider origin.

Only BM25 with the current chunk contract is supported, with at most 100
documents and 1,000 questions. Future replay requires compatible implementations.
Sixteen regressions cover bindings, replay, raw invalid/duplicate JSON, metadata
label exclusion, Unicode offsets, tampered summaries, review denominators and
output-path protection. The five-case authored fixture is synthetic protocol
evidence, not real-model accuracy. Independent data, real outputs and semantic
review remain necessary for quality claims.

Read [answer_evaluation.py](../../src/voren/knowledge/answer_evaluation.py) and
[tests](../../tests/test_answer_evaluation.py): why count citation integrity,
abstention decisions and semantic support separately?
