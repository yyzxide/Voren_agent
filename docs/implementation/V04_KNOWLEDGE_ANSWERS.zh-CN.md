# 0.4.0：引用校验的知识问答与冻结语料

[English](V04_KNOWLEDGE_ANSWERS.md)

2026-10-08。0.3.0 的固定样例中，6 个无答案问题仍有 3 个返回检索结果。
排名、文档命中和真实引用都不能单独证明资料包含答案。本切片新增独立的
`knowledge ask`：模型可以根据本次证据选择回答或拒答，应用校验逐条引用；
新 Web Run 同时记录知识版本，避免等待审批期间激活新资料改变原任务的来源。

## 无凭据使用

安装已有依赖后运行受控协议演示：

```bash
python scripts/demo_knowledge_answers.py
```

演示使用临时 SQLite 与预先编写的 JSON 提案，不修改日常数据库、不外呼。
默认报告写入 `.voren/artifacts/knowledge-answers.json`，包含提案、原始结果、
语料版本、预期状态、源码版本及完整性摘要。它检查协议行为，不测真实模型判断力。

查询既有活动资料时可先查看召回的片段，再用本地 JSON 提案验证引用：

```bash
voren knowledge search '审批 回执' --mode bm25
voren knowledge ask '审批 回执' --draft proposal.json
```

提案只有 `status`、`claims` 和可选 `reason`。回答的 `status` 是 `answer`，
每条 claim 包含 `text` 与非空 `citations`。引用字段为当前检索片段的 `hit_id`
（等于 `chunk_id`）、精确 `quote`、绝对字符位置 `start` 和 `end`。
来源 URL、标题和版本由应用从真实 hit 填入，模型不能提供这些字段。
拒答提案示例：

```json
{"status":"abstain","claims":[],"reason":"召回片段没有所需信息。"}
```

`--draft` 只验证所给提案，不自动生成答案。默认 BM25 不调用任何在线接口。
成功回答与主动拒答返回退出码 0；非法提案、检索或模型故障、取消返回 2。
参数、配置或提案文件读取失败沿用 CLI 错误处理。

## 显式使用模型与向量接口

```bash
voren knowledge ask '审批 回执' --allow-model-api --model MODEL_ID
voren knowledge ask '审批 回执' --allow-model-api --model MODEL_ID --mode hybrid --allow-embedding-api
```

模型沿用已有 Responses adapter 和凭据配置；不提供默认在线模型。
`--allow-model-api` 允许发送问题与召回片段，可能产生供应商费用。
Dense/hybrid 另需 `--allow-embedding-api`、独立向量配置与覆盖冻结语料的既存索引，
参见 [0.3.0 配置与索引边界](V03_KNOWLEDGE_RETRIEVAL.zh-CN.md)。它不自动建索引、重试或降级。
`--draft` 使用 dense/hybrid 时也必须显式允许发送查询给向量接口。

