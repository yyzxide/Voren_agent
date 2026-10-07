# RunGuild 面试问答：50 个可继续追问的问题

核查日期：2026-09-22。代码基线：`/home/sid/runguild`，HEAD `a51ccc37004e12f49b21a77c5b3cc7f23267b2e1`，核查时工作树干净。**源码复核与定向离线验证范围见 [12-SOURCE-RECHECK.md](/home/sid/Voren_agent/docs/interview-prep/12-SOURCE-RECHECK.md)**。测试引用用于定位断言，实际执行、结果与环境限制以 12 为准，不代表全量测试、真实 Mission 或付费模型验证。新增实现边界以静态源码为依据，离线复现另有标注；8/31 实验和 9/14 测试成绩仍按历史记录陈述。

先读 [03-RUNGUILD-ARCHITECTURE.md](/home/sid/Voren_agent/docs/interview-prep/03-RUNGUILD-ARCHITECTURE.md)。练习时先用 45–90 秒回答“可口述答案”，再打开源码解释“机制/例子”。不要求背行号；要求能指出对象、状态、事务及失败边界。以下答案不替 Sid 声称全部代码手写或独立设计，个人贡献以实际参与为准。

完整交付主线以“需要评审、计划要求 test_run、确实修改代码”的 Task 为例。其他任务按自己的 required 证据和 reviewRequired 配置判断；无评审代码任务的集成条件还有 R41/R50 所述缺口，不能把主线泛化成所有配置均已贯通。

## 一、定位与个人讲述

### R01. 用一分钟介绍 RunGuild，你最想让面试官记住什么？

**可口述答案：**RunGuild 是一个把自然语言开发需求转成可追踪、可恢复、可核验交付过程的个人项目。人批准任务计划后，系统按 DAG 分派 Agent；执行状态、工具结果和证据进 PostgreSQL。以需评审且要求测试的代码修改为例，代码在 Task Worktree 中修改，完成要检查对应提交的证据、独立评审和合并候选验证，最后由人确认交付版本。重点是让模型之外的系统按明确条件判断完成，同时保留对当前门禁缺口的说明。

**机制/例子：**Builder 说“测试通过”不改变业务事实。对要求测试的任务，即使已创建 Submission 或获得 Review 批准，缺少当前提交的有效测试仍会被完成门禁挡住；Run 已结束时 Task 仍可能 reviewing。提交、评审和完成检查不是同一个动作。

**追问/陷阱：**别扩张成“已替代研发团队”“生产级”或“显著提升效率”。当前证据支持个人平台的工程链路和故障恢复，不支持业务收益指标。

**证据：**[完成校验](/home/sid/runguild/packages/database/src/completion-verifier.ts:22)、[任务完成事务](/home/sid/runguild/packages/database/src/task-repository.ts:610)。

### R02. 已经有会写代码的模型，为什么还需要这套平台？

**可口述答案：**模型生成代码只是过程的一步。较长任务还需要保存计划、确定依赖、协调多个执行者、恢复中断、追踪费用、判断证据是否对应最终代码，以及控制最终交付。RunGuild 把这些责任从一段聊天里拆到显式状态和持久记录中。代价是维护成本与协调开销，所以简单修改可以走单 Builder。

**机制/例子：**一个 Worker 改完代码但没有提交评审就退出。只保存最终回答很难恢复；这里能查到 Run transcript、Task attempt、Worktree HEAD 和缺失 Submission，再决定恢复还是重新尝试。

**追问/陷阱：**“为什么不用现成框架”不应答成它们都做不到。个人实现的价值在于学习和验证特定交付约束；是否采购或复用框架要比较集成、运维和一致性成本。

**证据：**[运行持久层](/home/sid/runguild/packages/database/src/runtime-repository.ts:82)、[Worker 恢复入口](/home/sid/runguild/apps/worker/src/agent-loop.ts:250)。

### R03. 代码大量由 AI 生成，你如何证明自己理解项目？

**可口述答案：**我会说明代码主要由 AI 辅助生成，正在把项目中的关键设计和故障路径逐段掌握，不把它描述为全部手写。能证明理解的方法是解释一条真实链路、指出状态与事务边界、复现一个失败并独立判断修复是否成立。对于尚未独立完成的部分，我会区分“已读懂”“已复现”和“我实际改过”。

**机制/例子：**可以现场讲旧 attempt 的通过证据为什么不能覆盖本轮失败，再定位 SQL 的 attempt/head/tree 与较新失败条件。只有实际动手修改并验证后，才把该修改说成自己的完成经历。

**追问/陷阱：**不要虚构“我主导了所有设计”。Git 作者名也不能证明每行代码由谁手写；代码解释和可重复验证比隐藏辅助工具更有说服力。

**证据：**[证据门禁](/home/sid/runguild/packages/database/src/evidence-gate.ts:4)、[旧 attempt 回归](/home/sid/runguild/packages/database/test/completion-verifier.pglite.test.mjs:229)。

### R04. TypeScript/Node 不熟，你怎样把项目知识迁移到 Python 岗位？

**可口述答案：**先掌握运行机制：异步 I/O、数据库事务、状态机、工具协议和恢复，而不是先重写所有界面。TypeScript 的接口对应数据契约，`async/await` 可以用 Python coroutine 理解，repository 对应持久层。语言语法可以补，但“同一请求重试是否重复产生效果”是跨语言问题。

**机制/例子：**`withTransaction` 就是获取连接、BEGIN、执行回调、COMMIT，异常 ROLLBACK。把这个过程换成 Python 数据库库时，一样要保证批准记录、Task 和依赖边共享事务，而不是三个独立提交。

**追问/陷阱：**`await` 不意味着断电后继续，TypeScript 类型也不等于运行时校验。求职可优先补 Python Agent 应用实现，同时用 RunGuild 学持久执行；不应宣称精通尚未熟悉的 Node 内部机制。

**证据：**[事务包装](/home/sid/runguild/packages/database/src/transaction.ts)、[类型与状态](/home/sid/runguild/packages/protocol/src/states.ts)。

### R05. RunGuild 与 Voren 为什么不是两个重复项目？

**可口述答案：**RunGuild 重点是软件任务内部的协作、依赖、持久执行和代码交付门禁；Voren 的定位应围绕个人助理的外部动作、明确授权和执行后核验，具体以另一份文档为准。两者可以共享 Agent Loop、工具和追踪概念，但业务失败后果不同，不能只换一套技术栈包装相同故事。

**机制/例子：**RunGuild 的关键错误是把旧 HEAD 的测试拿来批准新代码、任务未完成却解锁后继；这需要 commit/tree/attempt 绑定与事务。把 Java/C++ 工作经历连接到这种工程理解可以，但不能虚构公司里已经使用了个人 Agent 项目。

**追问/陷阱：**若岗位只关注 Python 外部业务 Agent，可主讲 Voren，再用 RunGuild 补充分布式与验收能力；这是表达取舍，不是声称两个项目有真实生产集成。

**证据：**[RunGuild 证据门禁](/home/sid/runguild/packages/database/src/evidence-gate.ts:4)、[依赖解锁](/home/sid/runguild/packages/database/src/task-repository.ts:610)。

## 二、业务状态与调度

### R06. Mission、Task、Run 的区别是什么？

**可口述答案：**Mission 表示一次用户目标，Task 是目标中可调度且有验收条件的工作，Run 是某 Agent 对某 Task 的一次尝试。一次 Task 可以有多个失败与重试 Run；每个 Run 有独立预算、轨迹和 attempt。三层状态分开，避免把模型一轮结束误当用户目标已经交付。

**机制/例子：**Builder 的 Run succeeded 表示本轮显式完成被接受，但 Task 可仍在 reviewing 等评审或集成；全 Task completed 后 Mission 仍是 reviewing，直到人确认最终版本。

**追问/陷阱：**不要用一个 success 布尔值统管三层。若面试官问“为什么这么复杂”，回答每层有不同 owner、恢复粒度和验收条件。

**证据：**[三类状态](/home/sid/runguild/packages/protocol/src/states.ts)、[完成解释](/home/sid/runguild/packages/database/src/completion-verifier.ts:90)、[最终交付](/home/sid/runguild/packages/database/src/mission-repository.ts:521)。

