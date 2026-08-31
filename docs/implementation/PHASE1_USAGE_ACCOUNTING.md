# Phase 1 Model-Usage Accounting

## Outcome

Voren now carries provider-reported model usage through the complete execution
path:

```text
Responses API -> provider adapter -> ModelResponse -> AgentLoop
              -> RuntimeResult / run events -> evaluation artifact / CLI
```

This slice makes efficiency evidence reproducible without pretending that an
unreported request cost zero tokens.

## Provider contract

`OpenAIResponsesModelAdapter` maps the Responses API usage object into the
provider-neutral `ModelUsage` contract:

| Responses field | Voren field |
| --- | --- |
| `input_tokens` | `input_tokens` |
| `input_tokens_details.cached_tokens` | `cached_input_tokens` |
| `input_tokens_details.cache_write_tokens` | `cache_write_input_tokens` |
| `output_tokens` | `output_tokens` |
| `output_tokens_details.reasoning_tokens` | `reasoning_output_tokens` |
| `total_tokens` | `total_tokens` |

The provider boundary rejects booleans, negative values, malformed detail
objects, a cached-input count greater than total input, a reasoning count
greater than total output, and a `total_tokens` value that differs from input
plus output. An absent usage object remains `None`; it is not converted to an
all-zero report.

## Runtime completeness

`AgentLoop` maintains a `RuntimeUsage` accumulator for every run. It records:

- `model_requests`: every attempted model request, including provider failures;
- `reported_model_requests`: requests whose completed response carried usage;
- the summed token fields from all reported responses;
- `complete`: whether the two request counts match.

Consequently, `300 total_tokens, complete=false` means that Voren observed at
least 300 tokens but does not claim that 300 is the complete run total.

Each `model.responded` event stores the normalized usage object and an explicit
`usage_reported` flag. The event does not persist the raw provider response,
prompt, model output, or tool observation body.

## Evaluation artifact and CLI

At this slice's checkpoint the evaluation artifact schema became
`voren-evaluation/v2`. Each trial stores
its `RuntimeUsage`, and each mode summary aggregates all trial usage while
preserving request-report completeness. The interactive CLI prints the same
normalized fields after a run; evaluation summaries show total tokens and the
reported/requested ratio.

Voren does not estimate monetary cost in this slice. Correct cost attribution
must bind usage to the exact provider, model/version, pricing snapshot, and
date. Adding a floating current price later would make an old artifact
non-reproducible. Provider compute-unit fields are also outside the current
neutral contract.

## Source map

Read the implementation in this order:

1. `src/voren/runtime/models.py` — `ModelUsage` and `RuntimeUsage` invariants.
2. `src/voren/providers/openai_responses.py` — provider-field parsing and
   rejection of malformed usage.
3. `src/voren/runtime/agent_loop.py` — per-request accumulation and audit
   events.
4. `src/voren/evaluation/models.py` — per-trial persistence and per-mode
   aggregation.
5. `src/voren/cli.py` — human-readable reporting.
6. `tests/test_openai_responses_adapter.py`, `tests/test_agent_loop.py`, and
   `tests/integration/test_agentdojo_evaluation.py` — boundary, failure, and
   end-to-end evidence.

## Verification boundary

This slice had 57 passing tests at its checkpoint. The later
[provider-cancellation slice](PHASE1_PROVIDER_CANCELLATION.md) upgrades the
artifact schema to `voren-evaluation/v3` and brings the complete suite to 66
tests. These tests use deterministic scripted provider responses and verify
parsing, validation, aggregation, incomplete reports, CLI output, and
evaluation artifacts.

No live model request was made for this checkpoint because `OPENAI_API_KEY` and
`VOREN_MODEL` were not configured in the development environment. Therefore,
this checkpoint proves the accounting path, not a live provider's current
behavior or model quality.
