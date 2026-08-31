# Phase 1 模型用量统计

## 本步结果

Voren 现在会让 Provider 报告的模型用量贯穿完整执行路径：

```text
Responses API -> Provider Adapter -> ModelResponse -> AgentLoop
              -> RuntimeResult / Run Events -> Evaluation Artifact / CLI
```

这使效率数据可以复核，同时不会把“没有报告 Token”的请求误写成“消耗了 0
Token”。

## Provider 契约

`OpenAIResponsesModelAdapter` 会把 Responses API 的 Usage 对象转换为与
Provider 无关的 `ModelUsage`：

| Responses 字段 | Voren 字段 |
| --- | --- |
| `input_tokens` | `input_tokens` |
| `input_tokens_details.cached_tokens` | `cached_input_tokens` |
| `input_tokens_details.cache_write_tokens` | `cache_write_input_tokens` |
| `output_tokens` | `output_tokens` |
| `output_tokens_details.reasoning_tokens` | `reasoning_output_tokens` |
| `total_tokens` | `total_tokens` |

Provider 边界会拒绝布尔值、负数、错误的 Details 结构、超过 Input 总数的
Cached Input、超过 Output 总数的 Reasoning，以及不等于 Input 加 Output 的
`total_tokens`。如果响应中没有 Usage 对象，结果会保留为 `None`，而不是伪造
一份全零报告。

## Runtime 完整性

`AgentLoop` 为每个 Run 维护一个 `RuntimeUsage` 累加器，其中包括：

- `model_requests`：所有发起过的模型请求，包括 Provider 失败；
- `reported_model_requests`：完成响应并且携带 Usage 的请求数；
- 所有已报告响应的各项 Token 总和；
- `complete`：上述两个请求数是否相等。

因此，`300 total_tokens, complete=false` 的含义是：Voren 至少观察到了 300
Token，但不会声称 300 就是完整的 Run 总量。

每个 `model.responded` 事件都会保存归一化 Usage 和明确的
`usage_reported` 标志，但不会持久化原始 Provider 响应、Prompt、模型输出或
Tool Observation 正文。

## Evaluation Artifact 与 CLI

在这个切片的 Checkpoint，Evaluation Artifact Schema 升级为
`voren-evaluation/v2`。每个 Trial 保存自己
的 `RuntimeUsage`；每种 Mode 的 Summary 会聚合所有 Trial，同时保留“报告请求
数 / 总请求数”这一完整性信息。交互式 CLI 会在 Run 结束后输出同一组归一化
字段，评测摘要则展示总 Token 和报告比例。

本切片不估算金额成本。正确的成本归因必须绑定具体 Provider、模型与版本、价格
快照和日期；如果以后直接套用不断变化的当前价格，旧 Artifact 就无法复现。
Provider 的 Compute Units 字段也尚未纳入当前中立契约。

## 源码阅读顺序

建议按以下顺序阅读：

1. `src/voren/runtime/models.py`：理解 `ModelUsage` 和 `RuntimeUsage` 的不变量；
2. `src/voren/providers/openai_responses.py`：理解 Provider 字段映射和错误 Usage
   的拒绝逻辑；
3. `src/voren/runtime/agent_loop.py`：理解逐请求累加和审计事件；
4. `src/voren/evaluation/models.py`：理解 Trial 持久化与 Mode 聚合；
5. `src/voren/cli.py`：理解面向操作者的展示；
6. `tests/test_openai_responses_adapter.py`、`tests/test_agent_loop.py` 和
   `tests/integration/test_agentdojo_evaluation.py`：查看边界、失败路径和端到端
   证据。

## 验证边界

这个切片的 Checkpoint 共有 57 个通过的测试。后续
[Provider Cancellation 切片](PHASE1_PROVIDER_CANCELLATION.zh-CN.md) 将 Artifact
Schema 升级为 `voren-evaluation/v3`，并把完整 Suite 增加到 66 个测试。它们使用
确定性的 Scripted Provider 响应，覆盖 Usage 解析、校验、累加、报告不完整、
CLI 输出和 Evaluation Artifact。

开发环境没有配置 `OPENAI_API_KEY` 与 `VOREN_MODEL`，所以这个 Checkpoint 没有
发起真实模型请求。它证明的是用量统计链路，而不是真实 Provider 当前行为或模型
质量。
