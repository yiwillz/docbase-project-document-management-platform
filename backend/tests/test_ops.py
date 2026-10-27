"""第 17 步：备份与恢复、日志切分、存储和备份状态。

备份要看到已提交的数据，所以备份测试用一个临时库（测试库名 + _bk_test），恢复到另一个临时库（+ _rs_test），测完删掉。
"""

import hashlib
import json
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app import logs
from app.models import Job, JobStatus
from app.services import backup, system
from app.services.errors import Invalid
from app.services.storage import LocalDiskStorage

CSRF = {"X-Docbase": "1"}


# ---------------------------------------------------------------- 临时库
def _admin(url):
    return create_engine(make_url(url).set(database="postgres"), isolation_level="AUTOCOMMIT")


def _drop(url) -> None:
    eng = _admin(url)
    with eng.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{make_url(url).database}" WITH (FORCE)'))
    eng.dispose()


def _reset_caches() -> None:
    from app.config import get_settings
    from app.db import get_engine, get_sessionmaker
    from app.services import storage

    try:
        get_engine().dispose()
    except Exception:  # noqa: BLE001
        pass
    for f in (get_settings, get_engine, get_sessionmaker, storage._local):
        f.cache_clear()
    system._cache.update(at=0.0, value=None)


@pytest.fixture
def scratch(migrated_db, monkeypatch, tmp_path):
    """一个有已提交数据的临时库 + 临时文件目录 + 临时备份目录；返回 (接口客户端, 路径们)。"""
    src = make_url(migrated_db).set(database=make_url(migrated_db).database + "_bk_test")
    dst = src.set(database=make_url(migrated_db).database + "_rs_test")
    src_s, dst_s = src.render_as_string(hide_password=False), dst.render_as_string(hide_password=False)
    for u in (src_s, dst_s):
        _drop(u)
    eng = _admin(src_s)
    with eng.connect() as c:
        c.execute(text(f'CREATE DATABASE "{src.database}"'))
    eng.dispose()
    paths = {"data": tmp_path / "data", "backup": tmp_path / "backup", "restore": tmp_path / "restore",
             "src": src_s, "dst": dst_s}
    monkeypatch.setenv("DOCBASE_DATABASE_URL", src_s)
    monkeypatch.setenv("DOCBASE_DATA_DIR", str(paths["data"]))
    monkeypatch.setenv("DOCBASE_BACKUP_DIR", str(paths["backup"]))
    _reset_caches()
    from app.main import app

    try:
        with TestClient(app) as c:  # 启动时自动迁移这个临时库
            yield c, paths
    finally:
        _reset_caches()
        for u in (src_s, dst_s):
            _drop(u)
        monkeypatch.undo()
        _reset_caches()


def _seed(c: TestClient) -> tuple[int, list[int]]:
    """建项目、种类、设备，返回 (项目 id, 应交项 id)。"""
    p = c.post("/api/projects", json={"code": "BK-1", "name": "Backup demo"}, headers=CSRF).json()
    base = f"/api/projects/{p['id']}"
    m = c.post(f"{base}/milestones", json={"name": "Design"}, headers=CSRF).json()
    c.post(f"{base}/document-types/bulk", json={"milestone_id": m["id"], "rows": [{"name": "Spec"}, {"name": "Layout"}]},
           headers=CSRF)
    items = c.get(f"{base}/deliverables").json()["items"]
    return p["id"], [i["id"] for i in items]


