# Phase 1 安全动作核心

[English](PHASE1_ACTION_CORE.md)

## 已完成范围

这个切片实现了“工具调用提议”与“外部状态变化”之间的确定性边界，暂时不包含
LLM Loop 或生产环境 Connector。

```text
已校验输入
    |
    v
EffectManifest -> ActionProposal -> ApprovalDecision
                                       |
                                       v
                              SQLite Operation Ledger
                                       |
                                       v
                                    Commit
                                       |
                                       v
                                观察外部状态
                                       |
                                       v
                           Verify -> ActionReceipt
```

当前使用的 Operation State：

```text
prepared -> authorized -> committing -> verified
                                  |\-> verification_failed
                                  |\-> ambiguous
                                  \-> failed
```

## 已强制执行的不变量

1. 先校验并规范化输入，再具体化 Effect；
2. SHA-256 Proposal Digest 同时绑定 Operation ID、Action/Version、规范化参数和
   全部 Effect；
3. Approval 必须同时匹配 Operation ID 与 Proposal Digest，并且不能过期；
4. Commit 前再次检查 Ledger 中注册的 Proposal。Approval 后修改嵌套 JSON 也会
   使 Digest 失效；
5. 已完成的 Operation 直接返回持久化 Receipt，不再次调用 Adapter；
6. Commit 后发生超时时，先观察外部状态，不能自动重试；
7. 如果超时后无法根据状态消除歧义，Receipt 保持 `ambiguous`；重复调用不会
   再次提交；
8. Effect 缺失、内容不一致或出现额外 Effect，都会导致 Verification 失败。

## 黄金动作契约

已实现的 `create_calendar_event` 契约与 AgentDojo Spike 保持一致。一份 Action
Proposal 同时包含两个 Effect：

- 创建 `calendar.events/new_event`；
- 发送带有日程邀请的 `inbox.emails/new_sent_email`。

Fake Workspace 和固定版本的 AgentDojo Adapter 完成 Commit 后，都会从自身
状态中重新构造 Observed Effect。Gateway 比较观察结果与已审批 Manifest，
而不是直接相信 Adapter 的 Commit 返回值。AgentDojo Adapter 还会把无法识别
的 Workspace State Delta 转换成 Unexpected Effect。

## 源码位置

| 关注点 | 文件 |
| --- | --- |
| 不可变 Wire Model 与精确 Effect Verifier | `src/voren/actions/models.py` |
| Prepare/Authorize/Commit/Verify Protocol | `src/voren/actions/gateway.py` |
| 持久 Operation State | `src/voren/actions/ledger.py` |
| Adapter Protocol | `src/voren/actions/ports.py` |
| 共享邮件/日历 Action Contract | `src/voren/adapters/workspace_contracts.py` |
| 黄金动作、确定性世界与故障注入 | `src/voren/adapters/fake_workspace.py` |
| 固定版本 AgentDojo Workspace Adapter | `src/voren/adapters/agentdojo_workspace.py` |
| Fake 与 AgentDojo 审批 Demo | `scripts/demo_safe_action.py`、`scripts/demo_agentdojo_action.py` |
| 契约与故障测试 | `tests/test_action_gateway.py` |
| AgentDojo 集成契约 | `tests/integration/test_agentdojo_workspace_adapter.py` |

## 运行方式

运行完整受控环境测试：

```bash
python -m pip install -e '.[agentdojo]'
python -m unittest discover -s tests -v
python scripts/demo_agentdojo_action.py
```

仅用于非交互式本地演示时：

```bash
python scripts/demo_safe_action.py --simulate-approval
python scripts/demo_agentdojo_action.py --simulate-approval
```

Approval Simulator 属于评测和演示设施，不能用它声称真实外部动作经过了人工
审批。

## 当前证据

11 个确定性 Gateway 测试覆盖：

- 日程与邀请邮件组成的双 Effect Manifest；
- Approval 绑定到另一份 Proposal；
- Digest 生成后 Proposal 被修改；
- Approval 过期；
- 正常 Commit 与状态验证；
- 抑制重复 Commit；
- Commit 前的明确失败；
- Commit 后超时并通过状态恢复；
- 无法消除歧义时停止且不重试；
- 检测意外外部 Effect；
- 重新打开 SQLite Ledger 后恢复 Receipt。

另有 4 个 AgentDojo 集成测试证明：

- 已安装 Distribution 与 Benchmark API 符合研究阶段的版本固定；
- 通过 Voren 提交的状态可以通过 AgentDojo 官方 `user_task_18` Utility
  Grader；
- 重放已授权 Action 时，始终只新增一个日程和一封邮件；
- 无关的 AgentDojo 状态变化会被暴露成 Unexpected Effect。

## 明确尚未完成的部分

整个 Phase 1 仍在进行中。下一个切片还需要：

- 围绕 Operation Ledger 实现持久 Run Lifecycle 与 Approval 暂停/恢复；
- Append-only Event；
- 有界 Model/Tool Loop；
- 带 Provenance Label 的邮件/日历读取工具。

这两个受控 Workspace 都不会被描述成生产环境 Connector。Fake Workspace 的
进程内状态不持久化；AgentDojo 则刻意模拟内存 Benchmark，不模拟真实 Provider
的投递和 OAuth 行为。
