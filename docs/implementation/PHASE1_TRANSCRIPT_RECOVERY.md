# Phase 1 Encrypted Transcript Recovery

[中文版](PHASE1_TRANSCRIPT_RECOVERY.zh-CN.md)

## Scope

This slice makes an in-flight model/tool loop recoverable without writing raw
mail, prompts, or model context into the append-only audit stream. It persists
the complete context required at a safe model-request boundary:

- system and operator messages;
- assistant tool calls and provenance-labelled tool observations;
- the next model step and all hard tool-call counters;
- seen call IDs, repeated-call signatures, and evidence digests;
- accumulated provider-reported token usage and its completeness.

## Storage and key boundary

`SQLiteTranscriptStore` encrypts every checkpoint with AES-256-GCM. Fresh
96-bit nonces are generated on every revision. The schema version, run ID, and
frozen run-config digest are authenticated as associated data, so ciphertext
cannot be moved to another run or config without detection. After authenticated
decryption, a canonical payload digest is also checked against stored metadata.

SQLite stores only the key fingerprint, nonce, ciphertext, integrity metadata,
and revision. Key material comes from `VOREN_TRANSCRIPT_KEY` and is never stored
in the database or event stream. A wrong key, modified ciphertext, mismatched
config, or invalid checkpoint schema stops recovery.

Generate a local key once and keep it outside Git:

```bash
export VOREN_TRANSCRIPT_KEY="$(python3 -c 'from voren.runtime.transcripts import SQLiteTranscriptStore; print(SQLiteTranscriptStore.generate_key())')"
```

Losing this key intentionally makes existing transcript checkpoints
unrecoverable. Key rotation is not implemented in Phase 1.

## Recovery semantics

The loop saves an initial checkpoint before its first model request and a new
checkpoint after each completed batch of allowlisted pure reads. `resume(run_id)`
accepts only a durable run still in `running` state and verifies the checkpoint
against its frozen config before making another model request.

This gives a deliberate replay boundary:

- completed reads before the latest checkpoint are not executed again;
- model/tool budgets and usage continue instead of resetting after restart;
- an interruption inside the current model request or pure-read batch may replay
  work after the previous safe checkpoint;
- external actions are never committed in this replayable region: they still
  become a durable proposal and hard approval pause first;
- completed, failed, limited, and cancelled loops delete their checkpoints;
  approval-paused context is retained until the decision path finishes.

The append-only event stream continues to contain only digests and boundary
metadata. Sensitive model context exists only in the encrypted checkpoint.

## Verification

The automated recovery tests simulate a process interruption on the second
model request, close every SQLite connection, rebuild the runtime, and verify
that:

- the prior tool observation reaches the reconstructed model context;
- the already completed read is not replayed;
- model-step, tool-call, and token-usage budgets continue correctly;
- raw sensitive text is absent from SQLite ciphertext;
- the wrong key and a one-bit ciphertext mutation are rejected;
- the checkpoint is removed after terminal completion.

This slice does not yet persist a live connector's complete remote snapshot,
compact long transcripts, rotate keys, or expose recovery through a web API.
