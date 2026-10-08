# Controlled retrieval fixture

`controlled-bilingual.json` contains 22 synthetic document versions (20 active
documents) and 28 authored English/Chinese queries. It includes long-document
tails, repeated-term distractors, active-version corrections, multi-document
answers, and six no-answer cases. No private mail or real account data is used.

The labels identify relevant document IDs and exact source quotations. A document
hit without the expected quotation in its returned snippet can improve document
recall while still failing the evidence check. Old and unreviewed versions must
not enter the evaluated search corpus.

Run the credential-free lexical baseline and chunked BM25 evaluation:

```bash
python scripts/evaluate_knowledge_retrieval.py --k 5 \
  --output .voren/artifacts/knowledge-retrieval.json
```

The artifact records the canonical dataset digest, raw fixture-file digest,
source revision, active/version/case counts, per-case ranked source references,
and an integrity digest. The ranking digest excludes timings and creation time
so repeated local runs can compare ranking reproducibility.

Recall@k counts distinct relevant documents in the first k results, MRR uses the
first relevant hit, and both only credit citations matching the active source.
The evidence hit rate requires every labelled quotation for a case to occur in
a relevant returned snippet. No-answer false positives have their own denominator
and do not lower the positive-case recall denominator. Citation checks include
source versions/digests and exact Unicode character offsets for chunked results.

Dense/hybrid modes require an explicit API opt-in and a configured real embedding
provider; they are not part of the default run. See the project embedding setup
before enabling `--allow-embedding-api`. Query embedding requests can incur cost.
The report excludes corpus indexing from search timing but includes query
embedding time when used.
For providers exposing request/token accounting, the artifact records corpus
indexing and each mode's query requests separately, including whether token
usage was completely reported. Token counts are not a monetary cost estimate.

This is a small controlled fixture authored alongside the feature. It checks
retrieval, evidence, and citation behavior. It is not a held-out semantic
benchmark, independent quality validation, or evidence of production throughput.
There is no model judge and no required improvement target. Tests use an explicit
constant-vector double only to verify dense/hybrid evaluation plumbing; those
vectors provide no semantic-quality evidence.