### R07. 什么叫持续 Agent 身份？它有无限记忆吗？

**可口述答案：**持续身份是数据库中的 Agent id、角色、模型配置、Skill 分配和 Inbox 可以跨进程、跨任务存在。一次执行仍是独立 Run，模型并不会天然保留所有历史。系统明确加载并冻结选定数据库上下文与 Skill；这不涵盖 Worker 的全部环境配置，见 R25。持续身份是一种可寻址的协作对象，不是永不停止的模型进程。

**机制/例子：**同一个 Reviewer 昨天审核 Task A，今天审核 Task B，身份相同但评审材料快照不同。Worker 崩溃后重新启动，可以接续持久工作，但不能凭“它记得昨天”省掉数据库读取。

**追问/陷阱：**Run 隔离不代表 Task Worktree 每次重试都清空；代码状态可能有意保留，需要额外核验。也不能说 Agent 自动持续学习了新 Skill。

**证据：**[冻结上下文](/home/sid/runguild/packages/database/src/execution-context-repository.ts:125)、[Worker ownership 测试](/home/sid/runguild/packages/database/test/worker-instance-repository.pglite.test.mjs:58)。

### R08. 从用户输入到交付，完整讲一次调用链。

**可口述答案：**以需评审且要求测试的代码修改为例：消息持久化后创建规划请求与 Mission，Planner 输出结构化计划；人批准计划版本，系统落 Task 和依赖。Scheduler 发 dispatch，Agent Worker 原子领取并建立 Run，加载冻结上下文、准备 Worktree，进入模型工具循环。代码提交后测试，生成 Artifact Version 并提交独立 Review；通过后验证合并候选，集成及证据门禁满足才完成 Task、解锁后继。所有任务完成后，人再批准最终交付版本。

**机制/例子：**Researcher → Builder 是一条 DAG 边；Builder 不能因为看到 Researcher 的聊天回复就开工，必须等 Researcher Task 完成事实。所有步骤中“通知到了”和“状态允许”是不同条件。

**追问/陷阱：**Planner、Reviewer 的控制执行有独立记录，不能声称全部都是普通 Task Run。首次消息与 planning 在网页上仍是两请求；各 Task 的验收与评审要求由计划决定，当前无评审代码路径还有集成条件不一致。

**证据：**[规划事务](/home/sid/runguild/packages/database/src/conversation-planning-repository.ts:196)、[Agent loop](/home/sid/runguild/apps/worker/src/agent-loop.ts:250)、[Integration coordinator](/home/sid/runguild/packages/workspace-tools/src/integration-coordinator.ts:41)。

### R09. 人工批准计划为什么必须绑定版本？

**可口述答案：**用户批准的是当时看到的那一份计划，不是未来任何变化的计划。`approvePlan` 检查 expectedVersion，并锁住 Mission 与 revision；成功才在同事务中创建任务、验收条件、依赖与审批记录。计划已经变化时拒绝旧批准，避免用户同意两个任务，系统执行成另一组范围。

**机制/例子：**浏览器显示 v1，Planner 已提交 v2。人点击 v1 批准时，数据库返回 version_conflict，不会把这个点击解释为 v2 的授权。

**追问/陷阱：**只在前端禁用按钮不能抵抗并发或旧页面。用户批准计划也不自动批准所有危险工具动作或最终交付；不同批准绑定不同对象。

**证据：**[approvePlan](/home/sid/runguild/packages/database/src/mission-repository.ts:360)、[编排测试](/home/sid/runguild/packages/database/test/orchestration.pglite.test.mjs:61)。

### R10. DAG 如何验证，如何决定后继任务可以运行？

**可口述答案：**先校验任务 key、依赖存在性、重复依赖和环，再用拓扑过程确认图可执行。运行时不只信规划时的排序，而是检查数据库中所有 required 父任务都 completed。父任务完成和孩子 blocked→ready 放在同一事务，避免中间状态被当成可执行事实。

**机制/例子：**C 依赖 A 与 B。A 先完成只能让它自己的状态改变；SQL 的 NOT EXISTS 仍能找到未完成的 B，所以 C 保持 blocked。B 完成才会解锁 C。

**追问/陷阱：**拓扑顺序不等于必须串行执行，彼此无依赖的 Task 可以并行。环检测解决调度可达性，不证明任务拆分足够合理。

**证据：**[validateTaskGraph](/home/sid/runguild/packages/protocol/src/dag.ts:17)、[条件解锁](/home/sid/runguild/packages/database/src/task-repository.ts:610)。

### R11. 两个 Worker 同时领取同一 Task，怎么避免双重执行？

**可口述答案：**领取必须携带未过期的 Dispatch Token，并满足正确的 workspace/project/Agent/角色/attempt。`claimTask` 在事务中锁 Task 与 dispatch，再增加 attempt、创建 Run 和租约、消费 dispatch。第二个请求拿锁后重新检查，Task 已不再 ready 或 token 已消费，就领取失败。

**机制/例子：**两个收到重复 wake 的处理器同时拿到 token D。第一个提交了 Run R1；第二个不能创建 R2 来共用同一个 Task attempt。调度阶段还使用 SKIP LOCKED 分批减少互相等待。

**追问/陷阱：**数据库防双领不是阻止一切外部副作用的完整证明。实际并发语义还需要独立 PostgreSQL 测试；PGlite 用例不能直接代表所有多连接竞争行为。

**证据：**[claimTask](/home/sid/runguild/packages/database/src/task-repository.ts:246)、[真实 PG 竞争测试](/home/sid/runguild/packages/database/test/postgres.integration.test.mjs:73)。

### R12. Scheduler 如何选 Agent，多 Agent 是否必然更快？

**可口述答案：**当前 Scheduler 先按 Task 所需角色和 Project 成员关系筛 Agent，再参考活跃 Run 与 pending dispatch 数量选择负载较低者。这个策略能提供确定的资格与简单负载均衡，但不是全局最优调度。多 Agent 会增加规划、传递上下文、评审和集成开销，收益取决于可并行工作是否超过协调成本。

**机制/例子：**三个独立模块有机会并行；一个函数的局部 bug 再拆 Researcher 和 Builder，可能多花一次完整上下文与交接。DAG 中一条长依赖链也不会因为 Agent 数量多就自动缩短。

**追问/陷阱：**不要把角色为 active 说成 Worker 此刻必然在线。当前选择逻辑主要看持久身份和负载，实际执行还受 Worker 存活与 dispatch 超时影响。

**证据：**[角色与负载选择](/home/sid/runguild/packages/database/src/scheduler-repository.ts:92)、[配对报告计算](/home/sid/runguild/packages/evaluation/src/report.ts)。

### R13. 当前网页的“提交一个新任务”真的是原子的吗？

**可口述答案：**端到端还不是。服务端将已存在消息提升成 Mission、planning request 和 wake 是一个事务；网页首次提交先 postMessage，成功后清空草稿，再 createPlanningRequest。第二步失败时消息可能已保存而规划未创建；每次请求重新生成随机幂等键，也不利于丢响应后的安全恢复。

**机制/例子：**第一请求返回成功后断网，页面没有草稿，PG 有消息但没有规划。若用户重新输入同样内容，可能形成新请求身份；文字相同不意味着同一业务操作。

**追问/陷阱：**改进是稳定 client_request_id，加服务端原子复合命令或可恢复命令状态；不能只把两次 fetch 放在同一 JS 函数里就叫事务。此处是已确认的当前缺口，未做浏览器断网实测。

**证据：**[sendMessage](/home/sid/runguild/apps/web/src/App.tsx:939)、[两次随机幂等键](/home/sid/runguild/apps/web/src/api.ts:903)、[后半段事务](/home/sid/runguild/packages/database/src/conversation-planning-repository.ts:196)。

## 三、持久消息、故障恢复与工具

### R14. 为什么 PostgreSQL 存事实，Redis 只通知？

