# 0.2.0: resumable tasks with multiple actions

[简体中文](V02_MULTI_ACTION.zh-CN.md)

2026-09-26. After an action is verified, the same Run can continue reading, propose
another action for a separate approval, and finish with a persisted summary.
A Google workflow can create a calendar hold without invitations, then create a
reply draft. Rejecting or failing the second action preserves the first action's
receipt; completed effects are not automatically rolled back.

## Usage

The Web interface enables this mode by default:

```bash
voren-web
```

Choose the two-step scheduling example, approve its calendar and confirmation-mail
actions separately, and inspect both receipts and the final summary. This demo uses
the deterministic AgentDojo workspace. `POST /api/runs/{run_id}/resume`, exposed by
the Continue button, resumes work while leaving unapproved proposals pending.

With a model and a dedicated Google test account configured:

```bash
voren google --multi-action --database .voren/v02.sqlite3 \
  --request 'Find the review email, create a calendar hold without invitations, then prepare a reply draft. Ask me to approve each action.'

voren google --database .voren/v02.sqlite3 --resume RUN_ID
```

Use the Run ID printed by the first command. The existing model configuration
environment variables still apply. These commands call the configured live model;
approving an action changes the configured Google test account.
Resume restores frozen model settings, budgets, Memory/Skill references, and
Google account/calendar/time-zone identity. Credentials may rotate and are not
stored in RunConfig. CLI `--resume` is limited to Google Runs created with
`--multi-action`; existing single-action commands retain their behavior.
AgentDojo can execute multiple actions in one process, but its in-memory world
cannot be reconstructed after process loss.

## State and crash boundaries

`RunConfig.continue_after_action` opts into the new lifecycle. A verified action
returns the Run to `running`; only the model's final response completes the Run.
Each action has its own operation ID, proposal digest, and approval. Retrying a
decision for an earlier action cannot authorize a later proposal.

The model response is encrypted before its action proposal is created. Recovery
binds the saved call ID, name, and raw argument digest to that proposal. After
verification, the loop writes a checkpoint containing the exact receipt before
acknowledging its consumption. A crash between those writes is recovered by
finding the already-saved receipt, without dispatching the action again.

Web action history is reconstructed from durable events and the operation ledger.
The browser snapshot is a display cache. Final text remains in the encrypted
checkpoint, so a lost final HTTP response can be restored without another model
call or an external workspace. The final Run preserves `last_receipt_status` so
fully verified Runs remain eligible for existing learning-evidence checks.

## Recovery guarantees and limits

- Transcript v3 encrypts original provider outputs, including reasoning and
  function-call items required for Responses replay. They are bound to a provider
  configuration/endpoint digest; ordinary events contain hashes and counts only.
- v1/v2 checkpoints retain their original digest compatibility. Missing provider
  outputs from older checkpoints are not fabricated.
- New multi-action Runs reserve and persist a model attempt before network I/O.
  An interrupted request whose response was not saved does not refund its budget.
  `model_requests` counts reservations/attempts; unknown token usage keeps
  `usage.complete` false.
- An OS file lock allows one local loop executor per database, including separate
  processes. Process exit releases the lock. Independent Runs also serialize;
  this is not a distributed execution lease.
- Provider replay requires the same configuration, including timeout settings.
  Rotating an API key does not change its fingerprint.
- Google recovery checks the configured account, calendar, and time zone. This
  does not independently attest which account an access token belongs to.
- Ambiguous or temporarily invisible effects are reconciled by observation.
  Previously observed extra or mismatched effects cannot be cleared by a later
  clean observation.

## Local key storage

Web and the new CLI multi-action mode use `VOREN_TRANSCRIPT_KEY` when supplied.
Otherwise, first use creates `<database>.transcript.key` beside the database with
mode `0600`. Keep the database and key together. This encrypts stored context but
does not isolate keys from a compromised local host. A missing or empty key with
existing checkpoints causes an error rather than silent key replacement.
Legacy single-action CLI mode still requires an explicit key.

## Additional fixes and validation

Gateway and Ledger preserve unexpected/mismatched effects even for legacy
ambiguous receipts. Skill change limits now count unified-diff records inside
hunks, including header-like body text and lines without trailing newlines.

Tests use scripted models, the real Responses Adapter with HTTP doubles,
temporary SQLite databases, AgentDojo's test world, and local HTTP/MCP services.
They cover separate approvals, partial completion, stale retries, crash windows,
frozen context, budgets, exclusion locks, provider replay, final-text recovery,
and learning-evidence compatibility.

Local validation used CPython 3.12.3 on Linux. All 86 dependency versions and wheel
SHA-256 hashes matched `pylock.toml`; `pip check` passed. **All 262 tests passed**
in 108.918 seconds; see the [dated test log](../evidence/2026-09-26-v02-tests.log).
JavaScript syntax and `git diff --check` passed. The repository's `.venv` contains
the local 0.2.0 editable installation; start it with `.venv/bin/voren-web`.

```bash
python -m pip check
python -m unittest discover -s tests -v
```

This change adds no live-model benchmark result or real Google-account evidence.
Existing AgentDojo evaluations retain their single-action mode and historical
results. Proactive triggers, context compaction, editing an in-flight proposal,
and multi-agent orchestration remain outside this slice.

Read [RunManager](../../src/voren/runs/manager.py), then
[AgentLoop](../../src/voren/runtime/agent_loop.py),
[Transcript Store](../../src/voren/runtime/transcripts.py), and
[multi-action tests](../../tests/test_multi_action_runtime.py). Trace when an
action finishes, when its receipt becomes model context, and when the Run itself
is allowed to finish.
