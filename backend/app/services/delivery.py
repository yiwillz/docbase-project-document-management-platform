"""交付包（PRD F8.1）：按范围选出每份文件的最新版（或某个阶段基线里的版本），按目录结构放进 zip，
附文件清单；后台 worker 生成，做好后下载。可以同时"作为提交发给客户"：生成传送单，记下包里的版本。

目录结构（共享盘镜像也用同一套，见 mirror.py）：
- 按设备（默认）：项目编号/设备种类/设备/文件名；不对应设备的放 项目编号/通用文件/里程碑/种类/（按模块的再加模块）；
  客户文件放 项目编号/客户文件/里程碑/种类/；一个文件挂几台设备的，每台设备的文件夹里各放一份。
- 按里程碑：项目编号/里程碑/种类/文件名。
- 待归类（只有镜像里有）：项目编号/待归类/原文件名。
包里的路径控制在 200 个字符以内（解压到一般目录下也不超过 Windows 的 260）：太长先截文件夹名，再截文件名。
"""

import io
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal
from zipfile import ZIP_STORED, ZipFile

from openpyxl import Workbook
from openpyxl.styles import Font
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Job, JobStatus, TransmittalSource, Version
from app.services import naming
from app.services.browse import Snapshot
from app.services.deliverable_query import Item
from app.services.errors import Conflict, Invalid, NotFound
from app.services.history import Actor
from app.services.projects import get_project, get_writable_project
from app.services.storage import get_storage
from app.worker import Context, handler

Layout = Literal["equipment", "milestone"]
PACKAGE_KIND = "package"
MAX_PATH = 200
LABELS = {
    "zh": {"general": "通用文件", "client": "客户文件", "unclassified": "待归类", "untyped": "未分类设备",
           "manifest": "文件清单.xlsx"},
    "en": {"general": "General", "client": "Client documents", "unclassified": "Unclassified",
           "untyped": "Untyped equipment", "manifest": "file_list.xlsx"},
}
MANIFEST_HEAD = {
    "zh": ("包内路径", "文件名", "原文件名", "版本", "文件日期", "大小（字节）", "SHA-256", "来源", "应交项", "状态"),
    "en": ("Path in package", "File name", "Original name", "Version", "File date", "Size (bytes)", "SHA-256",
           "Source", "Deliverable", "Status"),
}


@dataclass
class Scope:
    milestone_ids: list[int] = field(default_factory=list)
    document_type_ids: list[int] = field(default_factory=list)
    equipment_ids: list[int] = field(default_factory=list)

    def match(self, it: Item) -> bool:
        return ((not self.milestone_ids or it.milestone_id in self.milestone_ids)
                and (not self.document_type_ids or it.document_type_id in self.document_type_ids)
                and (not self.equipment_ids or it.equipment_id in self.equipment_ids))

    def out(self) -> dict[str, list[int]]:
        return {"milestone_ids": self.milestone_ids, "document_type_ids": self.document_type_ids,
                "equipment_ids": self.equipment_ids}


@dataclass
class Entry:
    path: str  # 包里的路径（/ 分隔，不含项目编号那一层以外的东西）
    version_id: int
    sha256: str
    size: int | None
    file_id: int
    filename: str
    original_name: str
    version_no: int
    file_date: date | None
    source: str
    deliverable_ids: list[int]


def _folder(text: str) -> str:
    return naming.sanitize(text) or "_"


def _shorten(parts: list[str], limit: int = MAX_PATH) -> list[str]:
    """路径太长：先把文件夹名截到 40 个字符，还长就截文件名（保留扩展名）。"""
    if len("/".join(parts)) <= limit:
        return parts
    parts = [p[:40].rstrip(" .") or "_" for p in parts[:-1]] + [parts[-1]]
    over = len("/".join(parts)) - limit
    if over > 0:
        stem, ext = naming.split_ext(parts[-1])
        parts[-1] = stem[: max(len(stem) - over, 8)].rstrip(" .") + ext
    return parts


