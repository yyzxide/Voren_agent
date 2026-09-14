# Phase 2：确定性 Skill 路由

[English](PHASE2_SKILL_ROUTING.md)

## 边界

Runtime Skill Selection 现在会把 Operator Request 接到已经激活、已经审查的 Skill。
它不是额外一次 Model Call，也不会检查邮件、日历、Knowledge 或 Tool Observation
中的内容。

Active Skill 通过 `SKILL.md` 中逗号分隔的 `metadata.routing-keywords` 明确选择加入
自动路由。Router 会规范化 Operator Request，只匹配这些已配置短语，并按照匹配
短语总长度排序。Auto Mode 只在存在唯一最佳且契约兼容的结果时加载一个 Skill；
并列时 Fail Closed 到 `no_skill`。

## 加载指令前先检查能力契约

Router 只读取 Active Version 的 Metadata 与 `skill.yaml` Contract，不读取指令正文。
凡是 `tool_scope` 不是当前 Workspace 真实 Tool Name 子集的 Candidate 都会先被过滤。
只有选出精确 Version Reference 后，`SkillContextAssembler` 才校验完整性并加载指令。

因此 Routing 不能授予 Tool。例如仓库内的 AgentDojo Scheduling Skill 不会被静默
复用到能力更窄的 Google Workspace，因为两者的 Action Name 与 Effect Contract
并不相同。

## 可复现性与人工控制

`voren agentdojo` 和 `voren google` 默认使用保守的自动路由。`--skill NAME` 会显式
冻结经过审查的 Active Version，`--no-skill` 会关闭本次请求的路由，作为 Baseline；
两者互斥。

每次 Decision 只包含不带 Request 原文的 Evidence：Request SHA-256 Digest、可用 Tool
Name、精确选中版本、命中的已配置 Keyword、不兼容 Candidate、歧义状态与规范化
Decision Digest。该记录持久化到 `RunConfig.metadata.skill_routing`；选中的版本还会
独立冻结到 `RunConfig.skill_versions`，并由现有仅含 Metadata 的
`skill_context.assembled` Event 记录。

这里实现的是确定性 Intent Routing，不声称完成了基于 Embedding 的 Semantic
Retrieval。若未来加入概率式路由，需要单独设计并评测 Policy，不属于当前作品集的
验收边界。
