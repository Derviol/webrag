# scripts — 工具脚本

## 职责

- 独立可运行的开发/运维脚本，不依赖服务启动：

| 脚本 | 用途 |
| --- | --- |
| `init_milvus.py` | 创建问答缓存 collection（webrag_qa）与索引（幂等，可重跑） |
| `init_admin.py` | 创建管理员账号（管理后台登录用，`--username` / `--password` 参数） |
| `test_query.py` | 命令行单测一条问题（走完整检索链路） |

## 约定

- 脚本参数与配置从 config/ 读取，不硬编码；
- 脚本需能在本地环境与 Docker 容器内两种环境运行（容器内用 `uv run --no-sync python scripts/xxx.py`）。
