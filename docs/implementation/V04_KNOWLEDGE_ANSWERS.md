# 0.4.0: citation-checked knowledge answers and frozen corpora

[简体中文](V04_KNOWLEDGE_ANSWERS.zh-CN.md)

2026-10-08. In the 0.3.0 controlled corpus, 3 of 6 questions without labelled
answers still returned retrieval hits. Rankings, document hits and genuine
quotations cannot establish answerability. This slice adds standalone
`knowledge ask`: a model can propose cited claims or abstain, while the application
checks citations. New Web Runs also freeze knowledge versions across approval.

## Credential-free use

With the existing repository dependencies installed:

```bash
python scripts/demo_knowledge_answers.py
```

The protocol demo uses temporary SQLite and scripted JSON proposals, with no
external calls or changes to the everyday database. Its default artifact,
`.voren/artifacts/knowledge-answers.json`, includes proposals, raw results,
source versions, expected statuses, source revision and an integrity digest.
This tests protocol behavior, not a real model's judgement.

For already ingested and activated documents:

```bash
voren knowledge search 'approval receipt' --mode bm25
voren knowledge ask 'approval receipt' --draft proposal.json
```

A proposal has `status`, `claims` and optional `reason`. An answer uses status
`answer`; each claim has `text` and nonempty `citations`. Each citation contains
the retrieved `hit_id` (the chunk ID), exact `quote`, and absolute character
offsets `start` and `end`. The application supplies source URI, title and version
from the actual hit; the model cannot supply these fields. An abstention example:

```json
{"status":"abstain","claims":[],"reason":"The retrieved passages lack the requested information."}
```

`--draft` validates a supplied proposal rather than generating one. BM25 requires
no online interface. Answers and intentional abstentions return exit code 0;
rejected proposals, retrieval/model failures and cancellation return 2.
Argument, configuration and draft-file errors use the existing CLI error path.

## Explicit model and embedding use

```bash
voren knowledge ask 'approval receipt' --allow-model-api --model MODEL_ID
voren knowledge ask 'approval receipt' --allow-model-api --model MODEL_ID --mode hybrid --allow-embedding-api
```

The existing Responses adapter and credential configuration are reused, with no
default online model. `--allow-model-api` permits sending the question and
retrieved passages and may incur provider costs. Dense/hybrid additionally need
`--allow-embedding-api`, separate embedding configuration and an existing index
covering the frozen corpus. See the [0.3.0 configuration and index boundaries](V03_KNOWLEDGE_RETRIEVAL.md).
There is no automatic indexing, retry or fallback. A dense/hybrid local draft
also requires embedding opt-in to send the query to the embedding interface.

The application asks for JSON and validates it after receipt. This slice does
not enable provider-side Structured Outputs or add an SDK. Schema validity and
content correctness are distinct; see the [official Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs).

## Answer and citation contract

1. Capture and validate the active source versions, then retrieve from that
   same corpus. At most 20 source chunks are used; evidence JSON and proposals
   each have a 64,000-byte UTF-8 bound. Exceeding it fails explicitly.
2. Empty retrieval returns `abstained` with `no_retrieved_evidence`, without a
   model call. This does not establish that the entire corpus or world lacks an answer.
3. A nonempty window makes at most one model request, with no tools, repair or
   retry. Extra fields, duplicate JSON keys, empty answers, uncited claims and
   model tool calls are rejected.
4. Every citation must refer to this retrieval window, lie inside its chunk,
   and exactly match both the immutable source and the snippet. Offsets are
   Python Unicode characters in normalized imported content, with exclusive
   end. Forged hit identity, quote, offsets or source metadata cannot obtain
   verified citations.
5. Success is `answered` with `citation_integrity=verified`. Source versions,
   source/quote digests and readable citation text are filled by the application.
   Every result retains `semantic_support=unverified`.

