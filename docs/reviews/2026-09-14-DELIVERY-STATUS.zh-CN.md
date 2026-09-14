# Voren 交付状态 — 2026-09-14

[English](2026-09-14-DELIVERY-STATUS.md)

这是历史[作品集审计](2026-09-14-PORTFOLIO-AUDIT.zh-CN.md)中各项发现的当前闭环
记录。它把确定性实现证据与必须调用 Live Model 或真实 Google 账号才能得到的结果
分开，避免把 Contract Test 包装成线上能力。

## 已验证基线

- 实现版本：`1839c3e`；
- 干净锁定安装：CPython 3.12、Linux x86-64、`pylock.toml`；
- 本地结果：185/185 项单元与集成测试通过；
- 远端结果：[GitHub Actions run 34815893634](https://github.com/yyzxide/Voren_agent/actions/runs/34815893634) 通过；
- 测试集不需要 Credential，不调用 Live Model 或真实 Google 账号。

测试覆盖真实子进程/TCP/HTTP 浏览器服务边界、官方 MCP 协议往返、AgentDojo
集成、两个 SQLite Connection 竞争、模拟崩溃与重启、确定性外部 HTTP Contract，
以及完整的有界 Skill Lifecycle。

## 审计闭环

| 发现 | 当前状态 | 证据 |
| --- | --- | --- |
| V1：动作 Claim 非原子 | 已关闭 | `8f5d4df` 使用带状态条件的原子转换；两个 SQLite Connection 的回归证明只有一个派发者获胜。 |
| V2：外部提交后、Receipt 前崩溃 | 在可观察 Provider 语义内关闭 | `9da099b` 在重启后只通过观察做 Reconciliation，无法确认时绝不重发。若 Provider 既无幂等键也无查询能力，则明确不承诺普适 Exactly-once。 |
| V3：隐式假设 Provider 能力 | 已关闭 | `5e10bea` 增加显式 Responses Profile。DeepSeek Profile 使用前台请求、删除不支持的 Background 字段，并区分本地取消与 Provider 确认停止。 |
| V4：Skill 学习只是计划 | 有界机制范围已关闭 | `f9d1e6a` 至 `5bb1fd6` 实现 Evidence Admission、非激活有界 Candidate、配对 Held-out Evaluation、确定性 Decision、显式 Promotion/Rollback、报告与策略消融；`f899b6f` 把精确 Active Version 路由回 Runtime。 |
| C1：复现与交付不足 | 无 Credential 交付范围已关闭 | `56a798f` 增加完整平台锁与 CI，`c6a3f1a` 增加跨进程 Web 验证；绑定来源的 Knowledge/MCP、Profile Memory、Web Skill 路由和 Google Adapter 已在 `1839c3e` 前接通。 |

## 可以演示的路径

1. 在 AgentDojo 中执行带 Provenance 的邮件/日程工作流，对精确 Effect 暂停并只
   批准一次，最后验证外部状态。
2. 导入绑定来源的 Knowledge，并通过官方 MCP SDK 暴露同一个 Read Contract。
3. 将 Operator 编写的 Profile Version 与保守路由得到的 Skill 冻结到 RunConfig，
   同时不把检索文本当作指令。
4. 暂存绑定 Evidence 的非激活 Skill Candidate，拒绝安全退化版本，显式晋升通过
   版本，再精确回滚。
5. 通过本地 Web/SSE 驱动同一条工作流，并在页面刷新或进程重启后恢复可见状态。
6. 显式选择 Google Adapter，让 Draft 与私人 Calendar Hold 继续经过相同的
   Approval、Receipt、Verification 和 Reconciliation 边界。

## 仍需外部系统提供的证据

- 一次带日期的 Live-model 黄金 Run 与 Prompt-injection 评测 Artifact；
- 一次带日期的 Live-model `no_skill` 与 Skill 对比；
- 一次使用专用 Google 测试账号、经过脱敏的 Smoke Artifact。

这些是执行/证据门槛，不是自动化测试中隐藏的结论。在取得它们之前，仓库不声称
Live Model 规划质量、通用抗 Prompt Injection 能力或真实 Google 账号连接成功。

## 明确不声称

- 对任意 Provider 都成立的普适 Exactly-once；
- Agent 能够自主、持续修改自身；
- 学到的 Skill 对任意模型或任务分布都必然提升；
- 生产级多用户服务、后台收件箱自动化或通用个人助理覆盖；
- 工业级安全认证。