应用提示模型返回 JSON，并在返回后严格校验；本切片没有启用供应商端的
Structured Outputs，也没有新增 SDK。格式校验与内容正确性不同，参见
[官方 Structured Outputs 文档](https://developers.openai.com/api/docs/guides/structured-outputs)。

## 结果与引用契约

1. 捕获并校验当前活动版本集合，在同一冻结语料上检索。一个请求至多 20 个来源片段，
   证据 JSON 与提案各至多 64,000 UTF-8 字节；超限明确失败。
2. 空检索窗口直接返回 `abstained`，原因是 `no_retrieved_evidence`，不调用模型。
   它不表示整个知识库或现实世界没有答案。
3. 非空窗口至多调用模型一次，不提供工具，不自动修复或重新生成提案。
   多余字段、重复 JSON key、空回答、无引用 claim 与模型 tool call 都会被拒绝。
4. 每条引用必须属于本次召回窗口，位置在对应片段内，并同时匹配不可变正文与片段。
   起止位置沿用导入时规范化正文的 Python Unicode 字符下标，`end` 不包含尾字符。
   伪造 hit、原文、位置、来源元数据或 chunk 身份均不能获得已验证引用。
5. 成功结果是 `answered`、`citation_integrity=verified`；应用填入来源版本、
   整份内容摘要和引用摘要，生成可读文本。所有结果均保留 `semantic_support=unverified`。

其余状态分别是主动拒答 `abstained`、非法提案 `rejected`、基础设施故障 `failed`、
已取消 `cancelled`。后面三种保留有界错误码，不伪装成“资料没有答案”，不回显供应商异常正文。
`usage` 记录模型请求与已报告 token；`embedding_usage` 单独记录本次查询向量请求与已报告
用量。缺失计量明确标记未知或不完整，不能将未报告用量解释成零成本。
向量计数取 provider 实例本次前后差值，不能并发共享同一实例做用量归属；常规 CLI 使用独立实例。
取消会阻止模型开始或抑制返回后的答案；既有向量接口的在途 HTTP 请求仍受自身超时控制。

该服务是独立的一次知识问答，不是持久 Agent Run，也没有修改通用 AgentLoop 的自由文本
总结、邮件/日程行动或预算恢复。读取工具仍只返回 `external_untrusted` 数据，不在内部调用回答模型。

## Web / MCP 语料冻结

新 Web 任务的 `metadata["knowledge_corpus"]` 保存按 document ID 排序的唯一版本引用及摘要，
不复制文档正文。此集合在首次创建 MCP 工具与持久 Run 时一致。审批期间切换活动版本或新增
文档，不改变本 Run 的检索成员和版本；冻结为空的集合保持为空。

恢复、批准与继续前重新解析源版本；dense/hybrid 同时离线检查冻结片段的既存向量完整性，
不发送查询或修复索引。来源或索引缺失、损坏时要求恢复原始证据，批准前不派发动作。
拒绝待审批提案仍可继续完成拒绝操作。恢复是多步流程，检查后发生的数据库破坏仍可能导致
后续读取失败；它不是数据库与远程动作之间的分布式事务。

已有检索模式与 provider 指纹的冻结规则保留。历史 Run 没有 `knowledge_corpus` 时使用原来
实时活动版本行为，不补造不存在的历史快照。独立 MCP 未传入快照也继续查询活动版本。
通用 CLI Agent Run / Google CLI 的知识读取入口没有在本轮新增冻结保证。

## 验证范围

完整本地回归 **378 项全部通过**（107.100 秒，无跳过项），包含本机 HTTP/MCP 测试，见
[完整日志](../evidence/2026-10-08-v04-tests.log)。`pip check`、JavaScript 语法、Python 编译和
本切片 6 份文档的 151 个本地链接检查通过；
安装版本与源码版本均为 0.4.0。

[协议演示原始报告](../evidence/2026-10-08-v04-answers.json)绑定干净源码提交
`d212e24e825472c5467fb57c992d4f9f56b9c92d`（`code_dirty=false`）；10 个场景全部符合预设
协议状态，3 个非法引用均拒绝，1 个语义反例仍被接受并标记未验证。报告摘要与所有结果模型已重新校验。

协议演示的 10 个预设场景包括正常回答、主动拒答、空窗口、未知 hit、伪造原文、错误位置、
无引用 claim、意外工具调用与来源激活切换。3 个非法引用应被拒绝。
另有刻意构造的反例：虚构的号码引用真实的负责人句子，仍通过引用校验，且语义状态保持
`unverified`。这条反例用于展示边界，不能计作语义正确答案。

本轮没有在线模型、真实 Embedding 或 Google 账号新运行，未新增依赖。
旧固定检索样例的无答案误召回 3/6 仍是独立历史测量，不能被协议演示替换为新的拒答准确率。
真实问答正确率、缺失答案识别、检索覆盖率与拒答率需要独立语料、真实模型和人工语义标注。

## 阅读顺序

1. [models.py](../../src/voren/knowledge/models.py)、[store.py](../../src/voren/knowledge/store.py)：
   为什么冻结的是版本引用集合，空集合与没有快照为何不同？
2. [answers.py](../../src/voren/knowledge/answers.py)、[citations.py](../../src/voren/knowledge/citations.py)：
   一个模型提案怎样变成已验证引用？真实引用为什么仍可能支撑错误结论？
3. [service.py](../../src/voren/web/service.py)、[冻结恢复测试](../../tests/test_knowledge_snapshot_integration.py)：
   来源更新、索引丢失与拒绝审批分别改变哪些状态，动作什么时候还不能派发？
4. [协议演示](../../scripts/demo_knowledge_answers.py)、[问答测试](../../tests/test_knowledge_answers.py)：
   哪些失败是引用问题，哪些需要语义判断或真实供应商证据？
