# 第二轮源码复核：调用链、文档纠错与实际验证

日期：2026-09-22。本文记录用户要求重新阅读源码之后完成的核查。它替代初稿中“本轮仅静态核查”的总括，不改变历史实验的原始日期或结论。

## 1. 这次怎样核查

- Voren 基线：`c76ace8829449137d767b38a654ab92576b5c7f1`。
- RunGuild 基线：`a51ccc37004e12f49b21a77c5b3cc7f23267b2e1`。
- 主代理重新阅读两条用户入口到结束的主链，包含状态变化、事务、模型和工具调用、审批、完成条件及恢复分支；并核对独立审查发现的问题。
- 两个项目审查子任务逐题通读 V01—V50、R01—R50，对照实现与测试正文找错；另一个子任务交叉检查学习地图、口述稿、练习与事实边界。
- 本轮修正的是学习文档。没有修改业务源码、原仓库测试或项目配置，没有提交 Git。下面标出的实现缺口仍存在于上述基线。

这是核心业务链与文档主张的定向复核，不是每个文件逐行审计。测试通过只证明给定夹具下的断言，不证明真实账户、完整产品流程、生产并发或防护绝对有效。

## 2. Voren：亲自追踪的主调用链

以下以 Web 提交自然语言任务为主。真实自然语言 CLI 是 `voren agentdojo` / `voren google`；管理命令及固定动作 demo 不共享所有装配能力。

| 阶段 | 实际入口与动作 | 状态、数据与边界 |
|---|---|---|
| 接收任务 | [POST /api/runs](../../src/voren/web/app.py:153) → [submit](../../src/voren/web/service.py:200) | 同步服务函数开始处理；不是只投递后台任务就返回 |
| 请求去重 | [reserve](../../src/voren/web/index.py:44) | BEGIN IMMEDIATE 内绑定 client_request_id、请求摘要、run_id；已有同内容结果可返回，处理中则冲突 |
| 装配上下文 | [Web 装配](../../src/voren/web/service.py:230) | Workspace 读工具加 Knowledge MCP，选择 Memory/Skill；Web 没传 TranscriptStore |
| 创建 Run | [AgentLoop._execute](../../src/voren/runtime/agent_loop.py:160) → [start_new_run](../../src/voren/runs/manager.py:48) | 创建 created 再进入 running；冻结 RunConfig，与装配的版本引用比对 |
| 模型调用 | [模型请求与响应](../../src/voren/runtime/agent_loop.py:280) | 增加调用尝试计数、记录事件，调用 Adapter；序列化失败也可能在未发 HTTP 前结束 |
| 只读工具 | [读取分支](../../src/voren/runtime/agent_loop.py:460) | 顺序执行调用，校验观察摘要、工具身份与字节预算，再追加 tool 消息进入下一轮；适配器异常使 Run 失败 |
| 外部动作提案 | [propose_action](../../src/voren/runs/manager.py:93) → [prepare](../../src/voren/actions/gateway.py:65) | 参数校验、Effect 清单、Proposal 摘要、operations prepared；Run waiting_approval，Loop 返回 |
| 保存页面结果 | [RunView 保存](../../src/voren/web/service.py:308) | 提案和状态成为 Web 快照；这时第一个 HTTP 请求才结束 |
| 审批请求 | [decide](../../src/voren/web/service.py:325) → [resume_with_approval](../../src/voren/runs/manager.py:270) | 校验决定身份与摘要；复用已持久化审批；拒绝取消 Run，无效审批与拒绝不同 |
| 领取并执行 | [commit](../../src/voren/actions/gateway.py:105) → [mark_committing](../../src/voren/actions/ledger.py:105) | 授权、条件状态更新后派发外部操作；回执已存在则复用；第三方写入不在 SQLite 事务内 |
| 核验与结束 | [观察](../../src/voren/actions/gateway.py:242) → [finalize](../../src/voren/runs/manager.py:413) | verified 对应 completed；ambiguous/verification_failed 对应 needs_reconciliation；不会返回模型继续第二个独立动作 |
| 后续核对 | [reconcile](../../src/voren/actions/gateway.py:153) | 观察既有操作，不再次 commit；不同旧回执分支的限制不相同，见第 4 节 |

