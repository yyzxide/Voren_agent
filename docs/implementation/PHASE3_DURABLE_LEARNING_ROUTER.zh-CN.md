# Phase 3 Durable Learning Router

[English](PHASE3_DURABLE_LEARNING_ROUTER.md)

## 为什么只有 Reference 不够

最初 Candidate Staging 接受调用方构造的 Evidence ID 与 Digest。它只能证明
Candidate Record 绑定了“某个声称存在的 Digest”，不能证明对应不可变 Evidence
Object 真实存在。现在公开路径使用带完整性校验的 SQLite Evidence Store，并在
Staging 前解析 ID。

## 授权 Evidence 路径

只有两条显式路径：

- Operator Correction 把 UTF-8 文本、Operator Reference、Content Digest、创建
  时间和 Instruction-authority Bit 存入同一个类型化 Artifact；
- Verified Run 保存经过验证的 Run Record 与 Event Stream 的 Canonical Snapshot，
  并且永远不获得 Instruction Authority。

Model Reflection 与 External Observation 不能成为独立授权 Artifact。它们可以
为人工判断或之后的 Candidate Author 提供信息，但不能直接跨过 Durable-learning
Boundary。

## Verified Run 准入

Run 只有处于 Completed 且以 `run.completed` 结束时才可能准入。对每个 Proposed
Operation，Router 要求 Proposal、Accepted Approval 与 Final Receipt Event 的集合
完全匹配。Operation ID、Proposal Digest 与 Approval ID 必须一致；Final Receipt
必须已经 Commit、状态为 `verified`，且通过 Exact-effect Verification。Ambiguous
Receipt 之后若出现 Verified Reconciliation，最终观察结果胜出，因此可以准入，
同时不会重新发送 Effect。

如果 External-untrusted Observation 声称拥有 Instruction Authority，准入失败。
正常 Runtime 构造已经由 Provenance Model 阻止这种状态；Router 在 Durable
Boundary 再检查一次，作为 Defense in Depth。

## 完整性与 Holdout 隔离

Evidence Artifact 绑定 Payload Media Type、Source Reference、Case ID、Payload
Digest 与完整 Artifact Digest。Evidence ID 只有在 Artifact 完全相同时才幂等。
公开 CLI 暂存 Candidate 时加载这些 Artifact，并逐字段比较生成的 `EvidenceRef`
与已存 Reference。

创建 Run Evidence 时使用过的 Evaluation Case ID 会进入 Reference，因此 Paired
Policy 能拒绝把它们再次作为 Held-out Case。

## Threat-model 边界

Run Event Store 是本地 SQLite，不是远程 Attested Log。Router 会对它观察到的
Event Snapshot 做完整性绑定，但无法证明具备数据库管理员权限的攻击者没有在
Selection 之前重写历史。
