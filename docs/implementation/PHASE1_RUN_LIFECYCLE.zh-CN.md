# Phase 1 持久 Run Lifecycle

[English](PHASE1_RUN_LIFECYCLE.md)

## 已完成范围

这个切片在现有安全动作协议外增加了持久控制面：

```text
created -> running -> waiting_approval -> completed
                                  |\----> failed
                                  |\----> cancelled
                                  \-----> needs_reconciliation
```

`RunConfig`、Pending Proposal Identity、状态版本和最终 Receipt Status 都存入
SQLite。Run 在 Approval 前暂停后，可以关闭全部数据库连接，重新构建 Runtime
对象，然后继续处理同一份 Proposal。

## 持久化模型

实现在同一个 SQLite 文件中使用三个逻辑 Store：

- `operations`：Proposal、Approval、Operation State 和 Receipt；
- `runs`：冻结配置、Lifecycle State、Pending Operation 和乐观状态版本；
- `run_events`：只追加写的有序生命周期证据。

Run Configuration 带有 SHA-256 Digest。加载 Run 时会重新计算 Digest，拒绝
已经损坏或被意外修改的配置。

## 暂停与恢复协议

1. `RunManager.start_new_run` 持久化 `run.created` 和 `run.started`；
2. `propose_action` 请求 `ActionGateway` 规范化 Action，随后存储 Operation ID
   和精确 Proposal Digest；
3. Run 进入 `waiting_approval`，此时尚未调用 Adapter Commit；
4. 重启后，`resume_with_approval` 从 Operation Ledger 加载 Proposal，校验
   Decision，并通过同一 Gateway Commit；
5. Receipt 被映射成 Run 终态：
   - `verified` -> `completed`；
   - 明确发生在 Commit 前的 `failed` -> `failed`；
   - `ambiguous` 或 `verification_failed` -> `needs_reconciliation`；
6. 如果 Receipt 已持久化，但进程在更新 Run 前停止，
   `recover_pending_receipt` 会补全事件链，不会再次 Commit。

操作者拒绝 Approval 时，这个单 Action Run 会被取消，并且不调用 Adapter。
无效或过期 Approval 会被审计，Run 继续暂停以等待新的有效 Decision。

## Append-only Event 规则

Lifecycle-only Path 包含：

```text
run.created
run.started
action.proposed
run.waiting_approval
approval.accepted | approval.rejected | approval.invalid
action.receipt
run.completed | run.failed | run.cancelled | run.needs_reconciliation
```

Event 获得 SQLite Sequence 与 Run 内 Dedupe Key。使用相同 Key 和完全一致的
Canonical JSON 属于幂等重放；使用相同 Key 但不同 Event Type 或 Payload 会被
拒绝，不能静默丢失审计信息。Store 不提供更新或删除 Event 的操作。

Proposal 和 Receipt Event 只保存 Digest、Effect Classification、Verification
Result 与 External Reference，不重复存储 Action Argument 或 Effect Attribute。
Operation Ledger 为了恢复执行仍然需要完整 Proposal；这份主副本的加密与保留
策略属于后续 Storage Policy 决策。

## 源码位置

| 关注点 | 文件 |
| --- | --- |
| Run、Config 与 Event Model | `src/voren/runs/models.py` |
| SQLite Run 与 Event Store | `src/voren/runs/store.py` |
| Pause/Resume Orchestration | `src/voren/runs/manager.py` |
| Operation Approval 恢复 | `src/voren/actions/ledger.py` |
| Lifecycle 与崩溃边界测试 | `tests/test_run_lifecycle.py` |
| 重启演示 | `scripts/demo_run_lifecycle.py` |

## 运行方式

```bash
python scripts/demo_run_lifecycle.py
```

用于测试和 CI 的确定性演示：

```bash
python scripts/demo_run_lifecycle.py --simulate-approval
```

Demo 会在 Run 等待 Approval 时刻意关闭并重新打开 SQLite 组件。

## 当前证据

8 个 Lifecycle 测试覆盖：

- Happy Path Event 顺序；
- 重新打开两个 SQLite Store 后恢复；
- 操作者拒绝且不产生外部 Effect；
- 审计无效 Approval，同时保持暂停；
- Ambiguous Commit 映射为 `needs_reconciliation`；
- Approval 已持久化后的幂等 Resume；
- 模拟进程崩溃后的持久 Receipt 恢复；
- Event 幂等重放与冲突 Dedupe Key 拒绝。

在这个切片的 Checkpoint，加上 Action 与 AgentDojo 测试共有 23 个通过的测试。
后续 [Phase 1 Agent Loop](PHASE1_AGENT_LOOP.zh-CN.md) 扩展了 Event Vocabulary，
并将当时的 Checkpoint 增加到 36 个测试；再之后的 Model Adapter/CLI 切片将
当前完整 Suite 增加到 47 个测试。

## 剩余边界

这个切片证明了 Commit 前的持久暂停/恢复，以及 Receipt 已写入后的恢复。如果
真实 Provider 已经 Commit，但 Voren 在持久化 Receipt 前进程死亡，问题会更
困难：Adapter 必须能够根据 Idempotency Key 或外部 Postcondition 重新发现
远端 Operation。

Fake Workspace 和 AgentDojo World 都不会跨进程持久化，因此不能证明最后这个
生产边界。Voren 会明确记录它，不能仅凭 SQLite 持久化就声称端到端
Exactly-once Delivery。

## 建议阅读顺序

1. `runs/models.py`：识别 Lifecycle State 与不可变证据；
2. `runs/store.py`：跟踪一次状态更新和相关 Event 如何进入同一事务；
3. `runs/manager.py`：跟踪 Proposal、Approval、Receipt 与终态映射；
4. `test_run_lifecycle.py`：使用每个失败场景反问不变量是否成立。

你应当能够解释：为什么 `needs_reconciliation` 不能叫作 `failed`，为什么要在
两个位置保存 Proposal Digest，以及哪个崩溃窗口仍然需要真实 Provider 能力。
