# 0.3.0：分块引用、混合检索与固定语料评测

[English](V03_KNOWLEDGE_RETRIEVAL.md)

2026-10-08。本切片解决原关键词检索命中长文后半段、但摘要返回文档开头的问题。
默认改为分块 BM25；同一来源只返回评分最高的一个片段。保留 `lexical` 旧基线，
增加显式建索引的 `dense` 向量检索和 `hybrid` RRF 融合。它是已有邮件/日程 Agent
的知识读取能力，不是另一个自动问答或自动行动系统。

## 无凭据使用

在已安装仓库依赖的 Python 3.12 环境中：

```bash
python scripts/evaluate_knowledge_retrieval.py --k 5
voren knowledge search '审批 回执' --mode bm25
voren knowledge search '审批 回执' --mode lexical
```

评测脚本默认安装固定合成语料到临时 SQLite，不修改日常数据库，不调用模型。
输出包含原始排名、来源版本、证据判分、数据集与报告摘要，默认写入
`.voren/artifacts/knowledge-retrieval.json`。该路径下的运行数据不进入 Git。
`knowledge search` 查询既有活动文档；导入和激活沿用已有 `knowledge ingest` 流程。

本地 Web 与 MCP 默认使用 BM25。更换已有 Run 的检索模式或 Embedding 指纹会阻止继续执行；
恢复原配置即可继续。没有新字段的历史 Web Run 继续使用旧 `lexical` 行为。
读取已经保存的最终结果不要求重新配置检索或调用模型。

## 明确启用向量接口

配置独立的 `VOREN_EMBEDDING_ENDPOINT`、`VOREN_EMBEDDING_MODEL` 和
`VOREN_EMBEDDING_API_KEY`。接口必须兼容
[官方 Embeddings 请求与响应格式](https://developers.openai.com/api/reference/resources/embeddings/methods/create)。
Endpoint 是完整 `/embeddings` URL，使用 HTTPS；本机 HTTP 可用于自控服务。
不会借用聊天模型的 API Key，也没有默认在线模型。

```bash
voren knowledge index --allow-embedding-api
voren knowledge search '审批 回执' --mode dense --allow-embedding-api
voren knowledge search '审批 回执' --mode hybrid --allow-embedding-api
```

建索引发送活动文档的标题和片段；查询发送查询文本。CLI 必须显式选择上述参数。
Web/MCP 将 `VOREN_KNOWLEDGE_RETRIEVAL_MODE` 明确设置为 `dense` 或 `hybrid` 即启用
查询 API，需事先建好匹配索引。这些运行可能产生供应商费用。
`VOREN_EMBEDDING_DIMENSIONS` 可选；模型需支持该参数。
返回模型标识必须等于配置标识，不静默接受不同模型。

## 引用与排序

- 只读取显式活动文档版本，源文件内容与元数据摘要仍在读取时验证。
- 固定 600 字符片段、100 字符重叠。位置按 Python Unicode 字符计算，基于导入时
  `.strip()` 后的不可变内容；不是原 PDF 的页码、字节位置或未规范化源文件偏移。
- 片段 ID 绑定文档版本、起止位置和内容摘要。`snippet` 是源内容的精确切片，
  返回 `chunk_start`、`chunk_end`、`chunk_digest`，同时保留整份文档摘要与来源 URI。
- BM25 使用词频、逆文档频率和片段长度归一化。中英文词项保持确定性；中文二元组不跨标点。
- Dense 使用归一化向量的余弦相似度，仅保留正相似度候选；这不是校准过的无答案阈值。
- Hybrid 对两个排名使用 RRF（`k=60`），不直接相加 BM25 和余弦分数。
- `limit` 仍表示文档数量。按文档去重，每份文档选一个最佳片段，不能用长文填满所有结果。
- `ranking_score` 保留算法分数；兼容整数 `score` 是缩放显示分，均不是置信度或成功概率。
- 工具结果始终是 `external_untrusted`、`instruction_authority=false` 数据；检索命中不能授权写动作。

## 索引与失败行为

向量索引写在既有 SQLite 的新增派生表，不改动不可变文档。指纹包含 Endpoint、Model、
Dimensions 和接口契约，不包含 Key；轮换 Key 不要求重建索引。
活动来源版本变化、新增文档或模型指纹变化后，向量模式要求覆盖全部当前活动片段。
索引不足、内容/向量摘要不匹配、维度改变、零向量或非有限数值会报错；不自动建索引或降级。

每批至多 32 个文本，一次索引至多 2,000 个新增片段；已匹配片段不重复嵌入。
所有新片段成功后才一起提交到 SQLite；中途失败不留下半成品索引。
API 请求没有自动重试或重定向，错误不包含供应商正文、原文或 Key。
每个 HTTP Provider 对象默认最多 128 次尝试，超时默认 10 秒；可通过 `.env.example` 中
的独立设置调整。这个计数不跨进程持久化，不是整个 Run 的统一付费预算。
已向供应商发出的建索引请求即使本地没有提交也可能产生费用。

## 验证与结果范围

本地全量回归 **314 项全部通过**（90.612 秒，无跳过项），见
[测试日志](../evidence/2026-10-08-v03-tests.log)。`pip check`、JavaScript 语法和本切片
文档的 141 个本地链接检查通过。安装后的包版本为 0.3.0，未增加依赖。
固定语料的[原始评测报告](../evidence/2026-10-08-v03-retrieval.json)绑定干净源码提交
`4b202bdcdf883c9767955bb49927d907e97c60d7`；报告完整性与逐条指标已重新核验。

固定样例为 20 份活动文档、28 个问题：22 个有标注证据的问题，6 个无答案问题。
它包含长文尾部、中文、干扰文档、重复词项、未激活及修订版本。
该合成数据集与功能共同编写，用于机制验证，不是独立业务效果或语义质量证明。

本地 `k=5` 对照中，两个模式文档 Recall@5 和 MRR 均为 1；旧关键词模式包含完整标注证据的
问题为 20/22，BM25 为 22/22。两个模式的无答案问题均有 3/6 返回结果。
因此本切片改善了这些长文样例的证据呈现，没有证明能正确拒答，也不把“返回文档”当成回答正确。
全部返回来源在评测中匹配当前活动版本。延迟仅记录本次本机运行，不用于性能收益声明。

向量与融合的单元测试使用手写向量；HTTP 集成测试使用本机测试服务。
它们检查协议、排序和失败边界，不能证明真实 Embedding 的语义效果。
本轮没有新在线模型、真实 Embedding 或 Google 账号运行证据。
真实语料的质量评测、独立重排器、生成答案及引用正确性评分、无答案决策、OCR 与大规模向量索引均未纳入。

## 阅读顺序

1. [retrieval.py](../../src/voren/knowledge/retrieval.py)：为什么分块能让模型看到长文中的证据？RRF 为什么使用排名？
2. [store.py](../../src/voren/knowledge/store.py)：活动版本切换后，旧向量为何不能进入结果？失败索引何时写入？
3. [embeddings.py](../../src/voren/knowledge/embeddings.py)：配置指纹和输入顺序各防止什么混用？请求失败为什么不自动重试？
4. [evaluation.py](../../src/voren/knowledge/evaluation.py)：文档命中、证据命中和无答案误召回为何分别统计？
5. [检索测试](../../tests/test_chunk_retrieval.py)、[评测脚本](../../scripts/evaluate_knowledge_retrieval.py)：哪些是机制证据，哪些仍需真实语料？
