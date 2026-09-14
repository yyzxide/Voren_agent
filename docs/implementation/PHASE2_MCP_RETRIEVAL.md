# Phase 2: official MCP retrieval boundary

[简体中文](PHASE2_MCP_RETRIEVAL.zh-CN.md)

## Why MCP is a transport, not an authority grant

Voren exposes `search_meeting_knowledge` through the official MCP Python SDK
v2.2.0 and explicitly negotiates protocol revision `2026-07-28`. The MCP server
advertises a read-only, non-destructive, idempotent tool. Those annotations help
a host render and plan correctly; Voren still treats returned text as external
untrusted data with `instruction_authority=false`.

The server returns structured Pydantic output. The client validates that output,
records the configured source identity separately from optional server-reported
identity, and then creates a digest-bound `ToolObservation`. A protocol success
does not prove a result is truthful, and a SHA-256 content digest is not a
publisher signature.

## Runtime path

```text
AgentLoop
  -> MCPKnowledgeReadAdapter
  -> official MCP Client
  -> MCPServer search_meeting_knowledge
  -> SQLiteKnowledgeStore active versions
  -> structured MCP result
  -> non-authoritative Voren ToolObservation
```

`voren-knowledge-mcp` runs the server over stdio and reads the database path
only from `VOREN_KNOWLEDGE_DATABASE`. It never logs to stdout because stdout is
the JSON-RPC wire. A deployed variant can use Streamable HTTP without changing
the tool or observation contracts.

The MCP dependency is optional but pinned to `mcp==2.2.0`; the base action core
does not silently acquire a protocol dependency.

## Verification

`tests/integration/test_mcp_knowledge.py` verifies:

- the official client lists the read-only tool and negotiates the pinned
  protocol;
- a call crosses the MCP protocol and retains exact source metadata;
- prompt-like text in a result reaches the model only as non-authoritative data;
- the same adapter satisfies the Agent Loop read-tool boundary; and
- a real stdio child process completes a JSON-RPC round trip when the execution
  environment allows AnyIO's local IPC primitive.

The last test is capability-skipped inside sandboxes that prohibit the required
`socketpair`; CI and normal local environments execute it. This is an
environment restriction, not a mocked green result.

References: [official Python SDK](https://github.com/modelcontextprotocol/python-sdk),
[MCP server guide](https://modelcontextprotocol.io/docs/develop/build-server),
and [MCP client guide](https://modelcontextprotocol.io/docs/develop/build-client).
