# RunGuild / Voren：作品集审计与交付计划

审计日期：2026-09-14。性质：代码抽查、现有测试重跑、针对性故障复现、产品与交付计划复核，不是完整安全审计。本报告记录的是提交 `f5d6f85` 时的发现，不代表当前仓库仍存在这些问题。

> **历史报告提示：** V1–V4 及 C1 中与 Voren 相关的部分，后续已经完成代码整改和
> 确定性回归；当前证据、对应提交和仍未越过的 Live/Credential 门槛见
> [2026-09-14 交付状态](2026-09-14-DELIVERY-STATUS.zh-CN.md)。本文件保留原始缺陷描述，
> 用于展示“发现 -> 修复 -> 回归”的过程，不以事后结果改写审计现场。

## 1. 本次证据基线

| 项目 | 审计版本 | 本次实测 | 可得出的结论 |
| --- | --- | --- | --- |
| RunGuild | `c4b4457` | `npm test`：构建成功；199 项，198 通过、1 跳过、0 失败，约 151 秒 | 现有单元、PGlite、协议、API 等回归通过；没有覆盖所有真实并发和浏览器交互 |
| Voren | `4b14ba5` | 含 AgentDojo 的 `unittest discover`：85 项通过；Scripted Model Demo 的 `user_task_18 utility=True` | 确定性契约和黄金切片可运行；不证明真实模型规划质量或真实账号操作 |

Voren 本机原本缺少 Python 依赖及 `venv/ensurepip`。本次通过 `pip --target /tmp/voren-audit-deps-20260914` 隔离安装声明依赖，以 `PYTHONPATH=/tmp/voren-audit-deps-20260914:src` 执行测试。尚未生成可跨机器使用的完整依赖锁文件。

RunGuild 的外部 PostgreSQL 测试要求 `TEST_DATABASE_URL` 指向独立 `_test` 库；本次没有配置，因此跳过。它会清空测试库，不能对演示库运行。

本次本地部署检查：RunGuild Compose 无运行容器，4173/4000 均无法连接。因此不能沿用 9 月 6 日“服务已部署且可访问”的说法。本次没有启动生产账号连接、没有新发起付费模型实验、没有改变历史 Mission 或评审状态。

Git：审计开始时两个工作树干净；RunGuild 本地 `main` 相对本地 `origin/main` 引用领先 15 个提交，Voren 无领先提交。未执行 fetch，不能据此断言 GitHub 的实时状态。

## 2. 定位：保留两个项目，改变收尾重心

RunGuild：面向软件工程任务的持久化多 Agent 执行与交付平台。重点是任务依赖调度、Worker 恢复、证据审查和精确 Git 集成。

Voren：面向邮件与日程的单 Agent 助手。重点是有来源的检索上下文、可核验的外部动作，以及可拒绝、可回滚的 Skill 候选学习。

Agent Loop、Tool Calling、Harness、Context、Trace、Approval 是共同基础，重复实现部分基础不构成定位失败。两者应有不同业务演示与核心追问，不应都讲成“自研通用 Agent 框架”。

自研运行时有学习价值；使用 LangGraph 也有就业价值。保留已实现核心，在同一小场景做 LangGraph 对照，不要求把 RunGuild 改为 Python，也不把 Dify、AutoGen、CrewAI 全部加入依赖。

## 3. 必须处理的发现

### V1 / P1：动作领取不是原子竞争

位置：`src/voren/actions/ledger.py:93` 和 `:125`。

`mark_committing` 先读取 `authorized`，随后按 operation_id 无条件 UPDATE。两个独立 SQLite connection 可以同时读到 authorized，并都认为领取成功。Run Store 的 version 条件更新并不能保护这个单独的 Operation Ledger。

本次在临时数据库对读取与更新之间施加线程 barrier，两个 connection 的结果均为 `claimed`，而预期只允许一个成功。这是动作派发风险的可复现证据；没有在真实邮箱里执行双重动作。Fake Adapter 自身也会去重，因此不能把此复现直接表述成真实邮箱已重复发送。

复现脚本：`scripts/audits/action_ledger_20260914.py`。安装依赖后用 `PYTHONPATH=src python3 scripts/audits/action_ledger_20260914.py` 执行；它只创建临时数据库与 Fake World，输出观察结果，不是已通过的修复回归测试。修复后应将这些场景转换为断言新行为的正式测试。

