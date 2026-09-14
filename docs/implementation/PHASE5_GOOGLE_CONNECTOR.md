# Phase 5: conservative Google Workspace connector

[简体中文](PHASE5_GOOGLE_CONNECTOR.zh-CN.md)

## Boundary

This connector is deliberately smaller than a generic Gmail/Calendar client.
It exposes two provenance-labelled reads:

- Gmail message search followed by full-message retrieval;
- one local day's Google Calendar events with explicit time-zone bounds.

It exposes two reversible write proposals through the existing
`ActionGateway`:

- create a Gmail draft;
- create a private calendar hold with no attendees and `sendUpdates=none`.

There is no direct email-send action, delete action, attendee invitation, or
automatic approval. Email and calendar content is always
`external_untrusted` data with `instruction_authority=false`.

## Remote identity and recovery

Calendar writes derive a stable Google event ID from Voren's immutable
operation ID and store that operation ID in a private extended property.
Observation uses `events.get` and rejects an ID collision whose private marker
does not match.

Drafts carry deterministic `Message-ID` and `X-Voren-Operation-ID` headers. A
normal response records the returned draft ID for immediate observation. If
the HTTP response is lost, reconciliation searches only draft messages for
that deterministic identity, verifies exact approved content, and never sends
the message or repeats an uncertain create request.

The connector cannot make a universal exactly-once claim about arbitrary
Google methods. It only provides this identity-and-observation contract for
the two actions above. If observation cannot confirm the exact result, the
operation stays visible as ambiguous.

## Authentication and scopes

Voren accepts a short-lived OAuth access token only from
`GOOGLE_WORKSPACE_ACCESS_TOKEN`; the token is excluded from configuration
representations, traces, proposals, and receipts. OAuth browser consent and
refresh-token storage are intentionally outside this repository. A personal
test deployment needs the narrowest scopes compatible with its selected
features: Gmail read access plus draft management, and Calendar event access.
Google classifies broad Gmail read/compose scopes as restricted, so this first
release is not presented as a verified public OAuth application.

The REST contract follows Google's official
[Gmail message list/get](https://developers.google.com/workspace/gmail/api/guides/list-messages),
[draft creation](https://developers.google.com/workspace/gmail/api/guides/drafts),
[Calendar event insertion](https://developers.google.com/workspace/calendar/api/v3/reference/events/insert),
and [private extended properties](https://developers.google.com/workspace/calendar/api/guides/extended-properties)
documentation.

## Verification status

`tests/test_google_workspace_connector.py` runs the connector through the real
Voren `ActionGateway` and SQLite operation ledger against a deterministic HTTP
contract double. It covers read provenance, stable calendar IDs, draft-only
behavior, exact receipt verification, definitive authorization failure, lost
HTTP responses, and post-restart reconciliation without resending.

No Google credential is present in CI, so these tests prove translation and
failure semantics rather than a live Google account run. A dated live smoke
artifact remains a manual release gate and must never include mailbox content
or tokens.

## CLI vertical slice

The installed `voren google` command and the optional Web live workspace use
the same bounded Agent Loop,
encrypted transcript checkpoints, SQLite run events, operation ledger, exact
approval prompt, action gateway, and verified receipt as the AgentDojo path.
Only the read/action adapters and their frozen contract version change.

Configure model credentials, `VOREN_TRANSCRIPT_KEY`,
`GOOGLE_WORKSPACE_ACCESS_TOKEN`, `VOREN_GOOGLE_ACCOUNT_EMAIL`, and optionally
the calendar/time-zone variables shown in `.env.example`, then run:

```bash
voren google --model 'your-model-id' \
  'Read the project update and prepare a reply draft. Do not send it.'
```

The access token is environment-only: there is intentionally no CLI token
argument that could expose it in process listings or shell history. The CLI has
no automatic approval flag.

For the local Web surface, set `VOREN_WEB_MODE=live` and
`VOREN_WEB_WORKSPACE=google` before starting `voren-web`. Health output and the
page disclose model and workspace readiness separately. A pending Google
approval can survive a Web-process restart because its adapter is reconstructed
from stable remote operation identities; the disposable AgentDojo world cannot.
