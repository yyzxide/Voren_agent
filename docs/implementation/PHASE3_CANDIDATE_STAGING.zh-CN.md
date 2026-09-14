# Phase 3 Candidate 暂存

[English](PHASE3_CANDIDATE_STAGING.md)

## 目标

本切片创建一个非激活、可审查的 Skill Candidate。它不允许模型、Tool Result
或外部文档直接修改当前生效的 Skill。

## 准入边界

Candidate 必须指向精确的当前 Active Base Version，并至少引用一条人工纠正或
已验证 Run。模型反思与外部观察可以作为辅助证据，但不能独立授权创建
Candidate。Evidence Reference 是不可变 ID 与 SHA-256 Digest。后续
Durable-router 切片现已持久化并验证这些精确 Evidence Object；公开 CLI 只有在
验证之后才允许暂存。

第一版编辑策略有意限制得很窄：

- Package Name、Routing Metadata 与 `skill.yaml` Contract 必须完全不变；
- 不允许增加或删除 Package File；
- 只能修改 UTF-8 `SKILL.md` 的指令文本；
- Changed-line Count 与序列化 Diff Size 都有硬上限。

Contract 对比阻止学习出来的 Prose 增加 Tool、Effect 或 Evaluation Suite。
Runtime Permission 仍只来自类型化代码与冻结 Contract，永远不来自自然语言。

## 持久化与激活

Candidate Package 被安装为 Content-addressed Immutable Version。Candidate
Record 绑定 Base/Candidate Digest、Evidence Reference 与人类可读的 Unified
Diff。同一个 Candidate ID 与完全相同的记录可幂等暂存；用同一 ID 提交不同
内容会 Fail Closed。

暂存不会修改 `active_skills`。准入时会检查 Active Pointer，拒绝已经过期的
Base。若暂存期间发生并发 Pointer 变化，也不会造成危险，因为 Promotion 是独立
操作；Promotion 必须执行最终的 Compare-and-swap 校验。

## 已验证行为

确定性测试覆盖可信/不可信 Evidence、Contract Scope 不可变、编辑大小限制、
Stale Base、非激活暂存，以及跨进程重新加载。

## 本切片明确未实现

本切片不声称 Candidate 的效果更好。Paired Held-out Evaluation、Acceptance
Decision、Active-pointer Promotion 与 Rollback 均尚未实现，它们是接下来的
Phase 3 切片。
