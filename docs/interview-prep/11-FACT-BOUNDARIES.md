# 事实边界与容易说过头的地方

核查日期：2026-09-22。Voren 基线 c76ace8829449137d767b38a654ab92576b5c7f1；RunGuild 基线 a51ccc37004e12f49b21a77c5b3cc7f23267b2e1。

本次依据实际源码、测试定义、Git 记录与仓库文档整理。没有重新运行两个项目的全量测试、真实模型、Google 账户或生产压测。以下“已实现”指找到实现路径；测试引用表示存在对应断言。历史测试通过与历史实验结果必须带原日期和条件。

第二轮新增了端到端源码反查和定向离线验证，已纠正初稿的若干过强断言。最新实测范围、反例及环境阻断详见 [12 源码复核记录](12-SOURCE-RECHECK.md)。

## 1. 证据分层

| 标记 | 表示什么 | 能说到哪里 |
|---|---|---|
| 当前代码事实 | 当前 commit 中有可定位的函数、状态与约束 | “实现中这样处理……” |
| 已有测试定义 | 有输入、故障注入和断言 | “仓库有覆盖此场景的回归用例……” |
| 本轮离线实测 | 在注明的依赖、夹具及临时环境下实际执行 | 只说明该用例的条件和结果，不外推到真实账户、生产并发或完整系统 |
| 历史运行记录 | 已保存的日志、报告或 artifact | “某次固定条件下观察到……” |
| 静态推导风险 | 根据控制流发现可能窗口，本轮未复现 | “这里存在一个需要验证的风险……” |
| 未来设计 | 文档提出的改造或练习 | “如果继续完善，我会……” |
| 个人掌握 | 本人能够解释、定位、修改并提供验证记录 | 根据实际完成范围认领，不由仓库代码自动推定 |

## 2. Voren 核对表

