# 网页来源、用途与查阅边界

**统一查阅日期：2026-09-22。**本文件记录 05 基础问答使用的 56 条技术第一方资料及原论文、工程补习使用的 12 条官方资料，以及 2 个书籍入口、2 个中文提问线索，共 72 个来源条目。条目按页面计数，不等于独立机构数。

本次已进行实际网页搜索，并打开官方文档、原始论文、作者工程文章或通过页内查找读取相关正文。这里保留普通 Markdown 链接，不依赖会话内部引用标识。文档答案是围绕应用岗位重新组织的机制解释和工程推演，没有逐段搬运原文；项目是否实现某机制，以本资料其他文件的仓库证据为准。

## 搜索方法与可用范围

实际检索覆盖“AI Agent 大模型应用 开发 面试 RAG 工具调用 社招 牛客”“bojieli ai-agent-book 深入理解 AI Agent”，以及 function calling / structured outputs / streaming 的 OpenAI 官方域名检索。随后沿官方页面和原论文补齐 RAG、MCP、记忆、安全、评测与训练边界。

中文面经只帮助选择“该准备哪些问题”，**不能证明出题频率、公司实际题库或技术答案正确性**。技术答案使用下面 S01–S55 的官方/原始依据，并明确应用层推论。涉及模型、SDK、价格、上下文长度、缓存、云产品和框架接口的内容可能更新，所以不写死所谓最新推荐模型或固定价格；真正实施时应锁定目标版本再验证。

## 《深入理解 AI Agent》的官方入口

