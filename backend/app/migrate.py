"""数据库迁移：启动时自动执行到最新版本（配置项 DOCBASE_AUTO_MIGRATE 控制）。"""

from alembic.config import Config

from alembic import command
from app.config import BACKEND_DIR, get_settings


def alembic_config(database_url: str | None = None) -> Config:
    cfg = Config(BACKEND_DIR / "alembic.ini")
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.attributes["database_url"] = database_url or get_settings().database_url
    # 应用内调用时不覆盖应用自己的日志配置
    cfg.attributes["configure_logger"] = False
    return cfg


def run_migrations(database_url: str | None = None) -> None:
    """升级数据库结构到最新版本。"""
    command.upgrade(alembic_config(database_url), "head")