修复验收：以单条条件 UPDATE 或短事务完成 `authorized -> committing`，检查 affected rows；授权和回执写入也要校验合法前态/归属。两进程竞争只能有一个获得外部派发权，失败方不能覆盖回执。外部调用不放在长期 SQLite 写事务里。

### V2 / P1：外部提交后、Receipt 前的崩溃缺乏恢复路径

位置：`src/voren/actions/gateway.py:105`、`src/voren/runs/manager.py:327`。

本次在 Fake Workspace 执行完动作后、保存 Receipt 时模拟中断，再重开 Ledger：外部已有 1 个 event，状态停在 `committing`，无 Receipt；重试 commit 得到 `InvalidOperationStateError`。`recover_pending_receipt` 也只接受已经存在的 durable Receipt。

现有测试覆盖的是明确抛出 `AmbiguousCommitError` 后观察结果，以及 Receipt 已落盘后的 Run 恢复，不等价于所有崩溃窗口均被覆盖。

修复验收：为 committing/ambiguous 提供 reconciliation；先通过稳定 operation id 查询供应商记录和外部状态，再生成恢复回执。查不清时可见地停在 ambiguous，不能自动重发。供应商若没有幂等键/查询能力，必须明确不能提供普适 exactly-once。

### R1 / P1：Agent 产物测试的绿色结果不等价于独立验收

证据来自生成仓库 `/home/sid/runguild-targets/simple-game` 的 `08ef93e`，不属于 RunGuild 自身单元测试：

- `test/engine.test.ts:62` 的撞墙测试接受 `running` 或 `game-over`，无法证明撞墙终止。
- 同文件 `:90` 的难度测试只断言 `difficultyFactor() >= base`，原值不变也通过。
- 同文件 `:34` 标题说吃食物会增长，但分支里没有验证吃到食物后的增长。
- `test/interactive.test.ts` 只验证渲染帧，不能代替 Planner 要求的 controller + engine 得分阈值集成测试。

因此此前真实 Run 验证了流程、安装和现有测试，但不能用来声称“复杂贪吃蛇全部需求经独立验证”。

修复验收：在 Builder 启动前冻结至少一组外部验收用例，测试代码位于 Builder 不可改的验证环境；固定状态验证增长、碰撞原因、分数、暂停/恢复等。另注入有意破坏行为的 mutant，证明验收真的会失败。Reviewer 的模型意见只作为补充。完整源文件改了但 tests still green 的情况应能被门禁识别。

### R2 / P1：Worktree 和 argv 白名单不是 OS Sandbox

位置：`packages/workspace-tools/src/workspace-tools.ts:77`。执行器直接 spawn 宿主机子进程。虽然 `shell=false`、环境变量精简、文件工具路径受限，`npm test`/安装脚本仍可执行仓库里的任意 JS，并获得该进程的宿主用户权限。

这已经在现有面试指南中说明，应继续保持诚实。接触不可信仓库或开放多人远程使用前，需要受限容器：仅挂载 Task Worktree、无宿主凭据和 Docker socket、非 root、资源限制、进程组终止、验证阶段默认断网。依赖准备和测试分开处理。

作品集第一版可以明确仅支持个人可信环境；不能同时宣称完整安全沙箱和任意不可信任务可执行。

### R3 / P1：聊天提交跨两个 HTTP 请求，重试标识不稳定

位置：`apps/web/src/App.tsx:939`、`apps/web/src/api.ts:903`。

发送先保存消息并清空草稿，再请求建立规划任务。若第二步失败，已保存消息可能没有对应规划。API 客户端每次调用重新生成幂等键；响应丢失后重新调用不能自然复用原操作身份。

这是代码路径发现，本次未做浏览器断网复现。不能把它说成已观测到重复 Mission。

修复验收：引入持久 client_request_id；发送任务消息、关联 Mission/规划请求在服务端统一事务中完成或使用可恢复的命令状态。重试沿用原键。覆盖消息保存后断网、提交成功但响应丢失、刷新页面、已完成 Mission 中发新任务。日常任务与普通问候应有清楚语义，不能对“你好”建立代码 DAG。

### R4 / P1：多 Agent 效益和成本结论尚不成立

`/home/sid/runguild/docs/REAL_EVALUATION_2026-08-31.md` 明确记录：第一对实验单 Agent 成功、多 Agent 失败，且中途修复过平台；第二次仅多 Agent 的可靠性回归成功。这是有价值的工程证据，但不能拿来声称多 Agent 效率提升。

