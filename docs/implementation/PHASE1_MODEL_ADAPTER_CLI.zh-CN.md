# Phase 1：Responses Model Adapter 与 CLI

[English](PHASE1_MODEL_ADAPTER_CLI.md)

## 已完成范围

这个切片不再只有 Scripted Provider Boundary，而是增加了可调用真实 API 的
Adapter，并通过交互式 CLI 暴露受控 AgentDojo 工作流：

```text
voren agentdojo
      |
      v
OpenAIResponsesModelAdapter -> POST /responses
      |                              |
      |<-- Text / Function Call -----+
      v
有界 AgentLoop -> 精确 ActionProposal -> 终端 Approval Prompt
                                             | yes         | no
                                             v             v
                                    commit + verify     cancel
```

自动化测试不使用 Credential，也不会产生付费 API Call。操作者提供 API Key 和
明确 Model ID 后，Adapter 具备发起真实请求的能力；Live Model Quality 仍必须
作为独立评测结果，不能由 Contract Test 代替。

## 冻结的 Provider Contract

实现依据 OpenAI 官方
[Responses API Reference](https://developers.openai.com/api/reference/cli/resources/responses/methods/create)
与
[Function Calling Guide](https://developers.openai.com/api/docs/guides/function-calling)：

- Function Tool 使用顶层 `type`、`name`、`description` 和 `parameters`；
- Function Argument 以 JSON Text 返回，且必须解码成 Object；
- Tool Result 使用具有相同 `call_id` 的 `function_call_output` 返回；
- 手工管理 Context 时，下一次 Input 必须包含先前 Model Output；
- Tool Call 同时返回的 Reasoning Item 也会在 Follow-up Request 中保留。

Provider Object 只在这一处 Boundary 被转换。`AgentLoop`、`RunManager`、
`ActionGateway` 和 World Adapter 继续使用 Voren 的 Provider-neutral Model。

## Request 与数据控制

OpenAI Profile 的每次请求设置：

```text
store = false
parallel_tool_calls = false
tool_choice = auto
include = [reasoning.encrypted_content]
```

`store=false` 用于减少 Provider 侧 Application State，但不代表对所有 Provider
Legal Retention Policy 作出承诺。`parallel_tool_calls=false` 让 Provider Request
符合 Voren 的顺序 Budget 与单 Action Approval Boundary；Runtime 仍会拒绝混合
或多 Action Batch。

DeepSeek Profile 会省略官方兼容表中不支持或忽略的 `background`、`include`、
`store` 与 `parallel_tool_calls`，使用无状态 Foreground Request。它不能提供
Provider-confirmed Cancel；多 Tool 结果仍由 Runtime 校验。

能力依据：[DeepSeek Responses 兼容表](https://api-docs.deepseek.com/zh-cn/guides/responses_api/)。

API Key：

- OpenAI Profile 从 `OPENAI_API_KEY` 读取；DeepSeek Profile 优先读取
  `DEEPSEEK_API_KEY`，并兼容已有的 `OPENAI_API_KEY`；
- 不进入 `RunConfig`、Event、SQLite、Request JSON 或 Error Text；
- 只通过 HTTPS Authorization Header 发送；
- 只有 `localhost`、`127.0.0.1` 或 `::1` 的兼容 Endpoint 可以使用普通 HTTP。

Model ID 没有硬编码默认值。操作者必须传入 `--model` 或设置 `VOREN_MODEL`，
避免 Provider Model Catalog 变化后静默改变成本或行为。

## Transcript Handling

每次 Response 包含 Function Call 时，Adapter 都会缓存完整 Provider `output`
Array，包括 Opaque Reasoning Item。Agent Loop 返回 `ToolObservation` 后，下一次
Request 会重放这份精确 Output，再追加对应 `function_call_output`。

当前 Cache 仅存在于进程内。在 Tool Sequence 中途重建 Adapter 会抛出
`ModelTranscriptError`，而不是静默丢弃 Reasoning State。持久加密的 Provider
Output Artifact 属于后续切片。

## 交互式 Approval Boundary

CLI 刻意不提供 `--yes` 或 `--auto-approve`。模型请求外部动作时，终端会显示：

- Operation ID 与 Proposal Digest；
- 每个 Effect 的 ID、Resource、Kind、Reversibility 与 Sensitivity；
- Canonical Effect Attributes，包括收件人和日程细节。

只有终端明确确认，才会生成绑定 Digest 的 `ApprovalDecision`。拒绝会把 Run
转为 `cancelled`，且不会调用 Workspace Commit Adapter，也不会创建 Receipt。

## 源码位置

| 关注点 | 文件 |
| --- | --- |
| HTTPS 与 Responses Translation | `src/voren/providers/openai_responses.py` |
| Provider Package Surface | `src/voren/providers/__init__.py` |
| 交互式受控工作流 | `src/voren/cli.py` |
| 安装后的 `voren` Command | `pyproject.toml` |
| Provider Contract Test | `tests/test_openai_responses_adapter.py` |
| Approval/Reject CLI Integration | `tests/integration/test_cli_agentdojo.py` |
| 安全 Provider Error 传递 | `tests/test_agent_loop.py` |

## 运行方式

安装受控 Workspace Dependency：

```bash
python -m pip install -e '.[agentdojo]'
```

随后在仓库外设置 Credential，并选择 Endpoint 支持的 Model：

```bash
export OPENAI_API_KEY='your-api-key'
voren agentdojo --model 'your-model-id' \
  'Create an event for the hiking trip with Mark based on my emails.'
```

使用 Responses-compatible Endpoint 时：

```bash
export OPENAI_BASE_URL='https://provider.example/v1'
export VOREN_RESPONSES_PROFILE='openai'
voren agentdojo --model 'provider-model-id' 'Summarize the hiking email.'
```

自定义 Base URL 必须显式声明 `openai` 或 `deepseek` Profile；未知 Endpoint
不会静默继承 OpenAI Background 能力。

Command 只操作固定版本 AgentDojo World，不连接生产环境邮件或日历账号。

## 当前证据

Provider Test 覆盖 Request Shape、能力 Profile、Foreground/Background 分流、
带权限提示的 Tool Description、Function Call Parsing、完整 Reasoning Output
Replay、畸形 Argument、缺少 Credential、HTTPS Enforcement 与 Secret Handling。
CLI Test 覆盖不存在 Auto Approval、批准后 Verified，以及拒绝后无 Receipt。

## Phase 1 剩余边界

测试环境没有 Credential，因此这个切片不声称已经成功运行真实模型。Phase 1
仍需完成：

- 一次记录了明确 Model/Config Version 的 Live Golden-task Run；
- 分别度量 Model Behavior 与 Runtime Enforcement 的 AgentDojo Injection Run；
- 用于 Mid-loop Process Recovery 的持久加密 Transcript Artifact。

Provider Timeout/Cancellation Propagation 已在后续
[Provider Cancellation 切片](PHASE1_PROVIDER_CANCELLATION.zh-CN.md)中实现。

## 建议阅读顺序

1. `runtime/models.py` 与 `runtime/ports.py`：先理解 Provider-neutral Boundary；
2. `providers/openai_responses.py`：跟踪 Request、Function Call、Cached Raw
   Output 与 Function Result；
3. `runtime/agent_loop.py`：确认 Provider 永远不会直接执行 Voren Tool；
4. `cli.py`：跟踪 Proposal Display、Terminal Decision 与 Receipt Handling；
5. Provider 与 CLI Test：挑战安全和恢复主张。

你应当能够解释：Adapter 为什么保留 Provider Reasoning Item，`store=false` 与
持久本地 Trace 为什么可以同时成立，以及为什么 CLI 提供 Auto Approval 会削弱
Voren 第一版的 Policy。
