# Phase 1 Provider Cancellation

## 本步结果

Voren 现在具备一套可供未来网页“停止”按钮使用的取消契约，不会把“浏览器里按钮
变灰了”误写成“Provider 侧推理已经取消”：

```text
Operator / Deadline
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

Runtime 在 Model/Tool Boundary 观察到 Cancellation 后，本地 Run 会在后续 Tool
Call 或 Action Proposal 之前停止。它的终态会记录取消原因、Provider 已报告的
部分 Token Usage，以及 Provider 是否真正确认 `status=cancelled`。

## Background 与 Foreground Profile

Responses API 的 Cancel Endpoint 只接受由 `background=true` 创建的 Response。
所以 OpenAI Profile 会以 Background Mode 发起请求，在状态为 `queued` 或
`in_progress` 时轮询，只在状态变成 `completed` 后解析正常结果。

Voren 仍设置 `store=false`。OpenAI 官方文档说明：为了异步执行和轮询，
Background 数据仍会被临时保存；`store=false` 时大约十分钟后删除。这是真实的
隐私取舍，不能描述成“零留存”。

DeepSeek Profile 不支持 Background、Retrieve、Cancel、`include` 或 `store`
参数，因此使用同步 Foreground Request，并省略这些字段。取消只能阻止 Voren
继续处理迟到输出，`provider_confirmed=false`；不能声称远端生成已经停止。
Profile 必须显式配置，自定义 Base URL 不会按域名猜测能力。

参考资料：

- [OpenAI Background Mode](https://developers.openai.com/api/docs/guides/background)
- [OpenAI Cancel-response API](https://developers.openai.com/api/reference/cli/resources/responses/methods/cancel)
- [OpenAI Retrieve-response API](https://developers.openai.com/api/reference/cli/resources/responses/methods/retrieve)

## 取消状态

`CancellationToken` 是线程安全、Provider-neutral 的。未来 Web Run Registry
可以为每个 Active Run 保存一个 Token，并由另一个 Request Handler 发出取消
请求。Runtime 区分三种原因：

- `operator`：调用者显式要求取消；
- `deadline`：完整模型响应超过配置的 Deadline；
- `provider`：Provider 自行返回 Cancelled 终态。

Provider Confirmation 刻意设计为三态：

- `true`：Cancel Endpoint 返回 `status=cancelled`；
- `false`：Endpoint 失败或返回其他终态，例如取消太晚而已经 `completed`；
- `null`：还没有 Provider Request，或非 Provider Adapter 只完成了本地
  Cooperative Cancellation。

三种情况下本地 Runtime 都会取消，并且不会把迟到的模型输出继续处理成 Tool
Call。`provider_confirmed=false` 会被明确保留，因此 UI 不能宣称远端推理一定
已经停止。

## Timeout 语义

`--timeout-seconds` 现在同时限制完整 Background Model Response 与单次 HTTP
Call。已获得 Response ID 后超过 Deadline，会调用 Cancel Endpoint。底层
Socket Timeout 会归一化为 `provider_timeout`，与一般网络不可达分开。

仍有一个不可回避的边界：如果最初的 Create Request 在 Voren 收到 Response ID
之前失败或超时，Voren 没有可发送给 Cancel Endpoint 的 ID。此时只能让本地
Run 失败，不能声称远端已取消。

Response ID 当前只存在于存活的 Adapter Instance 中，不会写入 Audit Stream。
所以进程在 Polling 期间崩溃后，暂时无法恢复或取消该 Background Response；
持久加密 Mid-loop Recovery 仍是独立的 Phase 1 待办项。

## 持久证据

- `RuntimeResultStatus.CANCELLED` 携带取消原因与 Confirmation；
- `run.cancelled` 持久化这些字段以及归一化的部分 Usage；
- CLI 对 Runtime Cancellation 返回退出码 130；
- Evaluation Schema `voren-evaluation/v3` 在每个 Trial 保存取消原因与
  Confirmation，并按 Mode 汇总 Cancelled Run 数量。

Event Stream 不会新增原始 Prompt、Provider Output 或 Response ID；代价就是上面
说明的恢复限制。

## 源码阅读顺序

1. `src/voren/runtime/cancellation.py`：线程安全 Signal 与 Cancellation
   Exception；
2. `src/voren/runtime/ports.py` 和 `runtime/models.py`：Provider-neutral Contract
   与终态 Result；
3. `src/voren/providers/openai_responses.py`：Background Create、Poll、Deadline
   与 Cancel Protocol；
4. `src/voren/runtime/agent_loop.py`：Stop Check 与“不再进入 Tool”的规则；
5. `src/voren/runs/manager.py`：持久 Cancelled Transition；
6. Provider、Loop、Artifact 与 CLI Test：查看确认、未确认、Deadline、Timeout
   和无 Action 证据。

## 验证边界

这个 Checkpoint 使用 Scripted Transport Response，不声称已经记录过真实
Provider Cancellation。Web API 和 Active-run Registry 仍未实现，所以这是
未来 Interface 的取消基础，而不是已经交付的 Stop Button。
