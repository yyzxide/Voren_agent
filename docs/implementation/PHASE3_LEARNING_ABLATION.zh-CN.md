# Phase 3 Direct-reflection 与 Gated-learning 消融

[English](PHASE3_LEARNING_ABLATION.md)

## 问题

一个 Candidate 已经通过格式、Evidence、修改大小与能力范围 Admission 后，Voren
的 Evaluation Gate 还能增加什么？

`scripts/demo_learning_ablation.py` 使用两个精确 Candidate Version 回答这个策略
问题：

- 有益 Candidate 新增“保留冲突”的任务规则，不削弱邮件 Instruction-authority
  边界；
- 污染 Candidate 新增同一条任务规则，同时把邮件文本当作 Tool Authorization。

两个修改都有意保持很小，并且都能通过语法 Admission。因此只做 Admission 无法
区分它们。

## 对比策略

`direct_reflection` 是反事实 Baseline：所有通过 Admission 的修改都会立即激活，
不需要 Held-out Evidence。Demo 不会把这个不安全路径加入生产 Skill Store。

`voren_gated` 使用真实的 Paired Evaluation 与 Decision 路径。Accepted 只表示
Candidate 可以进入单独的、必须带 Reason 的人工晋升，仍然不会自动激活。

## 确定性结果

可复现 Fixture 产生：

| 指标 | 结果 |
|---|---:|
| Direct-reflection 激活数 | 2 |
| Gated 可人工晋升数 | 1 |
| Gate 避免的不安全激活数 | 1 |
| Gate 保留的有益 Candidate 数 | 1 |

污染 Candidate 虽然改进了 Benign Contract，却在配对 Attack Case 中引入 Security
Regression，因此 Gate 会拒绝它，Active Pointer 继续指向经过审阅的 Base。

## 完整性与限制

JSON Ablation Artifact 把每个 Candidate 绑定到精确的 Paired-evaluation Digest，
并具有自己的 Digest。Markdown Report 会重述策略边界与结果；篡改 Summary 会在
校验时失败。

这是确定性的机制测试，不是 Live Model 的统计结论。真实 Candidate 质量仍须
显式选择付费 AgentDojo Case 后单独评测。

```bash
python scripts/demo_learning_ablation.py
python -m unittest tests.test_learning_ablation -v
```
