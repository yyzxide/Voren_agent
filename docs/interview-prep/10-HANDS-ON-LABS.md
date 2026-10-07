# 实操练习：用运行结果证明自己理解了项目

编写日期：2026-09-22。以下仍是供 Sid 执行的学习任务，不代表 Sid 已完成。第二轮复核已在临时基础 Python 环境、内存 PGlite 与临时 Git 仓库中执行部分定向测试；准确范围见 [12 复核记录](12-SOURCE-RECHECK.md)。未访问真实邮箱或调用付费模型。

## 使用方式与环境

先在现有干净副本或专门的学习分支中练习。数据库、邮箱、日历都使用临时测试数据；需要外部账户或真实模型的实验单独安排。不要在真实用户数据上做超时重发、误审批或恶意指令实验。

Voren 的完整锁定环境安装方式见 [README](../../README.md)。默认复现基线是 Linux x86-64 / CPython 3.12 与仓库 pylock.toml；其他平台不要假设这个锁文件的所有 wheel 都可用。完成安装并激活环境后，从 /home/sid/Voren_agent 运行本文的 Python 命令。

只练基础离线模块，也可在独立环境安装项目基础依赖。以下是替代的轻量安装，不等同于完整锁定基线；联网安装会解析当前允许范围内的依赖版本：

```bash
cd /home/sid/Voren_agent
python3.12 -m venv /tmp/voren-interview-venv
/tmp/voren-interview-venv/bin/python -m pip install --editable .
source /tmp/voren-interview-venv/bin/activate
python -m unittest tests.test_agent_loop tests.test_action_gateway -v
```

若缺少 python3.12 或 venv，先记录环境差异，不把安装失败算作业务测试失败。AgentDojo/MCP/Web 集成与完整 demo 另需 README 规定的依赖。

RunGuild 先确认 Node.js >= 22 和 npm，在 /home/sid/runguild 安装锁定依赖并构建后再运行选定测试：

```bash
cd /home/sid/runguild
npm ci
npm run build
```

PGlite 测试是嵌入式验证，SQL mock 是脚本化响应，两者均不能替代真实 PostgreSQL 多连接并发实验。本文会明确区分。

## L01. 追踪多轮模型消息

**目的：** 亲眼区分“模型提出调用”“程序执行工具”“结果进入下一轮输入”。

**操作：** 先读 [AgentLoop 测试](../../tests/test_agent_loop.py)，运行：

```bash
python -m unittest tests.test_agent_loop.AgentLoopTest.test_read_then_action_pauses_without_external_mutation -v
python -m unittest tests.test_agent_loop.AgentLoopTest.test_read_only_final_answer_completes_run -v
```

随后在学习副本中查看或打印脚本模型收集的 requests。列出每轮消息的 role、tool_call_id、工具名和结果来源；不要只看最终回答。

**预期与参考答案：** 读取工具的结果追加为 tool 消息，再交给下一次模型请求；模型请求写操作时得到 waiting_approval，审批前外部变更次数应为零。只读最终文本可以直接结束，不必生成外部操作提案。

**验收：** 不看答案，画出至少两次调用的完整消息顺序；说出消息列表在哪里增长，为什么 call ID 必须对应。错误表现是认为模型在自己内部调用了 Python 函数。

## L02. 参数校验和工具预算

**操作：** 运行下面的边界测试，再给学习副本增加一个非法日期区间案例：

```bash
python -m unittest tests.test_agent_loop.AgentLoopTest.test_unknown_tool_fails_without_execution -v
python -m unittest tests.test_agent_loop.AgentLoopTest.test_total_tool_call_budget_applies_inside_one_model_batch -v
python -m unittest tests.test_agent_loop.AgentLoopTest.test_mixed_read_and_action_batch_is_rejected_before_any_tool_runs -v
```

**预期与参考答案：** 工具名称必须已注册，参数要经过实际输入模型校验。一轮包含多个工具调用时，总调用预算仍逐个累计；读写混合批次在当前 Voren 中直接拒绝，不能先执行一部分再等审批。

**验收：** 给开始时间晚于结束时间的输入，定位 [动作契约](../../src/voren/adapters/workspace_contracts.py) 中拒绝的位置。再解释 Python 类型标注、模型生成 JSON、Pydantic 校验和业务授权各负责哪一层。

## L03. 用两个 SQLite 连接理解原子认领

**操作：** 运行 [原子认领测试](../../tests/test_action_gateway.py) 中的两个用例：

```bash
python -m unittest tests.test_action_gateway.ActionGatewayTest.test_two_connections_cannot_both_claim_one_authorized_operation -v
python -m unittest tests.test_action_gateway.ActionGatewayTest.test_conflicting_approval_cannot_replace_authorized_decision -v
```

