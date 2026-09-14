# Phase 2：官方 MCP 检索边界

[English](PHASE2_MCP_RETRIEVAL.md)

## MCP 是传输协议，不是授权来源

Voren 使用官方 MCP Python SDK v2.2.0 暴露 `search_meeting_knowledge`，并显式
协商 `2026-07-28` 协议版本。MCP Server 将 Tool 标注为只读、非破坏、幂等；
这些 Annotation 能帮助 Host 正确展示和规划，但 Voren 仍把返回正文视为
`instruction_authority=false` 的外部不可信数据。

Server 返回结构化 Pydantic Output。Client 会验证 Output，将本地配置的 Source
Identity 与可选的 Server 自报 Identity 分开记录，再创建绑定 Digest 的
`ToolObservation`。协议调用成功不代表结果必然真实，SHA-256 Content Digest
也不是发布者数字签名。

## Runtime 路径

```text
AgentLoop
  -> MCPKnowledgeReadAdapter
  -> 官方 MCP Client
  -> MCPServer search_meeting_knowledge
  -> SQLiteKnowledgeStore Active Version
  -> Structured MCP Result
  -> 无指令权威的 Voren ToolObservation
```

`voren-knowledge-mcp` 通过 stdio 运行 Server，只从
`VOREN_KNOWLEDGE_DATABASE` 读取数据库路径。它不会向 stdout 记录日志，因为
stdout 是 JSON-RPC Wire。后续部署可改用 Streamable HTTP，而不改变 Tool 或
Observation Contract。

MCP 是可选依赖，并固定为 `mcp==2.2.0`；基础 Action Core 不会无意引入协议依赖。

## 验证

`tests/integration/test_mcp_knowledge.py` 验证：

- 官方 Client 能列出只读 Tool 并协商固定协议版本；
- 调用经过 MCP 协议并保留精确来源 Metadata；
- 结果中的 Prompt-like 文本只会作为无指令权威的数据进入模型；
- 同一 Adapter 满足 Agent Loop 的 Read Tool Boundary；
- 执行环境允许 AnyIO 本地 IPC Primitive 时，真实 stdio 子进程能完成 JSON-RPC
  往返。

最后一项在禁止所需 `socketpair` 的安全沙箱中会按环境能力 Skip；CI 与正常本地
环境会实际执行。这是环境限制，不是用 Mock 伪造绿色结果。

参考：[官方 Python SDK](https://github.com/modelcontextprotocol/python-sdk)、
[MCP Server 指南](https://modelcontextprotocol.io/docs/develop/build-server)与
[MCP Client 指南](https://modelcontextprotocol.io/docs/develop/build-client)。