**可口述答案：**任务状态、审批、执行记录需要可追溯和事务一致性，因此以 PG 为事实源。Redis 提供低延迟唤醒和跨实例通知，消息丢失或重复都不能成为业务真相。Agent 收到通知后仍读 PG；周期处理也会恢复持久工作。

**机制/例子：**调度已有 ready Task 时，Dispatch、Inbox 和 Outbox 在同一事务中提交。Redis 暂时不可用，Publisher 可重试 wake，Worker 也会周期查 PG；不会因为一次 pub/sub 丢消息就抹掉 Task。Task 创建或解锁与这次分派属于不同事务。

**追问/陷阱：**这不等于 Redis 故障毫无影响，实时性会变差、Outbox 会积压。也不能把 PG 的持久性当作无备份即可避免数据丢失的承诺。

**证据：**[事件与 Outbox 同写](/home/sid/runguild/packages/database/src/events.ts)、[Worker tick](/home/sid/runguild/apps/worker/src/tick.ts:50)、[Inbox 读取](/home/sid/runguild/packages/database/src/inbox-repository.ts)。

### R15. Outbox 发布成功但没标记成功就崩溃，会不会重复？

**可口述答案：**会，设计必须允许这一窗口重复。Outbox 领取带 claim token 与过期时间；旧进程失效后新 Publisher 可重领，Redis 可能再收到同一事实。消费侧通过业务 id、唯一约束、dispatch 状态、cursor 或工具幂等处理重复，不能宣称跨数据库与消息系统的 exactly once。

**机制/例子：**publish 已发送，markPublished 前断电；PG 仍显示未发布。下一次再发送只应触发“再检查工作”，不能产生第二份 Task 或第二次无条件副作用。

**追问/陷阱：**“先标记成功再发消息”不是修复，会改成可能永久丢通知。重点是投递至少可重试、业务消费可识别重复，以及监控积压和错误。

**证据：**[claimBatch/markPublished](/home/sid/runguild/packages/database/src/outbox-repository.ts:19)、[Publisher 顺序](/home/sid/runguild/apps/worker/src/tick.ts:78)。

### R16. Durable Inbox 的游标解决什么，为什么 acknowledge 带 expectedCursor？

**可口述答案：**Inbox 保存给某 Agent 的持久工作项，cursor 表示确认处理到哪里。acknowledge 使用 expectedCursor 条件更新，是乐观并发检查：另一个处理器已经推进游标时，当前旧视图不能盲目覆盖它。业务处理本身仍须幂等，因为处理完成但 ack 前宕机会再次看到消息。

**机制/例子：**处理器 A、B 都读到 cursor=10；A 先确认到 12，B 再用 expected=10 更新就失败。项目作用域检查还不会跨过一条外项目消息直接推进 cursor，以免跳过不应处理的内容。

**追问/陷阱：**游标不是对消息副作用的分布式事务，也不代表模型执行必须在同一个数据库事务里完成。

**证据：**[readBatch/acknowledge](/home/sid/runguild/packages/database/src/inbox-repository.ts)、[外项目消息测试](/home/sid/runguild/packages/database/test/inbox-repository.pglite.test.mjs:34)。

### R17. Worker 崩溃后 Task 如何恢复？

**可口述答案：**执行权有有限租约，Worker 周期续租。进程死亡后租约不再延长，Scheduler 的回收过程锁定过期租约和 Task，将未终止 Run 标记 timed_out，删除租约；还有 attempt 就把 Task 放回 ready，否则 failed。新领取产生新 attempt，而旧失败记录保留。

**机制/例子：**R1 第三次模型调用时宕机，不会永远让 Task running。若 maxAttempts 尚未耗尽，Task 被重排；能复用的代码和证据仍要通过新尝试的绑定规则，不能直接改 R1 为 succeeded。

**追问/陷阱：**正常失败不是都等到期。`resolveTerminalRunLease` 可以主动收尾，租约回收主要兜住不可控崩溃。超时设置太短会误接管，太长会增加恢复延迟。

**证据：**[回收](/home/sid/runguild/packages/database/src/task-repository.ts:523)、[主动结束租约](/home/sid/runguild/packages/database/src/task-repository.ts:407)、[续租](/home/sid/runguild/apps/worker/src/agent-loop.ts:331)。

### R18. 旧 Worker 卡住后恢复，为什么不能让它继续写结果？

**可口述答案：**旧 Worker 持有的执行 token 可能已经被接管者替换。工具执行记录的 finish 必须同时匹配记录身份、running 状态和当前 execution_token，旧 token 更新不到行就被拒绝。它没有直接检查到期时间，所以要区分“只是到期”和“已被接管”。Task 续租失败也会向执行链发 abort，尽量停止旧执行；最终 Task 完成和证据写入还有各自的状态与版本门禁，不能泛化成所有更新都受同一个 token 保护。

**机制/例子：**工具执行 A 超过租约，安全重试 B 拿到 token B；A 后来返回，不得覆盖 B 的结果。这里 token 是随机持有者标识，不是单调序号，但通过“仅当前值可写”实现拒绝旧 owner。

**追问/陷阱：**数据库 fencing 不会回滚 A 已经写到磁盘或网络的效果。需要区分控制平面的陈旧写入保护与操作系统副作用隔离，不能用一个 token 宣称全局安全。

**证据：**[Tool finish 条件](/home/sid/runguild/packages/database/src/tool-execution-repository.ts:240)、[Worker abort](/home/sid/runguild/apps/worker/src/agent-loop.ts:331)。

### R19. 什么错误可以重试，如何避免无限重试烧钱？

**可口述答案：**要分层。Task 有 maxAttempts，Run 有 hop 上限，模型协议错误有有限纠正次数，工具有 retryMode，Planner/Reviewer 也有自己的 lease 与模型尝试记录。业务失败、可重试 I/O、结构错误、未知副作用不能统一用 catch 后再调用一次处理。

**机制/例子：**模型叫错函数可以反馈当前合法名称、消耗有限一次纠错；写操作是否成功不明则先判定安全重试能力。已经存好的 Reviewer 有效决定可以直接恢复应用，不该再付费问一次。

**追问/陷阱：**通用 Runtime 默认修复 2 次，实际 Agent Worker 设置为 5 次。增加次数可能掩盖提示或模型能力问题，必须把失败类型和消耗保留在账本。

**证据：**[Runtime 纠错配置](/home/sid/runguild/packages/agent-runtime/src/runtime.ts:203)、[Worker 设置](/home/sid/runguild/apps/worker/src/agent-main.ts:269)、[Reviewer claim](/home/sid/runguild/packages/database/src/reviewer-execution-repository.ts:317)。

### R20. Tool Gateway 的幂等具体是什么，不是“加一个 UUID”就够吗？

**可口述答案：**幂等需要稳定操作身份和相同请求语义。Gateway 先向存储 reserve，持久化请求 hash、风险、重试模式和状态；相同请求可以返回旧结果、等待审批或等待执行中状态。同一个身份换参数必须拒绝。每次重试重新生成 UUID，实际上是在声明一个新操作，不能防重复。

**机制/例子：**模型已要求 commit，进程在回包前重启，Runtime 从 transcript 找回同一工具请求；Gateway 与 repo.commit 的 Git 核对一起避免随意制造另一个提交。若 request hash 不同，说明不是安全重放。

**追问/陷阱：**工具调用幂等和外部效果恰好一次不同。需要继续回答副作用发生、结果未持久化的窗口由工具专属恢复逻辑处理还是进入歧义状态。

**证据：**[Gateway reserve/replay](/home/sid/runguild/packages/tool-gateway/src/tool-gateway.ts:100)、[请求 hash 检查](/home/sid/runguild/packages/database/src/tool-execution-repository.ts:84)。

### R21. 副作用成功与否未知时怎么办？

**可口述答案：**未知不等于失败，也不等于可以重做。Tool execution 租约丢失且 retryMode 为 none 时，系统给出 ambiguous_effect 和 unknown 状态，阻止盲目接管重试；有可验证幂等机制的工具才允许按它的协议恢复。最终策略应根据操作可否查询、去重和补偿决定。

**机制/例子：**测试命令也可能写文件，所以 `test.run` 当前不是任意可自动重跑的纯读操作；commit 则能根据真实 Git HEAD 和持久信息核对已执行效果。不同工具的恢复能力不能靠名字猜。

