"""从备份恢复：数据库恢复到一个库，文件恢复到一个目录，然后核对数量。

用法（在 backend 目录下）：
  python scripts/restore.py --list                                   # 看有哪些备份点
  python scripts/restore.py --to docbase_restore --data-dir D:/restore-data          # 最新一份恢复到新库（演练）
  python scripts/restore.py --stamp 20261026 --to docbase_restore --data-dir D:/restore-data
默认恢复到一个新库（和当前用的库在同一台数据库服务器上），不会碰正在用的库。
真出事要覆盖正在用的库时：先停掉后端和 worker 服务，再加 --to <当前库名> --force，并按提示输入库名确认。
"""

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))


def main() -> None:
    parser = argparse.ArgumentParser(description="从备份恢复")
    parser.add_argument("--list", action="store_true", help="列出备份点")
    parser.add_argument("--stamp", help="备份点（如 20261026-010000，或只给日期取那天最后一份）；不给取最新")
    parser.add_argument("--to", help="恢复到哪个库（库名）")
    parser.add_argument("--data-dir", help="文件恢复到哪个目录")
    parser.add_argument("--force", action="store_true", help="目标库已有数据时覆盖")
    parser.add_argument("--test", action="store_true", help="用测试库的备份目录（试用）")
    args = parser.parse_args()
    if args.test:
        from backup import use_test_env  # noqa: E402  同目录

        use_test_env()

    from sqlalchemy.engine import make_url

    from app.config import get_settings
    from app.services import backup
    from app.services.errors import ServiceError

    if args.list or not args.to:
        import subprocess

        subprocess.run([sys.executable, str(Path(__file__).with_name("backup.py")), "--list",
                        *(["--test"] if args.test else [])], check=False)
        if not args.list:
            print("\n要恢复请给 --to 库名 和 --data-dir 文件目录")
        return
    if not args.data_dir:
        sys.exit("请给 --data-dir：文件恢复到哪个目录")
    settings = get_settings()
    current = make_url(settings.database_url)
    target = current.set(database=args.to)
    data_dir = Path(args.data_dir).resolve()
    overwrite_live = args.to == current.database or data_dir == Path(settings.data_dir).resolve()
    if overwrite_live:
        if not args.force:
            sys.exit(f"{args.to} / {data_dir} 是正在用的库或文件目录；真要覆盖请先停服务，再加 --force")
        typed = input(f"将覆盖正在用的库 {args.to}，输入库名确认：").strip()
        if typed != args.to:
            sys.exit("没有确认，已取消")
    try:
        r = backup.restore(target.render_as_string(hide_password=False), data_dir, args.stamp, force=args.force)
    except ServiceError as exc:
        sys.exit(f"失败：{exc.message}")
    print(f"已恢复备份点 {r['stamp']} → 库 {r['database']}，文件目录 {r['data_dir']}（复制 {r['files_copied']} 个文件）")
    print("核对：")
    for key, expected in r["expected"].items():
        got = r["restored"].get(key)
        print(f"  {key:<13} 备份时 {expected:>7}   恢复后 {got:>7}   {'一致' if got == expected else '不一致！'}")
    if not r["match"]:
        sys.exit("数量不一致，请把以上输出发给维护方")
    print("全部一致")


if __name__ == "__main__":
    main()
