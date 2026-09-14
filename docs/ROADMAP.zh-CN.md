# 路线图与源码阅读计划

[English](ROADMAP.md)

## 1. 交付策略

Voren 按照一系列可执行纵向切片构建。只有通过可观察的验收条件，某一阶段才
算完成；只创建 Interface 或数据库表不代表阶段完成。

## 2. Phase 0：环境与设计 Spike

状态：环境与工具契约范围已于 **2026-08-30 完成**。详见
[AgentDojo Workspace Spike](research/AGENTDOJO_SPIKE.zh-CN.md)。

目标：

- 原样运行一个固定版本的 AgentDojo workspace Task；
- 检查邮件/日历状态和 Grading Model；
- 确认依赖、License 和 Model Adapter 约束；
- 记录第一批架构决策；
- 把黄金工作流转化为具体 Test Fixture。

退出条件：

- 可以复现一次 AgentDojo Baseline Run；
- 明确记录启用的工具和 State Verifier；
- 全部流程不需要生产环境 Credential。

已记录结果：

- AgentDojo Distribution `0.1.35`，Commit
  `a75aba7631d3ca5fb7ab938965c97ead2f9ff84b`；
- Benchmark `v1.2.2`，Suite `workspace`；
- 黄金用例 `user_task_18`，并以 `user_task_17`、`user_task_20` 作为对照；
- 安全 Smoke Case：`injection_task_2`、`injection_task_3`、
  `injection_task_4`；
- 使用跨资源 Effect Manifest 取代单一的 Read/Write 标签；
- 对上游 Aggregate Self-check 的失败按已知原因拆分记录，不将它描述成全绿
  Baseline。

## 3. Phase 1：安全行动纵向切片

实现状态：**受控验收边界已于 2026-09-14 完成**。Action Core、持久 Approval
Lifecycle，以及使用 Scripted
Model 的 Provenance-aware Loop 已完成，详见
[Phase 1 安全动作核心](implementation/PHASE1_ACTION_CORE.zh-CN.md)、
[Phase 1 Run Lifecycle](implementation/PHASE1_RUN_LIFECYCLE.zh-CN.md) 和
[Phase 1 Agent Loop](implementation/PHASE1_AGENT_LOOP.zh-CN.md)。API-capable
Provider 与 CLI 详见
[Phase 1 Model Adapter 与 CLI](implementation/PHASE1_MODEL_ADAPTER_CLI.zh-CN.md)。

只实现黄金工作流需要的部分：

- CLI Request 与 Run Lifecycle；
- 一个 Model Adapter；
- 有界 Tool-calling Loop；
- AgentDojo Email/Calendar Adapter；
- Append-only Trace；
- Local Action Proposal；
- 精确副作用审批的暂停/恢复；
- Operation Ledger；
- Postcondition Verification。

Action Core 子切片已完成：

- 类型化跨资源 Effect Manifest 与 Proposal；
- 绑定精确 Digest 且检查过期时间的 Approval；
- SQLite Operation Ledger 与持久 Receipt；
- 基于状态的 Exact-effect Verification；
- 重复 Commit 抑制与 Ambiguous Commit 处理；
- 确定性 Fake Workspace 故障注入与测试；
- 固定版本 AgentDojo Workspace Adapter；
- 官方 `user_task_18` Utility Grader 集成；
- 持久 Run Pause/Resume 与 Receipt Recovery；
- 只追加写、有序且可去重的 Lifecycle Event；
- Provider-neutral Model Message、Tool Call 与 Definition；
- Model Step、总调用数、重复调用和 Observation Size 的硬限制；
- 带 Provenance Label 的 AgentDojo Email、Calendar 与 Contact Read；
- 通过 Snapshot 强制 Read Purity，并排除有状态的 Unread Email Read；
- 每个外部动作都必须经过 Proposal-and-pause 边界；
- 无 Credential 的 AgentDojo `user_task_18` Loop Demo；
- 带 Provider Output Replay 的 HTTPS Responses API Adapter；
- 不把 Secret 写入 Trace 的 API Key 与 Endpoint Handling；
- 安装后的 `voren agentdojo` Command；
- 只能在终端进行的 Exact-effect Approval，且没有 Auto Approval Option；
- 冻结 Case/Mode/Provider Config 的 AgentDojo Evaluation Manifest；
- 分离 Agent Behavior 与 Runtime Enforcement 的双模式 Runner；
- 带完整性 Digest、分模式指标和规范化 Trace 的 JSON Artifact；
- 需要显式选择 Case 与 Mode 的 `voren eval-agentdojo` Command；
- Responses Input/Output/Cached/Cache-write/Reasoning Token Usage 解析；
- 带完整性标记的 Run-level Usage 累计，并写入 Event、CLI 与 Evaluation Artifact；
- 带明确 Deadline 的 Background Responses 轮询；
- 将 Operator/Deadline Cancellation 传播到 Provider Cancel Endpoint；
- 在 Run 和 Artifact 中记录取消原因及 Provider Confirmation。
- 使用 AES-256-GCM 保存绑定冻结配置的 Transcript Checkpoint，并在安全的模型
  请求边界跨进程恢复上下文与预算。

