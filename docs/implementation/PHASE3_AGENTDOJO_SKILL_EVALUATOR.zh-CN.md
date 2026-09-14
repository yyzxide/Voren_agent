# Phase 3 AgentDojo Skill Evaluator

[English](PHASE3_AGENTDOJO_SKILL_EVALUATOR.md)

## 补上的集成缺口

通用 Paired Runner 本身不能证明任一 Skill 真的进入了模型上下文。
`AgentDojoSkillEvaluator` 在受控 Workspace 环境中补上了这条链路。

对于每个 `(Held-out Case, 精确 SkillVersionRef)`，它会：

1. 通过完整性校验加载该不可变 Skill Version；
2. 不查询 Active Pointer，直接构造有大小限制的 `SkillContextSnapshot`；
3. 把精确 Rendered Context 纳入 System-prompt Digest；
4. 把精确版本冻结进 `RunConfig.skill_versions`；
5. 使用原始 `agent_behavior` Mode 运行 Case；
6. 原子写入完整的底层 `ExperimentArtifact`；
7. 返回引用该 Artifact Digest 的 Measurement。

这里必须使用 Raw Behavior Mode，因为 Runtime Enforcement 可能拦截危险外部
动作，从而掩盖 Candidate 自身的 Prompt-injection 行为。Phase 1 的独立评测继续
负责报告 Enforcement Effectiveness。

## 可复现性与故障语义

每个底层 Config 还绑定 AgentDojo Manifest、Prompt、Tool Schema、Attack
Template、Provider/Model Label、Sampling Config、Source Revision 与 Code-dirty
Flag。Artifact Filename 包含自身 Digest，因此重新运行 Trial 不会静默覆盖已被
Candidate Decision 引用的证据。

Provider/Model-adapter Failure、Read-adapter Failure，以及 Provider/Deadline
Cancellation，都会先写入底层 Artifact，再被转换为显式 Infrastructure Failure。
因此 Paired Policy 不会把它们统计成 Utility 或 Security Regression。

## 验证

集成测试使用真实固定版本的 AgentDojo 环境与 Scripted Model。它验证 Base 与
Candidate Request 分别包含不同的精确指令，每个 Run 记录一个冻结 Skill
Version，两个底层 Artifact 均可读取且完整性有效，Paired Artifact 引用它们的
精确 Digest。该测试不声称 Live Model 得到了提升。

`voren skill eval-agentdojo` 在不合并 Decision 的前提下公开这条路径。它会在任何
模型调用之前拒绝重复 Selection 与不完整的 Security/Utility Coverage，要求使用
Skill Contract 声明的 Suite，写入全部 Artifact，打印每个 Pair 的两侧结果，并
让 Candidate 保持 Staged。
