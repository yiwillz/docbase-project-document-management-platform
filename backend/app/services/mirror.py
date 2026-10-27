"""共享盘只读镜像（PRD F8.2）：每个项目一个文件夹，放每份文件的最新版，目录结构和交付包"按设备"的一样
（客户文件放 客户文件/里程碑/种类，待归类的放 待归类）。

- 有变化（事件）时 worker 在 1 分钟内同步：按当前结构算出"应该有哪些文件"，和上次放进去的比，
  新增、删除、改名（= 删旧加新）都照这个做；
- 只动平台自己放进去的文件：每个项目的清单记在 镜像目录/.docbase-mirror/<项目 id>.json，
  别人放进镜像目录的文件不碰；
- 同一块盘用硬链接（不占空间），不同盘或盘不支持时自动改复制；DOCBASE_MIRROR_MODE=copy 一律复制；
- 放进去的文件设成只读。用硬链接时共享必须由 IT 设成只读，否则有人改了镜像里的文件，平台存的那一版也跟着变
  （用户 2026-10-26 确认）。
"""

import json
import os
import shutil
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Event, EventCursor, Job, JobStatus, Project
from app.services import delivery
from app.services.storage import get_storage
from app.worker import Context, handler

CONSUMER = "mirror"
MIRROR_KIND = "mirror"


def mirror_root() -> Path | None:
    d = get_settings().mirror_dir
    return Path(d) if d else None


def _manifest_path(root: Path, project_id: int) -> Path:
    return root / ".docbase-mirror" / f"{project_id}.json"


def _load_manifest(root: Path, project_id: int) -> dict[str, str]:
    p = _manifest_path(root, project_id)
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("files", {})
    except (OSError, ValueError):
        return {}


def _save_manifest(root: Path, project_id: int, files: dict[str, str]) -> None:
    p = _manifest_path(root, project_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"files": files}, ensure_ascii=False, indent=0), encoding="utf-8")
    os.replace(tmp, p)