| 编号 | 来源 | 本次确认与用途 | 查阅日期 |
| --- | --- | --- | --- |
| B01 | [李博杰：官方仓库 README](https://github.com/bojieli/ai-agent-book/blob/main/README.md) | 确认作者仓库、中文正文位置及当前主题编排；用于主题导航，没有据此声称已逐章读完或跑通全部实验。 | 2026-09-22 |
| B02 | [官方 Releases](https://github.com/bojieli/ai-agent-book/releases) | 页面提供 latest 滚动预发行；PDF/EPUB 随正文更新覆盖，不能将 stable URL 当成固定版本。 | 2026-09-22 |

本次打开的中文 main README 主题顺序为：1 入门；2 上下文；3 用户记忆和知识库；4 工具；5 Coding Agent 与通用 Agent；6 交互；7 评估；8 后训练；9 持续进化；10 多 Agent。搜索索引中还出现了旧章节排列，因而资料采用主题导航，不据旧目录硬配章号。这里未把滚动 Release 的标签提交当作当前 main 的精确快照；若要复现实验，应另行记录所用 commit、依赖锁文件和模型版本。

## 中文提问线索：不作为技术结论依据

| 编号 | 来源 | 对本题库的作用 | 限制 | 查阅日期 |
| --- | --- | --- | --- | --- |
| I01 | [JavaGuide：AI 应用开发面试指南](https://javaguide.cn/ai/interview-questions/ai-interview-guide.html) | 参考模型调用、RAG、Agent、系统设计的题型划分，对应 A01–A72 的主题选取。 | 第三方整理，未将其描述视为统计结论；答案重新用 primary sources 核验。 | 2026-09-22 |
| I02 | [牛客：阿里 AI Agent 开发岗面经-03](https://www.nowcoder.com/discuss/923740158455730176) | 已打开可见部分，参考切分、召回、状态、MCP/Skill、ReAct、记忆、审批、评测等提问方向，相关 A15、A19–A30、A34–A48、A54、A59、A68、A70。 | 用户投稿/汇编，真实性与代表性未独立证实；页面部分内容付费不可见，没有声称读到其完整内容。 | 2026-09-22 |

## 技术依据及题号映射

以下所有条目的查阅日期均为 **2026-09-22**。官方文档是所述产品/协议的第一方资料，论文是相关机制的原始研究，工程文章是作者团队的经验；它们的实验结果不自动适用于 Sid 的项目。

| 编号 | 官方 / 原始来源 | 支持题号 | 支持内容与边界 |
| --- | --- | --- | --- |
| S01 | [Hugging Face：Causal language modeling](https://huggingface.co/docs/transformers/tasks/language_modeling) | A01 | 下一 token 预测与因果可见范围；应用层推论不代表对模型内部机制的完整解释。 |
| S02 | [Vaswani 等：Attention Is All You Need](https://arxiv.org/abs/1706.03762) | A02 | Transformer、注意力原始论文；现代模型未必沿用原论文全部结构。 |
| S03 | [Hugging Face：Byte-Pair Encoding tokenization](https://huggingface.co/learn/llm-course/chapter6/5) | A03 | 子词合并与 tokenizer；不同模型的词表和编码不同。 |
| S04 | [OpenAI：Conversation state](https://developers.openai.com/api/docs/guides/conversation-state) | A04 | 上下文、输出和推理 token 预算以及会话状态；具体计费/保存规则随产品变动。 |
| S05 | [Hugging Face Transformers：Generation](https://huggingface.co/docs/transformers/main_classes/text_generation) | A02、A05、A31 | temperature、top-p、采样和 KV cache；不能据此推定每家 API 支持相同参数。 |
| S06 | [OpenAI：Prompt engineering](https://developers.openai.com/api/docs/guides/prompt-engineering) | A07 | 指令、示例和相关上下文；提示不是访问控制。 |
| S07 | [OpenAI：Model selection](https://developers.openai.com/api/docs/guides/model-selection) | A08、A46 | 质量、成本、延迟取舍，RAG 与行为优化；未采用供应商示例效果数字。 |
| S08 | [OpenAI：Streaming API responses](https://developers.openai.com/api/docs/guides/streaming-responses) | A09 | SSE、增量事件、流式消费；首字时间与总耗时的区别。 |
| S09 | [OpenAI：Background mode](https://developers.openai.com/api/docs/guides/background) | A10 | 后台任务、取消及断线后运行；普通请求不能直接套用后台模式语义。 |
| S10 | [OpenAI：Rate limits](https://developers.openai.com/api/docs/guides/rate-limits) | A11、A12 | 速率限制、指数退避和抖动；重试安全还取决于外部副作用。 |
| S11 | [OpenAI：Latency optimization](https://developers.openai.com/api/docs/guides/latency-optimization) | A13、A41 | 请求数量、token、并行与查询上下文化；优化收益需实测。 |
| S12 | [OpenAI：Structured model outputs](https://developers.openai.com/api/docs/guides/structured-outputs) | A14 | JSON mode 与 schema 约束、拒绝/截断边界；不保证业务事实正确。 |
| S13 | [OpenAI：Function calling](https://developers.openai.com/api/docs/guides/function-calling) | A15、A16 | 模型提出调用、应用执行、回传结果；Responses 与 Chat Completions 协议字段不同。 |
| S14 | [MCP：Tools，2025-11-25 规范](https://modelcontextprotocol.io/specification/2025-11-25/server/tools) | A17、A18 | 工具输入输出、执行错误、安全校验；固定版本作为解释依据，不声称是最新版本。 |
| S15 | [Anthropic：Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) | A19、A48 | 工作流与 Agent 的工程区分、路由/协作模式；术语不是行业统一标准。 |
| S16 | [Yao 等：ReAct](https://arxiv.org/abs/2210.03629) | A20 | 推理与动作交错的原始研究；未把论文收益转述为项目收益。 |
| S17 | [LangGraph：Workflows and agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents) | A21 | 协调者与 worker、计划及汇总；框架提供机制，任务设计仍由应用负责。 |
| S18 | [LangGraph：Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api) | A22、A49、A51 | 状态、节点、边、并行与 reducer；业务 DAG 不等于所有 Agent 图都无环。 |
| S19 | [LangGraph：Persistence](https://docs.langchain.com/oss/python/langgraph/persistence) | A23、A72 | checkpoint、thread 与恢复；不能据此承诺第三方副作用恰好一次。 |
| S20 | [Anthropic：Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) | A24、A27 | 工具语义、名称、响应体量、描述与评测。 |
| S21 | [MCP：Architecture，2025-11-25 规范](https://modelcontextprotocol.io/specification/2025-11-25/architecture) | A25 | host/client/server、能力协商与隔离；MCP 本身不等于完整 Agent。 |
| S22 | [Agent Skills：Specification](https://agentskills.io/specification) | A26 | SKILL.md、资源与渐进披露；不同宿主实现仍有差异。 |
| S23 | [Anthropic：Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) | A28、A32 | 上下文选择、压缩、结构化笔记；摘要是有损表示。 |
| S24 | [LangChain：Memory overview](https://docs.langchain.com/oss/python/concepts/memory) | A29、A30、A33 | 会话与跨会话记忆、命名空间；写入准入和失效为本文应用设计延伸。 |
| S25 | [OpenAI：Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching) | A31 | 缓存前缀与复用、压缩影响、模型差异；不写死阈值/TTL/默认模式。 |
| S26 | [Liu 等：Lost in the Middle](https://arxiv.org/abs/2307.03172) | A04 | 长上下文中的位置敏感性；历史模型实验不能推成所有现役模型相同退化幅度。 |
| S27 | [Lewis 等：Retrieval-Augmented Generation](https://arxiv.org/abs/2005.11401) | A06、A34、A46 | 检索与生成组合的原始研究；本文还讨论广义应用 RAG，不限定原论文训练架构。 |
| S28 | [Microsoft：Chunk documents for RAG](https://learn.microsoft.com/en-us/azure/search/vector-search-how-to-chunk-documents) | A35 | 按结构/长度/语义切分和 overlap；示例尺寸不是通用最优值。 |
| S29 | [OpenAI：Vector embeddings](https://developers.openai.com/api/docs/guides/embeddings) | A36、A37 | 向量表示、相似度和该提供商归一化性质；不得泛化到所有 embedding。 |
| S30 | [Elastic：Similarity / BM25](https://www.elastic.co/docs/reference/elasticsearch/index-settings/similarity) | A37 | 词频饱和、文档长度归一化；不是简单关键词出现次数。 |
| S31 | [Elastic：Reciprocal rank fusion](https://www.elastic.co/guide/en/elasticsearch/reference/current/rrf.html) | A38 | 按排名融合与参数；不同检索器原始分数不可随意相加。 |
| S32 | [pgvector：项目 README](https://github.com/pgvector/pgvector) | A39 | 精确检索、HNSW/IVFFlat、过滤对近似召回的影响。 |
| S33 | [Sentence Transformers：Retrieve & Re-Rank](https://www.sbert.net/examples/applications/retrieve_rerank/README.html) | A40 | bi-encoder 召回与 cross-encoder 重排的计算取舍。 |
| S34 | [Sentence Transformers：InformationRetrievalEvaluator](https://www.sbert.net/docs/package_reference/sentence_transformer/evaluation.html) | A42 | MRR、Recall@k、NDCG 和 relevant_docs 标注。 |
| S35 | [Es 等：Ragas](https://arxiv.org/abs/2309.15217) | A43 | 检索上下文、忠实性和生成质量的分维度评估；自动指标不是最终真值。 |
| S36 | [Microsoft：Query-time permission enforcement](https://learn.microsoft.com/en-us/azure/search/search-query-access-control-rbac-enforcement) | A44、A72 | 查询身份与文档 ACL；页面包含预览功能，不将其当作所有搜索服务能力。 |
| S37 | [Microsoft：Update or rebuild an index](https://learn.microsoft.com/en-us/azure/search/search-howto-reindex) | A45 | 更新、重建及 alias 切换；版本一致性是应用设计责任。 |
| S38 | [Anthropic：How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) | A47、A50 | 可并行任务、上下文隔离、协调和成本；其收益不能迁移为 RunGuild 实测。 |
| S39 | [Anthropic：Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) | A52 | 增量交付、进度产物和过早宣布完成的失败模式。 |
| S40 | [AWS Builders' Library：Making retries safe with idempotent APIs](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/) | A55 | 稳定客户端请求标识、语义等价与未知结果对账；业务补偿为应用层延伸。 |
| S41 | [LangGraph：Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) | A54 | 持久化暂停与人工恢复机制；审批绑定摘要等属于本文工程设计，不是框架自动保证。 |
| S42 | [Debenedetti 等：AgentDojo](https://arxiv.org/abs/2406.13352) | A56 | 外部工具数据中的 prompt injection 与任务/攻击评估；不证明任何防护绝对有效。 |
| S43 | [MCP：Security best practices，2025-11-25 规范](https://modelcontextprotocol.io/specification/2025-11-25/basic/security_best_practices) | A57 | 身份/授权、confused deputy、SSRF 等边界；不是部署安全审计结果。 |
| S44 | [OpenTelemetry：Traces](https://opentelemetry.io/docs/concepts/signals/traces/) | A58、A65 | trace/span、关联和耗时；模型字段及回放方法为应用设计。 |
| S45 | [OpenAI：Evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices) | A59、A64、A72 | 按任务定义指标、测试数据、持续评估和多 Agent 引入的额外变化。 |
| S46 | [scikit-learn：Common pitfalls / Data leakage](https://scikit-learn.org/stable/common_pitfalls.html) | A60、A63 | 数据泄漏、先拆分再学习和可比数据集；延伸到 Prompt/Skill 选择时同样需要隔离。 |
| S47 | [SciPy：binomtest](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.binomtest.html) | A61 | 二项检验与 exact proportion confidence interval；样本独立性假设不可省略。 |
| S48 | [Zheng 等：Judging LLM-as-a-Judge](https://arxiv.org/abs/2306.05685) | A62 | 位置、篇幅和自偏好等裁判偏差；不同领域需重新校准。 |
| S49 | [Anthropic：Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) | A52、A59、A61 | task/trial/grader、轨迹与环境最终状态、多次试验；没有采用厂商性能数字。 |
| S50 | [Ouyang 等：Training language models to follow instructions with human feedback](https://arxiv.org/abs/2203.02155) | A66、A67 | 示范数据 SFT 与偏好/RLHF 路径；不声称所有现代模型训练流程相同。 |
| S51 | [Hu 等：LoRA](https://arxiv.org/abs/2106.09685) | A68 | 冻结基座与低秩增量；不把论文资源节省数字当作任何任务保证。 |
| S52 | [Shinn 等：Reflexion](https://arxiv.org/abs/2303.11366) | A69 | 用语言反馈和经验记忆改进决策的路线；经验更新与参数训练需区分。 |
| S53 | [LangGraph：Overview](https://docs.langchain.com/oss/python/langgraph/overview) | A70 | 持久执行、人工介入等框架关注点；语言选型是本文结合 Sid 背景的建议。 |
| S54 | [Rafailov 等：Direct Preference Optimization](https://arxiv.org/abs/2305.18290) | A67 | 从偏好对直接优化策略的原始论文；不把 DPO 描述成所有 RLHF 系统的固定步骤。 |
| S55 | [Stripe：Idempotent requests](https://docs.stripe.com/api/idempotent_requests) | A53 | 幂等键、已有结果与参数一致性；具体过期/错误语义是产品约定，不能泛化。 |
| S56 | [Hugging Face：Quantization overview](https://huggingface.co/docs/transformers/quantization/overview) | A68 | 较低精度数值表示与模型存储、计算的关系；量化与低秩适配器训练不是同一机制。 |

## 容易被误读的证据边界

- A19 的 Agent / Workflow 区分采用明确的工程工作定义；不同团队用法可能不同，不靠术语争输赢。
- A25–A27：MCP、工具调用和 Skill 分别解决连接、调用表达和任务知识组织，任何一层都不自动授予业务权限。MCP 解释引用固定 2025-11-25 规范，没有将其称为最新规范。
- A31：本次 Prompt caching 文档已有模型代际差异；不沿用旧“达到固定 token 阈值就自动命中”的口诀。
- A34–A46：RAG、向量索引、ACL 等是通用知识与设计方案；不能据此把 Voren 当前的关键词检索说成已经实现向量检索。
- A47、A50：Anthropic 多 Agent 文章中的效果数据仅属于其任务、模型和评估条件；本资料未把它转为 RunGuild 的成功率或提速承诺。
- A53–A55：第三方 API 的幂等保存时间、取消和重试语义是接口契约；本地状态机不能自动制造跨系统原子性。审批提案绑定和未知结果处置是基于机制的工程设计推演。
- A61：6/6 的区间示例采用独立同分布二项试验假设；真实任务相关时不能机械套用。该数字是统计算例，不是新做的一次项目测试。
- A66–A69：解释 SFT、DPO、LoRA、反思和经验更新，不表示已有训练或持续学习上线经历。源论文的收益数字没有转为个人履历。
- 本次没有进行付费 API 调用、模型训练、真实邮箱/日历操作或书中实验复现；网页阅读与实现验证是不同证据。

## 工程补习与实验来源

以下页面已查阅。它们解释通用机制，项目是否使用及使用到什么程度仍以源码为准。PostgreSQL 采用与 RunGuild 容器配置对应的 17 版文档；其余页面的版本要求应与本地依赖一起核对。

| 编号 | 官方来源 | 用途与对应文档 | 查阅日期 |
|---|---|---|---|
| P01 | [Python 3.12 Tutorial](https://docs.python.org/3.12/tutorial/) | 06 第 3 节、A71：数据结构、函数、异常、类与模块，按项目需要补语言基础。 | 2026-09-22 |
| P02 | [Pydantic Models](https://docs.pydantic.dev/latest/concepts/models/) | 06 第 3 节、L02：模型构造、类型转换与数据校验；不能替代业务授权。 | 2026-09-22 |
| P03 | [Python 3.12 Coroutines and Tasks](https://docs.python.org/3.12/library/asyncio-task.html) | 06 第 13 节：协程、任务、等待、取消和超时的语义。 | 2026-09-22 |
| P04 | [FastAPI Concurrency and async / await](https://fastapi.tiangolo.com/async/) | 06 第 13 节：同步与异步处理、阻塞工作及线程池边界。 | 2026-09-22 |
| P05 | [TypeScript Everyday Types](https://www.typescriptlang.org/docs/handbook/2/everyday-types.html) | 06 第 9 节、L09：对象、联合类型、可选属性和类型收窄的阅读入口。 | 2026-09-22 |
| P06 | [Node.js Event Loop, Timers, and process.nextTick](https://nodejs.org/en/learn/asynchronous-work/event-loop-timers-and-nexttick) | 06 第 9 节：事件循环与异步调度；单线程不等于不存在异步竞争。 | 2026-09-22 |
| P07 | [PostgreSQL 17 Transaction Isolation](https://www.postgresql.org/docs/17/transaction-iso.html) | 06 第 10 节、S03、L10：事务可见性、竞争更新与隔离级别。 | 2026-09-22 |
| P08 | [PostgreSQL 17 SELECT](https://www.postgresql.org/docs/17/sql-select.html) | 06 第 10 节、L10：FOR UPDATE 与 SKIP LOCKED 的队列领取用途及限制。 | 2026-09-22 |
| P09 | [SQLite Write-Ahead Logging](https://www.sqlite.org/wal.html) | 06 第 10 节、L03：读写并发与单写者限制，不能把 WAL 理解成任意多写者并行。 | 2026-09-22 |
| P10 | [Redis Pub/Sub](https://redis.io/docs/latest/develop/pubsub/) | 06 第 11 节：通知的至多一次投递语义，与数据库事实、重试和轮询的分工。 | 2026-09-22 |
| P11 | [Git worktree](https://git-scm.com/docs/git-worktree) | 06 第 12 节、L12：多个工作目录与分支管理；不提供操作系统级隔离。 | 2026-09-22 |
| P12 | [Yjs Document Updates](https://docs.yjs.dev/api/document-updates) | 06 第 12 节、L13：文档更新、状态向量、合并与幂等性；业务审批仍需额外机制。 | 2026-09-22 |