**追问/陷阱：**不要回答“事务回滚就好了”：PG 事务不能撤销已经运行的外部命令。重试策略是业务协议的一部分，不只是网络库参数。

**证据：**[ambiguousResult 与过期预留](/home/sid/runguild/packages/database/src/tool-execution-repository.ts:60)、[test.run retryMode](/home/sid/runguild/packages/workspace-tools/src/workspace-tools.ts:998)。

### R22. 模型能不能把危险工具标成只读来绕过审批？

**可口述答案：**风险策略由服务端注册的 handler 决定。正常模型协议不提供最终 risk，Runtime 查询 handler 风险并填入程序请求；Gateway 再比较请求 risk 与 handler risk，不相符会拒绝。需要批准的动作由持久审批流程控制，模型自称安全没有授权效力。API 身份还要从真实会话或内部 token 推导，不能信任浏览器随意传的 actor。

**机制/例子：**模型请求 file.patch，Runtime 根据服务端注册信息生成风险字段。如果程序请求层把该字段误填成 read_only，Gateway 会拒绝不一致；这不是依赖模型如实声明风险。执行上下文和持久层还核对 workspace、project、mission、task、run 的归属，防止混用别的任务 id。

**追问/陷阱：**类型检查是编译期帮助，不是授权。prompt 中写“不要越权”有指导意义，真正拒绝必须在服务端代码和数据库边界发生。

**证据：**[Runtime 生成 risk](/home/sid/runguild/packages/agent-runtime/src/runtime.ts:491)、[风险核对](/home/sid/runguild/packages/tool-gateway/src/tool-gateway.ts:116)、[Tool execution store](/home/sid/runguild/packages/database/src/tool-execution-repository.ts)、[认证边界](/home/sid/runguild/apps/api/src/authentication.ts)。

### R23. Git Worktree 是否就是 Agent 的安全沙箱？

**可口述答案：**不是。Worktree 给每个 Task 独立目录和分支，配合路径校验、符号链接检查、exact argv 白名单，能减少任务互相污染和错误访问。但配置的测试脚本仍在宿主进程里执行，可以读其他文件、访问网络或派生进程；`shell:false` 只是不通过 shell 解释命令字符串。

**机制/例子：**允许 `npm test` 不代表 package.json 中的脚本无害。脚本可以使用 Node fs 或 child_process，路径工具的边界不会自动约束它。

**追问/陷阱：**面向不可信仓库的改进应是容器/非 root、最小挂载、无宿主凭据、资源和网络限制、进程组取消；这些当前不能当作已实现。个人可信环境的范围也要说清。

**证据：**[宿主 spawn](/home/sid/runguild/packages/workspace-tools/src/workspace-tools.ts:66)、[WorkspaceBoundary](/home/sid/runguild/packages/workspace-tools/src/workspace-tools.ts:182)、[GitWorktreeManager](/home/sid/runguild/packages/workspace-tools/src/git-worktree-manager.ts:140)。

### R24. Git commit 已成功，但写 Evidence 前崩溃，怎么避免重复提交？

**可口述答案：**repo.commit 不能把 Git 和 PG 当作一个原子系统。当前恢复会核对路径、预期分支、真实 HEAD 与已有记录，再计算 tree/diff、补足证据；工作区已干净时不会为了重放制造空提交。它能恢复可识别的 Git 状态，但没有操作专属标识证明某个新 HEAD 一定由原工具请求产生。

**机制/例子：**第一次提交已落到 Task 分支，进程却没保存回包；没有其他写入时，重启可读取该 HEAD，结合旧记录补录 commit/tree/diff。若外部进程也在同一分支提交了新内容，现有路径、分支和祖先检查不能独立证明提交归属，不能把这种并发情况说成已可靠区分。

**追问/陷阱：**工具请求去重与外部副作用恰好一次不同。这里仍依赖 Task 工作区没有未受控并发写入；更强的恢复需要把操作身份、预期前置 HEAD 与结果提交绑定，并明确发生冲突时怎样停止。

**证据：**[repo.commit 实现](/home/sid/runguild/packages/workspace-tools/src/workspace-tools.ts:791)、[Worktree 校验](/home/sid/runguild/packages/workspace-tools/src/git-worktree-manager.ts:551)、[工具回归](/home/sid/runguild/packages/workspace-tools/test/workspace-tools.test.mjs)。

## 四、上下文、预算与模型行为

### R25. 为什么要冻结 Run 上下文？重启重新读最新配置不是更方便吗？

**可口述答案：**同一个 Run 重启后如果悄悄更换目标或 Skill，执行语义会变化。首次加载会冻结 Mission、Task、验收标准、数据库中的模型标识、精确 Skill 版本及关联 Artifact id，保存 hash，后续复用并核对。但这不等于完整有效模型配置已冻结：Worker 环境可覆盖模型标识，推理参数和上下文预算也来自进程配置。

**机制/例子：**R1 已按 Skill v1 修改一半代码，管理员发布 v2，R1 重启仍使用冻结的 v1；但若重启时改变 MODEL_NAME，modelFor 优先用环境覆盖，实际模型可能改变。前一个保证已有数据库快照支持，后一个缺口需要额外冻结并核验有效配置。

**追问/陷阱：**数据库冻结测试只证明所覆盖字段和 Skill 的快照复用，没有覆盖 Worker 环境 override。仓库目标分支也可能变化，需要集成时重新检查；输入可追溯不保证模型确定性。Steering 是显式追加输入，其崩溃窗口见 R30。

**证据：**[ExecutionContextRepository.load](/home/sid/runguild/packages/database/src/execution-context-repository.ts:125)、[环境配置](/home/sid/runguild/apps/worker/src/agent-main.ts:122)、[modelFor 优先级](/home/sid/runguild/apps/worker/src/agent-main.ts:190)、[冻结测试](/home/sid/runguild/packages/database/test/execution-context.pglite.test.mjs:73)。

### R26. Skill 与普通 prompt 有何区别？项目有没有自动自我进化？

**可口述答案：**这里 Skill 是有人管理、带版本和内容 hash 的执行指令单元，可以按 Agent 分配、启用、排序或固定版本。运行时把选中的精确版本放入冻结上下文。它仍会成为模型指令，不是天然可靠的程序；项目这部分不意味着模型自动修改自己并无条件上线。

**机制/例子：**同一个 Skill 可以发布 v1、v2，某 Agent 固定 v1，另一 Agent 使用最新；每个 Run 保存自己实际使用的 versionId/hash，以后比较失败时不只看 Skill 名字。

**追问/陷阱：**多加 Skill 会占固定上下文预算，也可能冲突。不能说“有 Skill 就提升准确率”，必须用固定任务和对照验证效果；RunGuild 当前只凭版本治理不能证明效果增益。

**证据：**[Skill repository](/home/sid/runguild/packages/database/src/skill-repository.ts)、[版本选择与冻结](/home/sid/runguild/packages/database/src/execution-context-repository.ts:184)。

### R27. 历史太长怎么办，为什么不能直接保留最后 N 条消息？

**可口述答案：**上下文要保留强制指令和完整工具交换关系。当前 Context Builder 估计 token，保留初始固定内容与最近的原子 assistant/tool 单元，把较旧历史转成有界摘要。单纯截最后 N 条可能留下 tool_result 却删掉 tool_call，也可能删掉验收或权限约束。

**机制/例子：**某 assistant 同时发两个工具调用，后面有两个结果；保留时应作为一个交换单元。固定指令和工具定义已经超预算时直接失败，不能静默裁掉 Skill 的关键限制。

**追问/陷阱：**摘要不是无损压缩；字符/字节估算不等于模型 tokenizer 精确计数。完整 transcript 仍留在数据库，每 hop 快照表示实际可见视图，二者用途不同。

**证据：**[Context Builder](/home/sid/runguild/packages/agent-runtime/src/context-builder.ts:175)、[原子保留测试](/home/sid/runguild/packages/agent-runtime/test/context-builder.test.mjs:44)。

