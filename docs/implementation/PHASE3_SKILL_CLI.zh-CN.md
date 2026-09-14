# Phase 3 Skill Lifecycle CLI

[English](PHASE3_SKILL_CLI.md)

## 公开操作面

`voren skill` 在不削弱状态边界的前提下公开 Lifecycle：

- `install` 解析 Package 并按内容寻址；可选 Activation 只用于人工管理的
  Baseline，并且必须提供 Reason；
- `stage` 解析指定名称的 Active Base，校验一条显式 Evidence Reference，执行
  Bounded Admission，并保持 Candidate 非激活；
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

本切片消费已经存在的 Candidate Evaluation Artifact。下一 CLI 切片会调用精确
版本的 AgentDojo Evaluator 来生成 Artifact，并要求显式选择会产生模型费用的
Case。
