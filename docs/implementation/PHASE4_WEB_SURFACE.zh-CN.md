# Phase 4：本地 Web 与 SSE 展示

[English](PHASE4_WEB_SURFACE.md)

## 产品边界

Voren 第一版 Web 是本地单 Operator 应用。默认只监听 `127.0.0.1`；由于没有假装
实现多用户鉴权，入口会拒绝非 Loopback Host。RunGuild 负责 Workspace/Team
协作产品，Voren 负责一个人的邮件与日程行动流。

UI 不需要 Login，也不展示内部数据库 ID。Browser 发送稳定
`client_request_id` 后任务立即运行，只读步骤不再要求再点一个按钮；只有真正产生
External Action Proposal 时，才出现一次绑定精确副作用的批准/拒绝。

## 请求与审批不变量

- `client_request_id` 会持久绑定 Request Digest 与 Run ID；重试返回相同结果，
  同一个 ID 不能改绑另一段文本；
- Browser 会在 `localStorage` 保留尚未确认响应的 Submission/Decision ID，并用同一
  ID 重试；页面刷新后也会按最近 Run ID 恢复持久结果；
- Browser Snapshot 持久化到 SQLite，Final Answer、Proposal 与 Receipt 刷新后
  仍可读取，Secret 不会复制进 URL；
- Approval 使用独立 Decision ID，并携带精确 Proposal Digest；
- 完全相同的 Decision 重试返回同一 Receipt；Digest 变化或 Decision 冲突返回
  HTTP 409；
- 等待审批时，受控 AgentDojo Workspace Handle 保存在内存。进程重启后页面会
  明确标记审批不可恢复，不会派发或重试动作；Google Connector 使用稳定的远端
  Operation Identity，因此可以在重启后重建 Adapter，尚未决定的审批仍可恢复；
- 批准后的动作仍经过 `ActionGateway`、原子 Operation Ledger、Postcondition
  Verification 和持久 Run Event；Web 层不能直接修改 Workspace。

## 运行模式

默认 `VOREN_WEB_MODE=demo`。它使用确定性 Planner，在没有 API Key 时走完真实
Agent Loop、邮件/日历读取、MCP Knowledge Read、Action Proposal、审批与 Verified
Receipt。页面会明确标注模式；这是产品流程证据，不是模型质量证据。

`VOREN_WEB_MODE=live` 会从 `VOREN_MODEL`、显式 Provider Profile 和对应 API Key
构建已有 Responses Adapter。默认 `VOREN_WEB_WORKSPACE=agentdojo` 仍使用一次性
Benchmark World；显式选择 `VOREN_WEB_WORKSPACE=google` 时，同一个 Web Flow 会切换
到 Phase 5 的保守 Google Connector，而且 Google 不能被错误标成确定性 Demo。
Model 或 Workspace 配置缺失时返回 HTTP 503，同时释放 Request Reservation，修正
配置后可以安全重试。

## 连接 Skill Lifecycle

Web 默认与 CLI 使用同一个已导入 Knowledge Database 和经过审查的 Active Skill
Store：`.voren/voren.sqlite3` 与 `.voren/skills`。Browser Run Snapshot 仍保存在
`.voren/web.sqlite3`，因此建立连接不会迁移或丢弃已有 Web 状态。位置可分别通过
`VOREN_KNOWLEDGE_DATABASE`、`VOREN_SKILL_DATABASE` 与 `VOREN_SKILL_STORE` 配置。

每次请求都会在第一次 Model Call 前执行确定性的 Metadata Router。选中的精确版本
与不含 Request 原文的 Routing Evidence 同时持久化到底层 RunConfig 和 Browser 可见
Run Snapshot。页面会明确显示 `no_skill` 或选中的 `name@version`，不会暗示所有请求
都使用了已学习指导。`VOREN_WEB_SKILL_ROUTING=disabled` 提供显式 Web Baseline，
Health Response 会报告精确 Knowledge Database、Routing Mode 与 Active Skill 数量。

## Event 交付

`GET /api/runs/{run_id}/events/stream` 使用带 Sequence ID 的 Server-Sent Events
回放 Append-only Run Event。重连时可传入 `after=<sequence>` 继续读取，UI 不会重复
插入已有 Event；JSON Event Endpoint 也提供相同 Cursor Contract 便于排查。

本切片固定 FastAPI 0.135.1，因为使用其内置的 `EventSourceResponse` 与
`ServerSentEvent` API。参考[官方 SSE 指南](https://fastapi.tiangolo.com/tutorial/server-sent-events/)。

## 验证

`tests/integration/test_web_app.py` 覆盖静态页面与可读字号、Health/Mode Disclosure、
无 Credential 问候、绑定来源的知识回答、路由到的精确 Skill Version、立即执行的
日程流程、错误 Digest、批准后的 Receipt、拒绝、重复 Submission/Decision、冲突
Request ID、重启后丢失内存 Workspace、SSE 回放，以及模型配置失败后的重试。

`tests/integration/test_web_http_process.py` 补充进程边界验收路径：它在随机 Loopback
端口冷启动已安装的 Web 入口，通过真实 TCP/HTTP 加载页面、查询已导入且绑定来源的
文档、路由已安装的 Active Skill、提交日程任务、批准绑定精确 Effect 的 Digest、
消费终态 SSE，再使用同一 SQLite 数据库重启 Server，验证
已完成 Receipt 仍能恢复。该路径只使用确定性 Demo Workspace，不需要模型或 Google
Credential。

Test Client 与进程边界验收都需要本地 IPC；能力受限沙箱会跳过前置条件不满足的
测试。完整 Suite 也会在该边界外运行，普通 CI 环境会实际执行。
