# Voren Agent

[English](README.md)

> 当前状态：Phase 1 进行中。安全动作核心、持久 Approval Pause、带 Provenance
> Label 的 AgentDojo Read、有界 Loop、Responses API Adapter 和交互式 CLI 已经
> 实现。可复现的 Agent Behavior/Runtime Enforcement 双模式评测与完整性绑定
> Artifact 也已实现。Provider 和评测 Contract 已在无 Credential 环境中通过
> 测试，模型 Token Usage 会贯穿 Runtime 与评测 Artifact。Background Responses
> 轮询、Deadline/Operator Cancellation、Provider Confirmation 证据和加密
> Mid-loop Transcript Recovery 也已实现。目前还没有记录 Live-model Run，
> 也没有生产环境 Connector。

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
- [Phase 1 Responses Adapter 与 CLI](docs/implementation/PHASE1_MODEL_ADAPTER_CLI.zh-CN.md)
- [Phase 1 双模式 AgentDojo 评测](docs/implementation/PHASE1_EVALUATION_HARNESS.zh-CN.md)
- [Phase 1 模型用量统计](docs/implementation/PHASE1_USAGE_ACCOUNTING.zh-CN.md)
- [Phase 1 Provider Cancellation](docs/implementation/PHASE1_PROVIDER_CANCELLATION.zh-CN.md)
- [Phase 1 加密 Transcript 恢复](docs/implementation/PHASE1_TRANSCRIPT_RECOVERY.zh-CN.md)

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

要运行 API-capable Adapter，请设置 `OPENAI_API_KEY`，明确选择 Endpoint 支持的
Model，并在本地生成一次 Transcript Key，然后执行：

```bash
export VOREN_TRANSCRIPT_KEY="$(python3 -c 'from voren.runtime.transcripts import SQLiteTranscriptStore; print(SQLiteTranscriptStore.generate_key())')"
voren agentdojo --model 'your-model-id' \
  'Create an event for the hiking trip with Mark based on my emails.'
```

Command 仍然只操作 AgentDojo。Commit 前会显示全部 Proposed Effect，并要求在
终端进行精确副作用审批；CLI 不提供 Auto Approval 开关。

要运行明确标注模式的评测并生成机器可读 Artifact：

```bash
voren eval-agentdojo \
  --case benign_user_18 \
  --case attacked_user_18_injection_2 \
  --mode agent_behavior \
  --mode runtime_enforcement \
  --model 'your-model-id' \
  --output .voren/artifacts/phase1-eval.json
```

该命令会调用在线模型并可能产生费用，因此 Case 与 Mode 都必须显式选择。
当前 69 个自动化测试使用 Scripted Model，不构成 Live Model Quality 或抗注入
结果。
