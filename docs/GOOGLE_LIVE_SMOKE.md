# Google Workspace live smoke runbook

[简体中文](GOOGLE_LIVE_SMOKE.zh-CN.md)

This runbook closes the optional deployment-attestation boundary without
turning personal mailbox data into repository evidence. It does not replace the
deterministic connector tests or the AgentDojo security evaluation.

## Passing contract

One artifact must contain completed runs from one clean Git revision and one
exact model/provider endpoint. Across those runs, Voren must prove both paths:

1. successful `search_emails` -> grounded `create_email_draft` proposal ->
   explicit operator approval -> committed and verified receipt;
2. successful `get_day_calendar_events` -> grounded
   `create_private_calendar_event` proposal -> explicit operator approval ->
   committed and verified receipt.

The exporter rejects injected test connectors/models, incomplete or rejected
runs, ungrounded actions, missing coverage, dirty or mixed revisions, mixed
model boundaries, and failed receipts.

## Prepare a dedicated account

Use a test account, not a personal mailbox. Seed it with a uniquely named test
email and one harmless calendar item. Issue a short-lived OAuth access token
with only the Gmail read/draft and Calendar event scopes needed by this
connector. Keep credentials in the environment and run from a clean repository
checkout; never paste them into a command argument or commit them.

```bash
export VOREN_TRANSCRIPT_KEY='url-safe-base64-encoded-32-byte-key'
export GOOGLE_WORKSPACE_ACCESS_TOKEN='short-lived-oauth-token'
export VOREN_GOOGLE_ACCOUNT_EMAIL='dedicated-test-account@example.com'
export VOREN_GOOGLE_TIME_ZONE='Asia/Shanghai'
```

Configure the selected Responses provider in the same shell. For example, a
DeepSeek-compatible run also needs `DEEPSEEK_API_KEY` and the explicit
`deepseek` provider profile.

## Run the two paths

Use unique subjects/titles so the exact result can be observed. Read the
proposal shown by the CLI and approve only if it describes the intended test
effect. The command prints a `run id:` line after model execution.

```bash
voren google --provider-profile deepseek --model deepseek-v4-flash \
  'Search Gmail for the unique Voren smoke email, then create a reply draft to the test sender. Do not send it.'

voren google --provider-profile deepseek --model deepseek-v4-flash \
  'Read the dedicated test calendar for 2026-09-15, then create a private 15-minute Voren smoke hold that day. Add no attendees and send no invitations.'
```

Record the two printed Run IDs. A final status other than `completed`, a
rejected proposal, or a receipt that is not `verified` is not passing evidence.

## Export and inspect

```bash
voren export-google-smoke \
  --database .voren/voren.sqlite3 \
  --run-id '<gmail-run-id>' \
  --run-id '<calendar-run-id>' \
  --output '.voren/artifacts/google-live-smoke.json'
```

The signed JSON contains only run/config digests, dates, source revision,
provider/model/endpoint provenance, token counts, successful tool names,
redacted effect metadata, approval/receipt outcomes, and event-stream digests.
It deliberately excludes prompts, mailbox/calendar content, account identity,
recipients, OAuth tokens, and Google object IDs. Review the JSON before copying
it into `docs/evidence/`; commit it only if that inspection is clean.

The repository does not claim this external smoke passed until such a dated
artifact from a dedicated account is actually present.
