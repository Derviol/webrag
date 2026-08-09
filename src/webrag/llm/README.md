# llm — LLM 接入与生成

## 职责

- DeepSeek 调用封装（deepseek-chat / deepseek-reasoner，OpenAI 兼容接口）；
- Prompt 模板管理与版本化（约束"仅依据上下文回答"）；
- **引用标注**：要求模型输出 [1][2] 序号，并解析回 sources 列表。

## 所属角色

- LLM 接入（#7）。

## 接口约定

- 输入：用户问题 + 上下文片段（带序号）→ 输出：answer（含 [n] 标记）+ 引用映射；
- 上游：retriever（片段）；下游：main.py（组装响应）。

## 验收标准

- [ ] 回答仅基于给定上下文，越界内容有拒答兜底；
- [ ] 引用序号与 sources 一一对应，不产生幽灵引用；
- [ ] Prompt 版本有变更记录（docs/CHANGELOG.md 记录）。
