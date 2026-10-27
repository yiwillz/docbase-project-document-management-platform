"""把最近的日志和运行情况打成一个 zip，发给维护方排查问题。

用法（在 backend 目录下）：
  python scripts/collect_logs.py               # 最近 7 天，zip 放在当前目录
  python scripts/collect_logs.py --days 3 --out D:/
zip 里有：日志文件、版本、配置（密码已隐去）、数据库 / 存储 / 备份状态、最近的后台任务、磁盘空间。
不含任何项目文件和数据库内容。
"""

import argparse
import json
import platform
import shutil
import sys
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))


def redact(value: object) -> str:
    """配置里的密码隐去：数据库连接串里的密码、名字带 password / secret 的项。"""
    from sqlalchemy.engine import make_url

    text = str(value)
    if "://" in text:
        try:
            return make_url(text).render_as_string(hide_password=True)
        except Exception:  # noqa: BLE001
            return "***"
    return text


def collect(days: int, out_dir: Path) -> Path:
    from sqlalchemy import select

    from app.config import app_version, env_file, get_settings

    s = get_settings()
    info: dict[str, object] = {
        "collected_at": datetime.now().astimezone().isoformat(),
        "version": app_version(),
        "python": sys.version,
        "platform": platform.platform(),
        "env_file": str(env_file()),
        "config": {k: ("***" if any(w in k for w in ("password", "secret")) else redact(v))
                   for k, v in s.model_dump().items()},
    }
    disks = {}
    for name in ("data_dir", "backup_dir", "log_dir"):
        p = getattr(s, name)
        if p:
            try:
                u = shutil.disk_usage(p)
                disks[name] = {"path": str(p), "free_gb": round(u.free / 1e9, 1), "total_gb": round(u.total / 1e9, 1)}
            except OSError as exc:
                disks[name] = {"path": str(p), "error": str(exc)}
    info["disks"] = disks
    try:
        from sqlalchemy.orm import Session

        from app.db import get_engine
        from app.models import Job
        from app.services import system

        with Session(get_engine()) as session:
            info["status"] = system.status(session)
            jobs = session.scalars(select(Job).order_by(Job.id.desc()).limit(30))
            info["recent_jobs"] = [{"id": j.id, "kind": j.kind, "status": j.status.value,
                                    "created_at": j.created_at, "finished_at": j.finished_at,
                                    "error": (j.error or "").split("\n")[0][:300] or None} for j in jobs]
    except Exception as exc:  # noqa: BLE001  数据库连不上也要能打包日志
        info["status_error"] = str(exc)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(out_dir) / f"docbase-logs-{stamp}.zip"
    since = date.today() - timedelta(days=days - 1)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("info.json", json.dumps(info, ensure_ascii=False, indent=1, default=str))
        if s.log_dir and Path(s.log_dir).is_dir():
            for f in sorted(Path(s.log_dir).glob("*.log")):
                try:
                    day = date.fromisoformat(f.name[-14:-4])
                except ValueError:
                    day = date.fromtimestamp(f.stat().st_mtime)
                if day >= since:
                    z.write(f, f"logs/{f.name}")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="打包最近的日志发给维护方")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--out", default=".")
    args = parser.parse_args()
    out = collect(args.days, Path(args.out))
    print(f"已打包：{out.resolve()}")


if __name__ == "__main__":
    main()
