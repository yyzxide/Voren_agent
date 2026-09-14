# Phase 3 晋升与回滚

[English](PHASE3_PROMOTION_ROLLBACK.md)

## 生命周期边界

评测与激活被刻意拆开：

```text
staged -> accepted -> promoted -> rolled_back
       \-> rejected
```

只有 `accepted` Candidate 可以被 Promotion。`rejected` 或仅仅 `staged` 的
Candidate 都无法触及 Active Pointer。Promotion 是带非空 Reason 的显式人工或
Release-controller 操作；仅仅通过 Evaluation 永远不会自动激活学习指令。

## 原子 Pointer 切换

Promotion 获取 SQLite Immediate Write Transaction，并检查当前 Active Version
仍是精确的 Evaluated Base。在同一 Transaction 内，它会：

1. 有条件地把 `active_skills` 从 Base 切到 Candidate；
2. 有条件地把 Candidate Status 从 `accepted` 切到 `promoted`；
3. 追加一条同时记录两个精确 Reference 的 Lifecycle Event。

任何 Compare 失败都会回滚全部三项变化。这样，延迟到达的 Promotion 不会覆盖
较新的 Release，基于同一 Base 评测的两个 Candidate 也不可能同时胜出。

Rollback 使用对称规则：只有 Active Pointer 仍是精确的 Promoted Candidate 时，
才恢复精确 Base；它不会覆盖之后独立上线的新版本。成功 Promotion 或 Rollback
后的完全相同重试是幂等的，不会追加重复 Event。

## 带完整性链的审计

Candidate Staging、Evaluation Decision、Promotion 和 Rollback 都会追加类型化
Event。每条 Event 包含上一条 Event Digest，以及对自身 Canonical Content
计算的 SHA-256 Digest。读取时会验证连续 Sequence、Digest Link、Status
Transition、类型化 Event Invariant，以及 Event 最终状态与 Candidate Record
一致。在相应决策边界内，Evaluation、Pointer、Candidate 与 Event Record 在同一
Transaction 中更新。

这条链能够暴露篡改，但不能防御有能力重写整个数据库与全部 Digest 的数据库
管理员。若要覆盖更强的 Threat Model，需要外部 Append-only Sink 或数字签名。
