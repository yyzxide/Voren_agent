# Phase 2 静态 Skill Store

[English](PHASE2_STATIC_SKILL_STORE.md)

## 范围

这个切片先建立持久表示层，为后续把 Skill 放入模型 Context，以及生成 Candidate
Skill 做准备。当前实现包括：

- 严格发现 Skill Root 的直接子目录；
- 分离 Metadata、Instruction 和 Resource 的渐进加载 API；
- 必需的 Voren `skill.yaml` 执行/评测 Sidecar；
- 内容寻址的不可变版本；
- 显式且原子更新的 Active-version Pointer；
- 冻结在 `RunConfig` 中的精确 Skill Version Reference。

仓库内的 `skills/schedule-from-email` 是第一个人工编写的静态日程 Skill。

## 可移植格式与 Voren 扩展

Package 外层遵循 [Agent Skills 官方规范](https://agentskills.io/specification)：目录名必须与 `SKILL.md` 中的 `name`
一致；YAML Frontmatter 必须包含 `name` 和 `description`；可以包含规范允许的可选
Metadata；正文为 Markdown Instruction，并可携带 Resource 目录。

Voren 不修改 `SKILL.md`，而是增加 `skill.yaml`，其中记录：

- 语义 Scope；
- Skill 申请使用的最大 Tool Scope；
- 预期外部 Effect 类型；
- 应覆盖该 Skill 的 Evaluation Suite。

这些字段不会授予权限。Runtime 配置的工具、Action Gateway、Policy 和精确审批
只能进一步收窄 Sidecar Scope。Agent Skills 中仍属实验性的 `allowed-tools`
Frontmatter 会作为 `allowed_tools_hint` 保留兼容信息，但绝不会转换成 Voren 的
Runtime Permission。

## Progressive Disclosure

API 明确分为三个 Context Surface：

1. `discover_active()` 只返回精确版本引用和路由 Description；
2. `load(ref)` 返回完整 `SKILL.md` Instruction 和可用 Resource Path；
3. `load_resource(ref, path)` 按需返回一个经过完整性检查的 Resource。

当前实现会在加载时从磁盘验证完整 Package，但只把调用者选择的层级返回，因此
不会把全部内容一起塞进模型 Context。

## 不可变版本与激活

安装时会根据每个相对文件路径、文件 Digest 和字节长度计算规范化 Digest，并把
验证后的完整 Package 复制到：

```text
objects/<content-digest>/<skill-name>/
```

Content Digest 同时作为 Version ID。重复安装相同字节是幂等操作；Instruction、
Sidecar 或 Resource 任何变化都会形成新版本。新版本安装后默认不激活。
`activate(ref, reason=...)` 只会原子更新一个 SQLite Pointer，不会重写旧对象。

`freeze_active()` 返回精确的 `SkillVersionRef`。Run 会把这些引用写入
`RunConfig`，所以之后切换 Active Pointer 只会改变新 Run 的 Digest，不会改变
已经冻结的 Run。没有 Skill 的旧 Phase 1 Config 仍保持原来的 Digest 表示。

## 准入与完整性检查

Parser 会拒绝：

- 非法或与目录不匹配的 Skill Name；
- 不支持的 Frontmatter 和无效 Sidecar；
- 非字符串的可移植 Metadata；
- Symlink 与非普通文件；
- 逃逸 Package Root 的路径；
- 超出单文件、Package 总体积或文件数量限制的内容。

加载 Instruction 或 Resource 时，系统会重新解析已安装对象并检查内容地址。
文件系统权限不被当作安全边界：外部进程仍可能修改文件，但修改后的内容会在进入
模型 Context 前被发现。

## 当前边界

这个 Store 切片本身不负责：

- Profile 或 Episode Store；
- Skill 语义选择；
- 由模型生成 Candidate；
- Learned Version 的评测、晋升、拒绝或回滚。

后续的[版本冻结 Context 切片](PHASE2_SKILL_CONTEXT.zh-CN.md)已经可以把显式选择、
冻结后的版本注入 `AgentLoop`。前台 Loop 仍没有 Skill Store 写入路径，因此工具
Observation 不能安装或激活 Skill。
