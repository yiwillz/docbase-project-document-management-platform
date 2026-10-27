"""清空【测试库】和测试文件目录，重建测试账号（自动测试留下的一堆项目一次清掉）。

用法（在 backend 目录下）：
  python scripts/reset_test_db.py          # 会让你输入 yes 确认
  python scripts/reset_test_db.py --yes    # 不问

只动测试库（库名必须以 _test 结尾，且和正式库不同）和测试文件目录（DOCBASE_TEST_DATA_DIR，默认 backend/data_test）；
正式库、正式文件目录碰不到。清空前请先停掉连测试库的后端和 worker。
"""

import os
import shutil
import stat
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_server import ensure_test_users, test_database_url  # noqa: E402

from app.config import get_settings  # noqa: E402


def main() -> None:
    url = test_database_url()  # 不是测试库就直接退出
    settings = get_settings()
    data_dir = Path(settings.test_data_dir).resolve()
    if data_dir == Path(settings.data_dir).resolve():
        sys.exit("拒绝：测试文件目录和正式文件目录是同一个")
    from sqlalchemy.engine import make_url

    print(f"将清空测试库 {make_url(url).database} 的全部数据，并删除测试文件目录 {data_dir} 里的文件。")
    if "--yes" not in sys.argv and input("确认请输入 yes：").strip().lower() != "yes":
        sys.exit("已取消")

    from alembic import command
    from app.migrate import alembic_config

    cfg = alembic_config(url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    def writable_and_retry(func, path, _exc) -> None:
        # 镜像用硬链接时文件被设成只读，删之前先去掉只读
        os.chmod(path, stat.S_IWRITE)
        func(path)

    for child in ("blobs", "staging", "exports"):
        if (data_dir / child).exists():
            shutil.rmtree(data_dir / child, onexc=writable_and_retry)
    print("测试库已清空")
    ensure_test_users(url)


if __name__ == "__main__":
    main()