def _store(c: TestClient, pid: int, did: int, name: str, content: bytes) -> None:
    base = f"/api/projects/{pid}"
    r = c.post(f"{base}/files/preview", data={"deliverable_ids": str(did)}, headers=CSRF,
               files={"file": (name, content, "application/pdf")})
    assert r.status_code == 200, r.text
    r = c.post(f"{base}/files/uploads/{r.json()['token']}/confirm", json={}, headers=CSRF)
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------- 备份与恢复
def test_backup_incremental_restore_and_prune(scratch):
    c, paths = scratch
    pid, ids = _seed(c)
    _store(c, pid, ids[0], "Spec 20260226.pdf", b"spec v1")
    _store(c, pid, ids[1], "Layout 20260226.pdf", b"layout v1")
    now = datetime.now().astimezone()

    # 40 天前的一份（之后会被清理）、今天的两份：第二份只复制新增的文件
    old = backup.run_backup(now=now - timedelta(days=40))
    assert old["copied"] == 2
    first = backup.run_backup(now=now - timedelta(seconds=2))
    assert first["copied"] == 0 and first["skipped"] == 2 and first["pruned_points"] == 1
    _store(c, pid, ids[0], "Spec 20260301.pdf", b"spec v2")
    second = backup.run_backup(now=now)
    assert second["copied"] == 1 and second["skipped"] == 2
    assert [m["stamp"] for m in backup.manifests()] == [first["stamp"], second["stamp"]]
    assert second["counts"] == {"projects": 1, "deliverables": 2, "files": 2, "versions": 3, "blobs": 3}
    # 没有清单引用的文件下次被清掉
    stray = paths["backup"] / "files" / "ff" / "ff" / ("f" * 64)
    stray.parent.mkdir(parents=True)
    stray.write_bytes(b"x")
    assert backup.prune(paths["backup"], 30)["pruned_files"] == 1

    # 状态：页面提醒用
    status = backup.read_status()
    assert status["last_stamp"] == second["stamp"] and status["last_error"] is None

    # 恢复第一份（spec v2 之前）到新库、新目录：数量一致、文件内容一致
    r = backup.restore(paths["dst"], paths["restore"], first["stamp"])
    assert r["match"] and r["restored"]["versions"] == 2
    target = LocalDiskStorage(paths["restore"])
    for content in (b"spec v1", b"layout v1"):
        sha = hashlib.sha256(content).hexdigest()
        assert target.path(sha).read_bytes() == content
    assert not target.path(hashlib.sha256(b"spec v2").hexdigest()).exists()

    # 目标库已有数据：不加 force 拒绝；加了覆盖成最新一份
    with pytest.raises(Invalid) as e:
        backup.restore(paths["dst"], paths["restore"])
    assert e.value.code == "backup.target_not_empty"
    r = backup.restore(paths["dst"], paths["restore"], force=True)
    assert r["match"] and r["restored"]["versions"] == 3

    # 备份里少了文件：恢复前就报"备份已损坏"
    sha = hashlib.sha256(b"spec v2").hexdigest()
    (paths["backup"] / "files" / sha[:2] / sha[2:4] / sha).unlink()
    with pytest.raises(Invalid) as e:
        backup.restore(paths["dst"], paths["restore"], force=True)
    assert e.value.code == "backup.files_missing"


def test_backup_failure_is_recorded(scratch, monkeypatch):
    c, paths = scratch
    monkeypatch.setattr(backup, "pg_tool", lambda name, settings=None: "no-such-pg_dump-command")
    with pytest.raises(Exception):  # noqa: B017
        backup.run_backup()
    assert backup.read_status()["last_error"]


def test_find_manifest_by_day(tmp_path):
    (tmp_path / "manifests").mkdir()
    for stamp in ("20261025-010000", "20261026-010000", "20261026-130000"):
        (tmp_path / "manifests" / f"{stamp}.json").write_text(json.dumps({"stamp": stamp}), encoding="utf-8")
    assert backup.find_manifest(None, tmp_path)["stamp"] == "20261026-130000"
    assert backup.find_manifest("20261026", tmp_path)["stamp"] == "20261026-130000"
    assert backup.find_manifest("20261025-010000", tmp_path)["stamp"] == "20261025-010000"
    with pytest.raises(Invalid):
        backup.find_manifest("20261001", tmp_path)


# ---------------------------------------------------------------- 状态
def test_backup_status(session, monkeypatch, tmp_path):
    from app.config import get_settings

    monkeypatch.delenv("DOCBASE_BACKUP_DIR", raising=False)
    get_settings.cache_clear()
    try:
        assert system.backup_status(session)["configured"] is False
        monkeypatch.setenv("DOCBASE_BACKUP_DIR", str(tmp_path))
        get_settings.cache_clear()
        assert system.backup_status(session)["stale"] is True  # 从没成功过
        now = datetime.now().astimezone()
        (tmp_path / "status.json").write_text(json.dumps({"last_ok": now.isoformat()}), encoding="utf-8")
        assert system.backup_status(session)["ok"] is True
        (tmp_path / "status.json").write_text(json.dumps({"last_ok": (now - timedelta(days=3)).isoformat()}),
                                              encoding="utf-8")
        assert system.backup_status(session)["stale"] is True
        # 备份目录写不进去时，失败只记在任务里
        (tmp_path / "status.json").write_text(json.dumps({"last_ok": (now - timedelta(hours=30)).isoformat()}),
                                              encoding="utf-8")
        session.add(Job(kind=system.BACKUP_KIND, status=JobStatus.FAILED, error="磁盘满了\ntrace",
                        finished_at=now))
        session.flush()
        s = system.backup_status(session)
        assert s["ok"] is False and s["error"] == "磁盘满了"
    finally:
        get_settings.cache_clear()


