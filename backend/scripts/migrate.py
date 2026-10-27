"""升级数据库结构到最新版本（安装、升级脚本用；后端启动时也会自动做）。

用法（在 backend 目录下）：python scripts/migrate.py
"""

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import text  # noqa: E402

from app.db import get_engine  # noqa: E402
from app.migrate import run_migrations  # noqa: E402

if __name__ == "__main__":
    run_migrations()
    with get_engine().connect() as conn:
        print("数据库结构已是最新：", conn.execute(text("SELECT version_num FROM alembic_version")).scalar())