`packages/database/src/evaluation-repository.ts:676` 附近把缺失 price 的 SUM COALESCE 为 0。未知成本必须显示 unknown/partial，不能当免费；同时记录已定价调用数/总调用数和价格版本。

验收：先选 3 类小任务（局部 bug、接口实现、可分解跨模块修改），固定同一代码基线/工具/模型设置，每类每变体重复 3 次，形成 18 个 Trial 的工程小样本。该数量是本项目预算建议，不是统计显著性的保证。公开失败和人工干预，分别报告预算上限和实际用量；不得中途修框架后混算一组结果。多 Agent 若不占优，报告适用条件，保留简单任务走单 Agent 的路径。

### V3 / P1：Provider 能力必须显式协商

`src/voren/providers/openai_responses.py:260` 固定请求 background 和禁止并行工具调用。目前不能把此接口能力等同于任意兼容 endpoint。

2026-09-14 查阅的 DeepSeek 官方文档：Responses 已支持，但 previous_response_id、background 不支持；parallel_tool_calls 参数被忽略；某些参数静默忽略。Voren 应支持 foreground fallback，并区分本地取消、传输中止、供应商确认停止。多工具返回由 Runtime 校验，不能依赖服务端真的串行。

此外，DeepSeek 官方说明旧 `deepseek-v4-flash` 名称仍接受，但映射到当前 Flash 模型。后续实验保留 requested/returned model、时间、endpoint 和配置 digest；供应商未提供可固定快照时标注跨时间不可严格复现，不修改历史实验的旧标签。