**事务不能混说：** [RunStore.transition](../../src/voren/runs/store.py:111) 在一个事务中改变 Run 并追加事件；Proposal 登记通过另一个账本连接执行。即使在同一 SQLite 文件，也不表示“登记 Proposal、改变 Run、外部操作、Web 快照”是一个总事务。硬崩溃后的 reservation、未关联提案和页面快照恢复要分别推演。

**两种入口不能混说：** [自然语言 CLI](../../src/voren/cli.py:745) 注入 TranscriptStore，Memory 默认空、显式选择后加载，未拼接 Knowledge MCP；没有用户可调用的 Loop 续跑子命令。Web 默认读取活跃 Profile 并接 Knowledge，但没有模型循环检查点。真实 Provider 缺原始输出缓存的重建限制已通过离线测试确认。

## 3. RunGuild：亲自追踪的主调用链

以下主流程采用计划要求测试证据、`reviewRequired=true` 且有代码修改的任务。其他计划配置有不同分支，不能把这一套要求泛化到所有任务。

| 阶段 | 实际入口与动作 | 状态、数据与边界 |
|---|---|---|
| 发需求 | [sendMessage](../../../runguild/apps/web/src/App.tsx:939) | 先保存消息，再创建规划请求；是两次 HTTP 调用，不是同一事务 |
| 建立规划请求 | [ConversationPlanningRepository.create](/home/sid/runguild/packages/database/src/conversation-planning-repository.ts:196) | 校验成员、来源消息与 Planner；同一事务保存 Mission、请求、Inbox、Outbox |
| Planner 运行 | [ConversationPlanner.process](/home/sid/runguild/apps/worker/src/conversation-planner.ts:193) | 领取规划租约；保存模型结果与计划，提议 revision，进入待审批；保存模型结果与提议计划也要考虑中断边界 |
| 人批准计划 | [approvePlan](/home/sid/runguild/packages/database/src/mission-repository.ts:360) | 锁 Mission/revision，检查 expectedVersion；事务创建 Task、验收标准与依赖，Mission running |
| 调度与领取 | [dispatchReadyTasks](/home/sid/runguild/packages/database/src/scheduler-repository.ts:53) → [claimTask](/home/sid/runguild/packages/database/src/task-repository.ts:246) | 调度器写 Dispatch/Inbox/Outbox；Worker 凭 dispatch token 原子领取，Task claimed、Run starting、创建 Task lease |
| 装配并运行 | [AgentInboxProcessor.execute](/home/sid/runguild/apps/worker/src/agent-loop.ts:331) → [AgentRuntime.run](/home/sid/runguild/packages/agent-runtime/src/runtime.ts:259) | 加载任务上下文并续租；循环先消费控制、恢复待完成工具，再构建上下文并调用模型 |
| 工具执行 | [ToolGateway.execute](/home/sid/runguild/packages/tool-gateway/src/tool-gateway.ts:87) | 服务端决定风险，持久预留和幂等重放；需要批准则等待，不确定副作用不能当作未执行 |
| 代码与证据 | [repo.commit](/home/sid/runguild/packages/workspace-tools/src/workspace-tools.ts:786)、[test.run](/home/sid/runguild/packages/workspace-tools/src/workspace-tools.ts:998) | 提交、测试和 Git 快照记录为持久证据；passed、HEAD、tree、clean/stable 与计划要求分别检查 |
| Submission / Run 结束 | [submitArtifactVersion](/home/sid/runguild/packages/database/src/review-repository.ts:429) → [CompletionVerifier](/home/sid/runguild/packages/database/src/completion-verifier.ts:22) | 冻结版本与筛选后的证据；需评审任务有本 Run Submission 才可结束执行，Task 仍可 reviewing |
| 独立评审 | [ArtifactReviewer](/home/sid/runguild/apps/worker/src/artifact-reviewer.ts:179)、[reviewSubmission](/home/sid/runguild/packages/database/src/review-repository.ts:572) | 模型决定先保存再应用；changes_requested 可返工，approved 仍受 Task 完成门禁限制 |
| 合并候选 | [IntegrationCoordinator](/home/sid/runguild/packages/workspace-tools/src/integration-coordinator.ts:49) → [GitWorktreeManager](/home/sid/runguild/packages/workspace-tools/src/git-worktree-manager.ts:242) | 临时 Worktree 生成候选、验证、检查当前记录/租约/审批/目标基线，再发布；审批与 HEAD 绑定的独立再检查不足 |
| 解锁与交付 | [completeTaskAndUnlockDependents](/home/sid/runguild/packages/database/src/task-repository.ts:610) → [approveDelivery](/home/sid/runguild/packages/database/src/mission-repository.ts:521) | Task completed 后解锁必需父任务全完成的后继；全部 Task 完成使 Mission reviewing，人批准当前最终版本才 completed |

