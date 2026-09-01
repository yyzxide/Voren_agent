# 系统架构

[English](ARCHITECTURE.md)

## 1. 设计原则

1. **模型负责提出建议，确定性代码负责授权与执行。**
2. **外部任务是否成功，要根据最终状态判断，而不是根据流畅的模型输出判断。**
3. **Observation 是数据，不是权限。**
4. **经验必须先成为 Candidate，之后才可能成为正式行为。**
5. **每个被晋升的行为都必须绑定证据、版本和评测结果。**
6. **先完成一个完整纵向切片，再扩展通用平台能力。**

## 2. 系统上下文

```text
                                  +----------------------+
                                  | Evaluation Runner    |
                                  | 任务效果 + 安全评测   |
                                  +----------+-----------+
                                             |
                                             v
+----------+     +---------------+    +------+-------+    +----------------+
| Operator |<--->| CLI / API     |<-->| Run Manager  |<-->| Agent Runtime  |
+----------+     +---------------+    +------+-------+    +-------+--------+
                                             |                    |
                                             |                    v
                                      +------+-------+    +-------+--------+
                                      | Event Store  |    | Action Gateway |
                                      +------+-------+    +-------+--------+
                                             |                    |
                         +-------------------+                    v
                         v                               +--------+---------+
                +--------+---------+                     | World Adapters   |
                | Learning Control |                     | 首先 AgentDojo   |
                +--------+---------+                     +------------------+
                         |
                         v
                +--------+---------+
                | Memory / Skills  |
                +------------------+
```

第一个实现可以把所有组件运行在同一个进程中。这些是逻辑契约边界，不要求
一开始就拆成微服务。

## 3. 运行时流程

1. `RunManager` 创建不可变 Run 配置，其中包含模型、Prompt、Policy、工具
   Schema 和 Active Skill 版本；
2. `ContextAssembler` 加载操作者请求、可信 Profile 条目以及可用 Skill
   Metadata；
3. `AgentRuntime` 请求下一次模型响应；
4. 文本输出可以结束 Run；Allowlist 中的 Pure Read 生成带结构化 Provenance
   Label 的 `ToolObservation`，随后返回模型；
5. 被分类为外部动作的调用转化为 `ActionProposal`，Model/Tool Loop 绝不直接
   执行它；
6. `ActionGateway` 校验 Action Schema，并具体化精确 Effect Manifest；
7. Run 进入暂停状态，Approval Request 绑定规范化 Proposal Digest；
8. 获得授权后，Adapter 使用 Operation Key 提交动作；
9. Verifier 读取最终外部状态并生成 `ActionReceipt`；
10. 终止结果被记录，并可在之后进入学习候选流程。

## 4. 核心组件

### 4.1 Run Manager

负责生命周期与持久状态转换：

```text
created -> running -> completed
                \-> failed
                \-> waiting_approval -> completed
                                      \-> failed
                                      \-> cancelled
                                      \-> needs_reconciliation
```

它负责暂停/恢复、取消、预算以及进程中断后的恢复。恢复的 Run 继续使用创建时
冻结的精确配置。

### 4.2 Agent Runtime

Runtime 刻意保持较小范围：

- 与 Provider 无关的模型请求/响应类型；
- 有界工具调用循环；
- 结构化停止原因；
- 取消和超时传播；
- Context Budget 统计；
- 在每个模型和工具边界产生事件。

第一个版本不依赖 Graph 框架。如果后续出现经过验证的需求，可以通过 Adapter
集成相应框架。

当前 Phase 1 已实现 Provider-neutral Message 与 Tool Definition、同步 Model
Protocol、Model Step/总调用数/重复调用/Observation Bytes 硬限制，以及结构化
Boundary Event。Runtime 在生成一个 External-action Proposal 后刻意停止。
HTTPS Responses API Adapter 与交互式 AgentDojo CLI 已经实现 Provider
Boundary，但尚未执行有记录的 Live-model Run。Provider Timeout Cancellation
已经通过 Background Responses、受限轮询和 Provider Cancel Endpoint 实现。
线程安全 Cancellation Token 会贯穿 Loop；取消后本地不再处理 Tool，并记录
Provider 是否确认取消。Compaction 仍未实现。Mid-loop Transcript 会在安全的
模型请求边界保存为 AES-256-GCM 密文；密钥不进入事件数据库，且只有持久状态
仍为 `running` 的 Run 才能恢复上下文、限制、来源证据和 Usage。Responses 返回的
Input、Output、Cached、Cache-write 和 Reasoning Token 已在 Provider Boundary
验证，并累计到 Run Result、Event 和 Evaluation Artifact；缺少 Usage 的调用会
被明确标记为不完整，而不是按零成本处理。

