# Google Workspace 真实账号 Smoke 操作手册

[English](GOOGLE_LIVE_SMOKE.md)

这份手册用于关闭可选的部署证明边界，同时避免把私人邮箱数据变成仓库证据。它不能
替代确定性的 Connector Test，也不能替代 AgentDojo 安全评测。

## 通过条件

一份 Artifact 中的 Run 必须来自同一个干净 Git Revision，并使用同一个精确的
Model/Provider Endpoint。所有 Run 合起来必须证明两条路径：

1. `search_emails` 成功 → 基于该观察提出 `create_email_draft` → 操作者显式批准 →
   已提交且 Receipt 验证通过；
2. `get_day_calendar_events` 成功 → 基于该观察提出
   `create_private_calendar_event` → 操作者显式批准 → 已提交且 Receipt 验证通过。

Exporter 会拒绝注入的测试 Connector/Model、未完成或被拒绝的 Run、没有 Read
Evidence 的 Action、覆盖不完整、混用或 Dirty Revision、混用模型边界及未验证的
Receipt。

## 准备专用测试账号

使用测试账号，不要使用私人邮箱。预先放入一封标题唯一的测试邮件和一条无害的日程，
再签发一个短期 OAuth Access Token，只授予当前 Connector 需要的 Gmail Read/Draft
与 Calendar Event Scope。从干净仓库运行；Credential 只能放在环境变量中，不能写入
命令参数或提交到 Git。

```bash
export VOREN_TRANSCRIPT_KEY='url-safe-base64-encoded-32-byte-key'
export GOOGLE_WORKSPACE_ACCESS_TOKEN='short-lived-oauth-token'
export VOREN_GOOGLE_ACCOUNT_EMAIL='dedicated-test-account@example.com'
export VOREN_GOOGLE_TIME_ZONE='Asia/Shanghai'
```

同一个 Shell 中还要配置所选 Responses Provider。例如使用 DeepSeek 兼容端点时，
还需配置 `DEEPSEEK_API_KEY`，并显式选择 `deepseek` Provider Profile。

## 执行两条路径

使用唯一 Subject/Title，保证可以观察精确结果。仔细阅读 CLI 展示的 Proposal，只有
Effect 确实是预期的测试动作时才批准。模型执行后，命令会打印一行 `run id:`。

```bash
voren google --provider-profile deepseek --model deepseek-v4-flash \
  '搜索标题唯一的 Voren Smoke 测试邮件，然后给测试发件人创建回复草稿；不要发送。'

voren google --provider-profile deepseek --model deepseek-v4-flash \
  '读取专用测试日历在 2026-09-15 的安排，然后在当天创建一个 15 分钟的私人 Voren Smoke 占位；不要添加参与者，也不要发送邀请。'
```

记录两次打印出的 Run ID。最终状态不是 `completed`、Proposal 被拒绝，或者 Receipt
不是 `verified`，都不算通过。

## 导出并检查

```bash
voren export-google-smoke \
  --database .voren/voren.sqlite3 \
  --run-id '<gmail-run-id>' \
  --run-id '<calendar-run-id>' \
  --output '.voren/artifacts/google-live-smoke.json'
```

带摘要签名的 JSON 只包含 Run/Config Digest、时间、源码 Revision、
Provider/Model/Endpoint 来源、Token 计数、成功工具名、脱敏 Effect 元数据、
Approval/Receipt 结果与 Event-stream Digest。它不会写入 Prompt、邮件或日程内容、
账号身份、收件人、OAuth Token 或 Google 对象 ID。复制到 `docs/evidence/` 前仍要人工
检查 JSON，确认干净后才能提交。

只有专用账号真正运行并产生带日期 Artifact 后，仓库才会声称这项外部 Smoke 通过。