**恢复的控制权：** [runWorkerTick](/home/sid/runguild/apps/worker/src/tick.ts:50) 先回收失效 Task 租约和待重分派 Review，再调度并发布 Outbox；[Agent tick](/home/sid/runguild/apps/worker/src/agent-loop.ts:250) 消费 Inbox 后查可运行 Run。持久记录、通知、租约、工具重放各解决不同窗口；它们不构成跨 PG、Redis、Git 和外部 API 的单个原子操作。

## 4. 这轮具体纠正了什么

| 初稿问题 | 修正后的结论 | 证据强度 |
|---|---|---|
| Voren 所有违规观察都永久阻止成功 | 带违规的 verification_failed 保留失败；首次超时分支生成的 ambiguous 可在后续干净观察后变 verified，旧回执归档 | 源码 + 最小反例实际执行 |
| Skill 新增/删除绝对不超过 80 行 | 配置是 80 行，但 `_changed_lines` 把某些正文误当 diff header；101 条新增实际可被计为 1 条，compare_packages 接受 | 源码 + 最小反例实际执行；16,000 bytes 上限仍在 |
| held-out 自动排除所有训练原题 | 只排除显式登记的 evaluation_case_ids；遗漏登记的原题也可能通过 | policy/evidence 源码 |
| 所有候选评测都强制 raw behavior | 强制检查在 AgentDojoSkillEvaluator，通用 Runner 可接任意 evaluator | 两种入口对照 |
| Skill 合同不变等于工具/副作用权限隔离 | 准入保护合同声明；实际工具集合由应用装配，未按 Skill scope 裁剪 | Admission + Loop 装配 |
| CLI 与 Web 有相同上下文和恢复能力 | 区分自然语言 CLI、管理 CLI、固定 demo；Memory/MCP/Transcript 装配不同，CLI 无 Loop 续跑子命令 | CLI/Web/脚本入口对照 |
| Google 私人占位及核验范围过宽 | 指无 attendees、不请求邀请，不保证可见性仅本人；草稿查询不穷举所有重复对象，通知字段也不是独立发送历史 | Adapter/HTTP Double 测试定义；未真实账号验证 |
| Skill 不兼容直接意味着 no_skill；model_requests 是 HTTP 次数 | 不兼容版本先排除，其他版本仍可匹配；model_requests 计调用尝试 | 路由与 Loop 源码 |
| RunGuild 冻结了完整有效模型配置 | 数据库模型标识冻结；Worker 环境仍能覆盖模型、endpoint、reasoning 和预算 | ExecutionContext 与 modelFor 对照 |
| 用户取消直接 abort 在途调用 | 用户 cancel 在控制检查点生效；Worker/租约 AbortSignal 另一路。一批工具的后续调用可先执行 | 源码 + 脚本模型反例实际执行 |
| 持久控制一定可靠应用 | 先标 applied，再改变 Run 或写 steering，中间崩溃可丢应用 | 静态窗口；未杀进程复现 |
| 所有任务都必须 test_run 和独立评审 | 证据由 required criteria/kinds 决定；Review 由 reviewRequired 决定 | 计划、Verifier、SQL 对照 |
| 无评审代码任务已完整接通集成 | Discovery 允许，发布前断言却无条件要求 approved Review | 内存 PGlite 仓储层反例实际执行；未执行完整 Mission |
| 发布前独立核实了审批对应的精确 HEAD | 当前比较磁盘 HEAD 与 Worktree 记录，并检查 Task 有批准；未再连接批准快照中的 commit | 静态检查边界；没有声称复现 Worker 绕过审批 |
| Git 恢复证明了提交属于原 tool call | 检查实际 Git 状态可避免盲目再提交，但缺少操作与 commit 的唯一归属证明 | commit handler 源码；依赖无未受控并发写入 |
| 评测已冻结全部配置并记录完整成本/人工介入 | Scenario、Trial、执行账本已有；有效模型预算、价格覆盖率、人工介入及 Planner 成本范围仍需补齐 | Scenario/collector 源码 |
| Redis 任意丢消息都被发现；模型直接提交 risk 字段 | Yjs 全量同步明确挂在 subscriber 重连恢复；正常模型调用的风险由服务端 riskFor 填充 | fanout/Runtime/Gateway 对照 |