| 容易说过头 | 准确表述与原因 | 证据入口 |
|---|---|---|
| 支持任意位置断点续跑 | Loop 有加密检查点和恢复逻辑；Web 未注入该存储，真实 Responses Adapter 还依赖内存缓存 | [Loop](../../src/voren/runtime/agent_loop.py)、[Web](../../src/voren/web/service.py)、[Provider](../../src/voren/providers/openai_responses.py) |
| 审批后模型继续完成任意多动作任务 | 当前一次外部动作提案使 Loop 返回；审批执行后根据回执结束 Run | [RunManager](../../src/voren/runs/manager.py) |
| UUID 保证外部动作恰好一次 | 稳定身份、账本条件更新、已有回执复用和外部观察共同降低重复风险；外部不确定窗口仍需保守处理 | [Gateway](../../src/voren/actions/gateway.py)、[Ledger](../../src/voren/actions/ledger.py) |
| 任何已观察到的违规都永久阻止成功终态 | 带违规的 verification_failed 不再重查；但提交超时后的首次违规观察被标为 ambiguous，后续干净观察可变成 verified，旧回执仅归档；这个例外已离线复现 | [reconcile 与首次观察](../../src/voren/actions/gateway.py:153) |
| 全部敏感信息都加密落盘 | 加密针对 TranscriptStore；操作提案、回执和 Web 视图等仍有其他明文持久化路径 | [transcripts](../../src/voren/runtime/transcripts.py)、[Web index](../../src/voren/web/index.py) |
| 使用向量数据库实现完整 RAG | 当前 Knowledge 使用版本化文档与确定性词项匹配；向量检索练习是另外的学习任务 | [knowledge/store.py](../../src/voren/knowledge/store.py) |
| MCP 天然保证工具安全 | MCP 接通检索协议；工具权限、来源信任与外部动作审批仍由应用实现 | [MCP adapter](../../src/voren/mcp_bridge/adapter.py) |
| Skill scope 是强制工具沙箱 | 当前 Loop 检查 Skill 所需工具是否可用，并没有据此把整个工具表缩小成权限白名单 | [AgentLoop.__init__](../../src/voren/runtime/agent_loop.py) |
| 能自动从所有对话持续自我进化 | 候选由显式流程提交，修改范围有界，评测接受与正式启用分开；没有通用自动生成与上线后台循环 | [learning/service.py](../../src/voren/learning/service.py)、[policy.py](../../src/voren/learning/policy.py) |
| 所有 Skill 激活都强制通过候选评测 | promote 路径检查候选决策；可信操作者的直接安装/activate 管理路径可以激活版本，不应混成同一安全保证 | [cli.py](../../src/voren/cli.py)、[SkillStore](../../src/voren/skills/store.py) |
| Skill 改动绝对不超过 80 行 | 配置目标是 80 行、16,000 bytes diff；当前计数漏掉以特定前缀开头的正文变化，101 条新增可被记为 1 条；字节上限仍有效 | [准入与行计数](../../src/voren/learning/policy.py:99) |
| held-out 门禁自动排除所有学习原题 | 只比较显式登记的 evaluation_case_ids；operator correction 固定空集合，Run 证据也默认空集合，漏登记的原题不会自动被排除 | [学习证据](../../src/voren/learning/evidence.py:130)、[重叠检查](../../src/voren/learning/policy.py:184) |
| 所有候选评测都强制 raw behavior | AgentDojoSkillEvaluator 强制该模式；通用 PairedEvaluationRunner 依赖注入 evaluator 的正确实现 | [AgentDojo evaluator](../../src/voren/learning/agentdojo.py:94)、[通用 Runner](../../src/voren/learning/runner.py:30) |
| CLI 与 Web 接入能力完全相同 | 自然语言 CLI 保存 Transcript，但没有 Loop 续跑子命令、未拼接 Knowledge MCP，Memory 需显式选择；Web 默认活跃 Profile 且接入 Knowledge，但未注入 Transcript | [CLI 装配](../../src/voren/cli.py:745)、[Web 装配](../../src/voren/web/service.py:200) |
| Google 观察能证明不存在任何额外对象或通知 | 当前只比较适配器返回对象；草稿检索有数量上限且找到匹配后停止，不穷举重复草稿；私人占位指无 attendees、不请求邀请，不保证可见性仅本人 | [Google 观察](../../src/voren/adapters/google_workspace.py:597) |
| completed 就代表答案已被独立验证为正确 | 动作型 Run 有批准效果的回执检查；只读 Run 结束并不自带答案事实正确性的独立判定 | [learning/evidence.py](../../src/voren/learning/evidence.py) |
| Skill 回滚能撤销之前发出的邮件 | 回滚改变 Skill 激活版本，外部动作需要另一套补偿语义 | [learning/store.py](../../src/voren/learning/store.py) |
| Google 邮箱和日程已真实上线 | 连接器支持草稿与私人日历占位，有测试和证据导出；没有真实账号成功 artifact 支持生产声明 | [Google adapter](../../src/voren/adapters/google_workspace.py)、[交付状态](../reviews/2026-09-14-DELIVERY-STATUS.zh-CN.md) |
| 引入 Skill 已经提高模型成功率 | 历史小样本静态 Skill 结果退步；可说明需要评测和回滚，不能包装收益 | [历史模型证据](../evidence/2026-09-14-AGENTDOJO-LIVE.zh-CN.md) |

**新增静态风险：** Web 请求 reservation 已持久化，但进程在 response_json 保存前硬崩溃，重试可能持续落入 in-progress/409。代码中没有完整的占位超时接管路径。这是此次代码阅读的推导，尚未用杀进程实验复现；应与已验证回归分开记录。

## 3. RunGuild 核对表