`evaluation/` 已实现固定 Case Manifest、Agent Behavior 与 Runtime Enforcement
双模式 Runner、AgentDojo Grader 以及带完整性 Digest 的 JSON Artifact。它复用
相同 Runtime，但为每个 Trial 创建隔离 Workspace；评测 Ground Truth 只进入
确定性 Approval Simulator，不进入 Model Context。当前证据来自 Scripted Model，
Live Model Artifact 仍未生成。

### 4.3 Action Gateway

一个 `ActionDefinition` 至少包含：

```text
name
input_schema
effect_manifest
aggregate_risk
required_capabilities
approval_policy
idempotency_strategy
precondition_checker
postcondition_verifier
redaction_policy
```

Effect Manifest 必须是列表，因为一次工具调用可能修改多个资源。每个 Effect
至少声明：

```text
resource
kind
target_template
cardinality
reversibility
sensitivity
verifier
```

初始 Effect Kind 与策略如下：

| Kind | 示例 | 初始策略 |
| --- | --- | --- |
| `pure_read` | 搜索邮件、检查日历 | 在任务范围内允许执行 |
| `stateful_read` | 获取未读邮件并将其标为已读 | 作为声明式修改处理 |
| `local_draft` | 准备回复或日程提议 | 不产生外部副作用 |
| `create` / `update` | 创建或改期日程 | 精确副作用审批 |
| `send` | 发送邮件或日程邀请 | 预览并进行精确副作用审批 |
| `delete` | 删除邮件或取消日程 | 明确审批并验证结果 |
| `compensate` | 取消刚创建的日程 | 作为新的明确动作，不能静默回滚 |

例如 AgentDojo 的 `create_calendar_event` 会具体化两个 Effect：

```text
calendar.events : 创建一个 Event
inbox.emails    : 发送一封邀请邮件
```

Proposal Digest 必须同时绑定这两个 Effect。只审批日历变化，不足以授权发送
邮件。

后续可以让策略具备上下文感知能力，但第一个纵向切片要求所有外部修改都经过
审批。

第一个版本不把自动推导的自然语言计划当作安全边界。它的
`AuthorityEnvelope` 刻意保持狭窄：

- 部署配置决定模型可以看到哪些只读工具；
- 一次精确用户审批只授予对一个规范化外部修改的一次性 Capability；
- 模型生成的计划不能扩大以上任何规则。

后续版本可以让操作者审批结构化的多动作计划，但真正形成权限边界的是已经
审批的结构，而不是模型内部对任务的解释。

### 4.4 Operation Ledger 与验证

每个外部修改都获得稳定的 `operation_id`。对结果不明确的失败进行重试之前，
Voren 必须检查 Operation Ledger 和外部后置条件，不能把超时简单视为远端操作
失败。

`ActionReceipt` 记录：

- Proposal 与 Approval Digest；
- Adapter 和工具版本；
- 开始与完成时间；
- 前置条件结果；
- 规范化结果或失败；
- 预期和实际状态变化；
- Verifier 结果；
- 是否存在补偿操作。

Verifier 需要比较具体化后的 Effect Manifest 与实际 State Delta。声明的 Effect
缺失或出现未声明的额外 Effect，都属于验证失败。Adapter Contract Test 使用
受控 Fixture 进行同样的比较，避免上游实现变化静默改变 Voren 的权限边界。

### 4.5 Event Store

事件流只允许追加写入。初始事件类别包括：

- Run 生命周期；
- 模型请求和响应 Metadata；
- Skill Discovery 和 Activation；
- Action Proposal；
- Policy 与 Approval Decision；
- Tool Invocation 和 Observation；
- Verification 和 State Delta；
- Memory 或 Skill Candidate 创建；
- Evaluation 与 Promotion Decision。

体积较大或包含敏感信息的 Payload 应存为经过脱敏、内容寻址的 Artifact，而
不是复制到每个事件中。

### 4.6 Memory System

Voren 区分四类存储：

| 存储 | 用途 | 修改策略 |
| --- | --- | --- |
| Working Context | 当前 Run 状态 | 临时数据 |
| Profile | 长期事实和偏好 | 明确用户证据或已审查候选 |
| Episodes | 不可变执行证据 | 追加写并受保留策略控制 |
| Skills | 可复用程序 | 不可变版本和受控 Active Pointer |

初始阶段不需要向量数据库。在检索质量被实际测量为瓶颈之前，结构化 Metadata
和简单文本搜索已经足够。

### 4.7 Skill 表示

每个 Skill 版本是一个目录：

```text
skill-name/
  SKILL.md       # 兼容 Agent Skills 的人类可读流程
  skill.yaml     # Voren 执行与评测契约
  references/    # 可选支持材料
  scripts/       # 可选确定性脚本
```

