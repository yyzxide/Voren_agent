# Phase 3 配对评测

[English](PHASE3_PAIRED_EVALUATION.md)

## 比较对象

Paired Runner 接收一个已经暂存的 Candidate 与显式 Held-out Manifest。对每个
Case，它依次把精确 Base Reference 和精确 Candidate Reference 传给 Evaluator。
每个成功 Measurement 必须包含 Run ID、Utility Result 与底层 Run Artifact
Digest；Attack Case 还必须包含 Attack-success Result。

最终不可变 Artifact 绑定：

- Candidate ID 与精确 Base/Candidate Content Digest；
- Candidate 的精确 Evidence ID；
- Contract 声明的 Evaluation-suite ID 与 Manifest Digest；
- 每个 Held-out Case 的类型、Run、指标与 Run-artifact Digest；
- 对完整类型化 Artifact 计算的 Canonical SHA-256 Digest。

## 决策策略

默认 Policy 同时要求 Benign 与 Attack 覆盖，具体 Suite 可以配置更高阈值。
比较行为之前，Policy 会校验 Artifact Digest、Candidate/Evidence/Version 的精确
绑定、`skill.yaml` 中的 Suite 声明，以及 Evidence Case 与 Held-out Case 不重叠。

任一 Case 出现 Utility Regression、新增 Attack Success，或者整体没有测得任何
改进，Candidate 都会被拒绝。只有在没有退化且至少一个 Held-out Case 的
Utility 或 Security 得到改进时才会接受。Decision 与精确 Evaluation Artifact
在同一个 SQLite Transaction 中持久化；相同 Decision 的重试是幂等的。接受和
拒绝都不会改变 Active Skill Pointer。

## 基础设施故障

Provider Timeout 等显式分类的 Infrastructure Failure 不携带 Utility/Security
指标。它们会阻断 Decision 并让 Candidate 保持 Staged，而不会被错误统计为模型
失败。Runner 不会吞掉未预期的编程异常。

## 本切片边界

Runner 定义了编排和 Artifact Contract；具体 Evaluator 仍需执行冻结 Runtime，
并为每次 Trial 生成底层 Run Artifact。当前确定性测试证明的是绑定、Policy、
持久化和故障语义，不代表 Live Model 质量。Promotion 与 Rollback 在下一切片
实现。
