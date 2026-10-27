"""备份与恢复（PRD 第 5 节：每天备份、保留 30 天、一键恢复）。

备份目录结构：
    db/<时间>.dump          数据库（pg_dump 自定义格式），每次一份
    files/aa/bb/<sha256>    文件实体；按内容命名、从不修改，所以只复制还没有的（增量）
    manifests/<时间>.json   这次备份的清单：用哪个数据库文件、引用了哪些文件、各表数量（恢复后核对用）
    status.json             最近一次备份的结果（页面提醒用）

每份清单 + 它的数据库文件 + 清单里的文件 = 一个完整的恢复点，所以不需要"每周全量"。
清理：超过保留天数的清单和数据库文件删掉（最新一份永远保留），再删掉没有任何清单引用的文件。

数据库和文件的一致：开一个可重复读事务、导出快照，在这个快照里读文件列表和各表数量，
pg_dump 用同一个快照（--snapshot），所以清单和数据库文件看到的是同一时刻的数据。
"""

import json
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import URL, make_url

from app.config import Settings, app_version, get_settings
from app.models import Blob, Deliverable, File, Project, Version
from app.services.errors import Invalid
from app.services.storage import LocalDiskStorage, get_storage

STAMP = "%Y%m%d-%H%M%S"


# ---------------------------------------------------------------- 准备
def backup_root(settings: Settings | None = None) -> Path:
    root = (settings or get_settings()).backup_dir
    if not root:
        raise Invalid("backup.not_configured", "没有配置备份目录（DOCBASE_BACKUP_DIR）")
    return Path(root)


def pg_tool(name: str, settings: Settings | None = None) -> str:
    """找 PostgreSQL 命令：配置的 pg_bin → PATH → Windows 默认安装目录（取最高版本）。"""
    exe = f"{name}.exe" if sys.platform == "win32" else name
    pg_bin = (settings or get_settings()).pg_bin
    if pg_bin and (Path(pg_bin) / exe).is_file():
        return str(Path(pg_bin) / exe)
    found = shutil.which(name)
    if found:
        return found
    if sys.platform == "win32":
        base = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "PostgreSQL"
        versions = sorted((p for p in base.glob("*/bin") if (p / exe).is_file()),
                          key=lambda p: int(p.parent.name) if p.parent.name.isdigit() else 0)
        if versions:
            return str(versions[-1] / exe)
    raise Invalid("backup.no_pg_tool", f"找不到 PostgreSQL 的 {name} 命令，请在配置里设 DOCBASE_PG_BIN（如 "
                  r"C:\Program Files\PostgreSQL\17\bin）", tool=name)


def _conn_args(url: URL) -> tuple[list[str], dict[str, str]]:
    args = ["-h", url.host or "localhost", "-p", str(url.port or 5432)]
    if url.username:
        args += ["-U", url.username]
    env = {**os.environ, "PGCLIENTENCODING": "UTF8"}
    if url.password:
        env["PGPASSWORD"] = url.password
    return args, env


def _run(cmd: list[str], env: dict[str, str]) -> None:
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise Invalid("backup.command_failed", f"{Path(cmd[0]).name} 失败：{(r.stderr or r.stdout).strip()[-1500:]}")


def _counts(conn: Any) -> dict[str, int]:
    return {name: conn.execute(select(func.count()).select_from(model)).scalar() or 0
            for name, model in (("projects", Project), ("deliverables", Deliverable), ("files", File),
                                ("versions", Version), ("blobs", Blob))}


def _blob_path(root: Path, sha: str) -> Path:
    return root / "files" / sha[:2] / sha[2:4] / sha


def _write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------- 状态
def read_status(settings: Settings | None = None) -> dict[str, Any]:
    root = backup_root(settings)
    f = root / "status.json"
    if not f.is_file():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_status(root: Path, **changes: Any) -> None:
    try:
        status = read_status() if (root / "status.json").is_file() else {}
        status.update(changes)
        _write_json(root / "status.json", status)
    except OSError:
        pass  # 备份目录本身不可用：结果只记在任务里


# ---------------------------------------------------------------- 备份
def run_backup(now: datetime | None = None) -> dict[str, Any]:
    """做一次备份并清理过期的；返回这次的摘要。"""
    from app.db import get_engine

    settings = get_settings()
    root = backup_root(settings)
    now = (now or datetime.now()).astimezone()
    stamp = now.strftime(STAMP)
    try:
        for d in ("db", "files", "manifests"):
            (root / d).mkdir(parents=True, exist_ok=True)
        url = make_url(settings.database_url)
        dump = root / "db" / f"{stamp}.dump"
        part = dump.with_suffix(".dump.part")
        args, env = _conn_args(url)
        with get_engine().connect().execution_options(isolation_level="REPEATABLE READ") as conn, conn.begin():
            snapshot = conn.execute(text("SELECT pg_export_snapshot()")).scalar()
            shas = set(conn.execute(select(Blob.sha256)).scalars())
            counts = _counts(conn)
            revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            # 事务开着、快照有效时做 pg_dump
            _run([pg_tool("pg_dump", settings), *args, "-Fc", f"--snapshot={snapshot}", "-f", str(part),
                  url.database or ""], env)
        os.replace(part, dump)

        storage = get_storage()
        copied = skipped = 0
        missing: list[str] = []
        for sha in sorted(shas):
            dst = _blob_path(root, sha)
            if dst.is_file():
                skipped += 1
                continue
            try:
                src = storage.open(sha)
            except (OSError, Exception):  # noqa: BLE001  文件已被清理等
                missing.append(sha)
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(dst.name + ".part")
            with src, open(tmp, "wb") as out:
                shutil.copyfileobj(src, out, 1024 * 1024)
            os.replace(tmp, dst)
            copied += 1
        manifest = {
            "stamp": stamp, "created_at": now.isoformat(), "version": app_version(), "revision": revision,
            "database": url.database, "dump": f"db/{dump.name}", "dump_size": dump.stat().st_size,
            "counts": counts, "blobs": sorted(set(shas) - set(missing)), "missing": missing,
        }
        _write_json(root / "manifests" / f"{stamp}.json", manifest)  # 清单最后写：有清单才算备份完成
        pruned = prune(root, settings.backup_days, today=now.date())
        summary = {"stamp": stamp, "copied": copied, "skipped": skipped, "missing": len(missing),
                   "dump_size": manifest["dump_size"], "counts": counts, **pruned}
        _save_status(root, last_attempt=now.isoformat(), last_ok=now.isoformat(), last_error=None,
                     last_stamp=stamp, last_summary=summary)
        return summary
    except Exception as exc:
        _save_status(root, last_attempt=now.isoformat(), last_error=str(exc)[:1000])
        raise