def entries(session: Session, project_id: int, *, layout: Layout = "equipment", lang: str = "zh",
            scope: Scope | None = None, include_client: bool = False, include_unclassified: bool = False,
            versions: dict[int, Version] | None = None, snapshot: Snapshot | None = None) -> list[Entry]:
    """选出要放的文件和它们在包里的路径。versions：文件 id → 版本（按基线导出时给）；不给就用当前版本。"""
    snap = snapshot or Snapshot(session, project_id)
    labels = LABELS.get(lang, LABELS["zh"])
    scope = scope or Scope()
    root = _folder(snap.project.code)
    sizes: dict[str, int] = {}
    if versions:
        from app.models import Blob

        sizes = dict(session.execute(select(Blob.sha256, Blob.size).where(
            Blob.sha256.in_({v.blob_sha256 for v in versions.values()}))).all())
    out: list[Entry] = []
    for fid, row in snap.files.items():
        if versions is not None:
            v = versions.get(fid)
            size = sizes.get(v.blob_sha256) if v else None
        else:
            v, size = row.latest, row.size
        if v is None or v.purged_at is not None:
            continue
        source = row.file.source.value
        if source == "client" and not include_client:
            continue
        name = snap.name(row, v)
        items = [snap.items[d] for d in row.deliverable_ids if d in snap.items]
        placements: list[list[str]] = []
        if not items:
            if include_unclassified and not (scope.milestone_ids or scope.document_type_ids or scope.equipment_ids):
                placements.append([labels["unclassified"]])
        for it in items:
            if it.obsolete or not scope.match(it):
                continue
            ms = _folder(f"{it.milestone_code} {it.milestone_name or ''}".strip())
            doc = _folder(f"{it.submittal_no} {it.document_type_name}")
            if layout == "milestone":
                placements.append([ms, doc])
            elif source == "client":
                placements.append([labels["client"], ms, doc])
            elif it.kind == "per_equipment":
                etype = _folder(it.equipment_type_name or labels["untyped"])
                equipment = _folder(" ".join(x for x in (it.equipment_tag, it.equipment_name) if x))
                placements.append([etype, equipment])
            elif it.kind == "per_module":
                placements.append([labels["general"], ms, doc, _folder(it.module_name or "")])
            else:
                placements.append([labels["general"], ms, doc])
        seen = set()
        for folders in placements:
            parts = _shorten([root, *folders, name])
            path = "/".join(parts)
            if path in seen:
                continue  # 同一个文件在同一个文件夹里只放一份（如按里程碑时挂了几台设备）
            seen.add(path)
            out.append(Entry(path=path, version_id=v.id, sha256=v.blob_sha256, size=size, file_id=fid,
                             filename=name, original_name=v.original_name, version_no=v.version_no,
                             file_date=v.file_date, source=source, deliverable_ids=row.deliverable_ids))
    # 同一路径、内容不同的（不同文件算出了同一个名字）：后面的加 " (2)"
    used: dict[str, str] = {}
    for e in sorted(out, key=lambda e: (e.path, e.file_id)):
        path, n = e.path, 1
        while path in used and used[path] != e.sha256:
            n += 1
            stem, ext = naming.split_ext(e.path)
            path = f"{stem} ({n}){ext}"
        used[path] = e.sha256
        e.path = path
    out.sort(key=lambda e: e.path.casefold())
    return out


def manifest(snap: Snapshot, items: list[Entry], lang: str) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "files"
    ws.append(MANIFEST_HEAD.get(lang, MANIFEST_HEAD["zh"]))
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for e in items:
        locs = []
        for did in e.deliverable_ids:
            it = snap.items.get(did)
            if it:
                locs.append(" / ".join(x for x in (f"{it.submittal_no} {it.document_type_name}", snap.where(it)) if x))
        status = ", ".join(sorted({snap.items[d].status for d in e.deliverable_ids if d in snap.items}))
        ws.append([e.path, e.filename, e.original_name, f"V{e.version_no}", e.file_date, e.size, e.sha256,
                   e.source, "; ".join(locs), status])
    for col, width in zip("ABCDEFGHIJ", (70, 50, 40, 8, 11, 12, 20, 8, 40, 14), strict=True):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# ---------------------------------------------------------------- 生成


