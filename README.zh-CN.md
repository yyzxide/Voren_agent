# Voren Agent

[English](README.md)

> 当前状态：Phase 1 进行中。安全动作核心、持久 Approval Pause、带 Provenance
> Label 的 AgentDojo Read，以及 Provider-neutral 有界 Loop 已经能够通过
> Scripted Model 运行；目前尚未连接真实模型或生产账号。

Voren 计划成为一个面向邮件与日程工作的单 Agent 助手。它的工程重点不是
覆盖尽可能多的个人助理功能，而是回答一个更窄的问题：

> 一个能够对外行动的 Agent，怎样从经验中学习，同时避免把偶然成功、
> 不安全指令或行为回归晋升为长期有效的 Skill？

计划中的系统包括：

- 有界、可观测的 Agent Runtime；
- 带审批、幂等和结果验证的类型化外部动作；
- 相互分离的用户画像、执行经历和程序性 Skill 记忆；
- 基于来源明确的执行证据、且只能先生成候选版本的 Skill 学习；
- Skill 版本晋升前的任务效果与安全评测。

第一个可执行环境将使用 AgentDojo 的 `workspace` 套件。这样可以在连接
任何真实账号之前，先基于确定性的外部状态和提示注入测试邮件、日程行为。

## 初始范围

第一个纵向切片只完成一条端到端工作流：

1. 找到相关邮件线程；
2. 检查日历空闲时间；
3. 准备邮件回复和日程事件；
4. 将用户审批绑定到精确的待执行副作用；
5. 确保每个外部修改只执行一次；
6. 验证最终外部世界状态。

多 Agent 编排、消息渠道 Gateway、主动定时执行、插件市场、GUI 自动化和
多个生产环境连接器均不属于第一个版本。

## 设计文档

- [产品定义](docs/PRODUCT.zh-CN.md)
- [系统架构](docs/ARCHITECTURE.zh-CN.md)
- [评测方案](docs/EVALUATION.zh-CN.md)
- [路线图与源码阅读顺序](docs/ROADMAP.zh-CN.md)
- [AgentDojo Workspace Spike](docs/research/AGENTDOJO_SPIKE.zh-CN.md)
- [Phase 1 安全动作核心实现](docs/implementation/PHASE1_ACTION_CORE.zh-CN.md)
- [Phase 1 持久 Run Lifecycle](docs/implementation/PHASE1_RUN_LIFECYCLE.zh-CN.md)
- [Phase 1 Provenance-aware Agent Loop](docs/implementation/PHASE1_AGENT_LOOP.zh-CN.md)

## 当前可运行切片

```bash
python -m pip install -e '.[agentdojo]'
python -m unittest discover -s tests -v
python scripts/demo_agent_loop.py
```

Agent Loop Demo 会执行带 Provenance Label 的邮件与日历读取，在外部动作前
暂停并打印精确 Effects，然后应用 Scripted Operator Approval，最后使用
AgentDojo 官方 `user_task_18` Utility Grader 验证最终状态。它使用确定性
Scripted Model，不连接真实邮件或日历账号。单独的 Lifecycle Demo 还会在等待
Approval 时关闭并重建 SQLite Runtime。