### R28. 怎样防止模型一直读代码、最后没预算交付？

**可口述答案：**运行时有 max hops；Builder 的 required 验收包含 file_diff 时，才启用探索到实现的门禁，成功 patch/delete 打开下一段探索窗口。这类任务若还需评审，则保留末尾交付窗口：最后 8 hop 隐藏 broad search，最后 6 hop 隐藏 search 和 file.read，给 commit、测试、Artifact、Submission 和显式结束留空间。

**机制/例子：**模型读了很多文件却没写代码时，继续搜索不再是可调用工具；它必须实现或报告阻塞。最后阶段仍允许验收关键 patch/delete，这与 README “最后六步冻结 patch”不一致，应按源码解释。

**追问/陷阱：**预算门禁也可能迫使模型过早修改，需用真实 trace 调整。当前一些保留窗口提示仍写先测试再 commit，而新证据门禁要求 commit 后测；应指出提示冲突，不能照旧提示教学。

**证据：**[Worker 工具窗口](/home/sid/runguild/apps/worker/src/agent-main.ts:281)、[Runtime 动态门禁](/home/sid/runguild/packages/agent-runtime/src/runtime.ts:653)、[预算回归](/home/sid/runguild/packages/agent-runtime/test/runtime.test.mjs:371)。

### R29. 模型输出未知工具名或坏 JSON 时怎么办？

**可口述答案：**它是协议错误，不应把部分看起来合法的调用先执行掉。适配层检查本 hop 公开的函数名称与参数结构，失败不执行工具，保留 provider usage，并追加有限次数纠正反馈。恢复时重放完整 transcript，避免用不匹配的旧 continuation 状态继续。

**机制/例子：**模型用了上一轮允许、本轮因预算门禁隐藏的 repo.search，属于当前协议不合法；返回当前合法名称供其纠正。如果一次响应含一个有效调用和一个坏调用，不能先执行有效前缀再说整体失败。

**追问/陷阱：**结构合法不等于业务安全；后续还要 Gateway 权限与路径等校验。原始畸形参数也不能无差别进日志，以免暴露数据；usage 必须保留，否则看起来错误调用没花钱。

**证据：**[Runtime 协议处理](/home/sid/runguild/packages/agent-runtime/src/runtime.ts:387)、[混合响应测试](/home/sid/runguild/packages/agent-runtime/test/openai-adapter.test.mjs:389)、[有界修复测试](/home/sid/runguild/packages/agent-runtime/test/runtime.test.mjs:281)。

### R30. 人类在运行中取消或追加要求，系统怎样处理？

**可口述答案：**控制输入写成持久 Run control，并创建 wake；Runtime 在循环检查点消费 pending controls，遇 cancel 则结束 Run，遇 steering 则追加可追踪消息。用户取消目前没有直接连接到正在执行的模型/工具的 AbortSignal；Worker 停止或 Task 续租失败的 abort 是另一条路径。审批暂停保存 transcript 与待处理工具请求，恢复时可按原请求身份继续。

**机制/例子：**本轮脚本模型一次返回两个只读调用，内存持久化夹具在第一个工具完成时加入 cancel，第二个仍执行，下一循环才 cancelled。这实际验证了批次检查点的延迟，没有调用付费模型。另一个仅静态确认的窗口是 takePendingControls 先提交 applied，再 finish(cancelled) 或 appendMessage(steer)；中间崩溃可能使请求已标记、效果未落地，下次又取不到它。

**追问/陷阱：**要区分请求持久化、标记消费、实际状态变化和外部进程停止。当前控制消费与业务应用没有原子化，取消也不能撤回已有副作用或保证杀死所有派生进程。批次延迟反例不等于做过杀进程或真实外部副作用实验；applied 窗口尚未动态复现，具体记录见 12。

**证据：**[持久控制](/home/sid/runguild/packages/database/src/runtime-repository.ts:419)、[先标 applied](/home/sid/runguild/packages/database/src/runtime-repository.ts:483)、[随后应用](/home/sid/runguild/packages/agent-runtime/src/runtime.ts:626)、[Worker abort 来源](/home/sid/runguild/apps/worker/src/agent-loop.ts:338)、[暂停恢复测试](/home/sid/runguild/packages/agent-runtime/test/runtime.test.mjs:190)、[取消优先测试](/home/sid/runguild/packages/agent-runtime/test/runtime.test.mjs:232)、[本轮批次延迟反例](/home/sid/Voren_agent/docs/interview-prep/12-SOURCE-RECHECK.md)。

## 五、证据、版本与独立评审

### R31. 为什么模型 silence 或 stop 不能视为完成？

**可口述答案：**模型停下可能是没想到下一步、输出上限、协议问题或服务端结束；这些都不是任务完成证据。项目要求显式 run.set_status(done)，数据库 verifier 按计划的 required 验收条件检查持久证据；只有 reviewRequired=true 时额外要求当前 Run 的有效 Submission。条件不足时把拒绝原因反馈给后续 hop，直到满足或预算耗尽。

**机制/例子：**计划要求 test_run 且需要评审时，模型只说“代码完成”，没有有效测试或当前 Run Submission，完成申请会被拒绝。其他任务不必一律具备这两者：计划校验没有强制所有 Task 配置测试。即使 verifier 接受本轮结束，Task 仍可能等待独立 Review 或集成。

**追问/陷阱：**显式 done 只是申请状态转换，不是模型有权盖章。门禁只能执行已声明的验收要求，不能弥补计划漏写标准；Submission 创建成功也不表示所有 required 证据有效。不能把工具输出中 evidence id 的存在当作验收证明。

**证据：**[运行时完成契约](/home/sid/runguild/packages/agent-runtime/src/runtime.ts:29)、[数据库校验](/home/sid/runguild/packages/database/src/completion-verifier.ts:22)、[required 条件](/home/sid/runguild/packages/database/src/evidence-gate.ts:4)、[计划校验](/home/sid/runguild/packages/protocol/src/plans.ts:58)、[silence 测试](/home/sid/runguild/packages/agent-runtime/test/runtime.test.mjs:126)。

### R32. 为什么旧 attempt 的成功证据不能随便给新 attempt 用？

**可口述答案：**一次重试可能已经改了输入、代码或执行结果，历史成功不代表当前尝试成功。无 Worktree 的执行证据要求当前 attempt；代码任务可以按精确提交规则复用，但不能只凭同一 Task id。a51ccc3 把这套检查同时用于 Run 完成与 Task 解锁，避免一个入口严格、另一个入口放行。

**机制/例子：**attempt 1 的 command_result 通过，attempt 2 相同任务已经失败；如果 SQL 只查“这个 Task 曾有 passed=true”，孩子就会错误启动。当前 gate 约束生产者 attempt 或精确 HEAD/tree，并检查较新失败。

**追问/陷阱：**不能反过来说所有历史证据一律无效。同提交的证据补交有严格复用条件；要按事实范围，而不是按“旧”这个字粗暴判定。

**证据：**[统一 gate](/home/sid/runguild/packages/database/src/evidence-gate.ts:4)、[旧 attempt 不解锁](/home/sid/runguild/packages/database/test/completion-verifier.pglite.test.mjs:229)。

### R33. 测试为什么同时绑定 commit、tree、clean 和 stable？

**可口述答案：**commit 标识被交付的提交，tree 标识该提交的文件快照；clean 表示测试前后没有未提交变化，stable 表示测试前后快照状态没有变化。它们组合约束“测试的是准备交付的那份代码”，不能只看退出码。新门禁连同一个 Run 内的测试也必须满足这一绑定。

**机制/例子：**先测试 A 通过，再修改并提交 B，A 的绿灯不能满足 B 的测试验收，也不会作为对应 B 的有效测试选入 Submission。提交接口仍可能创建缺少必需测试的 Submission，完整性由完成门禁继续拦截。对要求测试的代码任务，正确顺序是修改→commit→对干净提交测试；测试后再改，就重新 commit 和 test。测试脚本修改了受 Git 状态观察的代码，也会导致 clean/stable 不成立。

**追问/陷阱：**这些快照不能观察所有宿主环境、依赖、忽略文件和运行中瞬时变化，更不能证明测试断言充分。不要把 Git 状态绑定说成完整可复现构建。