当前 Runner 的 [带日期 Live Evidence](evidence/2026-09-14-AGENTDOJO-LIVE.zh-CN.md)
现已保存 Golden Run、Prompt-injection Trial 和一次负向 Static-Skill 对照。Action
Envelope 与确定性双模式覆盖现已
包含日历 `injection_task_2` 和邮件 `injection_task_3`/`injection_task_4`，详见
[Phase 1 可验证 Email Action](implementation/PHASE1_EMAIL_ACTION.zh-CN.md)。
Contract Test 只能证明 Translation、Measurement 与 Orchestration，不能证明 Live
Model 的规划能力或抗注入能力。

退出条件：

- 黄金工作流端到端完成；
- Tool Result 不能单独触发无关修改；
- 每个外部写操作都有对应 Approval 和 Receipt；
- Commit 后超时不会重复动作。

## 4. Phase 2：Memory 与静态 Skill

实现状态：**声明的非 Embedding 范围已于 2026-09-14 完成**。兼容 Agent Skills
的解析、Progressive-loading API、内容寻址
不可变版本、原子 Active Pointer、精确 RunConfig Skill Reference，以及第一个
人工编写的 Scheduling Skill 已实现。详见
[Phase 2 静态 Skill Store](implementation/PHASE2_STATIC_SKILL_STORE.zh-CN.md)。显式的
`no_skill`/`static_skill` Runtime Context 现在会冻结并加载精确版本、限制 Context
大小、校验 Tool Compatibility，并产生只含 Metadata 的审计事件。详见
[版本冻结的 Skill Context](implementation/PHASE2_SKILL_CONTEXT.zh-CN.md)。
保守的 Metadata-only Routing 现在会先过滤与 Workspace Tool 不兼容的 Skill，再
选择至多一个唯一最佳 Active Skill，并记录不含 Request 原文的精确 Decision
Evidence；详见
[确定性 Skill 路由](implementation/PHASE2_SKILL_ROUTING.zh-CN.md)。

类型化 Profile/Episode Store、只允许从持久 Evidence 分类、精确 RunConfig Memory
Snapshot、有大小限制的模型 Context、默认脱敏的 CLI Inspect，以及 AgentDojo
显式加载均已完成，详见
[类型化 Memory 与冻结 Context](implementation/PHASE2_TYPED_MEMORY.zh-CN.md)。Web
现在默认冻结由 Operator Evidence 激活的 Profile，同时保持 Episode 仅显式选择，并且
只展示精确 Reference，不展示正文。
Version-pinned `no_skill`/`static_skill` Evaluation Artifact 已实现。基于 Embedding
的 Semantic Routing 被明确排除在当前验收边界外，不会被包装成已经完成。

实现：

- Profile、Episode 和 Skill Store；
- 兼容 Agent Skills 的 Discovery；
- Progressive Skill Loading；
- Immutable Skill Version 和 Active Pointer；
- Context 与 Evidence 中的 Trust Provenance；
- 一个手工编写的 Scheduling Skill。

退出条件：

- Run 冻结精确 Memory 和 Skill 版本；
- Preference Data 不会与 Procedural Instruction 混淆；
- 不可信邮件内容不能直接更新长期 Store；
- `no_skill`、`static_skill` 和可选 `oracle_skill` Trial 可以复现。

## 5. Phase 3：Candidate 学习与晋升