`skill.yaml` 可以声明 Scope、允许的工具、前置条件、风险限制、Verifier 引用、
父版本、原始证据和必须通过的评测套件。`SKILL.md` 中的文字不能授予运行时
权限。

Skill 生命周期：

```text
draft -> candidate -> evaluating -> active -> superseded -> archived
             |             |
             +-> rejected  +-> rejected
```

晋升以原子方式修改 Active Version Pointer。回滚是重新选择一个已评测的不可变
版本，不会重写历史。

### 4.8 Learning Control

学习路径运行在前台行动循环之外：

1. 选择符合资格的 Episode；
2. 丢弃不可信指令，同时保留完成任务所需的数据；
3. 判断长期学习类型；
4. 生成范围受限的候选修改；
5. 校验表示层和 Capability 变化；
6. 比较 Candidate 与 Active 版本；
7. 在策略要求时请求审查；
8. 晋升、拒绝或等待更多证据。

内容修改与 Capability 扩张是两种不同变更。新增工具、网络目标、Secret 或
副作用类型时必须经过更强的 Gate。

前台 Agent 没有 Active Skill Store 写权限。Learning Worker 只能写 Candidate
Namespace；Promotion 组件只能在拿到有效 Evaluation Decision 后修改 Active
Version Pointer。这种隔离限制被污染的前台 Trace 能够直接修改的范围，但它
不能替代评测，因为生成出的 Candidate 仍然可能包含有害内容。

### 4.9 Evaluation Runner

Runner 冻结模型、Prompt、工具、Active Skill、Candidate Skill、数据集、
随机性控制和 Evaluator 版本。它对 Baseline 与 Candidate 运行配对 Trial，输出
机器可读结果和人类可读报告。

## 5. Provenance 与授权

每条 Message、Artifact、字段和 Evidence Reference 都带有来源类型。初始策略
强制执行：

- 只有可信操作者意图可以建立任务的 Authority Envelope；
- 工具输出可以填充数据参数，但不能扩大 Authority Envelope；
- 模型不能把 Observation 转换成 Approval；
- 不可信内容不能直接更新 Profile 或 Skill Store；
- Skill Prose 不能覆盖 Action Policy；
- Verifier 成功不能追溯性地授权一次未授权动作。

这是实用的信息流规则，不声称实现了形式化 Noninterference。把整封邮件标记为
不可信，也不能自动区分邮件里的恶意指令和合法任务数据。Voren 使用 Provenance
限制权限和长期写入，再通过对抗评测测量剩余的模型级失败风险。

## 6. 失败语义

设计必须区分：

- 模型在产生 Proposal 之前失败；
- Approval 被拒绝或过期；
- 产生副作用前的前置条件失败；
- 明确发生在副作用前的 Adapter 失败；
- Commit 结果不明确；
- Commit 成功但 Verification 失败；
- 某个动作成功，但后续工作流失败。

只有确认发生在 Commit 之前的失败才可以直接重试。结果不明确时必须检查外部
状态。不可逆动作成功而后续工作流失败时，应向用户准确报告部分完成情况，
不能声称实现了并不存在的原子回滚。

## 7. 初始技术选择

- Python 3.12；
- 使用 `uv` 管理环境与依赖；
- 使用 Pydantic v2 定义领域对象和工具契约；
- 第一版保留同步 Runtime，通过 Cooperative Cancellation Token 与 Provider
  Background-response Cancellation 实现取消；Web Interface 阶段再引入 Async
  Task Supervision；
- 首先使用 SQLite 持久化 Event、Operation 和 Version Ledger；
- 使用标准库 `unittest` 编写单元、故障注入和集成测试；
- 在本地 Protocol 后实现一个 Responses-compatible HTTPS Model Adapter；
- 通过 Voren 自己的 Adapter 封装固定版本的 AgentDojo；
- 使用版本化 Manifest 和完整性绑定 JSON Artifact 保存评测结果。

CLI 纵向切片通过 Live Model 与 Injection Evaluation 之前，不引入 FastAPI 和
浏览器 UI。

## 8. 建议的 Package 边界

```text
src/voren/
  domain/       # 不可变契约与枚举
  runtime/      # Run Manager、Loop、Context、Budget
  actions/      # Definition、Policy、Approval、Receipt、Verification
  adapters/     # 模型与外部世界 Adapter
  memory/       # Profile 与 Episode Store
  skills/       # Discovery、Loading、Version、Lifecycle
  learning/     # Routing、Candidate Creation、Promotion
  evaluation/   # Trial、Grader、Report
  interfaces/   # 首先 CLI，之后 API
```

这些边界仍是暂定方案。应先通过第一个纵向切片验证，再完整创建 Package Tree。