**证据：**[快照](/home/sid/runguild/packages/workspace-tools/src/workspace-tools.ts:141)、[test.run](/home/sid/runguild/packages/workspace-tools/src/workspace-tools.ts:998)、[证据筛选](/home/sid/runguild/packages/database/src/review-repository.ts:82)。

### R34. 同一提交先通过、后失败，系统会不会仍挑旧通过结果？

**可口述答案：**当前 gate 除了找通过，还查同一命令、相关 attempt 或 HEAD 上是否有更晚失败；有就不允许旧通过满足验收。测试 content hash 纳入调用身份和 Git 状态，避免不同执行输出一样时被去重成旧结果，从而丢失时间顺序上的新事实。

**机制/例子：**10:00 `npm test` 通过，10:01 同 HEAD 重跑失败，不能只 SELECT 一个 passed=true 就放行。失败可能来自不稳定测试或环境，需要查原因，而不是为了完成忽略失败。

**追问/陷阱：**一次后续通过能否恢复还要看具体命令、时间与快照；当前设计不是完整的 flaky-test 统计模型。独立命令也不能随意互相替代。

**证据：**[较新失败子查询](/home/sid/runguild/packages/database/src/evidence-gate.ts:40)、[测试 hash](/home/sid/runguild/packages/workspace-tools/src/workspace-tools.ts:1032)、[Review 回归](/home/sid/runguild/packages/database/test/review-repository.pglite.test.mjs)。

### R35. Run 失败了，它的通过测试还有可能有效吗？

**可口述答案：**有可能。Run 失败可能只是用尽 hop，没完成 Artifact 提交，不代表此前每个工具结果都错误。新的证据补交 Run 可以选择前一 Task Run 对同一精确提交和 tree 的 clean、stable 通过测试，并重新形成当前 Submission。必须保留 producerRunId 和 attempt，不能把旧结果伪装为新执行。

**机制/例子：**R1 已提交 H、测试 H 通过，创建 Artifact 前超时；R2 未改代码，只补交。旧 HEAD、dirty 测试不会被选入；符合 H/tree 的证据可以复用，避免无意义重做，但仍需重新评审当前包。

**追问/陷阱：**这个例外不允许绕过更晚失败，也不允许复用其他 Task 的证据。审批的是当前准确证据包，不是“上次差不多完成”。

**证据：**[selectSubmissionEvidence](/home/sid/runguild/packages/database/src/review-repository.ts:82)、[证据补交回归](/home/sid/runguild/packages/database/test/review-repository.pglite.test.mjs:343)。

### R36. Artifact Version 为什么不可变，只有 JSON 内容 hash 够吗？

**可口述答案：**评审和批准必须指向固定交付内容，不能用户刚批准，LIVE 文档又变了。createVersion 同时冻结规范化内容投影和 exact Yjs 二进制状态，保存各自 hash，数据库 trigger 拒绝原位更新。同样文本的协作内部状态可能不同，双 hash 能把内容视图与实际协作状态一起绑定。

**机制/例子：**Reviewer 批准 v3 后，团队继续编辑 LIVE 为新状态，v3 仍可读取且不受影响。重新冻结产生或复用匹配的确切版本，最终批准必须明确选哪一个。

**追问/陷阱：**“不可变”是应用和数据库约束，不等于有管理员也无法删除数据库的密码学承诺。hash 验证内容身份，不证明内容正确。

**证据：**[createVersion](/home/sid/runguild/packages/collaboration/src/artifact-repository.ts:555)、[不可变触发器](/home/sid/runguild/packages/database/migrations/0005_artifacts.sql)。

### R37. Yjs 协同怎样恢复断线，为什么还要持久化？

**可口述答案：**客户端之间可交换增量，但服务端必须保存更新才能跨刷新和进程重启恢复。这里先校验并存 Yjs update，以 hash 去重，再通知；重建用 snapshot 加后续 updates，State Vector 决定对方缺少哪些增量。跨 API 实例的通知携带 seq/hash，实例回 PG 取真实更新。

**机制/例子：**API 接受更新后崩溃，只要事务已提交，重启仍能重建文档。Redis subscriber 重连恢复订阅时，服务端会触发持久全状态同步；这不表示任意静默丢消息都会被主动检测。Awareness 只是在线光标，过期就清理，不能当成 Artifact 内容。

**追问/陷阱：**CRDT 收敛不等于业务语义无冲突，两个人把需求写成互相矛盾的条目也可能正常收敛。Git 代码冲突并不是由 Yjs 解决。跨实例恢复测试用内存 repository 和 fake fanout 人工触发 recovery，不能代替真实 Redis/PG 断线实验。

**证据：**[appendUpdate/syncState/compact](/home/sid/runguild/packages/collaboration/src/artifact-repository.ts:408)、[重连触发](/home/sid/runguild/apps/api/src/redis-artifact-fanout.ts:58)、[全状态恢复](/home/sid/runguild/apps/api/src/artifact-realtime.ts:819)、[实时协作测试替身](/home/sid/runguild/apps/api/test/artifact-realtime.test.mjs:227)。

### R38. 独立 Reviewer 的“独立”体现在哪里？

**可口述答案：**至少有身份和输入两个方面。提交 Agent 不能审核自己的 Artifact，Agent Review 还要匹配指定 Reviewer，或由授权人类决定；Reviewer 读取固定 Submission 的验收、版本、证据和差异，不沿用 Builder 的自我判断。独立执行会单独记录模型尝试与成本。

**机制/例子：**Builder 说“所有需求都完成”只是材料之一，Reviewer 要根据测试与 diff 判断，并通过结构化决定 approved/changes_requested/rejected。Review 的提示也明确把 Artifact、diff 和工具输出当不可信数据。

**追问/陷阱：**不同 Agent 身份不意味着模型错误统计独立；同模型可能共有盲点。Reviewer 不能替代外部验收 oracle，尤其无法用弱测试证明完整需求。

**证据：**[禁止自审与指定身份](/home/sid/runguild/packages/database/src/review-repository.ts:572)、[Reviewer 输入要求](/home/sid/runguild/apps/worker/src/artifact-reviewer.ts:147)、[独立审查测试](/home/sid/runguild/packages/database/test/review-repository.pglite.test.mjs:226)。

### R39. 评审中又新增证据怎么办，为什么冻结证据 id？

**可口述答案：**Submission 保存选中的 `task_submission_evidence`，Reviewer 第一次领取时把材料冻结，后续恢复继续看这个集合。这样审批可以回答“到底审了哪组材料”，不会因为 Task 又多了一条测试而悄悄改变旧 Review。新变化需要新的有效提交流程，不能把旧批准泛化。

**机制/例子：**Review v1 时 Builder 再产生 E_new；旧快照不自动加 E_new。模型输入可对重复内容做有界去重，但持久快照保留每个选中 id、生产者和 attempt，不能丢掉证据来源关系。

**追问/陷阱：**冻结集合不表示集合里的每个事实永远可满足门禁；较新失败、代码变化和批准失效仍可能阻止 Task 最终完成。要区分评审审计输入与实时完成条件。

**证据：**[Submission 证据关系](/home/sid/runguild/packages/database/src/review-repository.ts:429)、[loadMaterials](/home/sid/runguild/packages/database/src/reviewer-execution-repository.ts:595)、[模型材料去重](/home/sid/runguild/apps/worker/src/artifact-reviewer.ts:56)。

### R40. Reviewer 产生决定后进程崩溃，会不会重复付费审核？

**可口述答案：**如果有效决定已持久化，就可以恢复应用该决定，不必再次调用模型。review_executions 区分 running、model_complete、completed，保存 decision、prompt/response 快照和租约；重领时看到 decision 非空，沿 model_complete 路径继续。若响应还没来得及持久化，则仍存在需要重试和潜在重复费用的窗口。

**机制/例子：**模型已 approved，completeModel 事务提交后 API 状态更新前宕机。恢复读取同一决定再执行幂等业务步骤，调用账本不会凭空出现第二次推理。