实现状态：**有界 Candidate-learning 范围已于 2026-09-14 完成**。Evidence
Eligibility、只允许 `SKILL.md` 指令文本发生有界
变化的 Admission Policy，以及非激活 Candidate 的 SQLite 持久化已完成。详见
[Phase 3 Candidate 暂存](implementation/PHASE3_CANDIDATE_STAGING.zh-CN.md)。
绑定完整性校验的 Paired Held-out Runner 与确定性 Acceptance Policy 也已完成，
详见 [Phase 3 配对评测](implementation/PHASE3_PAIRED_EVALUATION.zh-CN.md)。原子
Compare-and-swap Promotion/Rollback 与带完整性链的 Lifecycle Audit 已完成，
详见 [Phase 3 晋升与回滚](implementation/PHASE3_PROMOTION_ROLLBACK.zh-CN.md)。
Paired Contract 也已经接到精确 Version-pinned AgentDojo Runtime 与持久底层
Artifact，详见
[Phase 3 AgentDojo Skill Evaluator](implementation/PHASE3_AGENTDOJO_SKILL_EVALUATOR.zh-CN.md)。
显式 Install/Stage/Decide/Inspect/Promote/Rollback CLI 也已完成，详见
[Phase 3 Skill CLI](implementation/PHASE3_SKILL_CLI.zh-CN.md)，并已加入显式付费
的 `skill eval-agentdojo` Artifact-generation Command。Durable-learning Router
与 Integrity-bound Evidence Store 也已完成，详见
[Phase 3 Durable Learning Router](implementation/PHASE3_DURABLE_LEARNING_ROUTER.zh-CN.md)。
确定性 Markdown Report 现已汇总精确 Evidence Metadata、Skill Diff、Paired
Base/Candidate Result、Decision 与 Lifecycle Audit，详见
[Phase 3 Candidate Report](implementation/PHASE3_CANDIDATE_REPORT.zh-CN.md)。
确定性的 Direct-reflection 与 Gated-learning Policy Ablation 已完成，详见
[Phase 3 Learning Ablation](implementation/PHASE3_LEARNING_ABLATION.zh-CN.md)。
它只作为机制结果报告，不冒充 Live-model Evidence。

实现：

- Evidence Eligibility Rule；
- Durable-learning Router；
- Bounded Skill Edit；
- Candidate Lifecycle；
- Paired Evaluation Runner；
- Promotion Policy 与 Decision Report；
- Rollback。

退出条件：

- 选定 Trace 只能创建非激活 Candidate；
- 已知有害 Candidate 被拒绝；
- 已接受 Candidate 绑定精确 Evidence 和 Evaluation Artifact；
- 可以比较 Direct-reflection 与 Gated-learning Baseline；
- Rollback 能够在同一组测试中恢复之前行为。

## 6. Phase 4：安全与作品集报告

实现状态：**确定性证据范围已于 2026-09-14 完成**。双模式 AgentDojo Runner、
带完整性绑定的 JSON Artifact、污染测试、Candidate 生成式报告、Learning-policy
Ablation、Web Trace 展示与带日期的 Live-model Artifact 覆盖了下列事项。小规模
Live 样本与确定性测试分开报告，不能扩展为通用安全结论。

实现：

- AgentDojo Workspace Injection Run；
- Memory/Skill Contamination Test；
- 分离 Agent Behavior 与 Runtime Enforcement 的评测模式；
- Security-versus-Utility Report；
- 定向 Ablation；
- Trace 与 Skill Diff 检查 UI 或生成式报告；
- 明确记录 Limitations 与 Threat Model Boundary。

退出条件：

- 分别报告 Benign Utility 与 Attack Success；
- 不使用 Always-deny Policy 隐藏原始 AgentDojo Attack Success；
- Provenance 和 Promotion Gate 得到测试，而不只是文档描述；
- 所有公开结果都链接到可复现 Artifact；
- 项目具备用于面试的架构说明和阅读指南。

## 7. Phase 5：一个真实 Connector

实现状态：**Connector 与确定性 HTTP Contract 已于 2026-09-14 完成；需 Credential
的 Smoke 尚未执行**。Gmail/Google Calendar Read、Gmail Draft 和私人 Calendar
Hold 复用受控 Workspace 的 Provenance、Approval、Operation Ledger、Receipt、
Verification、Reconciliation、CLI 与 Web 边界。当前不声称真实账号运行成功。

只有受控系统稳定之后，才添加一个供应商体系，最可能是 Gmail 与 Google
Calendar，并保持保守默认策略。

