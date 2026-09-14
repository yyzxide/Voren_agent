# Voren delivery status — 2026-09-14

[简体中文](2026-09-14-DELIVERY-STATUS.zh-CN.md)

This is the current closure record for the findings in the historical
[portfolio audit](2026-09-14-PORTFOLIO-AUDIT.zh-CN.md). It distinguishes
deterministic implementation evidence from results that require a live model or
a real Google account.

## Verified baseline

- implementation revision: `6d9d779` (CI workflow maintenance: `529cb96`);
- clean locked installation: CPython 3.12 on Linux x86-64 using `pylock.toml`;
- local result: 201 of 201 unit and integration tests passed;
- remote result: [GitHub Actions run 34831675034](https://github.com/yyzxide/Voren_agent/actions/runs/34831675034) passed with the current Node 24-based official Actions;
- the suite is credential-free and does not call a live model or Google account.

The test suite includes a real subprocess/TCP/HTTP browser-service boundary,
official MCP protocol round trips, AgentDojo integration, two-connection SQLite
competition, simulated crash/restart, deterministic external HTTP contracts,
and the complete bounded Skill lifecycle.

## Audit closure

| Finding | State | Evidence |
| --- | --- | --- |
| V1: non-atomic action claim | Closed | `8f5d4df` uses a conditional state transition; two SQLite connections prove only one dispatcher wins. |
| V2: external commit before Receipt | Closed within observable-provider semantics | `9da099b` reconciles by observation after restart and never resends an inconclusive operation. Exactly-once is not claimed for a provider that offers neither an idempotency key nor lookup. |
| V3: implicit provider capability assumptions | Closed | `5e10bea` adds explicit Responses profiles. The DeepSeek profile uses foreground requests, omits unsupported background fields, and distinguishes local cancellation from provider-confirmed cancellation. |
| V4: Skill learning was only planned | Closed for the bounded mechanism | `f9d1e6a` through `5bb1fd6` implement evidence admission, inactive bounded candidates, paired held-out evaluation, deterministic decisions, explicit promotion/rollback, reports, and a policy ablation. `f899b6f` routes exact active versions back into runtime. |
| C1: reproducibility and delivery gaps | Closed for credential-free delivery | `56a798f` adds a complete platform lock and CI; `c6a3f1a` adds cross-process Web verification. Source-bound Knowledge/MCP, Profile Memory, Web Skill routing, and the Google adapter are connected through `1839c3e`. |

`aa88086` additionally binds the resolved endpoint and every provider-returned
model name to schema-v5 evaluation evidence while preserving v3/v4 reads.
`7cadfbd` adds a redacted Google live-smoke schema/exporter that rejects test
doubles, incomplete coverage, dirty revisions, ungrounded actions, and
unverified receipts; `14f318c` makes the required Run IDs visible in the CLI;
`6d9d779` further enforces the fixed Action/Read/Effect allowlist and the
Proposal-to-Approval-to-Receipt digest chain.

## Demonstrable paths

1. Run a provenance-labelled AgentDojo email/calendar workflow, pause on exact
   effects, approve once, and verify the resulting state.
2. Import source-bound Knowledge and expose the same read contract through the
   official MCP SDK.
3. Freeze operator-authored Profile versions and a conservatively routed Skill
   into RunConfig, without treating retrieved text as instructions.
4. Stage an evidence-bound inactive Skill candidate, reject a security
   regression, explicitly promote an accepted version, and roll back exactly.
5. Drive the same workflow over the local Web/SSE interface and recover its
   browser-visible state after refresh or process restart.
6. Select the Google adapter explicitly; preserve the same approval, receipt,
   verification, and reconciliation boundary for drafts and private holds.

## Dated live-model evidence

The [2026-09-14 live AgentDojo report](../evidence/2026-09-14-AGENTDOJO-LIVE.md)
and its two raw, integrity-checked artifacts close the live golden/injection and
No-Skill/static-Skill execution gates. The requested alias
`deepseek-v4-flash` returned `deepseek-flash` in all successful responses. The
Static-Skill sample was worse on utility and more expensive in tokens, so it is
recorded as negative evidence rather than an improvement claim.

## Optional external deployment attestation

- run the two-path [Google live-smoke procedure](../GOOGLE_LIVE_SMOKE.md) with a
  dedicated test account and publish its reviewed redacted artifact.

This is not an unfinished code path: the live/test boundary, strict coverage
validator, redaction projection, integrity digest, CLI, and runbook are
implemented and tested. It remains external evidence because this repository
has no Google credential. Until the artifact exists, the repository does
**not** claim successful access to a real Google account.

## Explicit non-claims

- no universal exactly-once guarantee across arbitrary providers;
- no autonomous or continuously self-modifying agent;
- no proof that learned Skills improve every model or task distribution;
- no production-scale multi-user service, background inbox automation, or
  general-purpose assistant coverage;
- no industrial security certification.
