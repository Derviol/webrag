# 决策与契约变更记录

> 协作方式：**Git**（main/dev/feature，见 README §4）。日常代码变更以 commit 记录为准，
> 此处仅留痕影响多人协作的重大决策（契约定版、环境变更、协作方式调整）。
>
> 格式：`MM-DD | 决策人 | 决策 | 影响模块 | 备注`

## 2026-08-04

| 日期 | 改动人 | 变更 | 影响模块 | 自测 |
| --- | --- | --- | --- | --- |
| 08-04 | #1 | 环境统一：pip 改为 uv（新增 pyproject.toml / uv.lock / .python-version），删除 requirements*.txt 与 scripts/freeze_env.py | 全部 | ✅ uv sync、init_milvus 建库 |
| 08-04 | #1 | 基础设施落盘：docker-compose.yml（etcd/minio/milvus v2.4.24/redis），docs/deploy.md 重写，本机 Attu 2.x 连 localhost:19530 | 部署/全部 | ✅ docker compose config、init_milvus |
| 08-04 | #1 | pymilvus 锁 2.4.x（>=2.4,<2.5）；新增 setuptools>=77,<82（pkg_resources 兼容，82 起移除） | milvus_store | ✅ init_milvus |
| 08-04 | #1 | 修复 MilvusStore.health()：connections.get_connection（2.4 不存在）→ utility.get_server_version | milvus_store / main | ✅ /health milvus:true |
| 08-04 | #1 | 协作方式变更：Git → 局域网共享文件夹（README §4、api.md §5、各模块 README 同步） | 全部（文档） | ✅ 全库检索无残留 |
| 08-04 | #1 | 协作方式修正：手动共享文件夹 → **Resilio Sync 全量实时同步**（README §4 重写：忽略清单/.env 策略/冲突文件处理；api.md §5、quickstart、config/README 同步） | 全部（文档） | ✅ 全库检索无残留 |
| 08-04 | #1 | 协作方式最终定：**Git 分支协作**（放弃 Resilio Sync/共享文件夹；README §4 恢复 main/dev/feature + 局域网 bare 仓库选项；托管平台待定 §9） | 全部（文档） | ✅ 全库检索无残留 |
| 08-04 | #1 | 契约定版（api.md v0.2）：embedder 双向量 EmbedResult / llm 两阶段 generate+build_response / milvus_store.search 双向量签名 / 补 analyze_query 与 QueryPlan | 契约（#4/#5/#6/#7/#8） | ✅ 字段与 models.py 逐项对齐 |

## 模板（新变更时复制到顶部）

```
MM-DD | #编号 | 一句话变更 | 影响模块 | ✅/❌ 自测结果
```
