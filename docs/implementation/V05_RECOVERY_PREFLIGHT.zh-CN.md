# 0.5.0：外部写入前的恢复校验

[English](V05_RECOVERY_PREFLIGHT.md)

2026-10-08。本切片修复 Web 审批路径的三个实际缺口：损坏的恢复记录可能在批准动作
已经写入之后才被发现；恢复时可以无提示地更换模型；同一工作区身份下动作契约改变
也未被拒绝。CLI 同时补齐已记录的模型 adapter 边界核对。

## 行为

- 批准前认证加密 checkpoint，核对 RunConfig 摘要、原始 pending tool call 与动作身份。
  缺失、损坏、密钥不可用或绑定不符时，不保存批准、不派发新的外部写入。
- 新 Web Run 保存不含凭据的模型身份。内置 Responses adapter 核对模型、实际端点、
  profile 和请求限制摘要；Demo 核对模式及稳定类。核对后的同一模型实例用于继续执行。
- 工作区身份、world adapter 与 action contract versions 都需匹配原 Run。
- `recovery_reason` 分别标识知识配置改变、原来源/索引不可用、模型配置改变、
  恢复记录不可用、动作契约变化。`RunView.knowledge_corpus` 展示原始版本，显式空集合与旧任务无快照不同。
- 同一工作区身份下，仍可拒绝尚未执行的提案；已有回执继续保留。
- Google CLI 不允许把 `injected_model` 任务改由环境中的 Responses provider 恢复，反之亦然。

## 边界与验证

自定义模型 adapter 只核对类和模式，明确保存 `configuration_verified=false`；其隐藏配置
与行为无法由类名证明不变。历史 Run 没有模型身份时保留原 factory 行为，不补造历史配置。
凭据不进入模型摘要，轮换 Key 不等于更换模型。已完成结果读取不要求重新配置模型。

检查与远程写入是多步流程，不构成分布式事务。检查后发生的本地数据破坏或供应商故障
仍可能让后续步骤失败；已派发而结果不确定的动作继续走观察与 reconciliation，不能重发。

新增 16 项 Web preflight 回归，覆盖损坏/缺失记录与 Key、合法加密但错误的绑定、
模型/端点/profile/mode 变化、不可用配置、契约变化、已获得回执后的恢复阻断。
Google CLI 新增模型边界切换零派发测试。测试使用临时 SQLite、脚本模型及假 Google
HTTP transport；不代表真实账号证据。最终完整回归与源码证据见 1.0 交付记录。

## 同批交付的知识 Web 面板

`voren-web` 的独立知识区域先用 BM25 查询，再基于同一份资料快照验证原始 JSON 提案，
或由操作者勾选允许发送问题和片段后，请求一次已配置的在线模型。只勾选不触发请求。
它不修改邮件/日程；普通行动会话的总结仍不经过知识问答引用校验。

`POST /api/knowledge/search` 返回 `corpus` 与精确片段；`POST /api/knowledge/ask`
接收 `question`、`limit`、可选 `corpus`，以及二选一的原始 `draft` 或 `allow_model_api=true`。
Web 知识接口只提供 BM25，不隐式使用向量接口。空检索不构造在线模型。传入快照上限
1000 个来源，文档 ID 至多 500 字符；其他参数与片段/提案预算仍受 0.4 契约约束。

面板分别呈现回答、拒答、非法提案和失败；结果标记 `operator_draft` 或 `online_model`，
始终显示 `semantic_support=unverified`。来源 URI、标题、回答与原文均以纯文本展示，
不将不可信 URI 转成可执行链接。修改问题后必须重新检索。
13 项独立 HTTP/Node DOM 测试及真实应用路由接入测试通过；没有新在线模型或账号证据。

阅读 [service.py](../../src/voren/web/service.py)、[model_context.py](../../src/voren/web/model_context.py)
和 [preflight 测试](../../tests/test_web_recovery_preflight.py)：为什么“最终能发现错误”仍不足够，
哪些错误必须在保存批准与派发动作之前发现？
