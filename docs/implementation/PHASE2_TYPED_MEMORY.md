# Phase 2 Typed Memory and Frozen Context

[简体中文](PHASE2_TYPED_MEMORY.zh-CN.md)

## Boundary

Voren stores profile preferences and episode summaries separately from
procedural Skills. They share durable evidence references, but not authority:

- a profile preference can only be classified from a persisted operator
  correction;
- an episode summary can only be linked to persisted verified-Run evidence;
- neither memory kind carries procedural instruction authority; and
- untrusted observations and model reflections cannot directly enter either
  durable store.

This is a typed local memory boundary, not a general vector database or an
automatic memory extractor.

## Versioning and run binding

Profile revisions are immutable, content-addressed objects behind an atomic
active pointer. Episode identities are immutable and reject conflicting
rewrites. A `MemoryContextSnapshot` contains exact references, rendered-context
digest, and byte count. `RunConfig.memory_versions` must match the assembled
snapshot before a Run can be created, so a later profile update cannot rewrite
the context of an earlier Run.

The model-visible JSON labels every record with
`instruction_authority=false`. The append-only Run event contains exact refs,
digest, size, and that authority label, but omits memory content.

## Public workflow

First persist evidence through the existing learning router, then explicitly
classify it:

```bash
voren skill evidence-correction preference.txt \
  --evidence-id operator:timezone-1 --operator operator:sid
voren memory profile --memory-id preference:timezone \
  --evidence-id operator:timezone-1 --reason 'explicit preference'

voren skill evidence-run --run-id RUN_ID \
  --evidence-id run:meeting-1
voren memory episode meeting-summary.txt --memory-id episode:meeting-1 \
  --evidence-id run:meeting-1

voren memory inspect
voren agentdojo --profile-memory preference:timezone \
  --episode-memory episode:meeting-1 --model MODEL 'Summarize the meeting.'
```

Inspection redacts content unless `--include-content` is explicitly supplied.
Agent runs load no memory by default; every included memory identity is explicit.

## Tested failure cases

- verified-Run evidence cannot be classified as a profile preference;
- operator correction evidence cannot be classified as an episode;
- an episode ID cannot be rewritten with different content;
- content tampering is detected during exact-version reload;
- a RunConfig/snapshot mismatch stops before the model call or Run creation;
- frozen context survives a later active-profile update; and
- audit payloads do not copy memory content.
