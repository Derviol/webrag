# Slide Outline

## Meta
- Topic: WebRAG 联网检索增强问答系统 — 团队项目汇报
- Scenario: 团队项目汇报（内部，侧重架构、技术亮点、分工协作与交付状态）
- Content Source: free creation — 基于项目 README、docker-compose.yml、pyproject.toml、TEAM_GUIDE.md 等文件知识生成
- Style: 深色科技风 — 深蓝/青色渐变背景，几何线条与网格装饰，等宽字体点缀代码/标签，高对比度白色文字，营造 AI 向量检索的科技感
- Slide Count: 13
- Generated At: 2026-08-08T22:35:00+08:00

## Source Materials
N/A — generated from project file knowledge (README.md, TEAM_GUIDE.md, pyproject.toml, docker-compose.yml).

## Slide-by-Slide Outline
1. **Slide 1 — Cover** — WebRAG 联网检索增强问答系统 · 项目汇报 | Layout hint: L02 | Image: no | Chart: no
2. **Slide 2 — Agenda** — 汇报内容概览（6 大模块） | Layout hint: L19 | Image: no | Chart: no
3. **Slide 3 — 项目背景与痛点** — 联网信息的时效性、来源可溯性、响应速度三大痛点 | Layout hint: L04 | Image: no | Chart: no
4. **Slide 4 — 产品定位** — 一句话定义 WebRAG：联网检索 + 缓存加速 + 来源标注的 RAG 问答系统 | Layout hint: L04 | Image: no | Chart: no
5. **Slide 5 — 系统架构总览** — 8 步流水线全景图（缓存检索→网页检索→清洗切块→向量化→检索→重排→生成→缓存落库） | Layout hint: L05 | Image: no | Chart: no
6. **Slide 6 — 技术栈选型** — Milvus / BGE-M3 / DeepSeek / Redis / FastAPI / Docker 六大核心组件 | Layout hint: L16 | Image: no | Chart: no
7. **Slide 7 — 核心流程详解** — 从用户提问到带来源回答的完整链路 | Layout hint: L13 | Image: no | Chart: no
8. **Slide 8 — 关键技术亮点** — QA 缓存秒回 / dense+sparse 双向量 / bge-reranker 重排 / 引用标注溯源 | Layout hint: L07 | Image: no | Chart: no
9. **Slide 9 — 团队分工** — 11 人团队 9 大角色模块化协作 | Layout hint: L15 | Image: no | Chart: no
10. **Slide 10 — 里程碑与交付** — D1-D4 四天交付节奏与出口标准 | Layout hint: L13 | Image: no | Chart: no
11. **Slide 11 — 部署方案** — Docker Compose 一键编排：Milvus + Redis + MySQL + 应用容器 | Layout hint: L05 | Image: no | Chart: no
12. **Slide 12 — 项目成果** — 核心指标：8 步链路全通 / 缓存命中秒回 / 引用可溯源 / 一键部署 | Layout hint: L11 | Image: no | Chart: no
13. **Slide 13 — 总结与展望** — 项目价值回顾 + 未来优化方向 | Layout hint: L20 | Image: no | Chart: no

## Visual Rhythm Notes
- Dark accent slides at positions: 1, 5, 11, 13（封面/架构/部署/收尾为深色强化页，形成视觉节奏）
- Chart-bearing slides at positions: 7（流程图）, 10（里程碑时间线）, 11（部署架构图）— 以 SVG 图形呈现，非文字堆砌
- Slides are viewed on large screens, often from a distance. Small text is unreadable. Every text element must be sized generously and fill its container space rather than floating in emptiness.
- Slides should feel visually rich and layered, not like text pasted on rectangles. Decorative elements add polish, and SVG charts communicate data far more effectively than text or numbers alone.
