# eval — 评测

## 职责

- 评测集建设：问答对 + 期望来源（eval/qa_set.json，人工标注）；
- 指标计算：检索 Recall@k、MRR（run_eval.py）；回答质量人工打分（准确性、引用完整性）；
- 输出阶段性评测报告（eval/reports/），驱动检索与 Prompt 迭代。

## 运行

```bash
uv run python eval/run_eval.py
```

## 交付物

- eval/qa_set.json：评测数据（已建立）；
- eval/run_eval.py：评测脚本（含 Live 模式：真实 /ask 调用）；
- eval/reports/baseline.json：基线评测结果。

## 约定

- 评测集变更在 docs/CHANGELOG.md 记录，标注者记录标注规则；
- 每次参数/模型调整需复跑基线，结果记录在 reports/。