**追问/陷阱：**不能说所有崩溃都不重复计费。模型服务与本地数据库不在同一事务；服务端已算完、本地未存结果的窗口需要承认。

**证据：**[claim 与 model_complete](/home/sid/runguild/packages/database/src/reviewer-execution-repository.ts:317)、[completeModel](/home/sid/runguild/packages/database/src/reviewer-execution-repository.ts:414)、[Reviewer 恢复测试](/home/sid/runguild/packages/database/test/review-repository.pglite.test.mjs:575)。

## 六、集成、验收、评测与改进

### R41. 两个分支都过测试、Git 合并没冲突，为什么还要测候选合并？

**可口述答案：**各自正确不代表组合正确，Git 只处理文本与历史。对需评审的代码修改，a51ccc3 核对磁盘 Task HEAD 与当前 Worktree 记录一致，用它与当前目标 HEAD 构造候选并执行配置验证；再检查候选没被改、集成租约和该 Task 的审批有效、基线没前进，才发布。没有验证命令则拒绝集成。

**机制/例子：**断言 `8*rate<=limit`：Task 把 rate 从 10 改 12，原 limit=100 时通过；目标分支把 limit 改 90，原 rate=10 时也通过。组合 96>90 失败，文本可以完全无冲突，候选测试能拦住发布。

**追问/陷阱：**候选测试仍受断言强度限制。发布层未再次比较当前 HEAD 与批准 Submission 的冻结 commit，这是静态数据库检查边界，不能夸成已复现实战绕过。另一处 reviewRequired=false 的条件不一致已用真实仓储 SQL + PGlite 复现：发现及预留成功，发布前断言却因无批准而拒绝；未跑完整 Mission。候选测试虽用真实 Git，lost-lease 仍是 fakeStore 注入异常，不是真实 PG 租约竞争。

**证据：**[磁盘与记录 HEAD 核对](/home/sid/runguild/packages/workspace-tools/src/git-worktree-manager.ts:253)、[候选验证](/home/sid/runguild/packages/workspace-tools/src/git-worktree-manager.ts:376)、[发现条件](/home/sid/runguild/packages/database/src/worktree-repository.ts:304)、[审批检查](/home/sid/runguild/packages/database/src/worktree-repository.ts:364)、[HEAD 更新条件](/home/sid/runguild/packages/database/src/worktree-repository.ts:258)、[组合回归与 mock](/home/sid/runguild/packages/workspace-tools/test/git-worktree-manager.test.mjs:436)、[无评审集成反例](/home/sid/Voren_agent/docs/interview-prep/12-SOURCE-RECHECK.md)。

### R42. 审核通过的代码与主分支冲突怎么办？

**可口述答案：**不能让 Integration 随意改代码继续沿用旧批准。当前冲突恢复在 Task Worktree 准备待解决 merge，记录新的目标基线和错误，令旧提交退出有效集成队列并使旧 Submission 失效。Builder 修复冲突、commit、重测、重新提交版本，由独立 Review 再判断。

**机制/例子：**Reviewer 审的是 H1，但冲突解决产生 H2；H2 可能改变两边行为，必须有新的测试和批准。若只是无冲突合并，候选验证也要覆盖组合结果。

**追问/陷阱：**旧 Review 记录不应删除，它是历史事实；失效的是它作为当前交付批准的适用性。冲突解决仍可能失败并消耗 attempt，不能保证无限自动修复。

**证据：**[prepareIntegrationConflict](/home/sid/runguild/packages/workspace-tools/src/git-worktree-manager.ts:343)、[数据库冲突处理](/home/sid/runguild/packages/database/src/worktree-repository.ts:409)、[冲突测试](/home/sid/runguild/packages/workspace-tools/test/git-worktree-manager.test.mjs:277)。

### R43. 集成更新 Git 后，数据库标记前宕机怎么办？

**可口述答案：**Git 和 PG 没有共同事务，必须对这个窗口做恢复。重试读取真实目标 ref，判断是否已包含当前 Worktree 记录对应的 Task HEAD，重新构造或验证候选，再补 integrated 记录。正常发布前检查基线不变；未检出 ref 使用 `update-ref new old`，不覆盖已变化的旧 SHA。当前 HEAD 与批准快照的再次绑定缺口仍适用，不能把恢复核对说成独立证明审批内容。

**机制/例子：**第一次已经创建 merge commit M，PG 未记成功并回到 committed；Task 记录仍为 H。重试发现目标历史包含 H，不需要再制造同样 merge，但仍会验证候选。回归定义还覆盖源仓库变脏时拒绝、清理后恢复；该测试使用真实 Git 和 fakeStore，不是真实 PG 与 Git 同时宕机实验。

**追问/陷阱：**当前已检出分支走 clean+fast-forward，不应一概说全部发布都用 CAS，也不应承诺和任意外部 Git 操作原子化。token 复验和 Git 发布之间仍不是跨系统事务。

**证据：**[integrate 恢复](/home/sid/runguild/packages/workspace-tools/src/git-worktree-manager.ts:243)、[发布选择](/home/sid/runguild/packages/workspace-tools/src/git-worktree-manager.ts:422)、[崩溃恢复回归](/home/sid/runguild/packages/workspace-tools/test/git-worktree-manager.test.mjs:202)。

### R44. 所有 Task 都完成，为什么 Mission 还没完成？

**可口述答案：**Task 门禁证明各工作项满足平台条件，Mission 最终交付还需要用户认可精确版本。全部 Task 完成后系统进入 reviewing；approveDelivery 再检查所有 Task、当前候选 Artifact Version 和人类身份，创建 mission_delivery Approval 才进入 completed。这样不会把自动化流程跑通直接当作用户接受结果。

**机制/例子：**当前评测以全部 Task completed、Mission 为 reviewing 或 completed 计算 success，因此 Trial 可以 success=true，同时仍等待人工最终交付批准。各 Task 只需满足自己配置的证据、评审及集成条件；这个 success 不等于外部验收器重新证明全部需求。两种完成范围必须在报告里解释。

**追问/陷阱：**最终批准不能对任意“最新内容”泛化，必须有 expectedArtifactVersionId。用户要求修改时需有可审计返工流程，不是把终态字段随手改回去。

**证据：**[approveDelivery](/home/sid/runguild/packages/database/src/mission-repository.ts:521)、[Trial success 条件](/home/sid/runguild/packages/database/src/evaluation-repository.ts:584)、[最终版本测试](/home/sid/runguild/packages/database/test/orchestration.pglite.test.mjs:264)、[拒绝后的修复任务](/home/sid/runguild/packages/database/test/orchestration.pglite.test.mjs:338)。

### R45. 你会怎样评价单 Agent 与多 Agent？

**可口述答案：**先分当前能力和下一轮实验要求。当前场景版本保存需求、验收、Git baseline 与 single/multi 两份计划，按 repetition 配对并用独立 Trial ref 隔离代码；从账本收集成功、耗时、token、估计费用、重试和工具失败等指标。有效模型配置与预算还没有统一冻结，费用覆盖率和人工干预也未成为指标字段；这些是下一轮应补齐的条件。报告配对差值要先检查有效配对，不能只挑最好的一次。

**机制/例子：**single 用一个 Builder，multi 用 Researcher→Builder，是两种编排策略。baseline 相同仍不够；如果 Worker 环境覆盖改变实际模型或预算，比较就混入别的因素。任务图本身属于实验干预，失败 Trial 也应入结果。当前预制计划评测收集执行 Agent 和 Reviewer 费用，不能当作包含 Planner 推理费用的端到端成本。

**追问/陷阱：**PGlite 配对测试创建 Mission 后用夹具写入账本，证明物化及聚合规则，不证明真实模型跑完 Trial。下一轮即使完整冻结配置，也仍需多任务类型和重复；当前均值差不是显著性检验，尚不能推广“多 Agent 更优”。

**证据：**[场景字段](/home/sid/runguild/packages/protocol/src/evaluation.ts:36)、[指标字段](/home/sid/runguild/packages/protocol/src/evaluation.ts:96)、[评测物化](/home/sid/runguild/packages/evaluation/src/mission-driver.ts)、[账本聚合](/home/sid/runguild/packages/database/src/evaluation-repository.ts:660)、[配对报告](/home/sid/runguild/packages/evaluation/src/report.ts)、[成对 Trial 测试](/home/sid/runguild/packages/database/test/evaluation-repository.pglite.test.mjs:263)。

