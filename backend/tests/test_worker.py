"""第 14A 步：后台 worker——领任务、租约过期后接着做、暂停 / 继续、失败记原因、每天的例行清理。"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app import worker
from app.models import Job, JobStatus

KIND = "test_steps"


@worker.handler(KIND)
def _steps(ctx: worker.Context) -> dict:
    """做 3 步，每步记断点；payload.pause_after 指定做完第几步时模拟页面点了暂停。"""
    job = ctx.job
    done = (job.result or {}).get("done", 0)
    for step in range(done + 1, 4):
        job.result = {**(job.result or {}), "done": step}
        if job.payload.get("pause_after") == step:
            job.status = JobStatus.PAUSED
        if job.payload.get("fail_at") == step:
            raise RuntimeError("boom")
        ctx.session.commit()
        ctx.heartbeat(progress=step, total=3)
    return {"finished": True}


def make(session, **payload) -> Job:
    job = Job(kind=KIND, status=JobStatus.QUEUED, payload=payload)
    session.add(job)
    session.commit()
    return job


def test_runs_queued_job(session):
    job = make(session)
    assert worker.run_once(session, "w1", [KIND])
    session.refresh(job)
    assert job.status is JobStatus.SUCCEEDED and job.result == {"done": 3, "finished": True}
    assert (job.progress, job.total, job.attempts) == (3, 3, 1)
    assert job.lease_owner is None and job.finished_at is not None
    assert not worker.run_once(session, "w1", [KIND])  # 没有任务了


def test_expired_lease_is_taken_over(session):
    job = make(session)
    job.status, job.lease_owner = JobStatus.RUNNING, "dead-worker"
    job.lease_until = datetime.now(UTC) + timedelta(seconds=30)
    job.result = {"done": 2}
    session.commit()
    assert not worker.run_once(session, "w2", [KIND])  # 租约还没过期，不抢
    job.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()
    assert worker.run_once(session, "w2", [KIND])
    session.refresh(job)
    assert job.status is JobStatus.SUCCEEDED and job.attempts == 1 and job.result["done"] == 3  # 从断点接着做


def test_pause_and_resume(session):
    job = make(session, pause_after=1)
    worker.run_once(session, "w1", [KIND])
    session.refresh(job)
    assert job.status is JobStatus.PAUSED and job.result == {"done": 1}
    assert not worker.run_once(session, "w1", [KIND])  # 暂停的不领
    job.status = JobStatus.QUEUED
    job.payload = {}
    session.commit()
    worker.run_once(session, "w1", [KIND])
    session.refresh(job)
    assert job.status is JobStatus.SUCCEEDED and job.result["done"] == 3


def test_failure_is_recorded(session):
    job = make(session, fail_at=2)
    worker.run_once(session, "w1", [KIND])
    session.refresh(job)
    assert job.status is JobStatus.FAILED and job.error.startswith("boom")
    assert job.result == {"done": 1}  # 第 1 步的断点已提交


def test_maintenance_scheduled_once_a_day(session, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "maintenance_hour", 2)
    early = datetime(2026, 10, 25, 1, 0).astimezone()
    assert worker.ensure_maintenance(session, early) is None
    today = datetime.now().astimezone().replace(hour=3)
    first = worker.ensure_maintenance(session, today)
    assert first is not None and worker.ensure_maintenance(session, today) is None
    assert worker.run_once(session, "w1", [worker.MAINTENANCE_KIND])
    session.refresh(first)
    assert first.status is JobStatus.SUCCEEDED
    assert set(first.result) == {"purged_versions", "blobs_deleted", "orphans_deleted", "staging_cleaned",
                                 "packages_deleted"}
    assert session.scalar(select(Job).where(Job.kind == worker.MAINTENANCE_KIND,
                                            Job.status == JobStatus.QUEUED)) is None