来源：[DeepSeek 入门](https://api-docs.deepseek.com/)、[Responses 兼容表](https://api-docs.deepseek.com/guides/responses_api/)。本次未实测在线 endpoint，以上是官方能力声明与本地代码的对照。

### V4 / P1：主打 Skill 学习与真实业务仍是计划

当前有静态 Skill Store、内容寻址版本与冻结 Context；没有从 Episode 自动产生候选、独立评测后晋升的完整闭环。当前动作契约主要是创建日历事件及关联邀请，不是通用邮件收发系统。

至少完成一次：成功/失败轨迹 -> 有限候选 Diff -> 隔离评测 -> 拒绝坏候选/晋升合格候选 -> 回滚。不要把模型参数更新与 Skill 文件更新混淆，不预先承诺学习后一定提升。

### C1 / P2：交付和维护证据不足

- 两个仓库均未发现 `.github` CI 配置；现有测试不能代替持续集成。
- RunGuild 尚需独立 PostgreSQL 测试、浏览器 E2E、冷启动/重启演示。
- Voren 尚需依赖锁定、provider 兼容配置、真实模型 golden run、至少一份攻击评测 Artifact。
- README 部分文案过时：Voren 仍写 79 项测试，实测 85；RunGuild 描述纠错上限 2，但当前 Worker 显式配置 5，通用 Runtime 默认 2。应分清默认值和运行配置，不维护脱离版本的永久测试数。

## 4. 完成程度与收尾规模

不使用按代码行数计算的百分比。以下是交付阶段判断和粗估，不是工期承诺；每个有效开发日按约 5 小时实现、测试和复盘估算。

| 项目 | 当前阶段 | 达到可信作品集版本还缺 | 估算 |
| --- | --- | --- | --- |
| RunGuild | 工程主链已实现、有历史 live run；尚未达到稳定独立验收版本 | R1/R3、可信执行边界、PG/浏览器回归、实验和演示材料 | 5–8 个有效开发日 |
| Voren | Phase 1 待真实模型验收，Phase 2 部分完成 | V1/V2/V3、live 切片、小型业务服务、RAG/MCP、最小 gated-skill 闭环 | 15–25 个有效开发日 |

合计约 100–165 小时工程工作，不含独立学习和投递。若每日能投入 5–6 小时，边做边学习和求职时先安排 4–7 周，第一周结束按完成的验收项重新估算。账号申请、网络、模型质量、个人掌握程度会影响工期。

## 5. 调整后的实施顺序

| 顺序 | 交付切片 | 验收与停止条件 |
| --- | --- | --- |
| 1 | Voren 账本竞争和中断恢复 | 原子 claim，commit/receipt 间崩溃可 reconciliation；重复/竞争/无法确认均有测试 |
| 2 | RunGuild 独立验收与入口恢复 | Builder 不可改的外部 tests；断网重试不重复；正常任务发送有可见进展 |
| 3 | 双项目复现基线 | CI、依赖锁、单并发测试、独立 PG；一条启动/检查命令，断开后可恢复 |
| 4 | Voren 真实模型纵向切片 | 读邮件、查日历、提议、审批、执行、核验；保存成功和失败 Artifact |
| 5 | Voren 小型 FastAPI/SSE 演示与一套 Connector | 同一 Runtime 提供 CLI 和 Web；一个测试账号/自控服务，读和 Draft 先行；真实连接与 AgentDojo 结果分开 |
| 6 | Voren 业务检索与 MCP | 对会议历史/附件做来源可回溯的检索；一个真实 MCP Client/Server 往返，工具仍进入 Action Gateway |
| 7 | Voren 最小 Skill 学习闭环 | no_skill/static/gated 对照；场景族划分；候选不可直接激活；安全退化拒绝；可回滚 |
| 8 | RunGuild 重复实验与双项目发布资料 | 可复现小样本、失败分析、架构图、录屏、阅读顺序、事实版简历 |

修正旧路线图的一个排序：真实业务切片与展示不再等到“大而全的自学习框架全部完成”之后；但真实写操作仍必须等待动作可靠性修复。Connector 只选择一个可访问的供应商，不为国际平台访问条件耗费无界时间。账号选择另行确认。

RAG 不另起第三个大型项目：在 Voren 中查历史邮件/会议纪要/用户规则，用明确来源的检索结果支持答复和操作建议。具体日期、参与者、空闲时段查询保留结构化工具，不强行向量检索所有内容。Profile、Episode、Skill 分开存储，外部文本没有长期写权限。

第一版不增加：插件市场、多个供应商、桌面客户端、Kubernetes 集群、全自动后台学习、通用自主互联网操作、第三套编排引擎。

## 6. 实验与展示标准

RunGuild：展示一条 5–10 分钟可讲清的路径——输入任务 -> 可见计划 ->执行 -> 审查/退回 -> 精确提交 -> 独立验收。另有中断恢复录像。业务不要只剩“贪吃蛇”，增加一个有清楚输入输出的接口/数据处理需求。

Voren：先复现成功动作和被拒绝的注入；再演示“用户偏好/历史检索 -> 准备会议 -> 审批”，最后展示一个坏 Skill 候选被拒绝、旧版本恢复。可用合成测试邮件，但不能标成真实生产数据。

建议 Voren 初始评测覆盖 3 个场景族，冻结不少于 12 个正常用例和对应攻击变体；预算内重复 3 次。分别记录任务效果、攻击成功、权限拒绝、重复动作、模型与工具调用、耗时和 usage 完整性。这是拟定的收尾门槛，尚未执行，也不代表泛化安全保证。

## 7. 参考材料的取舍

| 来源 | 学什么 | 不扩张到 |
| --- | --- | --- |
| Pi 的 core / provider 层 | 简洁 Loop、事件、模型能力适配 | 全部 TUI、扩展生态 |
| Cumora | 已参考的持久身份、消息/唤醒协作理念 | 把聊天产品完整复制一遍 |
| LangGraph | 持久化、interrupt/resume、状态和对照实现 | 强制重写现有两个 Runtime |
| Hermes | 按需 Skill 加载、可读 Skill 编辑/管理 | “比 Hermes 首创自学习”的宣传 |
| MCP 官方 | Host/Client/Server 边界、真正互操作 | 以为接入协议就获得权限保障 |
| Anthropic 的 Agent/eval 工程文章 | 简单系统优先、独立 graders、明确评测口径 | 用一个模型分数替代外部结果核验 |

本次核验了上述官方仓库说明/文档，不代表逐行读过这些上游的完整源码。下一轮每次只选与当前缺陷相关的一条路径，记录固定 commit、采用/不采用的机制和落到本地的回归测试。

参考：[Pi](https://github.com/earendil-works/pi)、[Cumora](https://github.com/yetone/cumora)、[LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)、[Hermes Skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills)、[MCP](https://modelcontextprotocol.io/docs/learn/architecture)、[Agent evals](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)。

其他开源项目具有某功能，不妨碍个人项目把该功能作为工程亮点；区别在于能否解释并验证自己的实现，而非是否首次发明。