def manifests(root: Path | None = None) -> list[dict[str, Any]]:
    """全部备份点（旧的在前）；坏掉的清单跳过。"""
    root = root or backup_root()
    out = []
    for f in sorted((root / "manifests").glob("*.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def prune(root: Path, keep_days: int, today: date | None = None) -> dict[str, int]:
    """删掉超过保留天数的备份点（最新一份总是留着），再删没有清单引用的文件和数据库文件。"""
    today = today or date.today()
    oldest = today - timedelta(days=keep_days - 1)
    all_m = manifests(root)
    keep, drop = [], []
    for i, m in enumerate(all_m):
        day = datetime.strptime(m["stamp"], STAMP).date()
        (keep if day >= oldest or i == len(all_m) - 1 else drop).append(m)
    for m in drop:
        (root / "manifests" / f"{m['stamp']}.json").unlink(missing_ok=True)
    used_dumps = {Path(m["dump"]).name for m in keep}
    dumps = 0
    for f in (root / "db").glob("*"):
        if f.name not in used_dumps:
            f.unlink(missing_ok=True)
            dumps += 1
    used = {sha for m in keep for sha in m.get("blobs", [])}
    files = 0
    for f in (root / "files").glob("*/*/*"):
        if f.name not in used:  # 包括没写完的 .part
            f.unlink(missing_ok=True)
            files += 1
    return {"pruned_points": len(drop), "pruned_dumps": dumps, "pruned_files": files}


# ---------------------------------------------------------------- 恢复
def find_manifest(stamp: str | None, root: Path | None = None) -> dict[str, Any]:
    """stamp 为空取最新；可以只给日期（20261026）取那天最后一份。"""
    all_m = manifests(root)
    if not all_m:
        raise Invalid("backup.none", "备份目录里没有备份")
    if not stamp:
        return all_m[-1]
    hits = [m for m in all_m if m["stamp"] == stamp or m["stamp"].startswith(stamp.replace("-", ""))]
    if not hits:
        raise Invalid("backup.not_found", f"没有 {stamp} 的备份", stamp=stamp)
    return hits[-1]


def _database_exists(url: URL) -> tuple[bool, bool]:
    """(库存在, 库里有表)。"""
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as c:
            exists = bool(c.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"),
                                    {"n": url.database}).scalar())
        if not exists:
            return False, False
        eng = create_engine(url)
        try:
            with eng.connect() as c:
                has = bool(c.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")).scalar())
        finally:
            eng.dispose()
        return True, has
    finally:
        admin.dispose()


def restore(target_url: str, target_data_dir: Path, stamp: str | None = None, *, force: bool = False,
            root: Path | None = None) -> dict[str, Any]:
    """把一个备份点恢复到 target_url（库）和 target_data_dir（文件目录），返回核对结果。

    目标库已有数据时拒绝，除非 force（会先清掉库里的表）。
    """
    root = root or backup_root()
    m = find_manifest(stamp, root)
    dump = root / m["dump"]
    if not dump.is_file():
        raise Invalid("backup.dump_missing", f"备份的数据库文件不见了：{dump}")
    lost = [sha for sha in m["blobs"] if not _blob_path(root, sha).is_file()]
    if lost:
        raise Invalid("backup.files_missing", f"备份里少了 {len(lost)} 个文件（如 {lost[0]}），备份已损坏",
                      count=len(lost))
    url = make_url(target_url)
    exists, has_tables = _database_exists(url)
    if has_tables and not force:
        raise Invalid("backup.target_not_empty", f"目标库 {url.database} 里已经有数据；确认要覆盖请加 --force",
                      database=url.database)
    # 先放文件（目标目录不能写就在动数据库之前停下），再恢复数据库
    target = LocalDiskStorage(Path(target_data_dir))
    target.check()
    copied = 0
    for sha in m["blobs"]:
        dst = target.path(sha)
        src = _blob_path(root, sha)
        if dst.is_file() and dst.stat().st_size == src.stat().st_size:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".part")
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
        copied += 1

    args, env = _conn_args(url)
    if not exists:
        admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
        try:
            with admin.connect() as c:
                c.execute(text(f'CREATE DATABASE "{url.database}"'))
        finally:
            admin.dispose()
    cmd = [pg_tool("pg_restore"), *args, "--no-owner", "--no-privileges", "--exit-on-error", "-d", url.database or ""]
    if has_tables:
        cmd += ["--clean", "--if-exists"]
    _run([*cmd, str(dump)], env)

    eng = create_engine(url)
    try:
        with eng.connect() as c:
            counts = _counts(c)
    finally:
        eng.dispose()
    return {"stamp": m["stamp"], "database": url.database, "data_dir": str(target_data_dir),
            "files_copied": copied, "expected": m["counts"], "restored": counts,
            "match": counts == m["counts"]}
