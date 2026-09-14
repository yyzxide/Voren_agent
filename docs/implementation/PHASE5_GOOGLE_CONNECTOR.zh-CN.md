# Phase 5：保守的 Google Workspace Connector

[English](PHASE5_GOOGLE_CONNECTOR.md)

## 边界

这个 Connector 有意小于通用 Gmail/Calendar Client，只暴露两个带 Provenance 的
只读能力：

- 先搜索 Gmail Message，再读取完整消息；
- 使用显式时区边界，读取某个本地日期的 Google Calendar Event。

写侧只有两个可逆 Proposal，并且必须经过已有 `ActionGateway`：

- 创建 Gmail Draft；
- 创建没有 Attendee、且使用 `sendUpdates=none` 的私人日历占位。

不存在直接发送邮件、删除、邀请参与者或自动批准。邮件和日历内容始终标记为
`external_untrusted` Data，且 `instruction_authority=false`。

## 远端身份与恢复

日历写入根据不可变 Operation ID 生成稳定的 Google Event ID，同时把 Operation ID
写入 Private Extended Property。观察阶段调用 `events.get`；即使出现相同 Event ID，
只要 Private Marker 不匹配，就不会把它当作本次动作的结果。

Draft 带确定性的 `Message-ID` 与 `X-Voren-Operation-ID` Header。正常响应会暂存
Google 返回的 Draft ID，便于立即观察；若 HTTP 响应丢失，Reconciliation 只搜索
具有确定身份的 Draft Message，核对被批准的精确内容，不发送邮件，也不重复一次
结果未知的 Create Request。

该 Connector 不对任意 Google Method 宣称普适 Exactly-once，只对上述两个 Action
提供身份与观察契约。无法确认精确结果时，Operation 会以 Ambiguous 状态公开停住。

## 认证与 Scope

Voren 只从 `GOOGLE_WORKSPACE_ACCESS_TOKEN` 读取短期 OAuth Access Token；Token 不会
出现在 Config Representation、Trace、Proposal 或 Receipt。浏览器 OAuth Consent
和 Refresh Token Storage 刻意不放进这个仓库。个人测试部署应只申请所选功能需要
的最小 Scope：Gmail Read 加 Draft 管理，以及 Calendar Event 访问。Google 将广泛
的 Gmail Read/Compose Scope 分类为 Restricted，因此首版不会包装成已经通过公共
OAuth 审核的产品。

REST Contract 以 Google 官方的
[Gmail Message list/get](https://developers.google.com/workspace/gmail/api/guides/list-messages)、
[Draft 创建](https://developers.google.com/workspace/gmail/api/guides/drafts)、
[Calendar Event 插入](https://developers.google.com/workspace/calendar/api/v3/reference/events/insert)
和 [Private Extended Property](https://developers.google.com/workspace/calendar/api/guides/extended-properties)
文档为准。

## 验证状态

`tests/test_google_workspace_connector.py` 使用确定性 HTTP Contract Double，让
Connector 真正经过 Voren `ActionGateway` 与 SQLite Operation Ledger。测试覆盖
Read Provenance、稳定 Calendar ID、Draft-only 行为、精确 Receipt、明确的认证失败、
HTTP 响应丢失，以及进程重启后不重发的 Reconciliation。

CI 不具备 Google Credential，因此这些测试证明的是 Translation 和故障语义，不是
真实 Google 账号已经成功运行。新增的 `export-google-smoke` 命令只接受覆盖完整的
Live-boundary Suite，将其绑定到同一个干净源码 Revision 与精确模型 Endpoint，并
生成经过完整性校验的脱敏 Artifact。带日期的真实 Artifact 仍是外部 Release
Attestation，而且绝不能包含邮箱正文或 Token。操作步骤见
[真实账号 Smoke 手册](../GOOGLE_LIVE_SMOKE.zh-CN.md)。

## CLI 纵向切片

安装后的 `voren google` 与可选的 Web Live Workspace 都和 AgentDojo 路径复用
相同的有界 Agent Loop、加密
Transcript Checkpoint、SQLite Run Event、Operation Ledger、精确 Approval Prompt、
Action Gateway 和 Verified Receipt；只替换 Read/Action Adapter 及其冻结的 Contract
Version。

配置模型 Credential、`VOREN_TRANSCRIPT_KEY`、`GOOGLE_WORKSPACE_ACCESS_TOKEN`、
`VOREN_GOOGLE_ACCOUNT_EMAIL`，以及 `.env.example` 中可选的 Calendar/Time-zone
变量后执行：

```bash
voren google --model 'your-model-id' \
  '阅读项目更新邮件并准备回复草稿，不要发送。'
```

Access Token 只能从环境读取；CLI 刻意不提供可能把 Token 暴露在进程列表或 Shell
History 中的参数，也没有自动批准开关。

本地 Web 需要在启动 `voren-web` 前设置 `VOREN_WEB_MODE=live` 与
`VOREN_WEB_WORKSPACE=google`。Health 与页面会分别披露 Model/Workspace 是否就绪。
Google Approval 使用稳定远端 Operation Identity，Web 进程重启后可以重建 Adapter；
一次性的 AgentDojo World 则不能恢复。
