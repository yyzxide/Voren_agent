# Voren Agent

[English](README.md)

> 当前状态：Phase 1 进行中。安全动作核心已经能够在确定性 Fake World 和固定
> 版本的 AgentDojo Workspace 中运行；目前还不存在连接模型或生产账号的
> Agent。

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

## 当前可运行切片

```bash
python -m pip install -e '.[agentdojo]'
python -m unittest discover -s tests -v
python scripts/demo_agentdojo_action.py
```

Demo 会通过 AgentDojo 执行已审批 Action，并使用官方 `user_task_18` Utility
Grader 检查结果。它不会连接任何真实邮件或日历账号。