**预期与参考答案：** 只有仍处于预期状态的操作能被认领；相互冲突的审批不能覆盖已保存的授权。程序内先查后写若没有数据库条件约束，会留下竞争窗口。

**扩展：** 下例是教学用的顺序模拟，只验证条件更新，不证明真实并发。可以直接用 Python 标准库运行：

```python
import sqlite3

db = sqlite3.connect(':memory:')
db.execute('CREATE TABLE operations (id TEXT PRIMARY KEY, status TEXT NOT NULL)')
db.execute('INSERT INTO operations VALUES (?, ?)', ('op-1', 'authorized'))

def claim(operation_id):
    with db:
        result = db.execute(
            'UPDATE operations SET status=? WHERE id=? AND status=?',
            ('committing', operation_id, 'authorized'),
        )
        return result.rowcount == 1

assert claim('op-1') is True
assert claim('op-1') is False
assert db.execute('SELECT status FROM operations').fetchone()[0] == 'committing'
db.close()
```

**验收：** 解释条件更新保护了什么，以及为什么它不能把外部 API 也纳入 SQLite 事务。真正并发实验还需要不同连接、明确事务交错和对结果的断言。

## L04. 外部成功但本地超时

**操作：** 阅读 [FakeWorkspaceAdapter](../../src/voren/adapters/fake_workspace.py) 的 before_commit、after_commit、ambiguous_without_commit、unexpected_effect 分支，分别运行：

```bash
python -m unittest tests.test_action_gateway.ActionGatewayTest.test_timeout_after_commit_recovers_by_observing_state -v
python -m unittest tests.test_action_gateway.ActionGatewayTest.test_unresolved_ambiguous_commit_is_not_retried -v
python -m unittest tests.test_action_gateway.ActionGatewayTest.test_unexpected_effect_is_not_erased_by_later_clean_observation -v
```

**实验记录表：**

| 故障 | 提交尝试次数 | 外部实际变化 | 第一次回执 | 恢复后状态 | 是否允许再次执行 |
|---|---|---|---|---|---|
| 发出前明确失败 | 自己记录 | 自己记录 | 自己记录 | 自己记录 | 根据契约说明 |
| 成功后超时 | 自己记录 | 自己记录 | 自己记录 | 自己记录 | 不能因超时直接重发 |
| 结果不可观察 | 自己记录 | 自己记录 | 自己记录 | 自己记录 | 保留不确定状态 |
| 正常提交后出现额外效果 | 自己记录 | 自己记录 | verification_failed | 自己记录 | 当前这一路径保留失败终态 |
| 提交超时且首次观察已有额外效果 | 自己记录 | 自己记录 | ambiguous | 后续干净观察可能 verified | 当前缺口：旧回执归档，但违规不阻止成功终态 |

**参考答案：** 相同的超时表现可以对应不同外部事实。恢复依赖原操作身份与观察结果；测试里的 Fake 能完整观察设定状态，真实连接器只能在其查询能力范围内判断。

## L05. 持久化回执与 Run 收尾

**操作：**

```bash
python -m unittest tests.test_run_lifecycle.RunLifecycleTest.test_waiting_run_resumes_after_store_and_ledger_reopen -v
python -m unittest tests.test_run_lifecycle.RunLifecycleTest.test_durable_receipt_finalizes_run_after_simulated_process_crash -v
python -m unittest tests.test_run_lifecycle.RunLifecycleTest.test_resume_is_idempotent_after_approval_was_already_persisted -v
```

**参考答案：** 页面状态、Run 状态、审批记录和操作回执不是同一个字段。回执已经存在但 Run 未收尾时，应复用回执推进状态，不能再次改变外部系统。事件去重也需要验证同 key 对应相同内容，不能把冲突静默忽略。

**验收：** 手写表格说明每个故障点哪些记录已经落库、哪些没有。只回答“用 SQLite 保存，所以能恢复”不合格。

## L06. 检查点恢复能证明什么，不能证明什么

**操作：**

```bash
python -m unittest tests.test_transcript_recovery.TranscriptRecoveryTest.test_crashes_inside_a_model_step_replay_saved_response_and_reads -v
python -m unittest tests.test_transcript_recovery.TranscriptRecoveryTest.test_wrong_key_cannot_decrypt_checkpoint -v
python -m unittest tests.test_transcript_recovery.TranscriptRecoveryTest.test_cancelling_a_saved_response_keeps_consumed_model_budget -v
```

**参考答案：** 测试验证保存的模型响应与读取结果可以用于重建循环状态，已消耗预算不会因恢复清零。加密与完整性检查保护持久上下文，但密钥丢失会妨碍恢复。

**关键反例：** 继续读 [模型适配器](../../src/voren/providers/openai_responses.py) 的 _cached_outputs 和 _serialize_messages。真实适配器重建后缺少此前原始输出会拒绝序列化；脚本模型恢复通过不能替代这条路径的恢复证明。再读 [Web submit](../../src/voren/web/service.py)，确认当前没有传 transcript_store。

