"""pytest 公共配置。

测试一律连测试库（DOCBASE_TEST_DATABASE_URL，可写在 backend/.env），
导入 app 之前先把它设成 DOCBASE_DATABASE_URL，保证不会碰到正式库。
"""

import os
import tempfile
from pathlib import Path

import pytest

from app.config import get_settings

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
PRIVATE_DIR = FIXTURES_DIR / "private"

_settings = get_settings()
TEST_DATABASE_URL = _settings.test_database_url

# 测试库上认 X-User 请求头、匿名也当系统管理员（只在库名以 _test 结尾时生效，见 services/auth.py）
os.environ["DOCBASE_AUTH_DEV_HEADER"] = "true"
os.environ["DOCBASE_AUTH_DEV_ANONYMOUS"] = "true"

# 文件存储放到临时目录，测试不碰正式文件，也不留下文件
os.environ["DOCBASE_DATA_DIR"] = tempfile.mkdtemp(prefix="docbase-test-")
get_settings.cache_clear()

if TEST_DATABASE_URL:
    if TEST_DATABASE_URL == _settings.database_url:
        raise RuntimeError("DOCBASE_TEST_DATABASE_URL 不能和 DOCBASE_DATABASE_URL 相同，拒绝运行测试")
    os.environ["DOCBASE_DATABASE_URL"] = TEST_DATABASE_URL
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def test_db_configured() -> bool:
    return bool(TEST_DATABASE_URL)


@pytest.fixture(scope="session")
def migrated_db(test_db_configured) -> str:
    """把测试库清空后重新迁移到最新版本（同时验证 downgrade / upgrade 都能跑通）。"""
    if not test_db_configured:
        pytest.skip("未配置 DOCBASE_TEST_DATABASE_URL")
    from alembic import command
    from app.migrate import alembic_config

    cfg = alembic_config(TEST_DATABASE_URL)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    return TEST_DATABASE_URL


@pytest.fixture
def session(migrated_db):
    """测试用 Session；测试结束后回滚，不留数据。"""
    from sqlalchemy.orm import Session

    from app.db import get_engine

    conn = get_engine().connect()
    trans = conn.begin()
    # 接口里的 commit 只提交到保存点，测试结束整体回滚
    s = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    yield s
    s.close()
    trans.rollback()
    conn.close()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def api_client(session):
    """调用接口用的客户端：接口和测试共用同一个 session，测试结束不留数据。"""
    from fastapi.testclient import TestClient

    from app.db import get_session
    from app.main import app

    app.dependency_overrides[get_session] = lambda: session
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.fixture(scope="session")
def sample_mdl_path() -> Path:
    """真实项目 MDL（私有数据，只在本机）；不存在时跳过相关测试。"""
    path = PRIVATE_DIR / "sample_mdl.xlsx"
    if not path.exists():
        pytest.skip(f"私有样本不存在：{path}")
    return path
