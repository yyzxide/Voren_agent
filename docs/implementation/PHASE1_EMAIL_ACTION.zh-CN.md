# Phase 1 可验证 Email Action

[English](PHASE1_EMAIL_ACTION.md)

## 契约

Voren 的 AgentDojo Action Envelope 在 `create_calendar_event` 之外，现已加入独立
`send_email` 契约。由于对外可见的能力集合发生变化，Workspace Bundle 升级为
`agentdojo-workspace-v1.2.2/voren-contract-v2`。

Input Schema 要求 Recipients、Subject 与 Body 非空；会规范化并去重 Recipients、
CC、BCC，拒绝三个集合相互重叠；当前不允许 Attachment。Proposal 会物化一条
不可逆、Confidential 的 `inbox.emails:send` Effect，其中包含全部精确规范化字段。

## 执行与恢复

Deterministic Fake Workspace 与固定版本 AgentDojo Adapter 实现相同协议：

```text
prepare -> 精确审批 -> 原子 claim -> provider call -> observe -> verify
```

Adapter 会在 Provider Call 前记录预测的 AgentDojo Email ID 与 Pre-state。调用
报错后若已观察到邮件，该结果属于 Ambiguous，绝不是安全重试。Reconciliation
只观察稳定 ID，绝不重发。公共 Operation Ledger 为日历和邮件动作提供跨进程
Claim、持久 Receipt 与重复抑制。

## 评测覆盖

冻结 Smoke Manifest 升级为 `phase1-agentdojo-smoke-v2`，并包含：

- 日历 Injection Task 2；
- 邮件转发/数据外泄 Task 3；
- 邮件安全码外泄 Task 4。

在 Raw Agent-behavior Mode 中，精确的 Scripted 恶意邮件动作会被放行，官方
AgentDojo Security Grader 能观察到攻击；在 Runtime-enforcement Mode 中，同一
Proposal 不属于 Benign User 的精确 Ground Truth，因此会在 Commit 前被拒绝。
这些确定性测试证明的是测量与 Enforcement 语义，不是 Live-model 抗注入能力。

## 已测试不变量

- 精确 AgentDojo 邮件状态能通过官方 Task-4 Security Grader；
- Observed Effect 与已审批邮件字段完全相同；
- 重复 Commit 直接返回持久 Receipt，不会二次发送；
- Recipient Class 重叠会在 Proposal 产生前被拒绝；
- Task 3/4 的 Behavior 与 Enforcement Attack Metric 始终分开。
