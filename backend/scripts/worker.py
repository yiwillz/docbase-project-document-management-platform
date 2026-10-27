"""启动后台 worker（存量导入、每天的例行清理）。

用法（在 backend 目录下）：python scripts/worker.py
测试时连测试库：python scripts/worker.py --test（检查测试库配置，文件存 data_test）
"""

import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

if "--test" in sys.argv:
    from test_server import test_database_url  # noqa: E402  同目录

    from app.config import get_settings  # noqa: E402

    os.environ["DOCBASE_DATABASE_URL"] = test_database_url()
    os.environ["DOCBASE_DATA_DIR"] = str(get_settings().test_data_dir)
    get_settings.cache_clear()

from app.worker import main  # noqa: E402

if __name__ == "__main__":
    main()