### R46. 真实评测没有证明多 Agent 更好，你怎么讲这个结果？

**可口述答案：**如实说首次配对只是系统故障发现：一轮 single 成功、multi 失败，而且 single 等待时修过平台 Review 恢复，耗时不能公平比较。后续仅 multi 的可靠性回归成功，证明那次链路能走通，不是新的优势对照。项目收获是从真实 trace 找到可复现的运行问题并定位修复。

**机制/例子：**历史记录里的 multi 出现未知/隐藏工具名、JSON 参数错误、探索耗尽 hop；这些促成有限协议纠错和交付预算窗口。Researcher 完成也不保证 Builder 交付成功，协调本身有成本。

**追问/陷阱：**不把失败美化成性能提升；也不用一次负结果断言多 Agent 永远无用。8/31 数据是历史文档记录，本次没有查 live DB 或重跑。

**证据：**[历史评测](/home/sid/runguild/docs/REAL_EVALUATION_2026-08-31.md)、[协议修复测试](/home/sid/runguild/packages/agent-runtime/test/runtime.test.mjs:281)、[预算配置](/home/sid/runguild/apps/worker/src/agent-main.ts:281)。

### R47. 账本里 estimatedCostUsd=0，可以说免费运行吗？

**可口述答案：**不能。当前一些兼容模型没有价格配置，LLM 费用为空，评测聚合仍用 COALESCE(SUM(...),0)，会把未知当零。token 和调用数仍可用，但费用比较必须标明 unknown/partial；后续应记录 pricedCalls/totalCalls、价格版本和估计规则，而不是只显示一个数字。

**机制/例子：**10 次调用只有 3 次有价格，求和显示的金额只是可定价部分。Reviewer 已有单独 reviewer_model_calls 并纳入当前收集，历史第一次 Trial 则发生在补齐前，不应默默改成完整成本数据。

**追问/陷阱：**输入/缓存输入/output 的费率可能不同，记录 usage 不等于已经能准确算账。当前没有配置价格时也不能声称已实现完整美元预算硬控制。

**证据：**[当前成本聚合](/home/sid/runguild/packages/database/src/evaluation-repository.ts:674)、[Reviewer ledger](/home/sid/runguild/packages/database/src/reviewer-execution-repository.ts:488)、[历史限制](/home/sid/runguild/docs/REAL_EVALUATION_2026-08-31.md)。

### R48. 你怎样判断测试证据够强，而不是“有很多测试就可靠”？

**可口述答案：**先区分平台测试与 Agent 生成产物的业务验收。平台测试验证状态、scope、幂等、证据绑定和故障恢复；产物验收必须检查需求行为。测试数量与退出码只说明现有断言成立，无法证明断言本身正确。关键需求最好先冻结外部验证器，并用 mutant 检查它真的能拒绝错误。

**机制/例子：**9/14 审计记录游戏的撞墙测试允许仍 running、增长测试未验证吃到食物变长，即使全部绿也不够。a51ccc3 修复测试和代码身份绑定、组合候选验证，未自动补强这些业务断言。

**追问/陷阱：**旧审计 198 通过、1 跳过不是本次 HEAD 的验证结果，本轮定向离线结果见 12。PGlite 断言不能代替独立 `_test` PostgreSQL 的多连接竞争；候选集成用真实 Git 加 fakeStore，Yjs 跨实例恢复用内存 repository/fanout。环境阻断也不能未经定位就报成项目逻辑失败。

**证据：**[本轮实际验证](/home/sid/Voren_agent/docs/interview-prep/12-SOURCE-RECHECK.md)、[候选拒绝回归](/home/sid/runguild/packages/workspace-tools/test/git-worktree-manager.test.mjs:436)、[PG 测试门禁](/home/sid/runguild/packages/database/test/postgres.integration.test.mjs:17)、[独立验收缺口审计](/home/sid/runguild/docs/AUDIT_2026-09-14.md)。

### R49. 如果只有一周继续做，你会先改什么，如何定义完成？

**可口述答案：**先把已确认的控制和发布条件写成回归，再选一项修完整：控制消费与应用的崩溃窗口，或无评审集成条件和批准 commit 再校验。统一 commit 后测试的提示也应随交付链修正。稳定入口身份、外部验收器与 mutant、有效模型预算冻结、unknown/partial 成本属于后续明确待办；一周不承诺全部完成。是否执行不可信仓库会改变容器隔离的优先级。这些是计划，不是已交付功能。

**机制/例子：**若选控制恢复，就故意在消费标记后、状态或 steering 写入前中断，恢复后必须最终应用一次或保留明确可重试状态。若选发布绑定，则在批准 H1 后改变 Worktree 记录为 H2，发布检查应拒绝不匹配；还要让 reviewRequired=false 的发现与发布规则一致。数据库复现和真实 Worker/Git 验证须分开记录。

**追问/陷阱：**一周不应承诺解决所有生产化问题。每项完成标准是可复现故障变成可观测拒绝或可恢复状态，再决定是否扩大范围；不要为了技术栈关键词先整仓改 Python。

**证据：**[控制消费窗口](/home/sid/runguild/packages/database/src/runtime-repository.ts:483)、[集成审批检查](/home/sid/runguild/packages/database/src/worktree-repository.ts:364)、[当前入口缺口](/home/sid/runguild/apps/web/src/App.tsx:939)、[提示冲突位置](/home/sid/runguild/apps/worker/src/agent-main.ts:298)、[未知成本](/home/sid/runguild/packages/database/src/evaluation-repository.ts:674)。

### R50. 用户说“任务一直卡在 reviewing”，你如何排查？

**可口述答案：**先分清卡的是 Mission 还是 Task，再沿关联 id 查事实链。Task reviewing 先查 reviewRequired 和 required 验收配置，需要评审才沿当前 attempt 的 Submission、Review 分派与执行状态排查，再查证据 gate 和 Worktree 集成；Mission reviewing 且所有 Task completed 则可能只是等待最终人类批准。不要先重启所有 Worker 或手工改 completed。

**机制/例子：**需评审任务的 Submission 已提交但 Review 不存在，可查 recoverPendingReviewAssignments。Review approved 但 Worktree committed，检查 Integration Worker、验证命令、冲突/基线错误；若 reviewRequired=false 且有代码修改，还要检查“发现允许、发布却要求批准”的当前不一致，不能诊断成单纯缺少 Reviewer。Worktree integrated 但 Task 未完成，再查证据和完成事务。用 correlation/run/task/submission id 串起事实。

**追问/陷阱：**修复观察与直接篡改事实不同。审批或验收不满足不能为了演示改数据库绿灯；实际执行过哪些查询、重跑和恢复，面试时必须如实报告。

**证据：**[Review 分派恢复](/home/sid/runguild/packages/database/src/review-repository.ts:330)、[Integration tick](/home/sid/runguild/packages/workspace-tools/src/integration-coordinator.ts:41)、[发现与发布条件](/home/sid/runguild/packages/database/src/worktree-repository.ts:304)、[任务门禁](/home/sid/runguild/packages/database/src/task-repository.ts:610)、[最终批准](/home/sid/runguild/packages/database/src/mission-repository.ts:521)。

## 练习顺序与个人证据记录

第一轮连续讲 R01、R06、R08、R11、R14、R17、R20、R31、R33、R41、R44：能把完整链条串起来。第二轮练 R13、R18、R21、R28、R34、R39、R43、R47、R48：主动解释边界与失败，避免只背理想流程。第三轮练 R03、R46、R49、R50：把“做过什么、知道什么、还没验证什么”说清楚。

每完成一次真实学习或复现，可以自己另记四项：读了哪个函数；提出了哪个可证伪判断；执行了什么命令或故障注入；结果与原判断是否一致。只有这些已发生记录，才逐步转成面试中的第一人称贡献。本文不替你补写尚未发生的经历。