Other statuses distinguish intentional `abstained`, invalid `rejected`, infrastructure
`failed` and `cancelled`. The last three expose bounded error codes rather than
claiming no answer exists or returning provider exception bodies. `usage` is
model-only: attempts and reported tokens. Separate `embedding_usage` records
this query's embedding attempts and reported usage; unavailable metering is
unknown/incomplete rather than proof of zero cost. Deltas use cumulative provider
counters, so one provider instance must not be shared concurrently for attribution;
the normal CLI path creates its own instance. Cancellation prevents a
model call or suppresses its returned answer; an embedding HTTP request already
in progress remains governed by its own timeout.

This is a separate, single knowledge question, not a durable Agent Run. Generic
AgentLoop final text, email/calendar actions and budget recovery are unchanged.
Read tools still return `external_untrusted` data and do not hide answering calls.

## Web / MCP corpus freezing

New Web tasks store `metadata["knowledge_corpus"]`: sorted, unique document
version references and their digest, not copied document bodies. The MCP tool
and persisted Run use the same set. Activation changes and newly added documents
during approval do not change that Run's membership or versions. An explicitly
empty corpus remains empty.

Recovery, approval and continuation resolve those exact sources again.
Dense/hybrid also check the frozen chunks' existing vectors offline, without
sending a query or repairing an index. Missing/corrupt sources or vectors require
restoring evidence before approval dispatches an action. Rejecting a pending
proposal remains available. These checks and remote writes are not a distributed
transaction: later database damage can still cause a subsequent read to fail.

Existing frozen retrieval-mode/provider-fingerprint checks remain. Historical
Runs without `knowledge_corpus` retain live active-source lookup, without invented
historical snapshots. A standalone MCP server without a supplied snapshot also
uses active versions. No new knowledge snapshot guarantee is added to generic
CLI Agent Runs or the Google CLI in this slice.

## Verification boundaries

All **378 tests passed** locally (107.100 seconds, no skips), including local
HTTP/MCP tests; see the [full log](../evidence/2026-10-08-v04-tests.log).
`pip check`, JavaScript syntax, Python compilation and 151 local
links across this slice's six documents passed. Installed and source versions
are both 0.4.0.

The [raw protocol demo artifact](../evidence/2026-10-08-v04-answers.json) binds clean
source commit `d212e24e825472c5467fb57c992d4f9f56b9c92d` (`code_dirty=false`). All ten
cases match their authored protocol statuses: three invalid citations are rejected,
and one semantic counterexample is accepted with support unverified. The artifact
digest and every result model have been checked again.

Ten authored demo scenarios cover valid answers, intentional abstention, empty
retrieval, unknown hits, forged quotations, wrong offsets, uncited claims,
unexpected tools and changed activation. Three invalid citations should be rejected.
A deliberate counterexample pairs an invented pager number with a genuine
ownership sentence. Citation checks still accept it with semantic support
`unverified`; it must not count as a semantically correct answer.

No new online model, real embedding or Google account run was made, and no
dependency was added. The previous 3/6 retrieval false positives remain a
separate historical measurement, not a new abstention-accuracy result.
Answer quality, missing-answer detection, retrieval coverage and abstention rates
require independent data, real models and human semantic labels.

## Reading order

1. [models.py](../../src/voren/knowledge/models.py) and [store.py](../../src/voren/knowledge/store.py):
   why freeze version references, and why is empty different from absent?
2. [answers.py](../../src/voren/knowledge/answers.py) and [citations.py](../../src/voren/knowledge/citations.py):
   how does a proposal become a verified citation, and why can its claim still be wrong?
3. [service.py](../../src/voren/web/service.py) and [snapshot recovery tests](../../tests/test_knowledge_snapshot_integration.py):
   what changes on activation, missing vectors and rejection; when is dispatch blocked?
4. [protocol demo](../../scripts/demo_knowledge_answers.py) and [answer tests](../../tests/test_knowledge_answers.py):
   which failures are citation errors, and which need semantic judgement or live-provider evidence?