| 容易说过头 | 准确表述与原因 | 证据入口 |
|---|---|---|
| 每个 Agent 都永久运行并记住全部历史 | Agent 是持续身份；Worker、Task attempt 与 Run 有独立生命周期，上下文按配置和快照加载 | [Agent Worker](/home/sid/runguild/apps/worker/src/agent-loop.ts)、[执行上下文](/home/sid/runguild/packages/database/src/execution-context-repository.ts) |
| 每个 Run 都拥有新的物理隔离环境 | Worktree 以 Task 为单位管理，可能跨重试复用；上下文隔离不等于进程或宿主隔离 | [Worktree manager](/home/sid/runguild/packages/workspace-tools/src/git-worktree-manager.ts) |
| Redis 负责所有任务的可靠存储 | PostgreSQL 保存任务、事件和消息事实；Redis 通知与数据库轮询配合 | [Inbox](/home/sid/runguild/packages/database/src/inbox-repository.ts)、[Scheduler](/home/sid/runguild/packages/database/src/scheduler-repository.ts) |
| 租约过期等于旧进程已经停止 | 令牌检查拒绝关键陈旧写入；旧子进程或外部执行仍需另行约束 | [TaskRepository](/home/sid/runguild/packages/database/src/task-repository.ts) |
| 所有写接口都校验同一个 Task token | 续租检查 Task token 与到期时间；工具 finish 检查 execution_token 和 running 状态，不直接检查到期时间；最终 Task 完成不接收 leaseToken，要按状态/证据/评审/集成门禁逐条核对，证据写入也没有统一的 Task token 检查 | [任务完成](/home/sid/runguild/packages/database/src/task-repository.ts)、[工具完成](/home/sid/runguild/packages/database/src/tool-execution-repository.ts)、[证据写入](/home/sid/runguild/packages/database/src/evidence-repository.ts) |
| 冻结上下文等于冻结实际模型和全部预算 | 数据库快照冻结模型标识，但 Worker 环境可覆盖模型、endpoint、reasoning、输出与上下文预算 | [modelFor](/home/sid/runguild/apps/worker/src/agent-main.ts:190) |
| 用户 cancel 立即中断正在执行的调用 | 用户控制在 Runtime 检查点消费；与 Worker/lease 的 AbortSignal 不同。已用脚本模型复现一批工具中的后续调用在 cancel 被处理前仍执行 | [Runtime 控制](/home/sid/runguild/packages/agent-runtime/src/runtime.ts:626)、[Worker AbortSignal](/home/sid/runguild/apps/worker/src/agent-loop.ts:331) |
| 控制请求持久化后必然可靠应用 | takePendingControls 先标 applied，返回后才结束 Run 或追加 steering；中间崩溃的丢应用窗口仅静态核查，未故障注入 | [控制消费](/home/sid/runguild/packages/database/src/runtime-repository.ts:483) |
| 每个代码任务都强制测试和独立评审 | 门禁检查计划指定的 required criteria/kinds；独立评审由 reviewRequired 控制，计划校验未强制所有 Builder 都包含 test_run | [完成校验](/home/sid/runguild/packages/database/src/completion-verifier.ts:22)、[计划校验](/home/sid/runguild/packages/protocol/src/plans.ts:88) |
| 无评审代码任务也已完整走通集成 | discovery 允许 review_required=false，assertIntegrationLease 却无条件要求 approved review；已用 PGlite 复现该仓储层不一致 | [发现与断言](/home/sid/runguild/packages/database/src/worktree-repository.ts:303) |
| 发布前独立确认当前 HEAD 就是被批准的 HEAD | Manager 比较磁盘 HEAD 与当前 Worktree 记录；审批查询只查 Task 有批准，没有再关联冻结证据中的 commit。不能把该缺口说成已经复现实际 Worker 绕过审批 | [HEAD 比较](/home/sid/runguild/packages/workspace-tools/src/git-worktree-manager.ts:252)、[审批查询](/home/sid/runguild/packages/database/src/worktree-repository.ts:364) |
| 任意 passed=true 测试都可用于完成 | 门禁检查当前尝试或精确提交/代码树、干净稳定状态、证据种类、较新失败及评审绑定 | [evidence-gate.ts](/home/sid/runguild/packages/database/src/evidence-gate.ts) |
| 测试和评审已绑定，所以完整需求必然实现 | 版本对应关系与验收断言是否充分是两个问题；独立 oracle 仍需补强 | [项目审计](/home/sid/runguild/docs/AUDIT_2026-09-14.md) |
| Worktree 已提供执行不可信代码的安全沙箱 | 代码在宿主机执行，路径与命令限制不等于文件、网络、进程和凭据隔离 | [workspace-tools.ts](/home/sid/runguild/packages/workspace-tools/src/workspace-tools.ts) |
| Git 与 PostgreSQL 在同一事务里发布 | 两者存在故障窗口，需要核对、恢复与再次验证；不能许诺跨系统原子提交 | [GitWorktreeManager](/home/sid/runguild/packages/workspace-tools/src/git-worktree-manager.ts) |
| Yjs 保证需求内容正确并解决 Git 冲突 | Yjs 用于协作文档增量与收敛；固定 ArtifactVersion 支持评审；代码由 Git 管理 | [ArtifactRepository](/home/sid/runguild/packages/collaboration/src/artifact-repository.ts) |
| 估计 token 数是严格上限 | 按字节估计的上下文预算可能高估或低估，需结合模型 tokenizer/usage 与余量 | [context-builder.ts](/home/sid/runguild/packages/agent-runtime/src/context-builder.ts) |
| 最后交付窗口禁止修改代码 | 当前 Worker 在最后 6 hop 屏蔽搜索/读文件，但允许验收关键 patch/delete；旧 README 描述更强 | [agent-main.ts](/home/sid/runguild/apps/worker/src/agent-main.ts) |
| 网页任务提交已完全可恢复 | 消息保存与 planning request 仍是两个请求，且客户端调用生成新随机幂等键 | [App.tsx](/home/sid/runguild/apps/web/src/App.tsx)、[api.ts](/home/sid/runguild/apps/web/src/api.ts) |
| 报表成本为零就是没有花钱 | 成本聚合仍可能把价格缺失映射为 0；应区分 unknown/partial/priced | [evaluation-repository.ts](/home/sid/runguild/packages/database/src/evaluation-repository.ts) |
| 实验已证明多 Agent 更快、更省钱 | 现有对照有样本量和实验条件限制，后续单独成功也不能补成公平配对 | [历史真实评测](/home/sid/runguild/docs/REAL_EVALUATION_2026-08-31.md) |

