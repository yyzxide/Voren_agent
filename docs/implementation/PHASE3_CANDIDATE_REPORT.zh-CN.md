# Phase 3 Candidate Decision Report

[English](PHASE3_CANDIDATE_REPORT.md)

## 目标

`voren skill report` 把持久 Candidate Record 转换成可审查的 Markdown Artifact。
相同数据库状态会得到确定性输出，因此可以与机器可读 JSON Artifact 一起放入
作品集。

Report 包含：

- 当前 Status 与精确 Base/Candidate/Diff/Evaluation Digest；
- Evidence ID、Source Reference、Media Type、Digest 与 Payload Size；
- 每个 Paired Held-out Case 的 Base/Candidate Utility 与 Attack Result；
- 作为缩进 Code Block 展示的 Bounded Instruction Diff；
- 带精确 Event Digest 的 Integrity-chained Lifecycle；
- 显式 Interpretation 与 Threat-model Limit。

Evidence Payload 永远不会进入 Report，防止 Operator-correction Text 或捕获的
Run Snapshot 泄露到报告或 CI Log。Skill Diff 会有意保留，因为报告的目的就是
审查 Proposed Procedural Change。

## 完整性行为

Renderer 首先重新验证每个 Evidence 与 Evaluation Artifact，并检查它们与
Candidate 的绑定。Lifecycle Event 已经由 Store 通过 Sequence、Digest Chain、
Transition 与 Final-state 验证。Report 使用 Atomic Replace 写入，CLI 会打印它的
SHA-256 Digest。

Markdown Digest 不是数字签名。公开作品集在给出实测结论时，应同时保留引用的
JSON Artifact 与 Source Revision。
