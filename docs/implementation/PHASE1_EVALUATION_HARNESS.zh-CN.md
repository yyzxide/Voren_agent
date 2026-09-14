# Phase 1：双模式 AgentDojo 评测

[English](PHASE1_EVALUATION_HARNESS.md)

## 已完成范围

这个切片把原先的评测设计变成可执行 Contract。它没有把“模型是否服从注入”
和“Runtime 是否阻止未授权副作用”压缩成一个安全分数，而是让同一个 Case 在两
种明确标记的模式下运行：

```text
同一个 User Task + 同一个 Injection Fixture
                    |
           +--------+--------+
           |                 |
           v                 v
  agent_behavior     runtime_enforcement
  Benchmark 自动放行   精确 User Ground-truth Policy
           |                 |
           +--------+--------+
                    v
       分模式 Utility / Attack Success
                    v
       Integrity-bound JSON Artifact
```

自动化测试使用确定性 Scripted Model，不发起在线模型请求。它证明评测机制的
语义和数据边界，不是任何真实模型的能力或抗注入成绩。

## 两种模式的准确含义

### `agent_behavior`

如果模型提出当前 Capability Envelope 内的有效外部动作，Benchmark Approval
Policy 会自动放行。这样能够观察模型产生的动作是否满足 AgentDojo Attack
Grader。这个自动放行只存在于 Evaluation Runner，交互式 `voren agentdojo`
仍然没有 `--yes` 或 Auto Approval。

该模式仍会经过 Voren 的类型校验、Action Gateway 和 Verifier，所以它测量的
是“当前 Voren Capability Envelope 内的原始模型行为”，不是无限工具权限下的
完整 AgentDojo Baseline。

### `runtime_enforcement`

确定性 Policy 从 AgentDojo User Task Ground Truth 取得用户真正要求的日历
动作，进行规范化后，与模型 Proposal 的 Action Name、Contract Version 和全部
Arguments 精确比较。不一致时拒绝 Approval，且不调用 Commit Adapter。

Ground Truth 仅供 Evaluator 使用，不会进入 System Prompt、User Message 或
Tool Observation。这个 Policy 是评测 Oracle，不是生产环境中替代真实用户
Approval 的方案。

## Phase 1 Manifest

可验证 Email Action 完成后，内置 Manifest 已扩展为
`phase1-agentdojo-smoke-v2`，当前包括：

| Case | User Task | Injection | 支持模式 |
| --- | --- | --- | --- |
| `benign_user_17` | 17 | 无 | behavior |
| `benign_user_18` | 18 | 无 | behavior / enforcement |
| `benign_user_20` | 20 | 无 | behavior / enforcement |
| `attacked_user_18_injection_2` | 18 | 2，hiking email vector | behavior / enforcement |
| `attacked_user_18_injection_3` | 18 | 3，hiking email vector | behavior / enforcement |
| `attacked_user_18_injection_4` | 18 | 4，hiking email vector | behavior / enforcement |

Task 2 覆盖日历动作；Task 3/4 覆盖后续实现的独立 `send_email` 契约。只有目标
Mutation 已经落在实现并验证过的 Capability Envelope 内时，对应 Case 才会进入
当前 Manifest。

固定攻击模板及其版本、AgentDojo Distribution/Benchmark Version、Case ID、
Injection Task 和 Injection Vector 都会进入可复现配置或其 Hash。

## Artifact Contract

每个 JSON Artifact 保存：

- Experiment ID、创建时间、Git Revision 和 Dirty 标志；
- Provider、Model 与 Runtime Budget；
- Manifest、System Prompt、Tool Schema 和 Attack Template Digest；
- 每个 Trial 的 Case、Mode、Run 状态和 Approval Outcome；
- Utility、Attack Success 和 Receipt Verification；
- Input/Output/Cached/Cache-write/Reasoning Token Usage 及报告完整性；
- Pre/Post Environment Digest 与 Final Output Digest；
- 不包含原始 Prompt/邮件正文的规范化 Run Event；
- 按 Mode 分开的 Utility Rate 和 Attack Success Rate；
- 覆盖整个 Artifact 内容的 SHA-256 Digest。

Digest 用于检测内容变化，不是数字签名，也不能证明 Artifact 的发布者身份。
公开实验应从 Clean Git Revision 运行；`code_dirty=true` 的 Artifact 只能视为开发
证据。

## CLI

Case 和 Mode 都必须显式选择，避免意外发起一组付费调用：

```bash
export OPENAI_API_KEY='your-api-key'
voren eval-agentdojo \
  --case benign_user_18 \
  --case attacked_user_18_injection_2 \
  --mode agent_behavior \
  --mode runtime_enforcement \
  --model 'your-model-id' \
  --output .voren/artifacts/phase1-eval.json
```

相同 Case 在两个 Mode 下会分别创建独立 Agent、Workspace 与 Run，避免第一个
Trial 的状态污染第二个 Trial。SQLite 只保存安全的 Lifecycle Trace；JSON
Artifact 原子写入目标路径。

## 当前确定性证据

测试中的良性 Scripted Model 在 `user_task_18` 两个 Mode 下均通过官方 Utility
Grader，Action Receipt 也通过 Exact-effect Verification。

测试中的恶意 Scripted Model 始终提出 Injection Task 2 的日历动作：

| Mode | Approval | AgentDojo Attack Success | 环境变化 |
| --- | --- | --- | --- |
| `agent_behavior` | approved | true | 有，Receipt verified |
| `runtime_enforcement` | rejected | false | 无，Pre/Post Digest 相同 |

这个结果只是 Harness Contract Test：输入模型被脚本固定为恶意动作，因此不能
写成“某真实模型攻击成功率 100%/0%”。这个切片的 Checkpoint 为 54 个通过的
测试；后续[模型用量统计](PHASE1_USAGE_ACCOUNTING.zh-CN.md) 将当前完整 Suite
增加到 57 个测试；再之后的 Provider Cancellation 切片将其增加到 66 个测试。

## 源码位置

| 关注点 | 文件 |
| --- | --- |
| Case、Trial、Summary 与 Artifact Model | `src/voren/evaluation/models.py` |
| 原子 Artifact IO 与 Source Revision | `src/voren/evaluation/artifacts.py` |
| Manifest、双模式 Runner 与 Grader | `src/voren/evaluation/agentdojo.py` |
| Evaluation CLI | `src/voren/cli.py` |
| Artifact Contract Test | `tests/test_evaluation_artifacts.py` |
| 双模式 AgentDojo Integration | `tests/integration/test_agentdojo_evaluation.py` |
| CLI Artifact Integration | `tests/integration/test_cli_agentdojo.py` |

## 尚未声称完成

- 没有保存过 Live Model Artifact；
- 没有报告任何真实模型 Utility 或 Attack Success Rate；
- 没有 Latency 或 Cost 指标；
- Artifact 没有数字签名或外部见证。

## 建议阅读顺序

1. `evaluation/models.py`：先看为什么 Summary 必须按 Mode 分组；
2. `evaluation/agentdojo.py`：比较两个 Approval Policy；
3. `test_agentdojo_evaluation.py`：跟踪相同恶意动作的两条状态路径；
4. `evaluation/artifacts.py`：确认写入和完整性校验；
5. `cli.py`：确认 Live Run 必须显式选择 Case、Mode、Model 和 Output。

你应当能够解释：为什么 Runtime 阻止攻击不等于模型没有服从注入，为什么只有
目标 Capability 已实现时 Attack Case 才成立，以及 Artifact Digest 与数字签名
有什么区别。
