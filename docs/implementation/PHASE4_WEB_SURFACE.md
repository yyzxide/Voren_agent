# Phase 4: local Web and SSE surface

[简体中文](PHASE4_WEB_SURFACE.zh-CN.md)

## Product boundary

The first Voren Web release is a local, single-operator application. It binds to
`127.0.0.1` by default and refuses a non-loopback host because it deliberately
does not pretend to have multi-user authentication. RunGuild owns the
workspace/team collaboration product; Voren owns one person's email/calendar
action flow.

There is no login screen and no internal database ID in the UI. The browser
sends a stable `client_request_id`, a task starts immediately, and read-only
work proceeds without another button. Only an actual external-action proposal
creates one exact-effect approve/reject decision.

## Request and approval invariants

- `client_request_id` is durably bound to the request digest and Run ID. A retry
  returns the same result; the same ID cannot be rebound to different text.
- The browser retains unacknowledged submission/decision IDs in `localStorage`
  and retries with the same identities; refresh restores the latest durable run.
- Browser snapshots are persisted in SQLite, so final answers, proposals, and
  receipts survive refresh without copying secrets into URLs.
- Approval carries a separate decision ID and the exact Proposal Digest.
- A repeated identical decision returns the same Receipt; a changed Digest or
  conflicting decision receives HTTP 409.
- The controlled AgentDojo workspace handle stays in memory while approval is
  pending. If the process restarts, the page marks that approval as
  unrecoverable and no action is dispatched or retried. The Google connector
  uses stable remote operation identities, so its adapter can be reconstructed
  after restart and a still-pending approval remains recoverable.
- Approved actions still pass through `ActionGateway`, the atomic Operation
  Ledger, postcondition verification, and durable Run events. The Web layer
  never writes the workspace directly.

## Modes

`VOREN_WEB_MODE=demo` is the default. It uses a deterministic planner to walk
through the real Agent Loop, email/calendar reads, MCP knowledge reads, action
proposal, approval, and verified receipt without an API key. The page labels
this mode; it is product-flow evidence, not model-quality evidence.

`VOREN_WEB_MODE=live` constructs the existing Responses adapter from
`VOREN_MODEL`, the explicit provider profile, and the corresponding API key.
The default `VOREN_WEB_WORKSPACE=agentdojo` still uses the disposable benchmark
world. Explicitly selecting `VOREN_WEB_WORKSPACE=google` switches the same Web
flow to the conservative Google connector described in Phase 5; Google cannot
be mislabeled as deterministic demo mode. Missing model or workspace
configuration returns HTTP 503 and releases the request reservation so a fixed
configuration can retry safely.

## Skill lifecycle connection

Web uses the same imported Knowledge database and reviewed active-Skill store
as their CLI commands by default: `.voren/voren.sqlite3` plus `.voren/skills`.
Browser Run snapshots remain in `.voren/web.sqlite3`, so introducing these
connections does not migrate or discard prior Web state. The locations are
independently configurable through `VOREN_KNOWLEDGE_DATABASE`,
`VOREN_SKILL_DATABASE`, and `VOREN_SKILL_STORE`.

Each request runs the deterministic metadata router before the first model call.
The selected exact version and request-free routing evidence are persisted both
in the underlying RunConfig and the browser-visible Run snapshot. The page shows
`no_skill` or the selected `name@version` instead of implying that every request
uses learned guidance. `VOREN_WEB_SKILL_ROUTING=disabled` provides an explicit
Web baseline. The Health response reports the exact Knowledge database, routing
mode, and active-Skill count.

## Event delivery

`GET /api/runs/{run_id}/events/stream` replays append-only Run events as
Server-Sent Events with sequence IDs. A reconnect may pass `after=<sequence>` to
continue without duplicating UI entries. The JSON event endpoint exposes the
same cursor contract for debugging.

FastAPI 0.135.1 is pinned because this slice uses its built-in
`EventSourceResponse` and `ServerSentEvent` API. See the
[official SSE guide](https://fastapi.tiangolo.com/tutorial/server-sent-events/).

## Verification

`tests/integration/test_web_app.py` covers the static page and readable font
baseline, health/mode disclosure, credential-free greeting, source-bound
knowledge answer, routed exact Skill version, immediate scheduling flow, exact
Digest mismatch, approved Receipt, rejection, duplicate submission/decision,
conflicting request IDs, lost in-memory workspace after restart, SSE replay, and
model-configuration failure retry.

`tests/integration/test_web_http_process.py` adds a process-boundary acceptance
path. It cold-starts the installed Web entry point on an ephemeral loopback
port, uses real TCP/HTTP requests to load the page, queries an imported
source-bound document, routes an installed active Skill, submits a scheduling
task, approves its exact effect digest, consumes the terminal SSE stream, and
then restarts the server against the same SQLite database to verify that the
completed receipt remains recoverable. It uses only the deterministic demo
workspace and never needs a model or Google credential.

The test client and process-boundary acceptance path need local IPC. Capability-
restricted sandboxes skip tests whose prerequisites are unavailable; the
complete suite is also run outside that boundary and normal CI runs it.
