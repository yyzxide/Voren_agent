# Voren Agent

[English](README.md)

[2026-09-14 作品集审计](docs/reviews/2026-09-14-PORTFOLIO-AUDIT.zh-CN.md)记录了
审计基线与调整后的交付顺序。后续切片已增加原子 Operation Claim，并使用只做
外部状态观察、绝不重发动作的 Reconciliation 处理外部提交后且 Receipt 落盘前
的崩溃窗口。现有确定性测试通过仍不代表 Live Model 质量。

> 当前状态：Phase 1 进行中。安全动作核心、持久 Approval Pause、带 Provenance
> Label 的 AgentDojo Read、有界 Loop、Responses API Adapter 和交互式 CLI 已经
> 实现。可复现的 Agent Behavior/Runtime Enforcement 双模式评测与完整性绑定
> Artifact 也已实现。Provider 和评测 Contract 已在无 Credential 环境中通过
> 测试，模型 Token Usage 会贯穿 Runtime 与评测 Artifact。Background Responses
> 轮询、Deadline/Operator Cancellation、Provider Confirmation 证据和加密
> Mid-loop Transcript Recovery 也已实现。目前还没有记录 Live-model Run，
> 也没有生产环境 Connector。Phase 2 已经开始：当前具备兼容 Agent Skills、
> 内容寻址的静态 Skill Store、精确 Active Version Snapshot，以及接入 Agent Loop
> 且有大小限制的版本冻结 Instruction Context。Phase 3 现已具备 Evidence-gated
> 非激活 Candidate、Paired Held-out Evaluation、事务化 Decision、原子
> Promotion/Rollback、完整性审计链，以及精确版本的 AgentDojo Skill Evaluator。
> 公开 Learning CLI 与无需 Credential 的生命周期对比已经完成；有记录的
> Live-model Skill Comparison 仍待完成。

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
- [Phase 2 静态 Skill Store](docs/implementation/PHASE2_STATIC_SKILL_STORE.zh-CN.md)
- [Phase 2 版本冻结的 Skill Context](docs/implementation/PHASE2_SKILL_CONTEXT.zh-CN.md)
- [Phase 2 类型化 Memory 与冻结 Context](docs/implementation/PHASE2_TYPED_MEMORY.zh-CN.md)
- [Phase 3 Candidate 暂存](docs/implementation/PHASE3_CANDIDATE_STAGING.zh-CN.md)
- [Phase 3 配对评测](docs/implementation/PHASE3_PAIRED_EVALUATION.zh-CN.md)
- [Phase 3 晋升与回滚](docs/implementation/PHASE3_PROMOTION_ROLLBACK.zh-CN.md)
- [Phase 3 AgentDojo Skill Evaluator](docs/implementation/PHASE3_AGENTDOJO_SKILL_EVALUATOR.zh-CN.md)
- [Phase 3 Skill Lifecycle CLI](docs/implementation/PHASE3_SKILL_CLI.zh-CN.md)
- [Phase 3 Durable Learning Router](docs/implementation/PHASE3_DURABLE_LEARNING_ROUTER.zh-CN.md)
- [Phase 3 Candidate Decision Report](docs/implementation/PHASE3_CANDIDATE_REPORT.zh-CN.md)
- [Phase 3 可复现 Skill-learning Demo](docs/implementation/PHASE3_REPRODUCIBLE_DEMO.zh-CN.md)

## 当前可运行切片

```bash
python -m pip install -e '.[agentdojo]'
python -m unittest discover -s tests -v
python scripts/demo_agent_loop.py
python scripts/demo_skill_learning.py
```

Agent Loop Demo 会执行带 Provenance Label 的邮件与日历读取，在外部动作前
暂停并打印精确 Effects，然后应用 Scripted Operator Approval，最后使用
AgentDojo 官方 `user_task_18` Utility Grader 验证最终状态。它使用确定性
Scripted Model，不连接真实邮件或日历账号。单独的 Lifecycle Demo 还会在等待
Approval 时关闭并重建 SQLite Runtime。

