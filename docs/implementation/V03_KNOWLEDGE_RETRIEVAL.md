# 0.3.0: chunk citations, hybrid retrieval, and fixed-corpus evaluation

[简体中文](V03_KNOWLEDGE_RETRIEVAL.zh-CN.md)

2026-10-08. The original retriever could find a long document but return its opening
instead of the relevant passage. This slice defaults to chunked BM25, keeps the
original `lexical` baseline, and adds explicitly indexed `dense` retrieval and
`hybrid` reciprocal rank fusion. It extends the existing Agent's knowledge read
tool; it is not a separate automatic question-answering or action system.

## Credential-free usage

In an installed Python 3.12 environment:

```bash
python scripts/evaluate_knowledge_retrieval.py --k 5
voren knowledge search 'approval receipt' --mode bm25
voren knowledge search 'approval receipt' --mode lexical
```

The evaluator installs a controlled synthetic corpus into a temporary SQLite
database, leaving normal knowledge untouched. Default modes make no API calls.
The report includes rankings, citation versions, reference-based scores and
dataset/artifact digests, and defaults to `.voren/artifacts/knowledge-retrieval.json`.
Normal search uses already active sources; ingest/activation retains its existing contract.

Web and MCP default to BM25. New Web Runs freeze the retrieval mode and embedding
fingerprint; changing them prevents continuation until the original configuration
is restored. Historical Runs without the field retain lexical retrieval. Reading
stored terminal results needs no retrieval configuration or new model request.

## Explicit embeddings

Set independent `VOREN_EMBEDDING_ENDPOINT`, `VOREN_EMBEDDING_MODEL` and
`VOREN_EMBEDDING_API_KEY` settings. The provider follows the
[official Embeddings request/response contract](https://developers.openai.com/api/reference/resources/embeddings/methods/create).
The endpoint is a full `/embeddings` URL using HTTPS; loopback HTTP is allowed for
controlled services. There is no default endpoint/model or reuse of chat credentials.

```bash
voren knowledge index --allow-embedding-api
voren knowledge search 'approval receipt' --mode dense --allow-embedding-api
voren knowledge search 'approval receipt' --mode hybrid --allow-embedding-api
```

Indexing transmits active source titles/chunks; searching transmits query text.
Web/MCP explicitly setting `VOREN_KNOWLEDGE_RETRIEVAL_MODE=dense` or `hybrid`
enables query API access and requires a previously built matching index. These
runs may incur provider charges. Optional `VOREN_EMBEDDING_DIMENSIONS` requires
model support. The returned model identifier must exactly match the configured identifier.

## Retrieval and citations

- Only explicitly active source versions are searched; source content and metadata digests are revalidated.
- Chunks use 600 characters with 100-character overlap. Offsets count Python Unicode
  characters in the immutable, stripped imported content, not original PDF pages or byte offsets.
- Chunk IDs bind source version, offsets and content digest. Snippets are exact
  slices with `chunk_start`, `chunk_end`, `chunk_digest`, full document digest and source URI.
- BM25 uses term frequency, inverse document frequency and chunk length normalization.
  Chinese bigrams remain within contiguous character runs.
- Dense ranks normalized vectors by positive cosine similarity; positivity is not
  a calibrated no-answer threshold.
- Hybrid uses RRF with `k=60`, rather than adding incompatible score scales.
- `limit` remains a document count. Each document contributes its best chunk.
- Floating `ranking_score` and the scaled compatibility integer `score` are ordering
  values, not confidence or success probabilities.
- Results remain `external_untrusted` data with `instruction_authority=false`;
  retrieval never grants permission for external writes.

## Index and failure boundaries

Embeddings are a derived table in the existing SQLite database. Immutable source
records are unchanged. Fingerprints bind endpoint, model, dimensions and contract,
excluding credentials. Key rotation does not require a new index.
All active chunks must have matching indexed vectors. New/changed active sources
or changed provider identity require explicit indexing. Missing coverage, corrupted
source/vector digests, dimension mismatch, zero or nonfinite vectors fail rather
than silently indexing or falling back.

Batches contain at most 32 texts; one indexing call allows at most 2,000 new chunks.
Existing matching chunks are reused. All new vectors commit together after all
batches succeed. Failed requests may have incurred provider charges even when no
index was committed. HTTP requests do not retry or redirect, and errors omit
provider bodies, source text and keys. A provider object defaults to 128 request
attempts and a 10-second timeout, configurable separately. This budget resets with
the object/process and is not the Run's unified durable paid-request budget.

## Evaluation scope

All **314 local regression tests passed** (90.612 seconds, no skips); see the
[test log](../evidence/2026-10-08-v03-tests.log). Dependency consistency, JavaScript
syntax and 141 local documentation links passed. The installed version is 0.3.0;
no dependencies were added. The [raw retrieval artifact](../evidence/2026-10-08-v03-retrieval.json)
binds clean source revision `4b202bdcdf883c9767955bb49927d907e97c60d7` and passed
artifact-integrity and per-case metric verification.

The bundled controlled bilingual corpus has 20 active documents and 28 cases:
22 answerable/evidence-labelled cases and 6 no-answer cases. It covers long-document
tails, distractors, repeated terms, Chinese text and inactive/revised versions.
It was authored alongside this feature, not as independent semantic validation.

At `k=5`, both local modes retrieved all labelled documents with MRR 1. The
lexical baseline returned the complete labelled evidence in 20/22 cases; BM25
did so in 22/22. Both returned hits in 3/6 no-answer cases. This demonstrates the
fixture's passage presentation improvement, not correct abstention or answer
generation. All returned citations matched active versions. Latency is a local
run observation, not a performance gain claim.

Dense/hybrid unit tests use hand-written vectors and HTTP integration uses a
loopback test double. Neither demonstrates real embedding quality. This slice
adds no online-model, real-embedding or Google-account run evidence. Real-corpus
evaluation, independent reranking, generated answer/citation grading, no-answer
decisions, OCR and large-scale vector indexes remain outside this slice.

## Reading order

1. [retrieval.py](../../src/voren/knowledge/retrieval.py): how do chunks expose evidence, and why does RRF use ranks?
2. [store.py](../../src/voren/knowledge/store.py): why can stale versions not enter results, and when do vectors commit?
3. [embeddings.py](../../src/voren/knowledge/embeddings.py): what do provider identity and result indexes bind?
4. [evaluation.py](../../src/voren/knowledge/evaluation.py): why separate document recall, evidence hits and no-answer false positives?
5. [Tests](../../tests/test_chunk_retrieval.py) and [evaluator](../../scripts/evaluate_knowledge_retrieval.py): which conclusions need a real corpus?
