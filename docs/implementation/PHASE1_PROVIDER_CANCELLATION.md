# Phase 1 Provider Cancellation

## Outcome

Voren now has a cancellation contract that can support a future web Stop
button without equating a hidden browser state change with provider-side
cancellation:

```text
operator / deadline
        |
        v
CancellationToken -> AgentLoop -> OpenAIResponsesModelAdapter
                                      |
                        POST /responses (background=true)
                                      |
                           GET /responses/{id} (poll)
                                      |
                        POST /responses/{id}/cancel
```

Once the runtime observes cancellation at a model/tool boundary, the local run
stops before any later tool call or action proposal. Its terminal state records
the cancellation reason, partial reported token usage, and whether the provider
actually confirmed `status=cancelled`.

## Why background Responses are required

The Responses API cancel endpoint accepts only responses created with
`background=true`. Voren therefore starts each provider request in background
mode, polls while its status is `queued` or `in_progress`, and parses the normal
result only after `completed`.

Voren keeps `store=false`. OpenAI documents that background data is still
temporarily retained for asynchronous execution and polling and is deleted
after roughly ten minutes when `store` is false. That is a real privacy
tradeoff and is not described as zero retention.

References:

- [OpenAI background mode](https://developers.openai.com/api/docs/guides/background)
- [OpenAI cancel-response API](https://developers.openai.com/api/reference/cli/resources/responses/methods/cancel)
- [OpenAI retrieve-response API](https://developers.openai.com/api/reference/cli/resources/responses/methods/retrieve)

## Cancellation states

`CancellationToken` is thread-safe and provider-neutral. A web run registry can
hold one token per active run and request cancellation from another request
handler. The runtime distinguishes three reasons:

- `operator`: an explicit caller request;
- `deadline`: the configured overall model-response deadline expired;
- `provider`: the provider returned a cancelled terminal response independently.

Provider confirmation is deliberately tri-state:

- `true`: the cancel endpoint returned `status=cancelled`;
- `false`: the endpoint failed or returned another terminal status, such as
  `completed` because cancellation arrived too late;
- `null`: no provider request existed yet, or a non-provider adapter handled
  only local cooperative cancellation.

The local runtime is cancelled in all three cases and never processes a late
model output into a tool call. `provider_confirmed=false` remains visible so
the UI cannot claim that remote inference definitely stopped.

## Timeout semantics

`--timeout-seconds` now bounds the complete background model response and each
individual HTTP call. Deadline expiry with a known response ID triggers the
cancel endpoint. Low-level socket timeouts are normalized as
`provider_timeout`, distinct from general reachability failures.

There is one unavoidable boundary: if the initial create request fails or
times out before Voren receives a response ID, Voren has no identifier to send
to the cancel endpoint. It fails the local run but cannot claim remote
cancellation.

The response ID is currently held only in the live adapter instance and is not
written to the audit stream. A process crash during polling therefore cannot
resume or cancel that background response. Durable encrypted mid-loop recovery
remains a separate Phase 1 item.

## Durable evidence

- `RuntimeResultStatus.CANCELLED` carries reason and confirmation;
- `run.cancelled` persists those fields and normalized partial usage;
- the CLI returns exit code 130 for runtime cancellation;
- evaluation schema `voren-evaluation/v3` stores cancellation reason and
  confirmation per trial plus cancelled-run counts per mode.

Raw prompts, provider output, and response IDs are not added to the event
stream; the recovery limitation above is the corresponding tradeoff.

## Source map

1. `src/voren/runtime/cancellation.py` — thread-safe signal and cancellation
   exception.
2. `src/voren/runtime/ports.py` and `runtime/models.py` — provider-neutral
   contract and terminal result.
3. `src/voren/providers/openai_responses.py` — background create, poll,
   deadline, and cancel protocol.
4. `src/voren/runtime/agent_loop.py` — stop checks and the no-more-tools rule.
5. `src/voren/runs/manager.py` — durable cancelled transition.
6. Provider, loop, artifact, and CLI tests — confirmed, unconfirmed, deadline,
   timeout, and no-action evidence.

## Verification boundary

The complete suite contains 66 passing tests. This checkpoint uses scripted
transport responses; it does not claim that a live provider cancellation has
been recorded. The web API and active-run registry do not exist yet, so this is
the cancellation foundation for that interface rather than a shipped Stop
button.
