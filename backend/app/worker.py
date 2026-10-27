"""后台 worker（数据库任务表 + 租约，不用 Celery / Redis）。

- 领任务：queued 的、或 running 但租约已过期的（上一个 worker 崩了）；SELECT … FOR UPDATE SKIP LOCKED，
  几个 worker 同时跑也不会领到同一个；领到后记下租约（默认 60 秒），干活时 heartbeat() 续租约。
- 任务处理函数自己记断点（如导入明细的结果），被打断后重新领到时从断点接着做。
- 暂停：页面把任务改成 paused；处理函数每做完一项调 heartbeat()，发现被暂停就停下（PauseRequested），
  继续时改回 queued。
- 每天 maintenance_hour 点做一次例行清理（回收区满期、没人引用的磁盘文件、过期暂存文件）。
- 配置了备份目录时，每天 backup_hour 点备份一次（services/backup.py）。
- 定时检查文件存储，断开 / 恢复时写日志。

用法：python scripts/worker.py（正式安装时注册成服务，见 deploy/）
"""

import logging
import os
import socket
import time
import traceback
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_engine
from app.models import Job, JobStatus

log = logging.getLogger("docbase.worker")
LEASE_SECONDS = 60
MAINTENANCE_KIND = "maintenance"


class PauseRequested(Exception):
    """任务被页面暂停（或取消）：处理函数停下，断点已记下。"""


class Context:
    """交给任务处理函数：续租约、检查暂停、报进度。"""

    def __init__(self, session: Session, job: Job, owner: str) -> None:
        self.session, self.job, self.owner = session, job, owner

    def heartbeat(self, progress: int | None = None, total: int | None = None) -> None:
        """续租约、写进度，并看看有没有被暂停 / 取消。调用前请先提交自己的数据。"""
        s = self.session
        s.refresh(self.job)
        if self.job.status in (JobStatus.PAUSED, JobStatus.CANCELLED):
            raise PauseRequested()
        if self.job.lease_owner != self.owner:
            raise PauseRequested()  # 租约被别的 worker 抢走了（我们太久没续）
        if progress is not None:
            self.job.progress = progress
        if total is not None:
            self.job.total = total
        self.job.lease_until = datetime.now(UTC) + timedelta(seconds=LEASE_SECONDS)
        s.commit()


Handler = Callable[[Context], dict | None]
HANDLERS: dict[str, Handler] = {}


def handler(kind: str) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn
    return register


def owner_name() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def claim(session: Session, owner: str, kinds: list[str] | None = None) -> Job | None:
    """领一个任务（先来先做）。"""
    now = datetime.now(UTC)
    stmt = (select(Job)
            .where(or_(Job.status == JobStatus.QUEUED,
                       (Job.status == JobStatus.RUNNING) & (Job.lease_until < now)))
            .where(Job.kind.in_(kinds or list(HANDLERS)))
            .order_by(Job.id).limit(1).with_for_update(skip_locked=True))
    job = session.scalar(stmt)
    if job is None:
        return None
    job.status, job.lease_owner = JobStatus.RUNNING, owner
    job.lease_until = now + timedelta(seconds=LEASE_SECONDS)
    job.attempts = (job.attempts or 0) + 1
    job.started_at = job.started_at or now
    job.error = None
    session.commit()
    return job


def run_job(session: Session, job: Job, owner: str) -> None:
    fn = HANDLERS[job.kind]
    ctx = Context(session, job, owner)
    try:
        result = fn(ctx)
    except PauseRequested:
        session.rollback()
        session.refresh(job)
        log.info("任务 %s（%s）已暂停", job.id, job.kind)
        return
    except Exception as exc:  # noqa: BLE001  任务失败：记下原因，不影响 worker
        session.rollback()
        session.refresh(job)
        job.status, job.error = JobStatus.FAILED, f"{exc}\n{traceback.format_exc()[-2000:]}"
        job.finished_at, job.lease_owner, job.lease_until = datetime.now(UTC), None, None
        session.commit()
        log.exception("任务 %s（%s）失败", job.id, job.kind)
        return
    session.refresh(job)
    if job.status is JobStatus.RUNNING:
        job.status = JobStatus.SUCCEEDED
        job.result = {**(job.result or {}), **(result or {})}
        job.finished_at, job.lease_owner, job.lease_until = datetime.now(UTC), None, None
        session.commit()


def run_once(session: Session, owner: str | None = None, kinds: list[str] | None = None) -> bool:
    """领一个任务做完；没有任务返回 False。测试和命令行都用它。"""
    owner = owner or owner_name()
    job = claim(session, owner, kinds)
    if job is None:
        return False
    run_job(session, job, owner)
    return True


def ensure_maintenance(session: Session, now: datetime | None = None) -> Job | None:
    """到了每天的清理时间、今天还没排过，就排一个例行清理任务。"""
    now = now or datetime.now().astimezone()
    if now.hour < get_settings().maintenance_hour:
        return None
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    exists = session.scalar(select(Job.id).where(Job.kind == MAINTENANCE_KIND, Job.created_at >= start)
                            .limit(1))
    if exists:
        return None
    job = Job(kind=MAINTENANCE_KIND, status=JobStatus.QUEUED)
    session.add(job)
    session.commit()
    return job


@handler("backup")
def backup_job(ctx: Context) -> dict:
    """每天的备份：数据库 + 新增的文件，清理超过保留天数的。"""
    from app.services import backup

    return backup.run_backup()


@handler(MAINTENANCE_KIND)
def maintenance(ctx: Context) -> dict:
    """例行清理：回收区满期的版本、没人引用的磁盘文件、过期暂存文件。"""
    from app.services import files as files_svc
    from app.services.history import Actor
    from app.services.storage import get_storage

    storage = get_storage()
    storage.check()
    purged = files_svc.purge_recycled(ctx.session, Actor(name="system"))
    ctx.session.commit()  # 先提交，再删磁盘文件
    deleted = files_svc.delete_unused_blobs(ctx.session, storage, purged["blobs"])
    orphans = files_svc.delete_orphan_blobs(ctx.session, storage)
    staging = storage.cleanup_staging() if hasattr(storage, "cleanup_staging") else 0
    from app.services import delivery, mirror

    packages = delivery.cleanup_exports()
    if mirror.mirror_root() is not None:  # 每天全部对一遍镜像，补上漏掉的（如镜像盘曾经断开）
        mirror.request_full_sync(ctx.session)
        ctx.session.commit()
    return {"purged_versions": purged["versions"], "blobs_deleted": deleted,
            "orphans_deleted": orphans, "staging_cleaned": staging, "packages_deleted": packages}


def main(poll_seconds: float = 2.0) -> None:  # pragma: no cover - 长驻进程
    import app.services.delivery  # noqa: F401  注册交付包任务
    import app.services.importer  # noqa: F401  注册导入任务
    from app import logs
    from app.services import mirror, system

    logs.setup("worker")
    watch = system.StorageWatch()
    owner = owner_name()
    log.info("worker 启动：%s，任务：%s", owner, ", ".join(sorted(HANDLERS)))
    while True:
        try:
            with Session(get_engine()) as session:
                watch.tick()
                ensure_maintenance(session)
                system.ensure_backup(session)
                mirror.ensure_job(session)
                busy = run_once(session, owner)
        except Exception:  # noqa: BLE001  数据库暂时连不上等：等一会再试
            log.exception("worker 出错，稍后重试")
            busy = False
        if not busy:
            time.sleep(poll_seconds)
