# Voren 1.0.0: local single-operator delivery

[简体中文](V1_RELEASE.zh-CN.md)

2026-10-08. This closes the current email/calendar Agent and knowledge-answer
portfolio scope: implemented paths, controlled verification, distribution and
reproducible entry points. It does not establish real-account operation,
real-model answer quality or a production multi-user service.

## Delivered paths

| Path | Implementation | Verification boundary |
| --- | --- | --- |
| Email/calendar | Bounded loop, per-action approval, exact-effect verification, receipts and resume | Controlled regression; historical live AgentDojo evidence remains separate |
| Recovery preflight | Model/workspace/contracts, authenticated transcript and current pending call | Blocks new dispatch on damage/drift; not a distributed transaction |
| Memory/Skill | Typed contexts, candidate evaluation, explicit promotion/rollback | Mechanism evidence; historical Skill regression does not show general improvement |
| Retrieval | Chunked BM25, explicit dense indexing and RRF hybrid, immutable source citations | Synthetic corpus, manual vectors and local HTTP; real embedding quality unmeasured |
| Answers | CLI/separate Web panel, per-claim quotes, abstention, typed failures and usage | Genuine citations do not establish semantic correctness |
| Answer evaluation | Prepare/export/replay/verify and separate review labels | Replay authenticates consistency, not provider/reviewer origin |
| Google | Read/draft/no-attendee hold, approvals, verification and reconciliation | HTTP contracts/exporter; no new real-account success claim |

## Install and demo

The locked environment targets Python 3.12 / Linux x86-64:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade 'pip==26.2.1'
python -m pip install --requirement pylock.toml
python -m pip install 'setuptools==80.9.0' 'wheel==0.45.1'
python -m pip install --no-deps --no-build-isolation --editable .
voren --version
python scripts/demo_agent_loop.py
python scripts/demo_skill_learning.py
python scripts/demo_knowledge_answers.py
```

These are local scripted/controlled demonstrations, not online deployment.
Use README dependency guidance on other platforms. For Web knowledge:

```bash
voren knowledge ingest docs/fixtures/knowledge-quickstart.md \
  --document-id meeting:quickstart --title 'Atlas release notes' \
  --source-uri fixture://voren-1.0/atlas --source-kind meeting_note \
  --reason 'reviewed synthetic quickstart'
voren-web
```

At `http://127.0.0.1:8080`, search `Atlas checklist owner` in the knowledge panel.
Validate an original local draft offline, or explicitly select and request an
online model. Merely checking the option makes no request. See the
[0.4 proposal contract](V04_KNOWLEDGE_ANSWERS.md) and [0.6 evaluation workflow](V06_ANSWER_EVALUATION.md).
Source citations use the frozen corpus. Generic action-session summaries are
outside the citation-checked answer path. Labelled prepared evidence stays local;
model inputs use opaque references and exclude evaluation labels.

## Publication and verification

Separate commits/tags publish retrieval (0.3.0), answers (0.4.0), preflight/Web
(0.5.0), offline evaluation (0.6.0), then complete delivery (1.0.0), preserving
history. All **426 tests passed** locally in 122.040 seconds with no skips; see
the [raw log](../evidence/2026-10-08-v1-tests.log). Dependency consistency, Python
compilation, JavaScript syntax, demos and temporary-data CLI-to-Web offline
answering passed. The [package check](../evidence/2026-10-08-v1-package.json)
verifies version/modules/assets and imports the CLI from the built Wheel,
reusing existing runtime dependencies locally; CI performs the locked fresh install.

The [offline answer artifact](../evidence/2026-10-08-v1-answers.json) and
[ten-case citation protocol artifact](../evidence/2026-10-08-v1-answer-protocol.json)
bind clean source `d15ce968005bf3bbc93f4f0c41a439fa5d27117a` (`code_dirty=false`).
Five authored proposals yield two answers, two abstentions and one rejection;
one cited answer deliberately invents a pager number. All semantic labels stay
unreviewed, so no semantic accuracy is calculated. Three invalid citations in
the protocol artifact are rejected; a semantic counterexample remains accepted.
Digests, windows, original-text replay and summaries have been rechecked.

Verified batch CI runs: [0.3.0](https://github.com/yyzxide/Voren_agent/actions/runs/37740454141),
[0.4.0](https://github.com/yyzxide/Voren_agent/actions/runs/37740547760),
[0.5.0](https://github.com/yyzxide/Voren_agent/actions/runs/37742424013),
[0.6.0](https://github.com/yyzxide/Voren_agent/actions/runs/37742521556).
The final 1.0 workflow additionally builds the Wheel, checks JavaScript syntax
and replays released evidence; see [GitHub Actions](https://github.com/yyzxide/Voren_agent/actions/workflows/ci.yml) for its live result.

## Reading order

1. [service.py](../../src/voren/web/service.py) and [model_context.py](../../src/voren/web/model_context.py):
   what must match on resume, and why before dispatch?
2. [store.py](../../src/voren/knowledge/store.py) and [answers.py](../../src/voren/knowledge/answers.py):
   why preserve source versions, and why can a genuine quote support a wrong claim?
3. [answer_evaluation.py](../../src/voren/knowledge/answer_evaluation.py):
   which denominators separate decisions, citation integrity and semantic review?
4. [Skill lifecycle](PHASE3_SKILL_CLI.md): why does one success create only a candidate?

This remains a local deployment for one trusted operator and explicit external
action authorization. Multi-user authentication, background inbox polling,
scheduling, additional providers and cross-domain autonomous learning remain
future scope. Built-in model/new-Run freezes do not verify custom hidden settings
or reconstruct historical identities.