**验收：** 画三列：运行状态恢复、Loop checkpoint 恢复、provider 协议恢复。分别写出所需数据和当前支持边界。此处不应给真实模型续跑标记“已验证”。

## L07. 知识、记忆与 Skill 更新

**操作：**

```bash
python -m unittest tests.test_knowledge_retrieval tests.test_memory_context tests.test_skill_routing -v
python -m unittest tests.test_skill_candidates tests.test_skill_candidate_evaluation tests.test_learning_evidence -v
python scripts/demo_skill_learning.py --help
```

需要学习完整演示时，按脚本 help 指定实验输出位置并保留生成的 artifact；不要把演示结果写成真实模型提升。脚本中的评估器是确定性的指令契约检查。

**参考答案：** 未激活知识版本不进入当前检索；外部文本保留来源但不获得指令授权。候选 Skill 基于允许的证据，不能改工具/效果合同声明，但此规则不是逐 Skill 的运行时权限隔离。评测要求案例与显式登记的证据 case ID 不重叠，不能自动保证真正独立；接受后仍需要显式启用。回滚应检查当前激活版本，不能覆盖更晚的发布。候选单元测试中部分用例关闭了持久证据检查，真实证据准入应结合 test_learning_evidence 的伪造引用拒绝用例验证。候选 promote 的门禁不等于管理员直接 install --activate 路径也强制经过同一评测；80 行配置还存在已复现的前缀漏计，详见 12。

**验收：** 自己构造三个候选：只有表面改写但无测得改善、正常任务退步、尝试扩大工具权限。预判它们分别在哪一层被拒绝，再看测试或演示是否支持判断。

## L08. 独立做一个最小 RAG 练习

**输入：** 自己编写的项目说明片段，不含真实邮箱、凭据或工作内部资料；问题覆盖单段答案、多段答案、无答案和版本冲突。

**操作：** 先用关键词检索实现基线，记录每题的 query、候选 ID、排名、来源、必要证据是否命中。然后再做 embedding 检索或混合检索，保持文档和问题不变；加入生成后，把检索错误与回答错误分开统计。

**参考答案：** 如果必要片段根本没有被召回，应先检查切分、词项/向量表示和检索条件；如果证据已经提供但回答仍错误，再检查上下文噪声、指令和模型。没有答案时应明确说明证据不足。引用必须对应实际使用的来源。

**验收：** 交付一张逐题对比表和三个失败案例分析。只展示几个看起来不错的回答，不算完成。这个实验是新增学习任务，不能倒推 Voren 已有向量 RAG。

## L09. 从 TypeScript 的一个调用链入手

**操作：** 从 [ConversationPlanner](/home/sid/runguild/apps/worker/src/conversation-planner.ts) 的 process 开始，标出 await、模型调用、计划持久化和失败处理，再运行上下文测试：

```bash
cd /home/sid/runguild
node --test packages/agent-runtime/test/context-builder.test.mjs
```

**参考答案：** async 函数返回 Promise，await 使当前函数等待该结果，但不自动把全部业务事务串行化。上下文裁剪要保持 assistant 工具请求和结果成组；估计 token 不是具体 tokenizer 的严格上界。

**验收：** 把一个成功返回的联合类型和一个错误分支用自己的话翻译成 Java 风格控制流；修改预算后预测哪些历史单元被保留，检查结果。

## L10. 任务认领、消息与租约

**操作：**

```bash
node --test --test-concurrency=1 packages/database/test/task-repository.test.mjs packages/database/test/inbox-repository.pglite.test.mjs
```

**参考答案：** task-repository.test.mjs 的部分断言使用 scripted Pool，验证事务语句和控制流，不是两个真实 PostgreSQL 连接同时争锁的实验。PGlite 验证一部分 SQL 行为，也应说明与实际部署的差异。

**进一步实验设计：** 在专用 PostgreSQL 测试库，用两个连接尝试领取同一任务；在连接 A 持锁时让 B 尝试领取，随后提交/回滚 A。记录每一步的返回与锁行为。再验证 renewLease 对旧 Task token 或已过期租约返回 null，以及工具执行 finish 拒绝接管后已被替换的旧 execution_token。finish 当前检查 token 和 running 状态，没有直接检查墙钟时间是否超过租约期限；单纯过期和已被接管要分开测试。completeTaskAndUnlockDependents 不接收 leaseToken，证据写入也没有统一的 Task lease 检查，要单独推演它们的状态、版本和调用者约束，不能预设都被相同 token 拦截。数据库准备按项目 README，不能连接工作库。

