"""马上备份一次（worker 每天也会自动做），或列出已有的备份。

用法（在 backend 目录下）：
  python scripts/backup.py            # 备份一次：数据库 + 新增的文件，并清理超过保留天数的
  python scripts/backup.py --list     # 列出备份点
  python scripts/backup.py --test     # 用测试库、测试文件目录，备份到 backend/backup_test（试用）
备份目录在配置 DOCBASE_BACKUP_DIR 里设；恢复用 scripts/restore.py。
"""

import argparse
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))


def use_test_env() -> None:
    """切到测试库、测试文件目录、测试备份目录（restore.py 也用）。"""
    from test_server import test_database_url  # noqa: E402  同目录

    from app.config import get_settings

    os.environ["DOCBASE_DATABASE_URL"] = test_database_url()
    os.environ["DOCBASE_DATA_DIR"] = str(get_settings().test_data_dir)
    os.environ["DOCBASE_BACKUP_DIR"] = str(BACKEND_DIR / "backup_test")
    get_settings.cache_clear()


def main() -> None:
    parser = argparse.ArgumentParser(description="备份 / 列出备份")
    parser.add_argument("--list", action="store_true", help="列出备份点")
    parser.add_argument("--test", action="store_true", help="用测试库和测试目录")
    args = parser.parse_args()
    if args.test:
        use_test_env()

    from app.services import backup
    from app.services.errors import ServiceError

    try:
        if args.list:
            points = backup.manifests()
            if not points:
                print("还没有备份")
            for m in points:
                c = m["counts"]
                print(f"{m['stamp']}  版本 {m.get('version', '?')}  项目 {c['projects']}  应交项 {c['deliverables']}"
                      f"  文件 {c['files']}  版本 {c['versions']}  数据库 {m['dump_size'] // 1024} KB")
            return
        r = backup.run_backup()
    except ServiceError as exc:
        sys.exit(f"失败：{exc.message}")
    print(f"备份完成：{r['stamp']}")
    print(f"  新复制文件 {r['copied']} 个，已有 {r['skipped']} 个，找不到 {r['missing']} 个")
    print(f"  清理：过期备份点 {r['pruned_points']} 个、数据库文件 {r['pruned_dumps']} 个、文件 {r['pruned_files']} 个")


if __name__ == "__main__":
    main()