Skill-learning Demo 不需要 API Key。它会持久化人工纠正证据、暂存一个不激活的
有界修改、对精确 Base/Candidate 版本做配对评测，证明“接受”不会自动激活，
然后显式执行晋升与回滚。四份原始 Trial Artifact、完整性绑定的配对评测和
Markdown 审计报告都会写入新的 `.voren/demos/skill-learning-*` 目录。这个
Evaluator 被明确标注为确定性的 Instruction-contract Check，不属于 Live Model
或 AgentDojo Benchmark 结果。

Skill Lifecycle 通过另一组显式 Subcommand 操作：

```bash
voren skill install skills/schedule-from-email --activate \
  --reason 'reviewed baseline'
voren skill evidence-correction correction.txt \
  --evidence-id operator:correction-001 --operator operator:sid
voren skill stage path/to/candidate --candidate-id candidate-001 \
  --base schedule-from-email --evidence-id operator:correction-001
voren skill eval-agentdojo --candidate-id candidate-001 \
  --case benign_user_18 --case attacked_user_18_injection_2 \
  --suite scheduling-held-out-v1 --model 'your-model-id' \
  --output .voren/artifacts/candidate-001.json
voren skill decide --candidate-id candidate-001 \
  --artifact .voren/artifacts/candidate-001.json
voren skill inspect --candidate-id candidate-001
voren skill report --candidate-id candidate-001 \
  --output .voren/reports/candidate-001.md
voren skill promote --candidate-id candidate-001 \
  --reason 'reviewed held-out evaluation'
voren skill rollback --candidate-id candidate-001 \
  --reason 'post-promotion regression'
```

`decide` 永远不会激活 Skill；`promote` 与 `rollback` 仍是各自带 Reason 的独立
操作。`eval-agentdojo` 要求显式选择产生费用的 Case，并对两个精确版本都只运行
Raw Agent Behavior。它只写入证据；运行单独的 `decide` 前 Candidate 始终保持
Staged。

另一种 Evidence Source 是 `voren skill evidence-run --run-id ...`。它只会路由
已经 Completed，并且每个 Proposed Operation 都具备精确绑定的 Accepted Approval
与最终 Verified Receipt 的 Run。`stage` 只接受通过这些 Router 预先持久化的
Evidence ID，不信任调用方手填的 Digest。

Profile Preference 与 Episode Summary 复用同一个持久 Evidence Boundary，但
始终属于 Data，不是程序性指令。`voren memory profile`、`memory episode` 和默认
脱敏的 `memory inspect` 提供公开入口。AgentDojo Run 只有显式传入
`--profile-memory` 或 `--episode-memory` 才加载对应 Memory，并把精确版本冻结到
RunConfig。

要运行 API-capable Adapter，请设置 Credential，明确选择 Endpoint Profile 与
Model，并在本地生成一次 Transcript Key。默认 OpenAI Endpoint 可直接执行：

```bash
export VOREN_TRANSCRIPT_KEY="$(python3 -c 'from voren.runtime.transcripts import SQLiteTranscriptStore; print(SQLiteTranscriptStore.generate_key())')"
voren agentdojo --model 'your-model-id' \
  'Create an event for the hiking trip with Mark based on my emails.'
```

DeepSeek Responses 是无状态前台接口，必须显式选择相应能力 Profile：

```bash
export DEEPSEEK_API_KEY='your-api-key'
voren agentdojo --provider-profile deepseek --model deepseek-v4-flash \
  'Summarize the hiking email.'
```

自定义 `OPENAI_BASE_URL` 时也必须通过 `--provider-profile` 或
`VOREN_RESPONSES_PROFILE` 明确声明能力，不能把“格式兼容”当成全部能力相同。

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
自动化测试不调用在线模型，不构成 Live Model Quality 或抗注入结果。带日期的
实测结果见上方作品集审计。