初始生产行为优先只读和 Draft。发送、删除或修改外部状态仍然要求明确授权。
AgentDojo 路径继续作为 Regression Environment。

多个供应商、后台收件箱轮询、渠道集成和主动自动化继续延后。

## 8. 源码阅读顺序

源码研究以问题为导向。每份笔记应记录机制、假设、采用内容、拒绝内容，以及
它影响的 Voren 测试。

### 1. AgentDojo Workspace 与 Pipeline

首先阅读，因为它定义 Voren 的初始世界和评测契约：

- Workspace Email/Calendar Tool Definition；
- 普通 User Task 与 State Grader；
- Injection Task 与 Security Grader；
- Pipeline 和 Tool Execution Loop；
- Benchmark Runner 与 Result Format。

采用：基于状态的 Utility/Security Evaluation 和不可信工具输出测试。

拒绝：把 Voren Runtime 直接耦合到不稳定的 Benchmark API。

### 2. Pi Agent Core

阅读最小 Agent Runtime、Provider Abstraction、State、Event 和 Compaction
相关模块。

采用：易读的 Bounded Loop 和类型化 State/Event。

拒绝：把 Pi 进程权限视为足够的 Action Security Model。

### 3. Codex Action Boundary

定向阅读 Approval、Tool Orchestration、Cancellation 和 App Server Event
Contract，不通读整个产品。

采用：显式 Pending Item、精确 Approval Context、执行前 Policy，以及可恢复的
事件驱动交互。

拒绝：复制大型 Coding Agent 架构，或者不经转换就把文件系统 Sandbox 模型
套到 API 副作用上。

### 4. Agent Skills Specification

采用：可移植 `SKILL.md` 结构和 Progressive Disclosure。

扩展：增加 Voren Sidecar，记录 Effect Scope、Evidence 和 Evaluation。Prose
永远不能授予 Runtime Permission。

### 5. Hermes Agent

阅读 Background Review、Skill Management、Provenance、Staging 与 Approval。

采用：实用 Skill Authoring Lifecycle 和人类可读 Diff。

拒绝：仅仅因为任务复杂或调用了很多工具就创建长期 Skill。

### 6. SkillOpt 与 SkillOpt-Sleep

阅读 Scored Rollout Ingestion、Bounded Edit、Validation Selection、Rejected
Edit Handling 和 Offline Consolidation。

采用：根据 held-out Evidence 优化 Candidate。

转换：把通用 Benchmark Score 转换成 Voren 的 External State、Side Effect 和
Security Gate。

### 7. OpenClaw

阅读 Provenance、Action Receipt、Skill Proposal、Exact Revision Binding 和
Trust Boundary 文档。

采用：确定性 Admission Check 和与证据关联的 Lifecycle Event。

拒绝：Gateway、Channel、Device Node、Scheduler 和 Plugin Ecosystem 范围。

### 可选参考

- Memory 组织成为可测量瓶颈时再研究 Letta；
- 第二个真实 Adapter 证明需要更强 Plugin Boundary 时再研究 DeepSeek Harness；
- 需要更广的日常应用结果评测时再研究 AppWorld；
- 只有出现具体缺失机制时才研究 OpenHands 或 Goose，不做泛读。

## 9. 第一批实现决策

以下默认选择用于保持第一个切片一致：

- 语言：Python 3.12；
- Interface：CLI；
- Model Support：一个位于本地 Protocol 后的 OpenAI-compatible Adapter；
- World：AgentDojo `0.1.35` / Benchmark `v1.2.2` Workspace Adapter；
- Persistence：SQLite 加 Content-addressed Artifact；
- Mutation Policy：每个外部写操作都审批；
- Skill Activation：首先只显式选择一个静态 Skill；
- Learning：只离线生成 Candidate；
- UI、真实 OAuth、向量检索和通用 Plugin：延后。

这些是可逆的默认选择，不代表最终系统必须永远保持小规模。

## 10. 待解决设计问题

AgentDojo 版本和任务子集已经在上文确定。剩余问题如下：

1. 哪些 Run/Event 字段需要加密存储，而不是只做脱敏？
2. 能够清晰展示多个相关副作用的最小 Approval UI 是什么？
3. 初始 Scheduling Preference 应属于 Profile Rule、Skill Input，还是两者同时
   存在并定义明确优先级？
4. Candidate 从 `needs_evidence` 进入 `active` 至少需要多少配对样本，以及怎样
   定义不确定性规则？
