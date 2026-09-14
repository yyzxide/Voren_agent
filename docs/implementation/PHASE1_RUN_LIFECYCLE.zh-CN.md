# Phase 1 持久 Run Lifecycle

[English](PHASE1_RUN_LIFECYCLE.md)

## 已完成范围

这个切片在现有安全动作协议外增加了持久控制面：

```text
created -> running -> waiting_approval -> completed
                                  |\----> failed
                                  |\----> cancelled
                                  \-----> needs_reconciliation -> completed
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
   `recover_pending_receipt` 会补全事件链，不会再次 Commit；
7. 如果进程在外部 Commit 后、Receipt 落盘前停止，Gateway 会使用稳定的
   Operation ID 重新观察外部状态。精确匹配时生成恢复 Receipt；无法确认时保存
   `ambiguous`，保留 Pending Operation，且不会重新派发；
8. `reconcile_pending_action` 可在外部状态稍后可见时再次只读核验，并把 Run 从
   `needs_reconciliation` 推进到 `completed`。

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
action.reconciled
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

Lifecycle 测试覆盖：

- Happy Path Event 顺序；
- 重新打开两个 SQLite Store 后恢复；
- 操作者拒绝且不产生外部 Effect；
- 审计无效 Approval，同时保持暂停；
- Ambiguous Commit 映射为 `needs_reconciliation`；
- Approval 已持久化后的幂等 Resume；
- 模拟进程崩溃后的持久 Receipt 恢复；
- 外部 Effect 已发生但 Receipt 尚未持久化时的只读恢复；
- 无法确认时保持 Ambiguous 且不重新派发；
- 外部状态稍后可见后的二次核验；
- Event 幂等重放与冲突 Dedupe Key 拒绝。

## 剩余边界

这个切片实现了 Commit/Receipt 崩溃窗口的协调协议，但生产能力仍取决于 Adapter：
它必须能够根据稳定 Operation ID、供应商 Idempotency Key 或可归因的外部
Postcondition 重新发现远端 Operation。没有这种查询能力时，Voren 只会保持
`ambiguous` 并要求人工处理，不能宣称端到端 Exactly-once Delivery。

Fake Workspace 的确定性测试证明控制流不会自动重发；它不能替代真实邮件或日历
Provider 的持久化与一致性验证。

## 建议阅读顺序

1. `runs/models.py`：识别 Lifecycle State 与不可变证据；
2. `runs/store.py`：跟踪一次状态更新和相关 Event 如何进入同一事务；
3. `runs/manager.py`：跟踪 Proposal、Approval、Receipt 与终态映射；
4. `test_run_lifecycle.py`：使用每个失败场景反问不变量是否成立。

你应当能够解释：为什么 `needs_reconciliation` 不能叫作 `failed`，为什么要在
两个位置保存 Proposal Digest，以及为什么 Reconciliation 只能观察、不能重发。