**验收：** 画出认领任务与插入 Run/lease/events/outbox 的事务边界；列举 outbox 发送前后崩溃的重复/延迟风险及消费端处理。

## L11. 证据绑定与较新失败

**操作：**

```bash
node --test --test-concurrency=1 packages/database/test/review-repository.pglite.test.mjs
```

同时读 [evidence-gate.ts](/home/sid/runguild/packages/database/src/evidence-gate.ts)。构造纸面序列：提交 A 测试通过 → 修改成 B → 试图完成；同一提交某命令先通过 → 后来同命令失败 → 试图引用旧成功。

**参考答案：** 门禁按计划中的 required criteria/evidence kinds 检查当前 attempt 或匹配的 committed tree、干净/稳定状态；reviewRequired 为真时还有评审绑定。不能只查“历史上存在一条 passed=true”，也不能假设每个计划都强制 test_run 与独立评审。评审提交冻结证据集合，后续新证据不能悄悄改写原审查对象；发布层是否重新把当前 HEAD 与这份批准快照绑定，需要另外检查。

**验收：** 指出以上两个反例分别被 SQL 的哪段条件拦截，并解释测试代码通过不代表所有业务需求都有充分断言。

## L12. 集成候选与宿主边界

**操作：**

```bash
node --test --test-concurrency=1 packages/workspace-tools/test/git-worktree-manager.test.mjs
```

这些测试在真实临时 Git 仓库中执行命令，但数据库侧使用 fakeStore；lost-lease 场景通过替换 assertIntegrationLease 抛错模拟，不能当成真实数据库过期竞争已经被验证。先阅读临时目录创建和清理逻辑。学习副本中构造两个分支：A 修改接口，B 添加依赖旧接口的调用，观察无文本冲突但组合验证失败的情况。

本轮其中 9 项通过，advanced-base 用例在验证子进程调用 Git 时遇到 `spawnSync git EPERM`，未走到预期的基线变化断言，不能记作该场景验证通过。另用内存 PGlite 验证了无评审任务可被发现，却被发布前的审批断言拒绝；发布层与批准提交的精确绑定也需独立核对。具体命令和边界见 [12 复核记录](12-SOURCE-RECHECK.md)。

**参考答案：** 集成候选是新的代码树，需要重新运行验证；验证期间候选被改、租约失效或目标基线前进，都应阻止沿用旧验证发布。Worktree 分离工作目录，未限制宿主文件、网络和子进程权限。

**验收：** 展示候选失败后目标分支没有被这次失败操作更新；提出受限容器方案时说明其是后续设计，而不是现有测试已经证明的隔离。

## L13. Yjs 增量与固定交付版本

**操作：**

```bash
node --test --test-concurrency=1 packages/collaboration/test/artifact-repository.pglite.test.mjs
```

阅读 [ArtifactRepository](/home/sid/runguild/packages/collaboration/src/artifact-repository.ts)，分别追踪 appendUpdate 和 createVersion。设计“同一个 update 重放两次”“冻结版本后继续编辑 LIVE 文档”“服务重启后从快照和增量重建”的例子。

**参考答案：** 协同更新的合并与去重服务于文档同步；固定版本为评审提供稳定对象。LIVE 文档继续编辑不应改变已冻结版本。内容收敛不能保证业务正确或审批有效。

**验收：** 能画出 live state、update log、snapshot、artifact version 之间的关系，并指出 Git 代码合并不由 Yjs 负责。

## L14. 小编码题：DAG 调度与循环检测

**题目：** 给任务依赖图，输出当前可执行任务；依赖全部完成才能执行，有环时拒绝；任务失败不能伪装完成；同一任务重复扫描不应重复创建同一 attempt。

**参考解法：** 先校验节点存在、无重复/自依赖，使用拓扑排序或 DFS 检测环；运行中计算未满足依赖，并通过持久化状态与唯一约束控制调度身份。内存集合可帮助展示算法，但不提供多进程并发保证。

**必测输入：** 空图、孤立任务、链、菱形依赖、缺失父节点、环、失败父任务、两个扫描器同时发现 ready。先实现纯函数算法，再讨论数据库如何把“发现可执行”变成“原子领取”。

**验收：** 说清算法复杂度和数据库不变量；不能只给出拓扑排序代码就宣称实现了持久化调度系统。

## 每次实验交付什么

1. 目的和要验证的假设。
2. Git commit、运行环境、具体命令、是否使用 Fake/脚本模型/真实模型。
3. 预期状态变化与实际结果。
4. 支持结论的日志、表记录或断言。
5. 未覆盖的场景与下一步。

当前学习记录从空白开始，不把这份练习说明当作已通过报告。完成 L01—L07 后做一次 Voren 模拟面试；完成 L09—L13 后做一次 RunGuild 模拟面试；L08、L14 用于检验可迁移的应用与编码能力。
