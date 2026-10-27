"""建用户（装好后建第一个管理员用；以后在"用户管理"页面里建）。

用法（在 backend 目录下）：
  python scripts/create_user.py --admin                  # 交互输入用户名和密码，建系统管理员
  python scripts/create_user.py --username wang --admin  # 指定用户名，密码交互输入（不显示）
  python scripts/create_user.py --test ...               # 建在测试库里
  python scripts/create_user.py --admin --if-none        # 一个用户都没有时才建（安装脚本用）
已有这个用户名时改成新密码（并解除锁定）。
"""

import argparse
import getpass
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))


def main() -> None:
    parser = argparse.ArgumentParser(description="建用户 / 重设密码")
    parser.add_argument("--username")
    parser.add_argument("--password", help="不给就交互输入（推荐，不会留在命令历史里）")
    parser.add_argument("--display-name")
    parser.add_argument("--admin", action="store_true", help="设为系统管理员")
    parser.add_argument("--test", action="store_true", help="建在测试库里")
    parser.add_argument("--if-none", action="store_true", help="已经有用户就什么都不做")
    args = parser.parse_args()

    if args.test:
        from test_server import test_database_url  # noqa: E402  同目录

        os.environ["DOCBASE_DATABASE_URL"] = test_database_url()
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from app.config import get_settings
    from app.db import get_engine
    from app.migrate import run_migrations
    from app.models import User
    from app.services import users as svc
    from app.services.history import Actor

    get_settings.cache_clear()
    run_migrations()
    if args.if_none:
        from sqlalchemy import func

        with Session(get_engine()) as session:
            n = session.scalar(select(func.count()).select_from(User))
        if n:
            print(f"已有 {n} 个用户，不再新建")
            return
        print("还没有任何用户，请建第一个系统管理员：")
    username = args.username or input("用户名：").strip()
    password = args.password or getpass.getpass(f"密码（至少 {get_settings().password_min_length} 位）：")
    if not args.password and getpass.getpass("再输一次：") != password:
        sys.exit("两次输入的密码不一样")
    actor = Actor(name="create_user.py")
    with Session(get_engine()) as session:
        user = session.scalar(select(User).where(User.username == username))
        if user is None:
            user = svc.create_user(session, {"username": username, "password": password,
                                             "display_name": args.display_name, "is_sysadmin": args.admin}, actor)
            what = "已建用户"
        else:
            svc.reset_password(session, user.id, password, actor)
            if args.admin:
                user.is_sysadmin = True
            user.active = True
            what = "已重设密码"
        session.commit()
        print(f"{what}：{user.username}{'（系统管理员）' if user.is_sysadmin else ''}")


if __name__ == "__main__":
    main()
