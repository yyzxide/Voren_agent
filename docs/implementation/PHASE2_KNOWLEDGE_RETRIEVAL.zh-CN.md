# Phase 2：绑定来源的业务知识检索

[English](PHASE2_KNOWLEDGE_RETRIEVAL.md)

## 边界

本切片为会议纪要、Operator Note 和已提取的附件文本提供小规模语料检索。它不会
把每次数据库查询都包装成“RAG”，也不会在检索质量尚未需要时提前引入向量数据库。
精确日期、参与人和忙闲状态仍由结构化 Workspace Tool 查询。

## 不变量

- 文档版本不可变，并使用内容寻址；
- Search 只读取每份文档显式激活的版本；
- 每个命中项都包含精确文档/版本引用、Source URI、Source Kind 和 Content
  Digest；
- 导入文本一律为 `instruction_authority=false`，包括标记为 Operator Note 的
  文本；长期偏好仍必须经过类型化 Profile Evidence Boundary；
- 重新导入纠正后的文档会创建新版本，不修改旧版本；激活是单独且必须写明
  Reason 的操作；
- CLI Inspect 默认不显示正文。

第一版 Retriever 使用确定性词法匹配：同时处理拉丁语词项与中文字符/双字词，
返回长度受限的 Snippet，并使用稳定规则打破同分排序。它是可审计 Baseline，
不是“已经证明语义检索质量”的声明。

## CLI

```bash
voren knowledge ingest notes/demo.md \
  --document-id meeting:demo \
  --title 'Demo review' \
  --source-uri 'file:///controlled/notes/demo.md' \
  --source-kind meeting_note \
  --reason 'operator reviewed import'

voren knowledge search '审批 回执'
voren knowledge inspect --document-id meeting:demo
```

## 验证

`tests/test_knowledge_retrieval.py` 覆盖精确 Citation、Active Version 切换、中文
检索、篡改检测、未激活文档、CLI 默认脱敏，以及带 Provenance 的 Tool
Observation。

[MCP 传输切片](PHASE2_MCP_RETRIEVAL.zh-CN.md)会让同一个 Store 经过官方 MCP
Client/Server 往返。MCP 只改变传输边界，不会改变 Trust 或 Instruction
Authority 规则。