修正已同步到 01—04，并更新 00、06—08、10—11 中相关学习要求和口述内容。172 道核心问答数量未变；新增本记录供核查，避免靠增加题量掩盖原表述问题。

## 5. 实际执行的验证

### 5.1 环境与隔离范围

Voren 使用 CPython 3.12.3，在 `/tmp` 创建临时环境，只从已有 pip 缓存安装基础依赖；Pydantic 2.13.5、cryptography 45.0.7、PyYAML 6.0.3。没有联网安装，**不是完整 pylock 环境**，未安装全部 Web/AgentDojo 集成依赖。通过 `PYTHONPATH=src` 和 `-B` 读取当前源码。

RunGuild 从当前 Git HEAD 导出到 `/tmp`，复用已有依赖，并让 `@runguild/*` 指向临时副本；使用 Node 24.15.0、TypeScript 5.9.3 重新执行 `tsc -b`，通过后再测试。没有使用未核对的旧 dist 作为本轮证据。PGlite 是内存库，Git 测试操作临时仓库，未连接业务数据库或调用真实模型。

### 5.2 既有测试结果

| 验证组 | 结果 | 能说明什么 |
|---|---|---|
| Voren Loop、Gateway、Run 生命周期、Transcript、Skill candidate/paired evaluation/learning evidence，7 个模块 | 76 项通过 | 指定离线夹具中的状态、预算、审批、恢复与候选规则 |
| Responses Adapter 重建缺缓存 | 1 项通过 | 重建 Adapter 遇历史工具调用会抛 ModelTranscriptError；使用 FakeTransport |
| RunGuild Runtime、Worker、Completion、Review，4 个测试文件 | 38 项通过 | 脚本模型/内存持久化与 PGlite 下的现有断言 |
| GitWorktreeManager，1 个测试文件 | 9 项通过，1 项未走到目标断言 | 真实临时 Git + fakeStore；受限项为 advanced-base |
| 新增临时反例 | 4 类结果得到确认 | 见下一节；反例断言通过表示缺口得到复现，不表示缺口已修复 |

Python 实际命令的逻辑形式如下，`python` 指上述临时环境解释器：

```bash
cd /home/sid/Voren_agent
PYTHONPATH=src python -B -m unittest tests.test_agent_loop tests.test_action_gateway tests.test_run_lifecycle tests.test_transcript_recovery tests.test_skill_candidates tests.test_skill_candidate_evaluation tests.test_learning_evidence -v
PYTHONPATH=src python -B -m unittest tests.test_openai_responses_adapter.OpenAIResponsesModelAdapterTest.test_reconstructed_adapter_rejects_missing_provider_output -v
```

RunGuild 在导出的、已完成 `tsc -b` 的临时副本根目录运行：

```bash
node --test --test-isolation=none --test-concurrency=1 --test-reporter=tap packages/agent-runtime/test/runtime.test.mjs apps/worker/test/agent-loop.test.mjs packages/database/test/completion-verifier.pglite.test.mjs packages/database/test/review-repository.pglite.test.mjs
node --test --test-isolation=none --test-reporter=tap packages/workspace-tools/test/git-worktree-manager.test.mjs
```

