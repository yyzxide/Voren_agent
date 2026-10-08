# 0.6.0：离线问答评测与原始提案回放

[English](V06_ANSWER_EVALUATION.md)

2026-10-08。0.4 的问答服务已经校验引用，但预设提案演示不能测真实模型的判断能力。
本切片提供自定义语料/问题的准备、原始模型文本导入、离线回放、独立人工语义标注与报告验证。
它不发起在线模型或 Embedding 请求，也不自动标注答案质量。

## 两步准备与回放

```bash
python scripts/evaluate_knowledge_answers.py prepare \
  --dataset docs/fixtures/knowledge-answers.json \
  --output .voren/artifacts/answers-prepared.json \
  --proposals-output .voren/artifacts/answers-proposals.json \
  --model-inputs-output .voren/artifacts/answers-model-inputs.json
```

Prepared 包含完整资料、问题与操作者给定的 `answerable` 真值、固定版本和召回窗口。
提案模板先留空 `proposal_text`，空文本是非法提案，不默认拒答。填写原始模型/手工提案文本，
如实填写 `origin=external_model/manual/scripted`，保留 corpus/window 摘要，再运行：

```bash
python scripts/evaluate_knowledge_answers.py evaluate \
  --prepared .voren/artifacts/answers-prepared.json \
  --proposals .voren/artifacts/answers-proposals.json \
  --output .voren/artifacts/answers-evaluation.json
python scripts/evaluate_knowledge_answers.py verify \
  .voren/artifacts/answers-evaluation.json
```

不要将含真值的 prepared 包交给待评测模型。`model-inputs` 导出问题、证据、双摘要与提案
schema，去除真值和人工标注；用 opaque `case_ref` 替代可能泄漏预期类别的案例 ID。
本地 prepared 保存 ID/ref 映射，模型输出由操作者映射回提案模板。不重写原始文本，
因此非法 JSON、重复字段与超预算响应仍可被检查与复查。

## 报告含义

- 来源或窗口摘要不符、重复/缺少案例会作为输入错误拒绝，不能当作正确拒答。
- `answered/abstained/rejected/failed/cancelled` 分别计数。无答案标签下的非法提案仍是
  unresolved，不进入正确拒答计数。空窗口拒答记录零模型请求，与模型主动拒答可逐条区分。
- `answerable_answer_rate` 与 `no_answer_abstention_rate` 是对给定标签的决策率，不能解释为
  语义正确率。原有检索误召回率也仍是另一项测量。
- 结果中的请求与用量属于本次离线回放，不是原始在线生成的用量或费用证明。
- 可选 `human_support=supported/unsupported/unreviewed` 保存独立 reviewer 标识；仅经过
  复核且实际 answered 的案例进入该标注比例分母。它是提供的标注，不认证 reviewer 身份，
  不把服务结果的 `semantic_support=unverified` 改为自动通过。
- 读取报告时重建语料与检索窗口、重放原始提案、复核状态与统计。只重算 JSON 摘要后
  篡改指标仍会失败。摘要和一致性检查不是供应商签名，也不能证明文本确实来自某模型。

只支持 BM25 与既有分块参数，一份输入至多 100 份文档、1000 个问题。
历史报告回放需要兼容的检索/回答实现；它不是跨任意未来算法都能重现的保证。
16 项回归验证窗口/来源变化、同决策可重放、非法提案/重复 JSON、不泄漏真值元数据、
Unicode 位置、统计篡改、人工标注分母与路径防覆盖。

仓库夹具仅 5 个合成问题，与功能一起编写。最终报告中的脚本提案是协议验证，
不是独立业务语料或真实模型正确率。真实质量结论仍需要独立数据、真实输出与人工复核。

阅读 [answer_evaluation.py](../../src/voren/knowledge/answer_evaluation.py) 的 prepare、replay、summarize，
再看 [测试](../../tests/test_answer_evaluation.py)：为什么引用真实、主动拒答与语义支持必须分别统计？
