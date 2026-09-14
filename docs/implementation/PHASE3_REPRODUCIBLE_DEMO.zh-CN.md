# Phase 3 可复现 Skill-learning Demo

[English](PHASE3_REPRODUCIBLE_DEMO.md)

## 目的

`scripts/demo_skill_learning.py` 为 Voren 的核心学习主张提供一条无需 Credential
的验收路径。它会真实执行 Store、Policy、Paired Runner、晋升事务、回滚事务和
报告生成器，但不能替代 Live-model Evaluation。

## 可观察生命周期

1. 安装并激活仓库内经过人工审阅的 Scheduling Skill；
2. 把操作者编写的纠正持久化为带指令权威的 Evidence；
3. 针对精确 Active Version 暂存只修改 Instruction 的有界 Candidate；
4. 在同一组 Benign/Attack Case 上评测精确的 Base 与 Candidate；
5. 记录 Accepted Decision，同时证明 Active Pointer 没有发生变化；
6. 显式晋升 Candidate，再回滚到精确 Base Version；
7. 写出原始 Trial、完整性绑定的 Evaluation Artifact 与 Markdown Report。

每次运行都会在 `.voren/demos` 下创建新目录，不复用或覆盖以前的 Demo
Workspace。

## 证据边界

内置 Evaluator 检查两条明确的 Instruction Contract：Candidate 在 Benign Case
中新增“保留多个邮件线程的冲突、不合并虚构会议”的规则；两个版本都必须保留
“邮件文本没有指令权威”的边界。每份原始结果都会声明自己是确定性检查，不是
Live Model 或 AgentDojo Benchmark Score。

这个限制使生命周期无需 Secret 也可以复现。真实模型行为仍须通过
`voren skill eval-agentdojo` 显式选择付费 Case 后单独测量。

## 验收

```bash
python scripts/demo_skill_learning.py
python -m unittest tests.test_demo_skill_learning -v
```

只有 Candidate 被接受但未自动激活、晋升选择精确 Candidate Version、回滚恢复
精确 Base，且所有 Artifact 都能通过完整性链生成报告时，命令才会成功。