初次使用进程隔离时，输出只有文件级结果；为取得逐用例诊断改用 `--test-isolation=none`。Git 的 advanced-base 用例实际遇到 `spawnSync git EPERM`，验证子进程提前退出，断言期望的 “base advanced during verification” 没有出现。为定位原因，只在临时副本给编译产物加入 stderr 输出，捕获到该错误后恢复；没有修改原始实现或测试。这里既不能报告场景通过，也不能仅凭该环境拒绝判定集成算法错误。

原始记录：[Voren 76 项](evidence/voren-core.log)、[Provider 1 项](evidence/voren-provider.log)、[RunGuild 38 项](evidence/runguild-core.log)、[Git 10 项结果](evidence/runguild-git.log)、[Git 错误诊断](evidence/runguild-git-diagnostic.log)。完整依赖和真实 PostgreSQL 多连接测试仍未运行。

### 5.3 四个最小反例怎样构造

**一，超时后的违规回执可转为成功。** 使用现有 [ActionGatewayTest 夹具](../../tests/test_action_gateway.py:27) 与 FakeWorkspaceAdapter：选择 `unexpected_effect`，在真实模拟写入后额外抛 `AmbiguousCommitError`。首次回执为 ambiguous，包含 `unexpected_audit`。随后将观察函数改成过滤掉该项，再调用 `reconcile`：结果变为 verified，commit_attempts 仍是 1，旧回执归档 1 条。这验证了状态分支缺口；隐藏观察项模拟后续查询看不到旧违规，不代表真的撤销了外部副作用。

**二，Skill 行数漏计。** 在原 SKILL.md 指令后添加 1 条普通行和 100 条以 `++` 开头的行，保持 metadata、合同和其他文件不变。`_changed_lines` 只报告新增 1 行；进一步调用现有 [compare_packages](../../src/voren/learning/policy.py:73)，101 条真实新增仍被接受。原因是统一 diff 中这些正文行以 `+++` 开头，被误当文件头跳过。字节数仍在 16,000 上限内；没有证明字节限制失效。

**三，批次内用户取消延迟。** 复用 [Runtime 测试夹具](../../../runguild/packages/agent-runtime/test/runtime.test.mjs:21)，脚本模型一次返回两个只读调用。第一个工具完成时加入持久控制夹具的 cancel，第二个仍执行；进入下一循环后 Run 才 cancelled。最终工具调用数 2。此例验证控制检查点的位置，不涉及真实付费模型或外部副作用。

**四，无评审代码任务的集成条件不一致。** 在 [Worktree PGlite 夹具](../../../runguild/packages/database/test/worktree-repository.pglite.test.mjs:212) 的 `review_required=false`、committed、Task reviewing 状态下，先调用 `listApprovedPendingIntegration`，确认任务被选入，再 `reserveIntegration` 取得 token。随后 `assertIntegrationLease` 拒绝，原因为没有 approved Review。使用真实仓储 SQL 与内存 PGlite；未跑完整 Git 发布或真实 Mission。

日志：[Voren 回执/行计数反例](evidence/voren-probes.log)、[RunGuild 取消/集成反例](evidence/runguild-probes.log)。第二个反例还额外验证了完整 compare_packages 的接受行为，而不只调用计数辅助函数。

## 6. 后续学习时怎么使用这轮发现

先读 01 的请求—提案—审批—回执，再用第一个反例画出两个不同的状态分支；这能检验自己是否把“意图上的安全规则”误认成了全部实现。进入 RunGuild 后，把“用户 cancel”“Worker abort”“Task lease”“tool execution token”“评审批准”分别标到调用链上，说明各自在哪个函数生效。

面试讨论缺陷时，可以说清具体条件、现有行为、影响和建议；没有亲自完成的改造不说成自己的修复。下一轮若修改源码，应重新验证这些反例以及原有回归，并更新此处基线。当前资料已按实际行为校准，代码缺口本身仍待独立处理。
