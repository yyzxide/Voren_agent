# Phase 2: Source-bound knowledge retrieval

[简体中文](PHASE2_KNOWLEDGE_RETRIEVAL.zh-CN.md)

## Boundary

This slice adds small-corpus retrieval for meeting notes, operator notes, and
extracted attachment text. It deliberately does not call every database query
"RAG" and does not introduce a vector database before retrieval quality needs
one. Exact dates, attendees, and free/busy state remain structured workspace
tools.

## Invariants

- A document version is immutable and content-addressed.
- Search sees only the explicitly active version of each document.
- Every hit carries its exact document/version reference, source URI, source
  kind, and content digest.
- Imported text has `instruction_authority=false`, including text labelled as
  an operator note. Durable preferences still enter through the typed Profile
  evidence boundary.
- Re-importing a corrected document creates another version; it does not alter
  a prior version. Activation is a separate reason-bearing operation.
- CLI inspection is content-redacted by default.

The first retriever is deterministic lexical matching. It supports Latin terms
and CJK character/bigram terms, emits a bounded snippet, and has stable ranking
tie-breakers. This is an auditable baseline, not a semantic-retrieval quality
claim.

## CLI

```bash
voren knowledge ingest notes/demo.md \
  --document-id meeting:demo \
  --title 'Demo review' \
  --source-uri 'file:///controlled/notes/demo.md' \
  --source-kind meeting_note \
  --reason 'operator reviewed import'

voren knowledge search 'approval receipt'
voren knowledge inspect --document-id meeting:demo
```

## Verification

`tests/test_knowledge_retrieval.py` covers exact citations, active-version
switching, CJK retrieval, tamper detection, unactivated documents, redacted CLI
inspection, and provenance-labelled Tool Observations.

The [MCP transport slice](PHASE2_MCP_RETRIEVAL.md) exposes this same store
through an official client/server round trip. MCP does not change the trust or
instruction-authority rules.
