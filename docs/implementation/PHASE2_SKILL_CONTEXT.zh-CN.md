# Phase 2 版本冻结的 Skill Context

这个切片把不可变 Skill Store 接入模型边界，同时不把 Skill Prose 变成 Runtime
Authority。

## Context 条件

`SkillContextAssembler` 会在 Run 开始前生成一个不可变的
`SkillContextSnapshot`：

- `no_skill` 是显式的空白 Baseline；
- `static_skill` 必须显式指定 Active Skill Name，先冻结精确
  `SkillVersionRef`，校验内容寻址 Package，再渲染 Instruction。

`from_frozen()` 根据精确版本引用重建历史 Snapshot，不查询当前 Active Pointer。
因此后续替换 Active Skill，不会改变已经存在或恢复中的 Run。

渲染后的 Context 会明确说明：Skill 文本只是已配置、版本冻结的程序性指导，不能
覆盖操作者请求、Provenance 规则、Runtime Policy 或 Approval Boundary。该文本
拥有确定性 Digest，并受默认 64 KB 大小限制。

## Runtime 不变量

创建 Run 之前，`AgentLoop` 要求 Snapshot 的精确版本引用与
`RunConfig.skill_versions` 完全一致。构造 Loop 时还会检查 Skill Contract 申请的
Tool Scope 必须是 Runtime 实际注册工具的子集。因此 Skill 可以描述怎样使用某项
能力，但不能凭文本创造能力。

每个新 Run 的追加写 Trace 会记录 `skill_context.assembled`，其中包括：

- Context 条件和精确版本引用；
- Context Digest 与字节数；
- 声明的 Tool/Effect Scope；
- `configured_static` Source/Trust Label。

事件中不会写入 Instruction 原文。完整 Model Context 仍只存在于加密 Transcript
Checkpoint 中。`no_skill` 条件会逐字节保留原 Phase 1 System Instruction。

## 这层边界捕获的集成问题

仓库内第一个 Skill 原来申请的是 `search_calendar_events`，但 Runtime 实际注册的
工具名是 `get_day_calendar_events`。只测试 Store 时无法发现这个问题；新增的
Capability Check 在模型调用前拒绝了该 Skill，随后 Sidecar 已改为真实工具名。

## 当前边界

这个切片已经让 `no_skill` 和 `static_skill` 成为确定、可测试的 Runtime Input。
评测 Artifact 还不能在单次配对实验中同时执行两种 Skill 条件；Semantic Routing、
Profile/Episode Context、Candidate 生成与 Promotion Gate 也仍属于后续切片。