def _remove(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        path.unlink()
    except FileNotFoundError:
        pass


def _place(src: Path, dst: Path, mode: str) -> str:
    """硬链接（不行就复制）或复制，设成只读。返回 linked / copied。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        _remove(dst)
    how = "copied"
    if mode == "link":
        try:
            os.link(src, dst)
            how = "linked"
        except OSError:
            shutil.copyfile(src, dst)
    else:
        shutil.copyfile(src, dst)
    # 硬链接和平台存储是同一份数据，只读属性也是共享的：平台本来就不改文件，删除前会先去掉只读
    os.chmod(dst, stat.S_IREAD)
    return how


def _prune_dirs(root: Path, paths: list[str]) -> None:
    """删掉文件后留下的空文件夹（只往上找到镜像目录为止）。"""
    for rel in sorted(paths, key=lambda p: -p.count("/")):
        d = (root / rel).parent
        while d != root and d.is_dir():
            try:
                d.rmdir()  # 不空会失败，正好停下
            except OSError:
                break
            d = d.parent


def sync_project(session: Session, project: Project, root: Path | None = None,
                 mode: str | None = None) -> dict[str, int]:
    """把一个项目的镜像同步成当前的样子。"""
    root = root or mirror_root()
    if root is None:
        return {}
    settings = get_settings()
    mode = mode or settings.mirror_mode
    storage = get_storage()
    root.mkdir(parents=True, exist_ok=True)
    wanted: dict[str, str] = {}
    if project.active:
        for e in delivery.entries(session, project.id, layout="equipment", lang=settings.mirror_lang,
                                  include_client=True, include_unclassified=True):
            wanted[e.path] = e.sha256
    old = _load_manifest(root, project.id)
    counts = {"added": 0, "removed": 0, "kept": 0, "linked": 0, "copied": 0}
    removed = []
    for rel, sha in old.items():
        if wanted.get(rel) != sha:
            _remove(root / rel)
            removed.append(rel)
            counts["removed"] += 1
    done: dict[str, str] = {}
    for rel, sha in wanted.items():
        dst = root / rel
        if old.get(rel) == sha and dst.is_file():
            done[rel] = sha
            counts["kept"] += 1
            continue
        src = storage.path(sha)
        if src is None or not src.is_file():
            continue  # 文件实体不在（存储盘出问题）：下次再试
        counts[_place(src, dst, mode)] += 1
        counts["added"] += 1
        done[rel] = sha
    _save_manifest(root, project.id, done)
    _prune_dirs(root, removed)
    return counts


def _cursor(session: Session) -> EventCursor:
    c = session.get(EventCursor, CONSUMER)
    if c is None:
        c = EventCursor(consumer=CONSUMER, last_event_id=0)
        session.add(c)
        session.flush()
    return c


def pending(session: Session) -> int:
    c = session.get(EventCursor, CONSUMER)
    last = c.last_event_id if c else 0
    return session.scalar(select(func.count()).select_from(Event).where(Event.id > last)) or 0


def ensure_job(session: Session) -> Job | None:
    """worker 每一轮调用：配置了镜像目录、有新事件、还没有排着的同步任务，就排一个。"""
    if mirror_root() is None:
        return None
    c = session.get(EventCursor, CONSUMER)
    last = c.last_event_id if c else 0
    newest = session.scalar(select(func.max(Event.id))) or 0
    if newest <= last:
        return None
    busy = session.scalar(select(Job.id).where(Job.kind == MIRROR_KIND,
                                               Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING])).limit(1))
    if busy:
        return None
    job = Job(kind=MIRROR_KIND, status=JobStatus.QUEUED, payload={})
    session.add(job)
    session.commit()
    return job


@handler(MIRROR_KIND)
def mirror_job(ctx: Context) -> dict:
    """处理到最新的事件：有变化的项目各同步一次（payload.full = 全部项目都同步，例行清理时用）。"""
    s = ctx.session
    root = mirror_root()
    if root is None:
        return {"skipped": "no mirror_dir"}
    get_storage().check()
    cursor = _cursor(s)
    newest = s.scalar(select(func.max(Event.id))) or 0
    if ctx.job.payload.get("full"):
        project_ids = set(s.scalars(select(Project.id)))
    else:
        project_ids = {pid for pid in s.scalars(select(Event.project_id).where(
            Event.id > cursor.last_event_id, Event.id <= newest).distinct()) if pid}
    totals: dict[str, int] = {}
    for n, pid in enumerate(sorted(project_ids), 1):
        project = s.get(Project, pid)
        if project is None:
            continue
        for k, v in sync_project(s, project, root).items():
            totals[k] = totals.get(k, 0) + v
        ctx.heartbeat(progress=n, total=len(project_ids))
    cursor = _cursor(s)
    cursor.last_event_id = max(cursor.last_event_id, newest)
    cursor.updated_at = datetime.now(UTC)
    s.commit()
    return {"projects": len(project_ids), **totals}


def status(session: Session) -> dict[str, Any]:
    settings = get_settings()
    root = mirror_root()
    c = session.get(EventCursor, CONSUMER)
    last = session.scalar(select(Job).where(Job.kind == MIRROR_KIND).order_by(Job.id.desc()).limit(1))
    return {"enabled": root is not None, "dir": str(root) if root else None, "mode": settings.mirror_mode,
            "last_sync": c.updated_at if c else None, "pending_events": pending(session) if root else 0,
            "last_job": {"status": last.status.value, "finished_at": last.finished_at,
                         "result": last.result, "error": (last.error or "").split("\n")[0] or None}
            if last else None}


def request_full_sync(session: Session) -> Job:
    if mirror_root() is None:
        from app.services.errors import Invalid

        raise Invalid("mirror.disabled", "没有配置镜像目录（DOCBASE_MIRROR_DIR）")
    job = Job(kind=MIRROR_KIND, status=JobStatus.QUEUED, payload={"full": True})
    session.add(job)
    session.flush()
    return job