def test_ensure_backup_once_a_day(session, monkeypatch, tmp_path):
    from app.config import get_settings

    monkeypatch.setenv("DOCBASE_BACKUP_DIR", str(tmp_path))
    monkeypatch.setenv("DOCBASE_BACKUP_HOUR", "1")
    get_settings.cache_clear()
    try:
        now = datetime.now().astimezone().replace(hour=0, minute=30)
        assert system.ensure_backup(session, now) is None  # 还没到点
        later = now.replace(hour=3)
        session.query(Job).filter(Job.kind == system.BACKUP_KIND).delete()
        assert system.ensure_backup(session, later) is not None
        assert system.ensure_backup(session, later) is None  # 今天排过了
    finally:
        get_settings.cache_clear()


def test_status_api_and_health(api_client, monkeypatch, tmp_path):
    system._cache.update(at=0.0, value=None)
    r = api_client.get("/api/system/status").json()  # 测试里匿名用户是系统管理员
    assert r["storage"]["ok"] is True and "backup" in r and r["version"]
    h = api_client.get("/api/health").json()
    assert h["storage"] is True and h["ok"] is True

    # 存储断开：状态和健康检查都报告
    blocker = tmp_path / "unplugged"
    blocker.write_text("not a folder")
    monkeypatch.setattr(system, "get_storage", lambda: LocalDiskStorage(blocker / "data"))
    system._cache.update(at=0.0, value=None)
    r = api_client.get("/api/system/status").json()
    assert r["storage"]["ok"] is False and "unplugged" in r["storage"]["error"]
    assert api_client.get("/api/health").json()["ok"] is False
    system._cache.update(at=0.0, value=None)


def test_storage_watch_logs_changes(monkeypatch, tmp_path, caplog):
    states = iter([{"ok": True, "error": None}, {"ok": False, "error": "gone"}, {"ok": False, "error": "gone"},
                   {"ok": True, "error": None}])
    monkeypatch.setattr(system, "storage_status", lambda max_age=0: next(states))
    watch = system.StorageWatch()
    with caplog.at_level(logging.INFO, logger="docbase.system"):
        for _ in range(4):
            watch.next_at = 0
            watch.tick()
    msgs = [(r.levelname, r.getMessage()) for r in caplog.records if r.name == "docbase.system"]
    assert [lvl for lvl, _ in msgs] == ["INFO", "ERROR", "INFO"]  # 只在变化时写


# ---------------------------------------------------------------- 日志
def test_log_cleanup_by_days_and_size(tmp_path):
    today = date(2026, 10, 26)
    for i in range(40):
        day = today - timedelta(days=i)
        (tmp_path / f"api-{day.isoformat()}.log").write_bytes(b"x" * 100)
    (tmp_path / "notes.txt").write_text("keep")
    logs.cleanup(tmp_path, keep_days=30, max_bytes=10**9, today=today)
    assert len(list(tmp_path.glob("api-*.log"))) == 30 and (tmp_path / "notes.txt").exists()
    logs.cleanup(tmp_path, keep_days=30, max_bytes=550, today=today)  # 超总大小：从最旧的删，今天的留着
    left = sorted(f.name for f in tmp_path.glob("api-*.log"))
    assert len(left) == 5 and left[-1] == "api-2026-10-26.log"


def test_daily_file_handler(tmp_path):
    h = logs.DailyFileHandler(tmp_path, "worker", keep_days=30, max_bytes=10**9)
    h.setFormatter(logging.Formatter(logs.FORMAT))
    rec = logging.LogRecord("docbase", logging.INFO, __file__, 1, "存储可用", None, None)
    h.emit(rec)
    rec2 = logging.LogRecord("docbase", logging.INFO, __file__, 1, "第二天", None, None)
    rec2.created = rec.created + 86400
    h.emit(rec2)
    h.close()
    files = sorted(tmp_path.glob("worker-*.log"))
    assert len(files) == 2 and "存储可用" in files[0].read_text(encoding="utf-8")


def test_collect_logs(tmp_path, monkeypatch, migrated_db):
    import sys

    from app.config import get_settings

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import collect_logs

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / f"api-{date.today().isoformat()}.log").write_text("hello", encoding="utf-8")
    (log_dir / "api-2020-01-01.log").write_text("old", encoding="utf-8")
    monkeypatch.setenv("DOCBASE_LOG_DIR", str(log_dir))
    get_settings.cache_clear()
    try:
        out = collect_logs.collect(7, tmp_path)
    finally:
        get_settings.cache_clear()
    import zipfile

    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        info = json.loads(z.read("info.json"))
    assert f"logs/api-{date.today().isoformat()}.log" in names and "logs/api-2020-01-01.log" not in names
    password = make_url(os.environ["DOCBASE_DATABASE_URL"]).password
    assert not password or f":{password}@" not in json.dumps(info)  # 连接串里的密码隐去（用户名可能和密码相同）
    assert "status" in info and "recent_jobs" in info
