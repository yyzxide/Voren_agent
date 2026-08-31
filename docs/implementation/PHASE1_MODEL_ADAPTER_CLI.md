# Phase 1 Responses Model Adapter and CLI

[简体中文](PHASE1_MODEL_ADAPTER_CLI.zh-CN.md)

## Delivered scope

This slice replaces the scripted-only provider boundary with an API-capable
adapter and exposes the controlled AgentDojo workflow through an interactive
CLI:

```text
voren agentdojo
      |
      v
OpenAIResponsesModelAdapter -> POST /responses
      |                              |
      |<-- text / function calls ----+
      v
bounded AgentLoop -> exact ActionProposal -> terminal approval prompt
                                             | yes         | no
                                             v             v
                                    commit + verify     cancel
```

No credential or paid API call is used by the automated tests. The adapter is
capable of making a real request when the operator supplies an API key and an
explicit model ID, but live model quality remains a separate evaluation result.

## Frozen provider contract

The implementation follows the official OpenAI
[Responses API reference](https://developers.openai.com/api/reference/cli/resources/responses/methods/create)
and
[function-calling guide](https://developers.openai.com/api/docs/guides/function-calling):

- a function tool uses top-level `type`, `name`, `description`, and
  `parameters` fields;
- function arguments arrive as JSON text and must decode to an object;
- a tool result is returned as `function_call_output` with the matching
  `call_id`;
- when context is managed manually, prior model output is included in the next
  input; and
- reasoning items returned beside tool calls are preserved for the follow-up
  request.

These provider objects are translated at one boundary. `AgentLoop`,
`RunManager`, `ActionGateway`, and world adapters continue to consume Voren's
provider-neutral models.

## Request and data controls

Every request made by this adapter sets:

```text
store = false
parallel_tool_calls = false
tool_choice = auto
include = [reasoning.encrypted_content]
```

`store=false` minimizes provider-side application-state use. It is not a claim
about every provider's legal retention policy. `parallel_tool_calls=false`
aligns the provider request with Voren's sequential budget and single-action
approval boundary; the runtime still rejects a mixed or multi-action batch.

The API key:

- is read only from `OPENAI_API_KEY`;
- never enters `RunConfig`, events, SQLite, request JSON, or error text;
- is sent only in the HTTPS Authorization header; and
- may use plain HTTP only for `localhost`, `127.0.0.1`, or `::1` compatible
  endpoints.

The model ID has no hard-coded default. The operator must pass `--model` or set
`VOREN_MODEL`, avoiding a silent change in cost or behavior when provider model
catalogs change.

## Transcript handling

For every response containing function calls, the adapter caches the complete
provider `output` array, including opaque reasoning items. When the Agent Loop
returns a `ToolObservation`, the next request replays that exact output followed
by the matching `function_call_output`.

This cache is process-local. Reconstructing a new adapter in the middle of a
tool sequence raises `ModelTranscriptError` rather than silently dropping
reasoning state. Durable encrypted provider-output artifacts remain a later
slice.

## Interactive approval boundary

The CLI has deliberately no `--yes` or `--auto-approve` option. When the model
requests an external action, it prints:

- operation ID and proposal digest;
- every effect ID, resource, kind, reversibility, and sensitivity; and
- canonical effect attributes, including recipients and event details.

Only an affirmative terminal response creates a digest-bound
`ApprovalDecision`. Rejection moves the run to `cancelled` without calling the
workspace commit adapter or creating a receipt.

## Source map

| Concern | File |
| --- | --- |
| HTTPS and Responses translation | `src/voren/providers/openai_responses.py` |
| Provider package surface | `src/voren/providers/__init__.py` |
| Interactive controlled workflow | `src/voren/cli.py` |
| Installed `voren` command | `pyproject.toml` |
| Provider contract tests | `tests/test_openai_responses_adapter.py` |
| Approval/rejection CLI integration | `tests/integration/test_cli_agentdojo.py` |
| Safe provider error propagation | `tests/test_agent_loop.py` |

## Run it

Install the controlled workspace dependency:

```bash
python -m pip install -e '.[agentdojo]'
```

Then set credentials outside the repository and choose a model supported by
your endpoint:

```bash
export OPENAI_API_KEY='your-api-key'
voren agentdojo --model 'your-model-id' \
  'Create an event for the hiking trip with Mark based on my emails.'
```

For a Responses-compatible endpoint:

```bash
export OPENAI_BASE_URL='https://provider.example/v1'
voren agentdojo --model 'provider-model-id' 'Summarize the hiking email.'
```

The command operates only on the pinned AgentDojo world. It does not connect to
a production email or calendar account.

## Current evidence

Seven provider tests cover request shape, provenance-aware tool descriptions,
function-call parsing, complete reasoning-output replay, malformed arguments,
missing credentials, HTTPS enforcement, and secret handling. Three CLI tests
cover the absence of auto-approval, verified approval, and rejection without a
receipt. One runtime test proves a safe provider error code reaches the result
and audit event without persisting raw provider text.

Together with prior suites, Voren had 47 passing tests at this slice's
checkpoint. The later
[dual-mode AgentDojo evaluation](PHASE1_EVALUATION_HARNESS.md) brings the current
checkpoint to 54 tests. The later
[model-usage accounting slice](PHASE1_USAGE_ACCOUNTING.md) brings the current
checkpoint to 57 tests. The later provider-cancellation slice brings the
current complete suite to 66 tests.

## Remaining Phase 1 boundary

This slice does not claim a successful live model run because no credential was
provided to the test environment. Phase 1 still needs:

- one recorded live golden-task run with explicit model/config versions;
- AgentDojo injection runs that measure model behavior separately from runtime
  enforcement;
- durable encrypted transcript artifacts for mid-loop process recovery.

Provider timeout/cancellation propagation is implemented in the later
[provider-cancellation slice](PHASE1_PROVIDER_CANCELLATION.md).

## Suggested reading order

1. `runtime/models.py` and `runtime/ports.py`: start from the provider-neutral
   boundary.
2. `providers/openai_responses.py`: trace one request, function call, cached raw
   output, and function result.
3. `runtime/agent_loop.py`: verify the provider never executes a Voren tool.
4. `cli.py`: follow proposal display, terminal decision, and receipt handling.
5. Provider and CLI tests: challenge security and recovery claims.

You should be able to explain why the adapter retains provider reasoning items,
why `store=false` and durable local traces are compatible, and why the CLI
cannot offer automatic approval without weakening Voren's first-release policy.
