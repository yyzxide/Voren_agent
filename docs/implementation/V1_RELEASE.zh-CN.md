# Voren 1.0.0：本地单用户交付

[English](V1_RELEASE.md)

2026-10-08。本版本完成当前邮件/日程 Agent 项目及知识问答扩展的作品集交付范围。
“完成”指代码、受控验收、安装包和可复现入口闭环，不表示所有未来功能已经实现，
也不将真实账号、真实问答效果或生产多用户运行视为已经证明。

## 可运行能力

| 路径 | 当前实现 | 证据边界 |
| --- | --- | --- |
| 邮件/日程行动 | 有界 AgentLoop、逐动作审批、精确副作用验证、回执与跨进程恢复 | AgentDojo/假 HTTP 回归；历史 Live AgentDojo 证据单独保留 |
| 恢复前检查 | 冻结内置模型配置、workspace/契约，认证 checkpoint 和当前 pending call | 损坏、配置切换与错绑定在新外部派发前阻断；检查不是分布式事务 |
| Memory/Skill | Profile/Episode/Skill 分离、冻结上下文、Candidate→评测→显式晋升/回滚 | 有界门禁机制；历史 Skill 样本有退化，不能宣称总体效果提升 |
| 知识检索 | 分块 BM25、显式 dense 索引与 RRF hybrid、不可变版本及原文位置 | 合成固定语料、手写向量与本机 HTTP；真实向量语义质量未测 |
| 引用问答 | CLI 与独立 Web 知识面板，逐声明引用、拒答、错误分型、分离用量 | 引用真实性通过不等于语义正确，所有结果仍为 semantic_support=unverified |
| 离线问答评测 | prepare/export/evaluate/verify，原始提案、窗口摘要、独立人工标注 | 重放一致性不认证模型/标注者；真实效果需要独立输入和复核 |
| Google Connector | Gmail draft/读取、无参会者日历 hold，复用审批与 reconciliation | 确定性 HTTP contract 与脱敏 exporter；没有新的真实账号成功声明 |

## 安装与最短演示

完整锁定安装适用于 Python 3.12 / Linux x86-64：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade 'pip==26.2.1'
python -m pip install --requirement pylock.toml
python -m pip install 'setuptools==80.9.0' 'wheel==0.45.1'
python -m pip install --no-deps --no-build-isolation --editable .
voren --version
python scripts/demo_agent_loop.py
python scripts/demo_skill_learning.py
python scripts/demo_knowledge_answers.py
```

安装命令与本地脚本不是线上服务部署。其他平台沿用 README 的依赖解析说明。
上面三个演示分别检查行动流程、Skill 门禁、知识引用协议，均不需要线上凭据。

在 Web 页面检查知识来源：

```bash
voren knowledge ingest docs/fixtures/knowledge-quickstart.md \
  --document-id meeting:quickstart --title 'Atlas release notes' \
  --source-uri fixture://voren-1.0/atlas --source-kind meeting_note \
  --reason 'reviewed synthetic quickstart'
voren-web
```

打开 `http://127.0.0.1:8080`，知识面板输入 `Atlas checklist owner` 后先检索。
本地原始提案验证不调用模型；明确选择在线模型并点击请求，才发送问题与召回片段。
来源由本次快照提供。原始 JSON 提案格式见 [0.4 文档](V04_KNOWLEDGE_ANSWERS.zh-CN.md)。
普通行动会话与独立知识问答的输出契约不同；普通总结没有自动获得引用已验证状态。

导入外部模型原始响应做离线问答评测，使用 [0.6 两步流程](V06_ANSWER_EVALUATION.zh-CN.md)。
无标签导出用 opaque case_ref；完整 prepared 内含答案真值，只用于操作者本地评测。

## 发布与验证

版本按 0.3.0 检索、0.4.0 引用问答、0.5.0 恢复校验/知识 Web、0.6.0 离线评测、
1.0.0 完整交付分批快进推送。每批保留独立提交与标签，不覆盖历史。
最终全量测试、原始受控报告与安装包检查记录将在验收后绑定源码提交保存。

## 阅读与讲解

1. [service.py](../../src/voren/web/service.py)、[model_context.py](../../src/voren/web/model_context.py)：
   一个待审批任务恢复时，哪些状态必须匹配？为什么检查必须先于外部写入？
2. [store.py](../../src/voren/knowledge/store.py)、[answers.py](../../src/voren/knowledge/answers.py)：
   活动版本切换为什么不改写原任务证据？为什么真实引用不能保证答案正确？
3. [answer_evaluation.py](../../src/voren/knowledge/answer_evaluation.py)：
   对标签的拒答决策、引用完整性和人工语义支持各自的分母是什么？
4. [Skill lifecycle 文档](PHASE3_SKILL_CLI.zh-CN.md)：一次成功为什么只能成为候选，
   评测与显式晋升各改变了哪些持久状态？

本版本继续限定单个可信操作者、默认本机服务和显式外部动作授权。
多用户认证、后台收件箱轮询、主动定时、更多真实供应商和跨业务自动学习属于未来范围。
内置模型与新 Web Run 的冻结保证不扩展为任意自定义模型隐藏配置或历史 Run 的保证。
