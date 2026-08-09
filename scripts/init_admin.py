"""创建管理后台管理员账号（账号存 MySQL webrag_admin.users，role='admin'）。

用法（Docker 部署，MySQL 随 compose 已启动）：
    docker compose exec webrag-app uv run --no-sync python scripts/init_admin.py \
        --username admin --password <你的密码>

本机开发：
    uv run python scripts/init_admin.py --username admin --password <你的密码>

参数优先级：命令行 > 环境变量（ADMIN_USERNAME / ADMIN_PASSWORD）> 交互输入。
用户名已存在时默认拒绝（避免误覆盖）；加 --force 可重置该用户密码。
负责人：管理后台（离线知识入库）。
"""

import argparse
import getpass
import os
import sys
from pathlib import Path

# 保证从项目根导入 src（直接运行 scripts/xxx.py 时 cwd 不在 sys.path）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.webrag.admin.auth import hash_password
from src.webrag.admin.db import AdminDB, AdminDBError
from src.webrag.config import load_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="创建 / 重置管理后台管理员账号")
    parser.add_argument("--username", default=None, help="管理员用户名（默认取环境变量 ADMIN_USERNAME）")
    parser.add_argument("--password", default=None, help="密码（默认取环境变量 ADMIN_PASSWORD，再否则交互输入）")
    parser.add_argument("--force", action="store_true", help="用户名已存在时重置其密码（默认拒绝）")
    args = parser.parse_args()

    settings = load_settings()
    username = args.username or os.getenv("ADMIN_USERNAME", "")
    password = args.password or os.getenv("ADMIN_PASSWORD", "")

    if not username:
        username = input("用户名: ").strip()
    if not password:
        password = getpass.getpass("密码（输入不回显）: ")

    if not username or not password:
        print("[init_admin] 用户名与密码不能为空")
        sys.exit(1)

    db = AdminDB(settings)
    try:
        db.ensure_schema()
    except AdminDBError as exc:
        print(f"[init_admin] MySQL 不可用：{exc}")
        print("          请确认 mysql 服务已启动（docker compose up -d）且 .env 的 MYSQL_* 正确")
        sys.exit(1)

    existing = db.get_user_by_username(username)
    if existing and not args.force:
        print(f"[init_admin] 用户名 {username} 已存在（如需重置密码请加 --force）")
        sys.exit(1)

    hashed = hash_password(password)
    if existing:
        db.update_user_password(username, hashed)
        print(f"[init_admin] 管理员 {username} 密码已重置")
    else:
        db.create_user(username, hashed, role="admin")
        print(f"[init_admin] 管理员 {username} 创建成功（存于 MySQL {settings.mysql_database}.users，role=admin）")
        print("           浏览器打开 http://localhost:8000/admin/ 登录后即可上传/粘贴知识文档入库")


if __name__ == "__main__":
    main()