def exports_dir() -> Path:
    path = Path(get_settings().data_dir) / "exports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def estimate(session: Session, project_id: int, **kw) -> dict[str, Any]:
    """生成前先看看：多少个文件、多大、涉及多少应交项。"""
    items = entries(session, project_id, **kw)
    return {"files": len(items), "size": sum(e.size or 0 for e in items),
            "deliverables": len({d for e in items for d in e.deliverable_ids})}


def baseline_versions(session: Session, project_id: int, baseline_id: int) -> dict[int, Version]:
    from app.models import Baseline, BaselineItem

    b = session.get(Baseline, baseline_id)
    if b is None or b.project_id != project_id:
        raise NotFound("baseline.not_found", f"基线 {baseline_id} 不存在", id=baseline_id)
    rows = session.scalars(select(Version).join(BaselineItem, BaselineItem.version_id == Version.id)
                           .where(BaselineItem.baseline_id == baseline_id))
    return {v.file_id: v for v in rows}


def start_package(session: Session, project_id: int, actor: Actor, *, layout: Layout = "equipment",
                  lang: str = "zh", scope: Scope | None = None, include_client: bool = False,
                  baseline_id: int | None = None, submit: dict[str, Any] | None = None) -> Job:
    """排一个生成交付包的任务；选了"作为提交"就同时生成传送单，记下包里每个应交项的版本。"""
    project = get_writable_project(session, project_id) if submit else get_project(session, project_id)
    versions = baseline_versions(session, project_id, baseline_id) if baseline_id else None
    snap = Snapshot(session, project_id)
    items = entries(session, project_id, layout=layout, lang=lang, scope=scope, include_client=include_client,
                    versions=versions, snapshot=snap)
    if not items:
        raise Invalid("package.empty", "这个范围里没有文件")
    transmittal_no = None
    if submit is not None:
        from app.services.transmittals import create_transmittal

        by_deliverable: dict[int, set[int]] = defaultdict(set)
        for e in items:
            if e.source == "client":
                continue
            for did in e.deliverable_ids:
                if did in snap.items and (scope or Scope()).match(snap.items[did]) \
                        and not snap.items[did].obsolete:
                    by_deliverable[did].add(e.version_id)
        if not by_deliverable:
            raise Invalid("package.nothing_to_submit", "包里没有可以提交的文件（客户文件不算）")
        t = create_transmittal(session, project_id, sorted(by_deliverable), submit, actor,
                               source=TransmittalSource.PLATFORM,
                               versions={d: sorted(v) for d, v in by_deliverable.items()})
        transmittal_no = t.number
    job = Job(kind=PACKAGE_KIND, project_id=project_id, status=JobStatus.QUEUED, created_by=actor.id,
              total=len(items),
              payload={"layout": layout, "lang": lang, "include_client": include_client,
                       "scope": (scope or Scope()).out(), "baseline_id": baseline_id,
                       "transmittal_no": transmittal_no, "created_by": actor.name, "project_code": project.code,
                       "entries": [[e.path, e.version_id, e.sha256] for e in items],
                       "size": sum(e.size or 0 for e in items)})
    session.add(job)
    session.flush()
    return job


