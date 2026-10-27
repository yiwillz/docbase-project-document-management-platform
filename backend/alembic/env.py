"""Alembic 迁移环境：数据库地址从 app.config 读取，不写在 alembic.ini 里。"""

from logging.config import fileConfig

from sqlalchemy import create_engine, pool

import app.models  # noqa: F401  注册全部模型到 Base.metadata
from alembic import context
from app.config import get_settings
from app.db import Base

config = context.config

if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    # 调用方（如 app.migrate.run_migrations）可通过 attributes 指定地址
    return config.attributes.get("database_url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(get_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
