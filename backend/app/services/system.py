"""系统状态：文件存储能不能用、备份是否正常（页面顶部提醒、健康检查、worker 记日志用）。"""

import logging
import time
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import app_version, get_settings
from app.models import Job, JobStatus
from app.services import backup
from app.services.errors import ServiceError
from app.services.storage import get_storage

log = logging.getLogger("docbase.system")
BACKUP_KIND = "backup"
STALE_HOURS = 48  # 超过 2 天没有成功备份就提醒

_cache: dict[str, Any] = {"at": 0.0, "value": None}


def storage_status(max_age: float = 15.0) -> dict[str, Any]:
    """检查文件存储能不能写（写一个探测文件）；结果缓存几秒，很多人同时打开页面也不会频繁探测。"""
    now = time.monotonic()
    if _cache["value"] is not None and now - _cache["at"] < max_age:
        return _cache["value"]
    try:
        get_storage().check()
        value = {"ok": True, "error": None}
    except ServiceError as exc:
        value = {"ok": False, "error": exc.message}
    _cache.update(at=now, value=value)
    return value


def backup_status(session: Session) -> dict[str, Any]:
    """备份是否正常：没配置 / 最近成功时间 / 最近的错误 / 是否太久没成功。"""
    settings = get_settings()
    if not settings.backup_dir:
        return {"configured": False, "ok": False, "last_ok": None, "error": None, "stale": False}
    status: dict[str, Any] = {}
    error = None
    try:
        status = backup.read_status(settings)
    except OSError as exc:
        error = f"备份目录不可用：{exc}"
    if not settings.backup_dir.exists():
        error = f"备份目录不存在或不可用：{settings.backup_dir}"
    error = error or status.get("last_error")
    # 备份目录本身写不进去时，结果只在任务里
    job = session.scalar(select(Job).where(Job.kind == BACKUP_KIND).order_by(Job.id.desc()).limit(1))
    last_ok = status.get("last_ok")
    if job is not None and job.status is JobStatus.FAILED and job.finished_at is not None:
        if not last_ok or job.finished_at > datetime.fromisoformat(last_ok):
            error = (job.error or "").split("\n")[0][:500] or "备份失败"
    stale = not last_ok or datetime.now().astimezone() - datetime.fromisoformat(last_ok) > timedelta(hours=STALE_HOURS)
    return {"configured": True, "ok": not error and not stale, "last_ok": last_ok, "error": error, "stale": stale}


def status(session: Session) -> dict[str, Any]:
    return {"version": app_version(), "storage": storage_status(), "backup": backup_status(session)}


# ---------------------------------------------------------------- worker 用
def ensure_backup(session: Session, now: datetime | None = None) -> Job | None:
    """配置了备份目录、到了每天的备份时间、今天还没排过，就排一个备份任务。"""
    settings = get_settings()
    if not settings.backup_dir:
        return None
    now = now or datetime.now().astimezone()
    if now.hour < settings.backup_hour:
        return None
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if session.scalar(select(Job.id).where(Job.kind == BACKUP_KIND, Job.created_at >= start).limit(1)):
        return None
    job = Job(kind=BACKUP_KIND, status=JobStatus.QUEUED)
    session.add(job)
    session.commit()
    return job


class StorageWatch:
    """worker 定时看一眼存储，状态变了就写日志（断开写 ERROR，恢复写 INFO）。"""

    def __init__(self) -> None:
        self.ok: bool | None = None
        self.next_at = 0.0

    def tick(self) -> None:
        now = time.monotonic()
        if now < self.next_at:
            return
        self.next_at = now + get_settings().status_check_seconds
        s = storage_status(max_age=0)
        if s["ok"] != self.ok:
            if s["ok"]:
                log.info("文件存储可用：%s", get_settings().data_dir)
            else:
                log.error("文件存储不可用，上传和导入会被拒绝：%s", s["error"])
        self.ok = s["ok"]
