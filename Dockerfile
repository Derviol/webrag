# WebRAG 应用镜像（M4 完成：docker compose up -d 一键启动；脚本 scripts/ 一并打入供 init 服务建库）
# 构建/启动：
#   docker compose up -d
# 基础镜像用 python:3.12-slim（pyproject requires-python >=3.10,<3.13；本地已有该镜像，
# 避免 3.11-slim 需从被限流的 docker.io 拉取）
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

# uv 用 pip 安装（ghcr.io 的 uv 镜像在国内网络下偶发拉取截断，改走清华 PyPI 镜像；备用方案见 docs/deploy.md）
RUN pip install --no-cache-dir uv

WORKDIR /app

# 先拷依赖清单，利用 Docker 层缓存（改代码不重装依赖）
COPY pyproject.toml uv.lock ./
# torch 已随 uv.lock 统一管理（pyproject 依赖 torch>=2.2,<2.7 + [tool.uv] find-links 挂阿里云
# CPU 源：win/linux 解析 2.6.0+cpu、macOS 回落 2.6.0；nvidia-*/triton 已在 pyproject
# exclude-dependencies 剔除——纯 CPU 镜像不含 CUDA 依赖，体积 ~1GB（未剔除 ~2.5GB）。
# UV_HTTP_TIMEOUT / UV_CONCURRENT_DOWNLOADS 防 torch 大 wheel（~200MB）下载超时。
ENV UV_HTTP_TIMEOUT=600 \
    UV_CONCURRENT_DOWNLOADS=4
RUN uv sync --frozen --no-install-project --no-dev
# 纯 CPU 自检：torch 必须能 import 且 CUDA 不可用（防 exclude 剔除过头，构建期 fail-fast）
RUN .venv/bin/python -c "import torch; assert not torch.cuda.is_available(), 'CUDA must not be available'; print('torch', torch.__version__, '| cuda:', torch.cuda.is_available())"

# 应用代码
COPY src ./src
COPY static ./static
COPY config ./config
COPY scripts ./scripts

EXPOSE 8000
# models/ 通过 docker-compose volume 挂载（BGE-M3 2GB+，不进镜像）
CMD ["uv", "run", "--no-sync", "uvicorn", "src.webrag.main:app", "--host", "0.0.0.0", "--port", "8000"]
