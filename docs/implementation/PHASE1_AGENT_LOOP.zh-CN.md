# Phase 1：Provenance-aware Agent Loop

[English](PHASE1_AGENT_LOOP.md)

## 已完成范围

这个切片把 Voren 的持久 Run Manager、安全 Action Gateway 与 Provider-neutral
有界 Model/Tool Loop 连接起来：

```text
操作者请求
    |
    v
模型响应 --纯读取调用--> 带标签的 ToolObservation --+
    ^                                                  |
    +--------------------------------------------------+
    |
    +--外部动作调用--> ActionProposal --> waiting_approval
                                             |
                                         操作者决策
                                             |
                                  commit -> observe -> verify
```

无 Credential 的测试和 Demo 使用 Scripted Model。它会经过未来真实 Provider
Adapter 使用的同一套 Runtime Contract，但不能用来证明 LLM 的规划质量。

## Observation 与权限契约

每一条返回数据都有字段级 Provenance Metadata：

```text
trust
source
source_ref
retrieved_by
retrieved_at
instruction_authority
```

邮件正文、日历字段和联系人都被标记为 `external_untrusted`，并且
`instruction_authority=false`。这些内容仍可作为任务数据使用；Label 不会删除
恶意字符串，也不假装模型看不到它。System Instruction 和结构化 Tool Message
都会明确说明：这些字段无权发出指令。

这是权限边界，不是“LLM 永远不会受到 Prompt Injection 影响”的形式化保证。
因此 Runtime 仍实施确定性约束：

- 模型只能看到部署配置 Allowlist 中的工具；
- 第一个 Read Adapter 只暴露 `search_emails`、
  `get_day_calendar_events` 和 `search_contacts_by_name`；
- AgentDojo Read 在 Environment 的深拷贝上执行，上游所谓“读取”也不能修改
  Live World；
- `get_unread_emails` 会把邮件标为已读，因此不加入只读工具；
- 一次包含外部动作的模型响应不能同时包含其他 Tool Call；
- 外部动作只生成 Proposal，并让 Run 立即暂停；
- 只有绑定精确 Digest 的操作者 Approval 才能到达
  `ActionGateway.commit`。

## 有界 Loop

`RuntimeLimits` 实施四项互相独立的预算：

| 预算 | 默认值 | 超限行为 |
| --- | ---: | --- |
| Model Step | 8 | 以 `max_model_steps` 终止 Run |
| Tool Call 总数 | 12 | 在执行前拒绝下一次调用 |
| 相同调用重复次数 | 2 | 阻止同名、同参数的循环 |
| 单个 Observation | 64,000 Bytes | 不将其放入模型 Context |

同一 Run 内的 Tool Call ID 也必须唯一。未知 Tool 和混合 Read/Action Batch
都会 Fail Closed。纯读取的最终文本会完成 Run；修改请求则返回
`waiting_approval` 和精确 `ActionProposal`。

## Event 与数据保留边界

Loop 新增以下 Event Type：

```text
model.requested
model.responded
tool.called
tool.observed
runtime.limit_reached
```

Event 只保存 Step、Count、Name、Trust Label、Size 和 SHA-256 Digest。操作者原始
请求、模型文本、邮件正文与完整动作参数不会被复制进 Event Stream。因为 Approval
恢复需要完整 Proposal，Operation Ledger 仍会保存 Proposal 主副本。Action
Proposal Event 还会关联此前 Observation 的 Digest，在不授予 Observation 权限的
前提下保留因果 Evidence Lineage。Approval 绑定精确 Effects；Evidence Lineage
记录这些 Effects 为什么会被提出。

这属于数据最小化，不等于加密。后续 Storage Policy 切片仍需实现 Artifact
Encryption、Retention 和明确的 Redaction Rule。

## 源码位置

| 关注点 | 文件 |
| --- | --- |
| Provenance 与 Observation Model | `src/voren/observations/models.py` |
| Read Tool Contract | `src/voren/observations/read_tools.py` |
| 强制 Snapshot 的 AgentDojo Read | `src/voren/adapters/agentdojo_reads.py` |
| Provider-neutral Message 与 Limit | `src/voren/runtime/models.py` |
| Model Protocol | `src/voren/runtime/ports.py` |
| Action Contract 到 Model Tool Schema | `src/voren/runtime/tools.py` |
| 有界 Loop 与 Action Pause | `src/voren/runtime/agent_loop.py` |
| 确定性 Model Adapter | `src/voren/testing/scripted_model.py` |
| Runtime Boundary Event | `src/voren/runs/manager.py` |
| Loop Safety Test | `tests/test_agent_loop.py` |
| Provenance Invariant Test | `tests/test_observation_provenance.py` |
| AgentDojo Read Contract Test | `tests/integration/test_agentdojo_read_adapter.py` |
| 完整无 Credential Demo | `scripts/demo_agent_loop.py` |

## 运行方式

```bash
python -m pip install -e '.[agentdojo]'
python -m unittest discover -s tests -v
python scripts/demo_agent_loop.py
```

Demo 会先完成两次 AgentDojo Read，然后在包含两个 Effect 的 Calendar Proposal
处暂停，打印此时尚未 Commit；随后应用确定性的操作者 Approval、验证 Receipt，
并执行 AgentDojo 官方 `user_task_18` Utility Grader。

## 当前证据

8 个 Loop Test 覆盖：

- Read 到 Action 的暂停，且无外部修改；
- Model Message 中结构化的不可信 Provenance；
- Event Payload 最小化；
- 纯读取任务完成；
- 重复调用、总调用数、Step 和 Observation Size 限制；
- 拒绝混合 Read/Action Batch；
- 拒绝未知 Tool。

3 个 AgentDojo Read Adapter Test 覆盖恶意邮件内容、Calendar Provenance、
Snapshot Purity，以及排除有状态的未读邮件工具。加上此前 Action 与 Lifecycle
Suite，另有 2 个 Model-level Test 会拒绝不可信 Instruction Authority 和被篡改
的 Observation Digest。Voren 当前共有 36 个通过的测试。

## Phase 1 剩余边界

Provider-neutral Contract 当前只有 `ScriptedModelAdapter`。Phase 1 还需要一个
真实 Model Adapter、CLI Wiring，以及可复现的黄金任务和 Injection Run；其中
模型行为与 Runtime Enforcement 必须分别度量。

Loop Transcript 当前只存在于进程内。Run 和 Approval Pause 已经持久化，但进程
在连续 Read 期间死亡后，还不能恢复完全相同的模型 Transcript。真实 Provider
Timeout、Cancellation、Token Accounting 与 Context Compaction 也尚未实现。

## 建议阅读顺序

1. `observations/models.py`：确认哪些事实属于数据，哪些字段绝不具有权限；
2. `agentdojo_reads.py`：确认 Read Purity 来自 Snapshot Execution，而不是工具
   命名；
3. `runtime/models.py` 与 `runtime/ports.py`：检查 Provider Boundary；
4. `runtime/agent_loop.py`：跟踪 Budget、Read Handling 和强制 Action Pause；
5. `test_agent_loop.py`：用每个对抗用例挑战上述主张。

你应当能够解释：Provenance Label 和 Approval 为什么解决不同问题，
`get_unread_emails` 为什么不是 Pure Read，以及 Scripted Demo 为什么只能证明
Orchestration，不能证明模型智能。
