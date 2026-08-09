# WebRAG 团队协作速览（30 秒版）

> 完整规范见 README §4；接口契约见 docs/api.md；前端规格见 static/README.md。
> 协作方式：**Resilio Sync 实时同步 + 各人只改自己的模块目录**（无 Git）。

## 一句话工作流

```
加入 Resilio 同步 → 在自己的模块目录里开发 → 自测通过 → 保存即同步到全队
```

## 新成员接入（4 步）

1. 装 Resilio Sync，加入团队共享文件夹（自动拉取全量）；
2. 在项目根目录执行 `uv sync`（自动装 Python 3.11 + 全部依赖 + torch；`.venv/` 已被忽略，不会同步）；
3. 复制 `.env.example` 为 `.env`，填入 `DEEPSEEK_API_KEY` / `SEARCH_API_KEY`；
4. 在自己负责的模块目录开工（见下表）。

## 目录边界（只动自己那列）

| 成员 | 只能动这些 |
| --- | --- |
| #2 爬虫（2 人） | src/webrag/crawler/ |
| #3 清洗/切块（2 人） | src/webrag/parser/、src/webrag/chunker/ |
| #4 Embedding | src/webrag/embedder/ |
| #5 向量库 | src/webrag/milvus_store/ |
| #6 检索（2 人） | src/webrag/retriever/ |
| #7 LLM | src/webrag/llm/ |
| #8 后端/前端 | src/webrag/main.py、src/webrag/schemas/、static/ |
| #9 测试/评测 | tests/、eval/ |
| #1 总负责 | 其余一切（docs/、config/、scripts/、pyproject.toml 等） |

## 共享文件：先找 owner

| 文件 | owner | 你要改时 |
| --- | --- | --- |
| main.py / schemas/ | #8 | 提需求，由 #8 改 |
| pyproject.toml / uv.lock | #1 独改 | 需要新依赖找 #1 |
| config/settings.yaml | #1 | 找 #1 |
| docs/api.md | #1 定版 | 字段变更走 api.md §5 流程 |
| scripts/ 联调脚本 | #1 | 找 #1 |

## 五条铁律

1. **自测通过才算完成**：`uv run pytest` + `uv run ruff check` 全绿再算完；
2. **保存即同步**：未完成代码也会全网可见，没测完别依赖别人的结果；
3. **冲突**：出现 `xxx (conflicted copy).py` = 两人改了同一文件，协商留一份、删其余；
4. **变更留痕**：影响别人的改动（契约/接口）先在 docs/CHANGELOG.md 记一行；
5. **密钥**：`.env` 全队可见（内部项目默认接受）；要保密就找 #1 加忽略列表。

## 常用命令

```bash
uv sync                                     # 还原环境（改 pyproject 后执行）
uv run pytest                               # 全部测试
uv run ruff check                           # lint
uv run uvicorn src.webrag.main:app --reload # 起服务
uv run python scripts/init_milvus.py        # 建库（幂等可重跑）
docker compose up -d                        # 起 Milvus+Redis（需 Docker Desktop）
```

## 环境速查

| 项 | 值 |
| --- | --- |
| 服务地址 | http://localhost:8000（/ask、/health） |
| Milvus / Attu | http://localhost:19530（Attu 装 2.x） |
| Redis | localhost:6379 |
| Python | 3.11（`.python-version` 锁定，uv 自动装） |
| 依赖锁 | uv.lock（唯一版本真相，禁手改） |
| 模型 | BGE-M3 由 #4 下载到 models/，Resilio 分发给全队 |
| 历史备份 | GitHub：github.com/Derviol/webrag（只读存档） |

## 里程碑与验收（#1 每日盯）

- **D1**：单 URL 产出规范 Chunk；Milvus 建库 ✅（已完成）；BGE-M3 模型就绪
- **D2**：主链路——一条问题返回带来源的回答
- **D3**：重排 + 引用校验 + Web 页面（规格见 static/README.md）
- **D4**：测试 + 评测 + Docker 部署 + 文档归档
