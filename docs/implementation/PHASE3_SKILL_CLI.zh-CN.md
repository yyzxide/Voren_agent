# Phase 3 Skill Lifecycle CLI

[English](PHASE3_SKILL_CLI.md)

## 公开操作面

`voren skill` 在不削弱状态边界的前提下公开 Lifecycle：

- `install` 解析 Package 并按内容寻址；可选 Activation 只用于人工管理的
  Baseline，并且必须提供 Reason；
- `evidence-correction` 保存显式人工编写的文本，`evidence-run` 只准入经过语义
  验证的 Completed Run；
- `stage` 解析指定名称的 Active Base 与已经持久化的 Evidence ID，执行 Bounded
  Admission，并保持 Candidate 非激活；
- `eval-agentdojo` 要求显式选择付费 Case，对两个精确版本运行 Raw Behavior，
  并写入 Paired Artifact 与每个底层 Artifact；
- `decide` 对 Paired Evaluation Artifact 做完整性加载，并原子记录 Accepted 或
  Rejected Decision；
- `inspect` 把 Candidate、完整 Evaluation Artifact 与已验证 Lifecycle Chain 输出
  为机器可读 JSON；Evidence Payload 默认脱敏，只有显式传入
  `--include-evidence-payload` 才输出；
- `promote` 执行 Exact-base Compare-and-swap；
- `rollback` 执行 Exact-candidate Compare-and-swap。

每个命令都可以显式指定 Database 与 Immutable Object Root，默认使用 `.voren/`。
无论成功失败，命令都会关闭两个 SQLite Connection。Validation 与 Store Error
会被输出为正常 CLI Usage Error，而不是原始 Traceback。

## 安全属性

不存在 `--auto-promote`。`decide` 成功后只打印 Decision 并退出，Base 仍保持
Active。Promotion 与 Rollback 都要求单独运行命令并提供非空 Reason。
Operator-correction Evidence 的 Instruction Authority 由类型化 Durable
Artifact 确定；其他 Evidence Source 不能声明该权限。`stage` 不接受调用方手填
Source、Authority Bit 或 Digest。

Rejected Decision 返回 Exit Code 3，使自动化能够区分“有效的负面 Policy 决策”
与输入错误或基础设施故障。

## 当前边界

CLI 现在可以生成并消费 Candidate Evaluation Artifact。它不会自行编写 Candidate
Edit，也不会自动把任意 Trace 路由为长期学习。只有显式 Correction Router 与
Verified-run Router 能为 Bounded Staging Command 创建授权 Evidence。
