# Phase 3 Skill Lifecycle CLI

[English](PHASE3_SKILL_CLI.md)

## 公开操作面

`voren skill` 在不削弱状态边界的前提下公开 Lifecycle：

- `install` 解析 Package 并按内容寻址；可选 Activation 只用于人工管理的
  Baseline，并且必须提供 Reason；
- `stage` 解析指定名称的 Active Base，校验一条显式 Evidence Reference，执行
  Bounded Admission，并保持 Candidate 非激活；
- `eval-agentdojo` 要求显式选择付费 Case，对两个精确版本运行 Raw Behavior，
  并写入 Paired Artifact 与每个底层 Artifact；
- `decide` 对 Paired Evaluation Artifact 做完整性加载，并原子记录 Accepted 或
  Rejected Decision；
- `inspect` 把 Candidate、完整 Evaluation Artifact 与已验证 Lifecycle Chain 输出
  为机器可读 JSON；
- `promote` 执行 Exact-base Compare-and-swap；
- `rollback` 执行 Exact-candidate Compare-and-swap。

每个命令都可以显式指定 Database 与 Immutable Object Root，默认使用 `.voren/`。
无论成功失败，命令都会关闭两个 SQLite Connection。Validation 与 Store Error
会被输出为正常 CLI Usage Error，而不是原始 Traceback。

## 安全属性

不存在 `--auto-promote`。`decide` 成功后只打印 Decision 并退出，Base 仍保持
Active。Promotion 与 Rollback 都要求单独运行命令并提供非空 Reason。
Operator-correction Evidence 必须显式携带 Instruction-authority Flag；其他
Evidence Source 不能声明该权限。

Rejected Decision 返回 Exit Code 3，使自动化能够区分“有效的负面 Policy 决策”
与输入错误或基础设施故障。

## 当前边界

CLI 现在可以生成并消费 Candidate Evaluation Artifact。它不会自行编写 Candidate
Edit，也不会自动把任意 Trace 路由为长期学习；这些仍是 Bounded Staging Command
的显式输入。