@handler(PACKAGE_KIND)
def build_package(ctx: Context) -> dict:
    """写 zip：先写 .part，全部写完再改名；不压缩（PDF、DWG 压不了多少，这样更快），超过 4 GB 也行。
    暂停后继续时从头重写。"""
    s, job = ctx.session, ctx.job
    storage = get_storage()
    storage.check()
    p = job.payload
    target = exports_dir() / f"{job.token}.zip"
    part = target.with_suffix(".part")
    snap = Snapshot(s, job.project_id)
    rows = p["entries"]
    with ZipFile(part, "w", compression=ZIP_STORED, allowZip64=True) as zf:
        for n, (path, _vid, sha) in enumerate(rows, 1):
            src = storage.path(sha)
            if src is None or not src.is_file():
                raise Invalid("package.blob_missing", f"文件实体不存在：{path}")
            zf.write(src, path)
            if n % 20 == 0:
                ctx.heartbeat(progress=n, total=len(rows))
        # 文件清单：用包里的条目重新算一遍信息
        version_ids = {vid for _, vid, _ in rows}
        versions = {v.id: v for v in s.scalars(select(Version).where(Version.id.in_(version_ids)))}
        listed = []
        for path, vid, sha in rows:
            v = versions.get(vid)
            row = snap.files.get(v.file_id) if v else None
            if not v or not row:
                continue
            listed.append(Entry(path=path, version_id=vid, sha256=sha, size=None, file_id=v.file_id,
                                filename=path.rsplit("/", 1)[-1], original_name=v.original_name,
                                version_no=v.version_no, file_date=v.file_date, source=row.file.source.value,
                                deliverable_ids=row.deliverable_ids))
        sizes = {e.path: os.path.getsize(storage.path(e.sha256)) for e in listed}
        for e in listed:
            e.size = sizes[e.path]
        lang = p.get("lang", "zh")
        zf.writestr(f"{naming.sanitize(p['project_code'])}/{LABELS.get(lang, LABELS['zh'])['manifest']}",
                    manifest(snap, listed, lang))
    os.replace(part, target)
    ctx.heartbeat(progress=len(rows), total=len(rows))
    return {"file": target.name, "size": target.stat().st_size, "files": len(rows)}


def _download_name(job: Job) -> str:
    p = job.payload
    day = (job.finished_at or job.created_at).astimezone().strftime("%Y%m%d")
    tag = f"baseline{p['baseline_id']}" if p.get("baseline_id") else "package"
    return naming.sanitize(f"{p['project_code']}_{tag}_{day}_{job.id}.zip")


def package_out(job: Job) -> dict[str, Any]:
    p = job.payload or {}
    r = job.result or {}
    path = exports_dir() / f"{job.token}.zip"
    return {"id": job.id, "status": job.status.value, "progress": job.progress, "total": job.total,
            "error": (job.error or "").split("\n")[0] or None, "created_at": job.created_at,
            "created_by": p.get("created_by"), "layout": p.get("layout"), "lang": p.get("lang"),
            "include_client": p.get("include_client"), "scope": p.get("scope"),
            "baseline_id": p.get("baseline_id"), "transmittal_no": p.get("transmittal_no"),
            "files": len(p.get("entries") or []), "size": r.get("size") or p.get("size"),
            "available": job.status is JobStatus.SUCCEEDED and path.is_file(),
            "download_name": _download_name(job)}


def list_packages(session: Session, project_id: int, limit: int = 50) -> list[dict[str, Any]]:
    get_project(session, project_id)
    jobs = session.scalars(select(Job).where(Job.project_id == project_id, Job.kind == PACKAGE_KIND)
                           .order_by(Job.id.desc()).limit(limit))
    return [package_out(j) for j in jobs]


def package_file(session: Session, project_id: int, job_id: int) -> tuple[Path, str]:
    job = session.get(Job, job_id)
    if job is None or job.project_id != project_id or job.kind != PACKAGE_KIND:
        raise NotFound("package.not_found", f"交付包 {job_id} 不存在", id=job_id)
    path = exports_dir() / f"{job.token}.zip"
    if job.status is not JobStatus.SUCCEEDED:
        raise Conflict("package.not_ready", "交付包还没做好")
    if not path.is_file():
        raise Conflict("package.expired", "交付包已过期删除，请重新生成")
    return path, _download_name(job)


def cleanup_exports(days: int | None = None) -> int:
    """例行清理：删除超过 package_days 天的交付包和没写完的 .part。"""
    import time

    days = days if days is not None else get_settings().package_days
    cutoff = time.time() - days * 86400
    removed = 0
    for p in exports_dir().glob("*"):
        try:
            if p.is_file() and (p.stat().st_mtime < cutoff or (p.suffix == ".part" and p.stat().st_mtime < time.time() - 86400)):
                p.unlink()
                removed += 1
        except OSError:
            pass
    return removed
