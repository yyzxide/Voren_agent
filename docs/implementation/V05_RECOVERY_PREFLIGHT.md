# 0.5.0: recovery checks before external dispatch

[简体中文](V05_RECOVERY_PREFLIGHT.zh-CN.md)

2026-10-08. Web approval could dispatch an action before discovering a corrupt
transcript, silently change its model during recovery, or accept a changed
adapter/action contract under the same workspace identity. This slice checks
those boundaries before approved writes. Google CLI also checks its recorded
injected/environment model boundary.

Approval authenticates the encrypted checkpoint, RunConfig digest, original
pending tool call and action identity first. New Web Runs freeze credential-free
model identity; built-in Responses configuration, actual endpoint, profile,
request limits and Web mode must match. The same checked model instance continues
the Run. Workspace identity, world adapter and action contract versions must
also match. Failures expose distinct knowledge configuration/evidence, model
configuration, transcript or workspace-contract recovery reasons. Original corpus references are
visible in RunView, with empty and absent snapshots remaining distinct.

Rejection remains available under the original workspace identity. Completed
results remain readable without model configuration. Custom adapters verify
only class/mode (`configuration_verified=false`); hidden configuration and
behavior are outside that guarantee. Old Runs without model identity keep their
original factory behavior rather than receive invented historical identity.
Credentials are excluded from model identity, permitting key rotation.

These checks and remote writes are not a distributed transaction. Later local
damage or provider failures can still interrupt continuation; ambiguous already
dispatched actions retain observation/reconciliation without resend.

Sixteen new Web regressions exercise missing/corrupt checkpoints/keys, valid
encryption with wrong bindings, model/endpoint/profile/mode changes, unavailable
configuration, contract drift and receipt-before-continuation recovery. A CLI
regression blocks model-boundary switches before construction or dispatch.
Tests use temporary SQLite, scripted models and fake Google HTTP transports.
Full regression evidence is recorded in the 1.0 delivery notes.

The same batch adds an independent knowledge panel to `voren-web`, with
`POST /api/knowledge/search` and `/ask`. Search is offline BM25. Answering accepts
original JSON draft text or strict explicit `allow_model_api=true`, carrying
the same corpus snapshot. Checking the box alone makes no request; empty
retrieval does not construct a provider. Snapshot inputs are bounded to 1,000
sources and 500-character document IDs. No embedding or action tools are enabled.

Answers, abstentions, rejected proposals and infrastructure failures are shown
separately, with `operator_draft`/`online_model` origin and semantic support
unverified. Quotes, titles, URIs and claims are rendered as plain text, without
turning untrusted URIs into links. Changed questions require a new search.
Generic action-session final text remains outside this citation-checking path.
Thirteen HTTP/Node DOM tests and an application-routing smoke test pass; they
do not provide new online-model or real-account evidence.

Read [service.py](../../src/voren/web/service.py), [model_context.py](../../src/voren/web/model_context.py)
and [preflight tests](../../tests/test_web_recovery_preflight.py): which failures
must be detected before approval is saved and the action dispatched?
