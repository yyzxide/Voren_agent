# Phase 1 加密 Transcript 恢复

[English](PHASE1_TRANSCRIPT_RECOVERY.md)

## 范围

这个切片让执行中的模型/工具循环可以恢复，同时避免把原始邮件、Prompt 或模型
上下文写进只追加的审计事件流。系统会在安全的模型请求边界保存恢复所需的完整
状态：

- 系统消息与操作者消息；
- Assistant Tool Call 和带来源标签的工具观察；
- 下一次模型 Step 以及全部工具硬限制计数；
- 已使用的 Call ID、重复调用 Signature 和 Evidence Digest；
- 累计的 Provider Token Usage 及其完整性状态。

## 存储与密钥边界

`SQLiteTranscriptStore` 使用 AES-256-GCM 加密每一版检查点，每次保存都会生成
新的 96-bit Nonce。Schema 版本、Run ID 和冻结的 Run Config Digest 会作为
Associated Data 一起认证，所以密文不能在不被发现的情况下移动到另一个 Run
或配置。通过认证解密后，系统还会把规范化 Payload Digest 与已存元数据比较。

SQLite 只保存密钥指纹、Nonce、密文、完整性元数据和 Revision。密钥来自
`VOREN_TRANSCRIPT_KEY`，不会进入数据库或事件流。错误密钥、被修改的密文、
不匹配的配置或无效 Schema 都会阻止恢复。

本地只需生成一次密钥，并把它保存在 Git 之外：

```bash
export VOREN_TRANSCRIPT_KEY="$(python3 -c 'from voren.runtime.transcripts import SQLiteTranscriptStore; print(SQLiteTranscriptStore.generate_key())')"
```

密钥丢失后，已有检查点将按设计无法恢复。Phase 1 尚未实现密钥轮换。

## 恢复语义

Loop 会在第一次模型请求前保存初始检查点，并在每批 Allowlist Pure Read 完成后
保存新版检查点。`resume(run_id)` 只接受持久状态仍为 `running` 的 Run，并在
发起下一次模型请求前检查检查点与冻结配置是否一致。

这形成了明确的 Replay Boundary：

- 最近检查点之前已完成的读取不会再次执行；
- 模型 Step、工具预算和 Usage 不会在重启后归零；
- 如果中断发生在当前模型请求或 Pure-read Batch 内，上一个安全检查点之后的
  工作可能被重新执行；
- 外部动作永远不会在这个可重放区域直接提交，它仍会先变成持久 Proposal，
  然后进入强制审批暂停；
- 完成、失败、达到限制或取消的 Loop 会删除检查点；等待审批的上下文保留到
  决策路径结束。

只追加事件流仍然只记录 Digest 和边界元数据。敏感模型上下文只存在于加密
检查点中。

## 验证

自动化恢复测试会在第二次模型请求时模拟进程中断，关闭所有 SQLite Connection，
重建 Runtime，并验证：

- 先前的工具观察进入了恢复后的模型上下文；
- 已完成的 Read 没有被重放；
- 模型 Step、工具调用和 Token Usage 预算正确延续；
- SQLite 密文中不存在敏感明文；
- 错误密钥和只修改一个 Bit 的密文都会被拒绝；
- Run 终止后检查点会被删除。

这个切片尚未持久化真实 Connector 的完整远端 Snapshot，也没有实现长上下文
压缩、密钥轮换或 Web API 恢复入口。
