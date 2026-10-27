"""启动一个连【测试库】的后端，用于手动试接口 / 冒烟测试，不碰正式库。

用法（在 backend 目录下）：python scripts/test_server.py [端口，默认 8001]

安全检查（任一不满足就拒绝启动）：
- 配置了 DOCBASE_TEST_DATABASE_URL
- 测试库地址和正式库地址不同
- 测试库的库名以 _test 结尾

文件存储用 DOCBASE_TEST_DATA_DIR（默认 backend/data_test），不碰正式文件。
"""

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy.engine import make_url

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_settings  # noqa: E402


def test_database_url() -> str:
    settings = get_settings()
    url = settings.test_database_url
    if not url:
        sys.exit("拒绝启动：没有配置 DOCBASE_TEST_DATABASE_URL")
    if url == settings.database_url:
        sys.exit("拒绝启动：测试库地址和正式库地址相同")
    name = make_url(url).database or ""
    if not name.endswith("_test"):
        sys.exit(f"拒绝启动：测试库名 {name!r} 不是以 _test 结尾")
    return url


TEST_USER, TEST_PASSWORD = "tester", "tester123"
# 三种项目角色各一个测试账号（不是系统管理员），自动加进测试库里每个没归档的项目
ROLE_USERS = (("test_admin", "admin"), ("test_member", "member"), ("test_viewer", "readonly"))
ROLE_PASSWORD = "test12345"


def ensure_test_users(url: str) -> None:
    """测试库里备好能登录的账号：tester（系统管理员）、test_admin / test_member / test_viewer（三种项目角色）。"""
    os.environ["DOCBASE_DATABASE_URL"] = url
    get_settings.cache_clear()
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from app.db import get_engine
    from app.migrate import run_migrations
    from app.models import Project, ProjectMember, User
    from app.services import users as svc
    from app.services.history import Actor

    if not (make_url(url).database or "").endswith("_test"):
        sys.exit("拒绝：只在测试库上建测试账号")
    run_migrations()
    actor = Actor(name="test_server.py")
    with Session(get_engine()) as session:
        for name, password, admin in [(TEST_USER, TEST_PASSWORD, True)] + [(n, ROLE_PASSWORD, False) for n, _ in ROLE_USERS]:
            if session.scalar(select(User.id).where(User.username == name)) is None:
                svc.create_user(session, {"username": name, "password": password, "is_sysadmin": admin}, actor)
        session.flush()
        ids = {u.username: u.id for u in session.scalars(select(User).where(User.username.in_([n for n, _ in ROLE_USERS])))}
        added = 0
        for project in session.scalars(select(Project).where(Project.archived_at.is_(None))):
            members = set(session.scalars(select(ProjectMember.user_id).where(
                ProjectMember.project_id == project.id, ProjectMember.active)))
            for name, role in ROLE_USERS:
                if ids[name] not in members:
                    svc.set_member(session, project.id, ids[name], role, actor)
                    added += 1
        session.commit()
    print(f"测试库登录账号：{TEST_USER} / {TEST_PASSWORD}（系统管理员）")
    print(f"  三种项目角色：test_admin（项目管理员）、test_member（成员）、test_viewer（只读），密码都是 {ROLE_PASSWORD}")
    if added:
        print(f"  已把它们加进测试库里没归档的项目（新加 {added} 条成员）；之后新建的项目重启测试后端时再加")


def main() -> None:
    port = sys.argv[1] if len(sys.argv) > 1 else "8001"
    env = {**os.environ, "DOCBASE_DATABASE_URL": test_database_url(),
           "DOCBASE_DATA_DIR": str(get_settings().test_data_dir),
           # 页面测试的辅助接口调用带 X-User（只在测试库上认）；浏览器里要正常登录
           "DOCBASE_AUTH_DEV_HEADER": "true"}
    ensure_test_users(env["DOCBASE_DATABASE_URL"])
    # 网址前后留空格，避免终端把中文标点当成链接的一部分
    print(f"测试服务器（连接测试库）： http://localhost:{port}/docs ")
    subprocess.run(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", port],
        cwd=BACKEND_DIR,
        env=env,
        check=False,
    )


if __name__ == "__main__":
    main()
