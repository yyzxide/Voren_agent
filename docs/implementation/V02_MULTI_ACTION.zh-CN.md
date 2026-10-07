# 0.2.0：可恢复的多动作任务

[English](V02_MULTI_ACTION.md)

2026-09-26。本版本让同一任务在一个动作验证成功后继续执行，并对下一个动作重新审批。
例如 Google 工作流可以先创建无邀请的日历占位，再创建回复草稿，最后汇总两个动作的结果。
第二个动作被拒绝或失败时，第一个已经发生的动作及回执仍保留；不会假装整个任务都没有执行，
也不会自动撤销已经发生的副作用。

## 使用

本地 Web 默认使用多动作模式：

```bash
voren-web
```

点击页面中的“填入演示任务”。在确定性的 AgentDojo 世界中分别审批日历动作与确认邮件，
查看两条回执和最终总结。该演示不连接真实邮箱。刷新页面后可读取原 Run，
“继续执行”调用 `POST /api/runs/{run_id}/resume`；没有批准的动作仍会等待批准。

已配置模型和专用 Google 测试账号时，CLI 可以执行：

```bash
voren google --multi-action --database .voren/v02.sqlite3 \
  --request '查找评审邮件，先创建一个无邀请的日历占位，再准备回复草稿；每个动作分别让我确认。'

voren google --database .voren/v02.sqlite3 --resume RUN_ID
```

`RUN_ID` 替换为启动时输出的标识。模型配置沿用现有环境配置说明。
恢复读取原 Run 冻结的模型配置、预算、Memory/Skill 版本，以及 Google 账号、日历和时区；
不会借恢复命令更换任务配置。凭据可以轮换，但不会写入 RunConfig。
该命令会实际调用在线模型；批准 Google 动作后会修改专用测试账号。

CLI 的原单动作模式保留；`--resume` 仅用于 `--multi-action` 创建的 Google 任务。
AgentDojo CLI 支持同一进程中的多动作审批，但内存中的测试世界丢失后不能跨进程恢复它。

## 状态与持久化顺序

```text
RUNNING → WAITING_APPROVAL → 精确审批 → 提交并核验
                                         ├─ verified → RUNNING
                                         │              ↓
                                         │       保存含回执的检查点
                                         │              ↓
                                         │       清除待消费的动作指针
                                         │              ↓
                                         │       继续读取/提出下一动作/最终总结
                                         ├─ ambiguous → NEEDS_RECONCILIATION
                                         └─ rejected/failed → CANCELLED/FAILED
```

`RunConfig.continue_after_action` 明确区分新旧执行模式。新模式的 `verified` 只表示该动作完成，
Run 要等模型给出最终响应才进入 `completed`。每个 Proposal 保留独立 operation ID、参数摘要和
Approval；旧动作的决定不能批准后续动作。

模型产生动作调用的响应先加密保存，再创建 Proposal。恢复时核对调用 ID、动作名和原始参数摘要，
只有精确绑定的已批准、已验证回执可以作为该调用的结果交回模型。先保存含回执的下一步检查点，
再清除待消费指针；在两者之间崩溃，恢复会识别已保存的回执，避免重复添加或重复派发。

Web 的动作历史来自持久事件与账本，浏览器 Snapshot 只是展示缓存。
多动作终态的最终文本保留在加密检查点中；即使最终 HTTP 响应丢失，也能读取原总结，
不需要再调用模型或重新连接外部工作区。`last_receipt_status` 在最终完成时保留，
让全部动作已验证的 Run 仍可进入既有 Evidence Eligibility 检查。

## 恢复约束

- Transcript v3 保存原始 Provider output，包括后续 Responses 请求需要重放的 reasoning 和工具调用；
  输出绑定 Provider 配置和 Endpoint 摘要，和普通上下文一起加密。普通事件只记录摘要与计数。
- v1/v2 检查点及摘要仍可读取。旧检查点没有保存的 Provider 原始输出不能补造。
- 新模式在网络请求前预留并保存一次模型请求额度。若响应尚未保存便崩溃，这次额度不会退还；
  `model_requests` 表示预留/尝试次数，实际 Token 用量缺失时 `usage.complete` 为 false。
- 同一台机器、同一数据库通过 OS 文件锁只允许一个 Loop 执行者。进程退出会释放锁；
  独立 Run 也会串行。这不是跨主机分布式执行方案。
- 已有原始输出恢复时要求 Provider 配置一致，包含 timeout 等参数。更换 API key 不改变该指纹。
- Google 恢复核对配置声明的账号/日历/时区，不能替代供应商对 Token 真实归属的校验。
- `ambiguous` 或缺少可见结果的动作只观察核对；观察到额外或不匹配副作用的历史不得自动变成成功。

## 本地密钥

Web 和新 CLI 多动作模式优先使用 `VOREN_TRANSCRIPT_KEY`；未配置时，首次使用会创建数据库旁的
`<database>.transcript.key`，权限为 `0600`。它不会写入日志。数据库和密钥必须一起保管，
这提供落盘密文保护，不提供同一宿主机被攻破后的密钥隔离。
已有检查点但密钥缺失或为空时会拒绝恢复，不会生成新密钥覆盖问题。
原 CLI 单动作模式仍要求显式设置密钥。

## 同步修复

1. 初次提交超时已经观察到违规副作用时，保留失败事实；兼容旧的违规 `ambiguous` 回执，
   Gateway 和 Ledger 都拒绝用后续干净观察覆盖它。
2. Skill 修改限额按 unified diff 的记录和 hunk 计数，正确处理正文 `+++`/`---` 以及无末尾换行。

## 验证与范围

回归使用脚本模型、真实 Responses Adapter 配合 HTTP double、临时 SQLite、AgentDojo 测试世界、
本机 HTTP/MCP 服务。覆盖两动作两次审批、第二动作拒绝、旧决定重试、多个提交/检查点崩溃窗口、
冻结上下文、预算耗尽、执行锁、Provider 原始响应重放、最终文本恢复和学习证据兼容。

本次本地验证：CPython 3.12.3、Linux，86 项依赖的版本与 Wheel SHA-256 均匹配
`pylock.toml`；`pip check` 通过，**262 项测试全部通过**（108.918 秒）。完整记录见
[2026-09-26 测试日志](../evidence/2026-09-26-v02-tests.log)。JS 语法和 `git diff --check` 通过。
仓库内 `.venv` 已安装本地 0.2.0，可用 `.venv/bin/voren-web` 启动。

```bash
python -m pip check
python -m unittest discover -s tests -v
```

本版本不产生新的在线模型成绩或真实 Google 账号运行证明。现有单动作 AgentDojo 评测继续使用旧模式，
不能把它的历史成绩直接当作本版本多动作工作流成绩。主动触发、上下文压缩、审批中编辑参数及多 Agent
不在本次切片中。

## 阅读顺序

1. [RunManager](../../src/voren/runs/manager.py)：为什么动作完成后 Run 仍是 running？旧回执如何绑定原动作？
2. [AgentLoop](../../src/voren/runtime/agent_loop.py)：回执何时进入上下文？先存检查点再确认消费解决哪个崩溃窗口？
3. [Transcript Store](../../src/voren/runtime/transcripts.py)：请求额度、原始响应和锁分别保护什么？
4. [多动作回归](../../tests/test_multi_action_runtime.py)：第二次审批、模糊结果和中断如何改变可观察状态？