**提示一致性风险：** 对于配置了测试证据要求的代码任务，当前门禁要求测试对应提交后的代码，而一处交付保留提示仍含先验证再 commit 的表述。学习时以当前门禁的实际要求为准；修复提示冲突属于后续工作，本次没有改源代码。

## 4. 本次文档怎样验证

- 检查两个仓库 HEAD、源码入口、测试定义与历史文档的对应关系。
- 检查题号连续、每题答案与证据是否齐全、文件链接是否存在。
- 对学习文档中的具体 Python unittest 类/方法与 Node 测试文件进行静态存在性检查。
- 检查“未来方案、历史记录、当前实现、本次实际执行”是否混用。
- 可执行的小型标准库示例单独检查，不等同于项目回归测试。

**初稿检查记录：** 初稿为 12 份 Markdown 文件；V01—V50、R01—R50、A01—A72 共 172 道核心问答，另有 14 道场景题、10 道经历问题和 14 项练习。当时 504 处本地链接指向 140 个目标；170 处带行号链接在文件范围内。24 个 Python unittest 目标、6 个 Node 测试文件及 14 个 shell 代码块通过文档层检查；L03 的内存 SQLite 示例断言通过。这些统计仅描述初稿，不能替代后续源码复核。72 个网页来源条目保持不变。

**第二轮更新：** 已新增 12 复核记录，实际运行定向 unittest、Node 测试与临时反例；每组环境、通过数和未完成项均在那里记录。修正了学习资料，未修改两个项目的业务源码或提交 Git。发现的实现缺口仍保留在当前基线中，不能把文档修正说成代码问题已修复。

第二轮文档检查覆盖 13 份 Markdown：题号和答案结构连续完整，本地文件链接存在、所引行号在文件范围内；32 处 unittest 目标与 11 处 Node 测试文件引用存在，16 个 shell 代码块通过语法检查，L03 内存 SQLite 示例断言通过。引用检查按出现次数计数，不等同于实际执行的测试数；项目测试结果以 12 和附带日志为准。网页来源沿用初稿，本轮没有重新检索互联网。

仓库后续代码变化时，先更新本文件的基线与边界，再更新问答。读懂旧文档不等于已经核查新版本。
