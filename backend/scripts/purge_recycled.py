"""清理回收区：进回收区满 DOCBASE_RECYCLE_DAYS 天（默认 90）的版本，删除磁盘文件，记录保留。

用法（在 backend 目录下）：python scripts/purge_recycled.py
第 14 步做好 worker 后由 worker 每天自动执行。
"""

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy.orm import Session  # noqa: E402

from app.db import get_engine  # noqa: E402
from app.services.files import delete_unused_blobs, purge_recycled  # noqa: E402
from app.services.history import Actor  # noqa: E402
from app.services.storage import get_storage  # noqa: E402


def main() -> None:
    storage = get_storage()
    storage.check()
    with Session(get_engine()) as session:
        result = purge_recycled(session, Actor(name="system"))
        session.commit()  # 先提交，再删磁盘文件
        deleted = delete_unused_blobs(session, storage, result["blobs"])
    print(f"清理了 {result['versions']} 个版本，删除磁盘文件 {deleted} 个")


if __name__ == "__main__":
    main()
