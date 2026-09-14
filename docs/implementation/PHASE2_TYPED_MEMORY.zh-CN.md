# Phase 2 类型化 Memory 与冻结 Context

[English](PHASE2_TYPED_MEMORY.md)

## 边界

Voren 把 Profile Preference、Episode Summary 与程序性 Skill 分开存储。它们可以
引用同一类 Durable Evidence，但拥有不同权威：

- Profile Preference 只能从已经持久化的 Operator Correction 分类产生；
- Episode Summary 只能关联已经持久化的 Verified-run Evidence；
- 两类 Memory 都没有程序性指令权威；
- Untrusted Observation 与 Model Reflection 不能直接写入任一长期 Store。

这是类型化的本地 Memory Boundary，不是通用向量数据库，也不是自动记忆提取器。

## 版本与 Run 绑定

Profile Revision 是内容寻址的不可变对象，Active Pointer 以事务方式切换。Episode
Identity 不可变，使用同一 ID 写入不同内容会被拒绝。`MemoryContextSnapshot`
保存精确引用、渲染 Context Digest 与字节数。创建 Run 前，
`RunConfig.memory_versions` 必须与组装后的 Snapshot 完全相同，因此后续 Profile
更新不能改写旧 Run 的 Context。

模型可见 JSON 会把每条记录标记为 `instruction_authority=false`。Append-only Run
Event 只保存精确 Ref、Digest、大小和权威标签，不复制 Memory Content。

## 公开工作流

先通过现有 Learning Router 持久化 Evidence，再显式分类：

```bash
voren skill evidence-correction preference.txt \
  --evidence-id operator:timezone-1 --operator operator:sid
voren memory profile --memory-id preference:timezone \
  --evidence-id operator:timezone-1 --reason 'explicit preference'

voren skill evidence-run --run-id RUN_ID \
  --evidence-id run:meeting-1
voren memory episode meeting-summary.txt --memory-id episode:meeting-1 \
  --evidence-id run:meeting-1

voren memory inspect
voren agentdojo --profile-memory preference:timezone \
  --episode-memory episode:meeting-1 --model MODEL 'Summarize the meeting.'
```

Inspect 默认隐藏 Content，只有显式传入 `--include-content` 才显示。Agent Run
默认不加载任何 Memory，每个加入 Context 的 Memory Identity 都必须显式选择。

## 已测试的失败路径

- Verified-run Evidence 不能分类为 Profile Preference；
- Operator Correction Evidence 不能分类为 Episode；
- Episode ID 不能用不同内容重写；
- 精确版本重新加载时可以检测 Content 篡改；
- RunConfig/Snapshot 不一致会在 Model Call 和 Run 创建前停止；
- 冻结 Context 不受后续 Active Profile 更新影响；
- 审计 Payload 不复制 Memory Content。
