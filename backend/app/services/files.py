"""存文件（PRD F4 方式一）：上传 → 预览 → 确认；升版、去重、版本保留、回收区清理。

- 按设备的应交项：这一项（或这几台设备）已有文件就升版；
- 整个种类 / 按模块：标题相同才升版，标题不同是新文件（同一项下用标题区分）；
- 同一项目里已有内容完全相同的文件（未清理的版本）时拦截，告诉已有位置；
- 供应商 / 我方文件只留最新 N 版（默认 3），更早的进回收区；客户文件全部保留；
  被阶段基线或传送单引用的版本不进回收区；
- 回收区满 N 天（默认 90）后删除磁盘文件，版本记录保留并标记已清理。

上传不改应交项状态：状态仍由传送单和客户回复推出。
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    BaselineItem,
    Blob,
    ChangeAction,
    Deliverable,
    DocumentType,
    Equipment,
    File,
    FileDeliverable,
    FileSource,
    Job,
    JobStatus,
    ReplySource,
    Transmittal,
    TransmittalItem,
    TransmittalItemVersion,
    Version,
)
from app.services import naming
from app.services.crud import get_in_project
from app.services.errors import Conflict, Invalid, NotFound
from app.services.history import Actor, emit, record, record_summary, snapshot
from app.services.projects import get_project, get_writable_project
from app.services.storage import Storage

JOB_KIND = "file_upload"
MAX_UPLOAD_BYTES = 2 * 1024**3  # 单文件上限 2 GB（PRD 第 5 节）
DATE_SOURCES = ("filename", "modified", "upload", "manual")


# ---------------------------------------------------------------- 位置


@dataclass
class Target:
    """上传位置：同一文件种类下的一个或几个应交项（几台设备 → 一个文件挂在几台上）。"""

    doc: DocumentType
    deliverables: list[Deliverable]
    equipment: list[Equipment | None]

    @property
    def ids(self) -> list[int]:
        return [d.id for d in self.deliverables]


def load_target(session: Session, project_id: int, deliverable_ids: list[int]) -> Target:
    ids = list(dict.fromkeys(deliverable_ids))
    if not ids:
        raise Invalid("file.no_target", "请指定上传到哪个应交项")
    deliverables = [get_in_project(session, Deliverable, i, project_id, "deliverable") for i in ids]
    for d in deliverables:
        if d.obsolete:
            raise Conflict("deliverable.obsolete", f"应交项 {d.id} 已作废，不能上传", id=d.id)
    doc_ids = {d.document_type_id for d in deliverables}
    if len(doc_ids) > 1:
        raise Invalid("file.mixed_types", "一个文件只能挂在同一文件种类下的应交项上")
    doc = session.get(DocumentType, doc_ids.pop())
    if len(deliverables) > 1 and not doc.per_equipment:
        raise Invalid("file.multi_not_equipment", "只有按设备的文件种类才能一个文件挂几台设备")
    return _target(session, doc, deliverables)


def _target(session: Session, doc: DocumentType, deliverables: list[Deliverable]) -> Target:
    """按设备编号排好（72&73），没编号的在后。"""
    equipment = [session.get(Equipment, d.equipment_id) if d.equipment_id else None
                 for d in deliverables]
    order = sorted(range(len(deliverables)), key=lambda i: naming_sort(
        equipment[i].tag if equipment[i] else None))
    return Target(doc=doc, deliverables=[deliverables[i] for i in order],
                  equipment=[equipment[i] for i in order])


def naming_sort(tag: str | None) -> tuple:
    """设备编号排序：数字按数值，其余按文本。"""
    if tag is None:
        return (2, "")
    return (0, int(tag), "") if tag.isdigit() else (1, 0, tag)


def files_of(session: Session, deliverable_ids: list[int]) -> dict[int, list[int]]:
    """挂在这些应交项上的文件 → 它挂着的全部应交项 id。"""
    file_ids = session.scalars(select(FileDeliverable.file_id).where(
        FileDeliverable.deliverable_id.in_(deliverable_ids)).distinct()).all()
    out: dict[int, list[int]] = {f: [] for f in file_ids}
    for fid, did in session.execute(select(FileDeliverable.file_id, FileDeliverable.deliverable_id)
                                    .where(FileDeliverable.file_id.in_(file_ids))):
        out[fid].append(did)
    return out


def default_source(target: Target) -> FileSource:
    """设备有供应商时默认供应商，否则我方。"""
    return FileSource.VENDOR if any(e is not None and e.vendor for e in target.equipment) \
        else FileSource.OURS


# ---------------------------------------------------------------- 文件日期与文件名


def file_date_for(original_name: str, modified: date | None, today: date | None = None
                  ) -> tuple[date, str]:
    """文件名中的日期 → 文件最后修改时间 → 上传日。"""
    found = naming.parse_date(original_name)
    if found:
        return found, "filename"
    if modified:
        return modified, "modified"
    return today or date.today(), "upload"


def name_context(target: Target, *, original_name: str, version: int, file_date: date,
                 title: str | None, source: FileSource, with_title: bool = False) -> naming.NameContext:
    return naming.NameContext(
        no=target.doc.submittal_no, type_name=target.doc.name, per_equipment=target.doc.per_equipment,
        original_name=original_name, version=version, file_date=file_date,
        tags=[e.tag if e else None for e in target.equipment],
        equipment_names=[e.name for e in target.equipment if e],
        title=title, keep_original=source is FileSource.CLIENT, with_title=with_title)


def ext_of(name: str | None) -> str:
    return naming.split_ext(name or "")[1].casefold()


def file_exts(session: Session, file_ids) -> dict[int, str]:
    """每个文件的格式（最新版本原文件名的扩展名，小写）。"""
    ids = list(file_ids)
    if not ids:
        return {}
    rows = session.execute(select(Version.file_id, Version.original_name)
                           .join(File, File.id == Version.file_id)
                           .where(Version.file_id.in_(ids), Version.version_no == File.current_version_no))
    return {fid: ext_of(name) for fid, name in rows}


def same_document(f: File, links: list[int], ext: str, *, target_ids: list[int], title: str | None,
                  new_ext: str, per_equipment: bool) -> bool:
    """是不是"同一份文件"（再传就是升版）：标题相同、格式相同；按设备的还要挂着同样几台设备
    （用户 2026-10-25 确认：一台设备下可以有好几份不同的文件）。"""
    if per_equipment and sorted(links) != sorted(target_ids):
        return False
    return ext == new_ext and naming.title_key(f.title) == naming.title_key(title)


def sibling_count(session: Session, deliverable_ids: list[int], exclude: int | None = None) -> int:
    """挂着同样几项的非客户文件有几份（按设备的有好几份时，文件名里加标题）。"""
    existing = files_of(session, deliverable_ids)
    ids = [fid for fid, links in existing.items()
           if sorted(links) == sorted(deliverable_ids) and fid != exclude]
    if not ids:
        return 0
    return session.scalar(select(func.count()).select_from(File).where(
        File.id.in_(ids), File.source != FileSource.CLIENT)) or 0


def current_name(session: Session, version: Version, file: File | None = None) -> str:
    """按当前的结构名称算出的文件名（设备或种类改名后自动变）；待归类的用原名（可配置前缀）。"""
    file = file or session.get(File, version.file_id)
    project = get_project(session, file.project_id)
    ids = session.scalars(select(FileDeliverable.deliverable_id)
                          .where(FileDeliverable.file_id == file.id)).all()
    if not ids:
        return naming.unclassified_name(version.original_name, project.naming_templates)
    target = load_target_any(session, file.project_id, list(ids))
    with_title = target.doc.per_equipment and sibling_count(session, list(ids), exclude=file.id) > 0
    ctx = name_context(target, original_name=version.original_name, version=version.version_no,
                       file_date=version.file_date or version.uploaded_at.date(),
                       title=file.title, source=file.source, with_title=with_title)
    return naming.build_name(ctx, project.naming_templates)


def load_target_any(session: Session, project_id: int, deliverable_ids: list[int]) -> Target:
    """同 load_target，但不拦作废的应交项（算历史文件的名字用）。"""
    deliverables = [session.get(Deliverable, i) for i in deliverable_ids]
    return _target(session, session.get(DocumentType, deliverables[0].document_type_id), deliverables)


# ---------------------------------------------------------------- 预览


def find_duplicate(session: Session, project_id: int, sha256: str) -> dict[str, Any] | None:
    """项目里内容完全相同的版本（未清理的），返回位置。"""
    row = session.execute(
        select(Version, File).join(File, File.id == Version.file_id)
        .where(File.project_id == project_id, Version.blob_sha256 == sha256,
               Version.purged_at.is_(None))
        .order_by(Version.id.desc()).limit(1)).first()
    if row is None:
        return None
    version, file = row
    return {"file_id": file.id, "version_id": version.id, "version_no": version.version_no,
            "filename": current_name(session, version, file),
            "recycled": version.recycled_at is not None,
            "deliverable_ids": list(session.scalars(select(FileDeliverable.deliverable_id)
                                                   .where(FileDeliverable.file_id == file.id)))}


def plan(session: Session, project_id: int, deliverable_ids: list[int], *, original_name: str,
         sha256: str, modified: date | None = None, overrides: dict[str, Any] | None = None,
         today: date | None = None) -> dict[str, Any]:
    """算出这次上传会怎样：新文件还是升版、来源、标题、日期、文件名、是否重复。不写数据库。

    overrides 可改：deliverable_ids（换位置；空列表 = 待归类）、source、title、file_date、
    file_id（"new" 表示作为新文件，或指定要升版的文件）。
    """
    o = overrides or {}
    project = get_project(session, project_id)
    ids = o["deliverable_ids"] if o.get("deliverable_ids") is not None else deliverable_ids
    target = load_target(session, project_id, ids) if ids else None  # None = 待归类
    existing = files_of(session, target.ids) if target else {}
    files = {f.id: f for f in session.scalars(select(File).where(File.id.in_(existing)))} \
        if existing else {}
    title = (o.get("title") or "").strip() or naming.extract_title(
        original_name, no=target.doc.submittal_no if target else None,
        type_name=target.doc.name if target else None,
        equipment_names=[e.name for e in target.equipment if e] if target else None)
    new_ext = ext_of(original_name)
    exts = file_exts(session, files)

    # 候选：挂在这些应交项上的文件（选"作为哪个文件的新版本"用）
    candidates = [{"file_id": f.id, "title": f.title, "source": f.source.value,
                   "current_version": f.current_version_no,
                   "deliverable_ids": sorted(existing[f.id])}
                  for f in sorted(files.values(), key=lambda f: f.id)]

    # 默认：按设备 → 挂着同样几台设备的文件；其余 → 同标题的文件。
    # 明确选了来源时只找同来源的（客户退回的带章文件不会变成供应商文件的新版本）
    file: File | None = None
    if o.get("file_id") not in (None, "", "new"):
        file = files.get(int(o["file_id"]))
        if file is None:
            raise Invalid("file.not_candidate", "只能升版挂在这个应交项上的文件", file_id=o["file_id"])
    elif o.get("file_id") != "new" and target:
        pool = [f for f in files.values() if not o.get("source") or f.source.value == o["source"]]
        per_eq = target.doc.per_equipment
        same = [f for f in pool if same_document(f, existing[f.id], exts.get(f.id, ""), target_ids=target.ids,
                                                 title=title, new_ext=new_ext, per_equipment=per_eq)]
        if not same and not title:
            # 平台命名的文件再传回来（名字里没有标题，如 "3.8.13_P&IDs-Tool A-20260302-V2.pdf"）：
            # 这一项同格式的只有一份时就是它
            same_ext = [f for f in pool if exts.get(f.id) == new_ext
                        and (not per_eq or sorted(existing[f.id]) == sorted(target.ids))]
            same = same_ext if len(same_ext) == 1 else []
        file = max(same, key=lambda f: f.id) if same else None

    if file is not None:
        source = file.source
        if o.get("source") and o["source"] != source.value:
            raise Invalid("file.source_fixed", "升版时来源跟原文件一致，不能改", source=source.value)
        if not (o.get("title") or "").strip() and file.title:
            title = file.title  # 升版沿用原标题（文件名不跳来跳去）
        version_no = file.current_version_no + 1
    else:
        try:
            source = FileSource(o["source"]) if o.get("source") else (
                default_source(target) if target else FileSource.OURS)
        except ValueError as exc:
            raise Invalid("file.bad_source", f"来源不对：{o.get('source')}") from exc
        version_no = 1

    if o.get("file_date"):
        file_date = o["file_date"] if isinstance(o["file_date"], date) \
            else date.fromisoformat(str(o["file_date"]))
        date_source = "manual"
    else:
        file_date, date_source = file_date_for(original_name, modified, today)

    if target:
        with_title = target.doc.per_equipment and sibling_count(
            session, target.ids, exclude=file.id if file else None) > 0
        ctx = name_context(target, original_name=original_name, version=version_no,
                           file_date=file_date, title=title, source=source, with_title=with_title)
        filename = naming.build_name(ctx, project.naming_templates)
    else:
        filename = naming.unclassified_name(original_name, project.naming_templates)
    duplicate = find_duplicate(session, project_id, sha256)
    return {
        "deliverable_ids": target.ids if target else [],
        "unclassified": target is None,
        "document_type_id": target.doc.id if target else None,
        "per_equipment": target.doc.per_equipment if target else False,
        "mode": "version" if file else "new", "file_id": file.id if file else None,
        "version_no": version_no, "source": source.value, "title": title,
        "file_date": file_date, "date_source": date_source, "filename": filename,
        "original_name": original_name, "original_version": naming.parse_version(original_name),
        "duplicate": duplicate, "candidates": candidates, "ok": duplicate is None,
    }


def clean_name(filename: str) -> str:
    original = (filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not original:
        raise Invalid("file.no_name", "文件没有名字")
    return original


def preview(session: Session, project_id: int, storage: Storage, stream, *, filename: str,
            deliverable_ids: list[int], modified: date | None, actor: Actor) -> dict[str, Any]:
    """收进暂存区并预览（一次传完的小文件）；返回 token，确认时带回来。"""
    get_writable_project(session, project_id)
    load_target(session, project_id, deliverable_ids)  # 位置不对先报错，不白传
    original = clean_name(filename)
    staging_id, sha256, size = storage.stage(stream, max_bytes=MAX_UPLOAD_BYTES)
    return preview_staged(session, project_id, staging_id=staging_id, sha256=sha256, size=size,
                          storage=storage, original_name=original, deliverable_ids=deliverable_ids,
                          modified=modified, actor=actor)


def preview_staged(session: Session, project_id: int, *, staging_id: str, sha256: str, size: int,
                   storage: Storage, original_name: str, deliverable_ids: list[int],
                   modified: date | None, actor: Actor, recognition: dict[str, Any] | None = None,
                   overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """已在暂存区的文件（一次传完或分块传完）→ 预览，存一个令牌。"""
    if size == 0:
        storage.discard(staging_id)
        raise Invalid("file.empty", "文件是空的")
    result = plan(session, project_id, deliverable_ids, original_name=original_name, sha256=sha256,
                  modified=modified, overrides=overrides)
    job = Job(kind=JOB_KIND, project_id=project_id, status=JobStatus.SUCCEEDED,
              payload={"staging_id": staging_id, "sha256": sha256, "size": size,
                       "original_name": original_name, "deliverable_ids": result["deliverable_ids"],
                       "modified": modified.isoformat() if modified else None,
                       "recognition": recognition,
                       # 识别得出的默认值（如客户退回 → 来源客户），改预览和确认时都带上
                       "overrides": overrides or {}},
              result={"stage": "previewed"}, created_by=actor.id, finished_at=datetime.now(UTC))
    session.add(job)
    session.flush()
    out = {"token": job.token, "size": size, "sha256": sha256, **result}
    if recognition is not None:
        out["recognition"] = recognition
    return out


def _job(session: Session, project_id: int, token) -> Job:
    job = session.scalar(select(Job).where(Job.token == token, Job.kind == JOB_KIND))
    if job is None or job.project_id != project_id:
        raise NotFound("file_upload.not_found", "找不到这次上传，请重新上传")
    if (job.result or {}).get("stage") == "stored":
        raise Conflict("file_upload.already_stored", "这个文件已经存过了")
    return job


def _plan_job(session: Session, project_id: int, job: Job, overrides: dict[str, Any]
              ) -> dict[str, Any]:
    p = job.payload
    modified = date.fromisoformat(p["modified"]) if p.get("modified") else None
    merged = {**(p.get("overrides") or {}), **overrides}
    return plan(session, project_id, p["deliverable_ids"], original_name=p["original_name"],
                sha256=p["sha256"], modified=modified, overrides=merged)


def replan(session: Session, project_id: int, token, overrides: dict[str, Any]) -> dict[str, Any]:
    """改了位置 / 来源 / 标题 / 日期 / 升版对象后重新预览（不用重新上传）。"""
    job = _job(session, project_id, token)
    result = _plan_job(session, project_id, job, overrides)
    out = {"token": job.token, "size": job.payload["size"], "sha256": job.payload["sha256"], **result}
    if job.payload.get("recognition") is not None:
        out["recognition"] = job.payload["recognition"]
    return out


# ---------------------------------------------------------------- 确认入库


def confirm(session: Session, project_id: int, storage: Storage, token,
            overrides: dict[str, Any], actor: Actor) -> dict[str, Any]:
    """确认存入：文件落盘 → 文件 / 版本记录、变更记录、事件 → 版本保留 →（选了的话）记客户回复。
    由调用方提交。"""
    get_writable_project(session, project_id)
    job = _job(session, project_id, token)
    p = job.payload
    o = {k: v for k, v in overrides.items() if k != "record_reply"}
    result = _plan_job(session, project_id, job, o)
    if result["duplicate"]:
        raise Conflict("file.duplicate", "项目里已有内容完全相同的文件", **result["duplicate"])
    stored = store_staged(session, project_id, storage, staging_id=p["staging_id"],
                          sha256=p["sha256"], size=p["size"], original_name=p["original_name"],
                          result=result, actor=actor)
    version = session.get(Version, stored["version_id"])
    reply = None
    if overrides.get("record_reply"):
        reply = _record_reply(session, project_id, job, result, version, actor)
    job.result = {"stage": "stored", "file_id": stored["file_id"], "version_id": version.id}
    return {**stored, "reply": reply}


def store_staged(session: Session, project_id: int, storage: Storage, *, staging_id: str,
                 sha256: str, size: int, original_name: str, result: dict[str, Any], actor: Actor
                 ) -> dict[str, Any]:
    """按算好的预览（plan 的结果）把暂存文件存进来：文件落盘 → 文件 / 版本记录、变更记录、事件 →
    版本保留。上传确认和存量导入共用。由调用方提交。"""
    if not storage.staged_exists(staging_id):
        raise Invalid("storage.staging_missing", "暂存文件不存在（可能已超过 24 小时），请重新上传")
    storage.commit(staging_id, sha256)

    if session.get(Blob, sha256) is None:
        session.add(Blob(sha256=sha256, size=size))
    if result["file_id"]:
        file = session.get(File, result["file_id"])
        before = snapshot(file)
        file.current_version_no = result["version_no"]
        record(session, file, ChangeAction.UPDATE, before, snapshot(file), actor)
    else:
        file = File(project_id=project_id, source=FileSource(result["source"]),
                    title=result["title"], current_version_no=1)
        session.add(file)
        session.flush()
        for did in result["deliverable_ids"]:
            session.add(FileDeliverable(file_id=file.id, deliverable_id=did))
        record(session, file, ChangeAction.CREATE, None,
               {**snapshot(file), "deliverable_ids": result["deliverable_ids"]}, actor)
    version = Version(file_id=file.id, version_no=result["version_no"], blob_sha256=sha256,
                      filename=result["filename"], original_name=original_name,
                      file_date=result["file_date"], date_source=result["date_source"],
                      uploaded_by=actor.id, uploaded_by_name=actor.name)
    session.add(version)
    session.flush()
    record(session, version, ChangeAction.CREATE, None, snapshot(version), actor,
           project_id=project_id)
    emit(session, "version.created", version, {
        "file_id": file.id, "version_no": version.version_no, "sha256": sha256,
        "filename": version.filename, "deliverable_ids": result["deliverable_ids"]},
        project_id=project_id)
    recycled = apply_retention(session, project_id, file, actor)
    return {"file_id": file.id, "version_id": version.id, "version_no": version.version_no,
            "filename": version.filename, "mode": result["mode"], "recycled": recycled,
            "unclassified": result["unclassified"]}


def _record_reply(session: Session, project_id: int, job: Job, result: dict[str, Any],
                  version: Version, actor: Actor) -> dict[str, Any]:
    """客户退回的带章文件：按识别出的审核代码，给传送单里对应的项记回复（来源：退回文件）。"""
    from app.services.transmittals import set_reply  # 避免循环引用

    hint = (job.payload.get("recognition") or {}).get("client_reply") or None
    if not hint or not hint.get("transmittal_id") or not hint.get("item_ids"):
        raise Invalid("file.no_client_reply", "没有识别出客户回复（审核代码和传送单），不能记回复")
    items = session.scalars(select(TransmittalItem).where(
        TransmittalItem.id.in_(hint["item_ids"]),
        TransmittalItem.transmittal_id == hint["transmittal_id"])).all()
    item_ids = [i.id for i in items if i.deliverable_id in result["deliverable_ids"]]
    if not item_ids:
        raise Invalid("file.reply_not_matching", "退回文件的位置和传送单里的项对不上，不能记回复")
    counts = set_reply(session, project_id, hint["transmittal_id"],
                       {"reply_date": version.file_date, "reply_ref": version.original_name},
                       actor, items=[{"item_id": i, "code": hint["code"]} for i in item_ids],
                       source=ReplySource.FILE, reason=f"客户退回文件 {version.original_name}")
    return {"transmittal_id": hint["transmittal_id"], "transmittal_no": hint.get("transmittal_no"),
            "code": hint["code"], **counts}


# ---------------------------------------------------------------- 版本保留与回收区


def protected_version_ids(session: Session, version_ids: list[int]) -> set[int]:
    """被阶段基线或（未作废的）传送单引用的版本：不进回收区、不清理。"""
    if not version_ids:
        return set()
    in_baseline = session.scalars(select(BaselineItem.version_id)
                                  .where(BaselineItem.version_id.in_(version_ids)))
    in_transmittal = session.scalars(
        select(TransmittalItem.version_id).join(Transmittal, Transmittal.id == TransmittalItem.transmittal_id)
        .where(TransmittalItem.version_id.in_(version_ids), Transmittal.void.is_(False)))
    in_item_versions = session.scalars(
        select(TransmittalItemVersion.version_id)
        .join(TransmittalItem, TransmittalItem.id == TransmittalItemVersion.item_id)
        .join(Transmittal, Transmittal.id == TransmittalItem.transmittal_id)
        .where(TransmittalItemVersion.version_id.in_(version_ids), Transmittal.void.is_(False)))
    return set(in_baseline) | set(in_transmittal) | set(in_item_versions)


def apply_retention(session: Session, project_id: int, file: File, actor: Actor,
                    keep: int | None = None) -> list[int]:
    """供应商 / 我方文件只留最新 keep 版，更早的进回收区；客户文件不动。返回进回收区的版本号。"""
    if file.source is FileSource.CLIENT:
        return []
    keep = keep or get_settings().keep_versions
    versions = session.scalars(select(Version).where(Version.file_id == file.id)
                               .order_by(Version.version_no.desc())).all()
    older = [v for v in versions[keep:] if v.recycled_at is None]
    protected = protected_version_ids(session, [v.id for v in older])
    now, moved = datetime.now(UTC), []
    for v in older:
        if v.id in protected:
            continue
        before = snapshot(v)
        v.recycled_at = now
        record(session, v, ChangeAction.UPDATE, before, snapshot(v), actor, project_id=project_id,
               reason=f"只保留最新 {keep} 版，V{v.version_no} 进回收区")
        moved.append(v.version_no)
    return moved


def purge_recycled(session: Session, actor: Actor, *, now: datetime | None = None,
                   days: int | None = None) -> dict[str, Any]:
    """回收区满期的版本标记为已清理（记录保留），返回要删的磁盘文件。

    先提交数据库，再调 delete_unused_blobs 删磁盘文件：提交失败时文件还在，不会有记录指向已删的文件。
    """
    now = now or datetime.now(UTC)
    days = days if days is not None else get_settings().recycle_days
    due = session.execute(
        select(Version, File.project_id).join(File, File.id == Version.file_id)
        .where(Version.recycled_at.is_not(None), Version.purged_at.is_(None),
               Version.recycled_at <= now - timedelta(days=days))).all()
    protected = protected_version_ids(session, [v.id for v, _ in due])
    per_project: dict[int, int] = {}
    shas: set[str] = set()
    for v, project_id in due:
        if v.id in protected:
            continue
        v.purged_at = now
        shas.add(v.blob_sha256)
        per_project[project_id] = per_project.get(project_id, 0) + 1
    for project_id, n in per_project.items():
        record_summary(session, project_id=project_id, entity_type="versions", entity_id="purge",
                       action=ChangeAction.UPDATE, changes={"purged": [0, n]}, actor=actor,
                       reason=f"回收区满 {days} 天，清理磁盘文件")
    session.flush()
    return {"versions": sum(per_project.values()), "blobs": sorted(shas)}


def delete_unused_blobs(session: Session, storage: Storage, shas: list[str]) -> int:
    """删掉没有未清理版本再用的磁盘文件（同内容可能被别的版本用着），返回删了几个。"""
    deleted = 0
    for sha in shas:
        still_used = session.scalar(select(func.count()).select_from(Version).where(
            Version.blob_sha256 == sha, Version.purged_at.is_(None)))
        if not still_used:
            storage.delete(sha)
            deleted += 1
    return deleted


# ---------------------------------------------------------------- 查看与下载


def version_out(session: Session, v: Version, file: File, protected: set[int]) -> dict[str, Any]:
    blob = session.get(Blob, v.blob_sha256)
    return {"id": v.id, "version_no": v.version_no, "filename": current_name(session, v, file),
            "stored_name": v.filename, "original_name": v.original_name, "file_date": v.file_date,
            "date_source": v.date_source, "size": blob.size if blob else None,
            "uploaded_by": v.uploaded_by_name, "uploaded_at": v.uploaded_at,
            "recycled_at": v.recycled_at, "purged_at": v.purged_at,
            "protected": v.id in protected}


def list_files(session: Session, project_id: int, deliverable_id: int) -> list[dict[str, Any]]:
    """挂在这个应交项上的文件，每个带全部版本（新的在前）。"""
    get_in_project(session, Deliverable, deliverable_id, project_id, "deliverable")
    existing = files_of(session, [deliverable_id])
    out = []
    for file in session.scalars(select(File).where(File.id.in_(existing)).order_by(File.id)):
        versions = session.scalars(select(Version).where(Version.file_id == file.id)
                                   .order_by(Version.version_no.desc())).all()
        protected = protected_version_ids(session, [v.id for v in versions])
        out.append({"id": file.id, "title": file.title, "source": file.source.value,
                    "current_version": file.current_version_no,
                    "deliverable_ids": sorted(existing[file.id]),
                    "versions": [version_out(session, v, file, protected) for v in versions]})
    return out


def get_version(session: Session, project_id: int, version_id: int) -> tuple[Version, File]:
    v = session.get(Version, version_id)
    file = session.get(File, v.file_id) if v else None
    if v is None or file is None or file.project_id != project_id:
        raise NotFound("version.not_found", f"版本 {version_id} 不存在", id=version_id)
    if v.purged_at is not None:
        raise Conflict("version.purged", "这个版本在回收区已满期清理，文件已删除", id=version_id)
    return v, file


def current_version_ids(session: Session, deliverable_ids: list[int]) -> dict[int, list[int]]:
    """每个应交项上每份（非客户）文件的当前版本（传送单记"交的是哪几版"用）。"""
    out: dict[int, list[int]] = {}
    if not deliverable_ids:
        return out
    rows = session.execute(
        select(FileDeliverable.deliverable_id, Version.id)
        .join(File, File.id == FileDeliverable.file_id)
        .join(Version, (Version.file_id == File.id) & (Version.version_no == File.current_version_no))
        .where(FileDeliverable.deliverable_id.in_(deliverable_ids), File.source != FileSource.CLIENT)
        .order_by(Version.id))
    for did, vid in rows:
        out.setdefault(did, []).append(vid)
    return out


def latest_version_ids(session: Session, deliverable_ids: list[int]) -> dict[int, int]:
    """每个应交项最新上传的版本（传送单记"交的是哪一版"用）。"""
    out: dict[int, int] = {}
    rows = session.execute(
        select(FileDeliverable.deliverable_id, Version.id)
        .join(Version, Version.file_id == FileDeliverable.file_id)
        .where(FileDeliverable.deliverable_id.in_(deliverable_ids), Version.recycled_at.is_(None))
        .order_by(Version.id))
    for did, vid in rows:
        out[did] = vid  # 按 id 升序，最后一个就是最新的
    return out


def version_counts(session: Session, project_id: int) -> dict[int, tuple[int, int]]:
    """应交项 → (文件数, 最新版本号)，应交矩阵用。"""
    rows = session.execute(
        select(FileDeliverable.deliverable_id, func.count(File.id), func.max(File.current_version_no))
        .join(File, File.id == FileDeliverable.file_id)
        .where(File.project_id == project_id)
        .group_by(FileDeliverable.deliverable_id))
    return {did: (n, latest) for did, n, latest in rows}


# ---------------------------------------------------------------- 智能投递（方式二）


def drop_preview(session: Session, project_id: int, storage: Storage, *, staging_id: str,
                 sha256: str, size: int, original_name: str, modified: date | None,
                 description: str | None, folder: str | None, vendor: str | None, actor: Actor,
                 recognizer=None) -> dict[str, Any]:
    """投递：识别位置 → 排第一的候选置信度不低时按它预览，否则预览成待归类。"""
    from app.services import recognizer as rec

    get_writable_project(session, project_id)
    engine = recognizer or rec.RuleRecognizer(rec.ProjectIndex(session, project_id))
    found = engine.recognize(rec.RecognizeInput(filename=original_name, description=description,
                                                folder=folder, vendor=vendor))
    best = found.best
    ids = best.deliverable_ids if best and best.level != "low" else []
    # 识别出客户退回 → 来源客户（另存，不当成供应商文件的新版本）；猜的"供应商"只用于待归类的文件，
    # 有位置的按设备默认来源走，不挡升版
    overrides = {"source": found.source} if found.source == "client" or (found.source and not ids)         else {}
    return preview_staged(session, project_id, staging_id=staging_id, sha256=sha256, size=size,
                          storage=storage, original_name=original_name, deliverable_ids=ids,
                          modified=modified, actor=actor, recognition=found.out(),
                          overrides=overrides)


# ---------------------------------------------------------------- 待归类


def list_unclassified(session: Session, project_id: int) -> list[dict[str, Any]]:
    """没有挂到任何应交项上的文件（新的在前）。"""
    get_project(session, project_id)
    linked = select(FileDeliverable.file_id)
    out = []
    for file in session.scalars(select(File).where(File.project_id == project_id,
                                                   File.id.not_in(linked)).order_by(File.id.desc())):
        versions = session.scalars(select(Version).where(Version.file_id == file.id)
                                   .order_by(Version.version_no.desc())).all()
        protected = protected_version_ids(session, [v.id for v in versions])
        out.append({"id": file.id, "title": file.title, "source": file.source.value,
                    "current_version": file.current_version_no, "deliverable_ids": [],
                    "versions": [version_out(session, v, file, protected) for v in versions]})
    return out


def unclassified_counts(session: Session) -> dict[int, int]:
    """每个项目的待归类文件数（项目列表用）。"""
    linked = select(FileDeliverable.file_id)
    rows = session.execute(select(File.project_id, func.count(File.id))
                           .where(File.id.not_in(linked)).group_by(File.project_id))
    return dict(rows.all())


def classify(session: Session, project_id: int, file_id: int, deliverable_ids: list[int],
             actor: Actor, *, reason: str | None = None) -> dict[str, Any]:
    """把待归类的文件挂到应交项上（文件名随之按规则显示）。

    目标已有同样的文件（按设备：挂着同样几台设备的；其余：同标题的）时拒绝，请改用上传为新版本。
    """
    get_writable_project(session, project_id)
    file = session.get(File, file_id)
    if file is None or file.project_id != project_id:
        raise NotFound("file.not_found", f"文件 {file_id} 不存在", id=file_id)
    if session.scalar(select(func.count()).select_from(FileDeliverable)
                      .where(FileDeliverable.file_id == file.id)):
        raise Conflict("file.already_classified", "这个文件已经归类了", id=file_id)
    target = load_target(session, project_id, deliverable_ids)
    existing = files_of(session, target.ids)
    others = session.scalars(select(File).where(File.id.in_(existing))).all() if existing else []
    exts = file_exts(session, [*existing, file.id])
    clash = [f for f in others if f.source == file.source and same_document(
        f, existing[f.id], exts.get(f.id, ""), target_ids=target.ids, title=file.title,
        new_ext=exts.get(file.id, ""), per_equipment=target.doc.per_equipment)]
    if clash:
        raise Conflict("file.target_has_file", "目标位置已有这个文件，请改用\"上传为新版本\"",
                       file_id=clash[0].id)
    for did in target.ids:
        session.add(FileDeliverable(file_id=file.id, deliverable_id=did))
    session.flush()
    record(session, file, ChangeAction.UPDATE, {"deliverable_ids": []},
           {"deliverable_ids": target.ids}, actor, reason=reason)
    emit(session, "file.classified", file, {"deliverable_ids": target.ids})
    latest = session.scalar(select(Version).where(Version.file_id == file.id)
                            .order_by(Version.version_no.desc()).limit(1))
    return {"file_id": file.id, "deliverable_ids": target.ids,
            "filename": current_name(session, latest, file) if latest else None}


# ---------------------------------------------------------------- 分块上传（断点续传）

CHUNKED_KIND = "chunked_upload"


def begin_upload(session: Session, project_id: int, storage: Storage, *, filename: str,
                 size: int, actor: Actor) -> dict[str, Any]:
    """开始一次分块上传：返回上传编号；之后按已收到的字节数一块一块传。"""
    get_writable_project(session, project_id)
    original = clean_name(filename)
    if size <= 0:
        raise Invalid("file.empty", "文件是空的")
    if size > MAX_UPLOAD_BYTES:
        raise Invalid("storage.too_large", f"文件超过 {MAX_UPLOAD_BYTES} 字节", max_bytes=MAX_UPLOAD_BYTES)
    staging_id = storage.begin()
    job = Job(kind=CHUNKED_KIND, project_id=project_id, status=JobStatus.RUNNING,
              payload={"staging_id": staging_id, "filename": original, "size": size},
              total=size, created_by=actor.id, started_at=datetime.now(UTC))
    session.add(job)
    session.flush()
    return {"upload_id": job.token, "received": 0, "size": size}


def _upload_job(session: Session, project_id: int, upload_id) -> Job:
    job = session.scalar(select(Job).where(Job.token == upload_id, Job.kind == CHUNKED_KIND))
    if job is None or job.project_id != project_id:
        raise NotFound("upload.not_found", "找不到这次上传，请重新上传")
    if job.status is not JobStatus.RUNNING:
        raise Conflict("upload.finished", "这次上传已经完成，不能再传")
    return job


def upload_status(session: Session, project_id: int, storage: Storage, upload_id) -> dict[str, Any]:
    job = _upload_job(session, project_id, upload_id)
    received = storage.staged_size(job.payload["staging_id"])
    return {"upload_id": job.token, "received": received, "size": job.payload["size"],
            "filename": job.payload["filename"]}


def upload_chunk(session: Session, project_id: int, storage: Storage, upload_id, offset: int,
                 stream) -> dict[str, Any]:
    job = _upload_job(session, project_id, upload_id)
    received = storage.append(job.payload["staging_id"], offset, stream,
                              max_bytes=job.payload["size"])
    job.progress = received
    return {"upload_id": job.token, "received": received, "size": job.payload["size"]}


def finish_upload(session: Session, project_id: int, storage: Storage, upload_id
                  ) -> tuple[str, str, int, str]:
    """分块传完：核对大小、算哈希，返回 (暂存编号, sha256, 字节数, 原文件名)。"""
    job = _upload_job(session, project_id, upload_id)
    p = job.payload
    received = storage.staged_size(p["staging_id"])
    if received != p["size"]:
        raise Invalid("upload.incomplete", f"还没传完：已收到 {received} / {p['size']} 字节",
                      received=received, size=p["size"])
    sha256 = storage.finish(p["staging_id"])
    job.status, job.finished_at = JobStatus.SUCCEEDED, datetime.now(UTC)
    return p["staging_id"], sha256, received, p["filename"]


# ---------------------------------------------------------------- 闭环检查用


def versions_after_approval(session: Session, project_id: int, approved: dict[int, Any]
                            ) -> set[int]:
    """批准之后又传了新版本的应交项（PRD F7.7）。approved：应交项 id → 批准它的传送单明细。

    有"交的是哪一版"记录的，和那一版比；没有的（补记的传送单），比上传日期和回复日期。
    客户来源的文件（如退回的带章文件）不算。
    """
    if not approved:
        return set()
    rows = session.execute(
        select(FileDeliverable.deliverable_id, Version.id, Version.uploaded_at)
        .join(File, File.id == FileDeliverable.file_id)
        .join(Version, Version.file_id == File.id)
        .where(File.project_id == project_id, File.source != FileSource.CLIENT,
               FileDeliverable.deliverable_id.in_(approved)))
    # 交出去的全部版本（一项可能有好几份文件）：比交出去的最新那一版还新的就算
    submitted: dict[int, set[int]] = {}
    item_ids = {it.id: did for did, it in approved.items()}
    for item_id, vid in session.execute(
            select(TransmittalItemVersion.item_id, TransmittalItemVersion.version_id)
            .where(TransmittalItemVersion.item_id.in_(item_ids))):
        submitted.setdefault(item_ids[item_id], set()).add(vid)
    out = set()
    for did, vid, uploaded_at in rows:
        item = approved[did]
        known = submitted.get(did) or ({item.version_id} if item.version_id else set())
        if known:
            newer = vid > max(known)
        else:
            newer = item.reply_date is not None and uploaded_at.date() > item.reply_date
        if newer:
            out.add(did)
    return out


def delete_orphan_blobs(session: Session, storage: Storage, *, min_age_hours: float = 24) -> int:
    """例行清理：没有任何版本引用的文件实体（如整批撤销后）；磁盘上有、数据库里没有的文件
    （存文件后数据库没提交成功留下的，放了一天以上的才删）。返回删了几个。"""
    import time

    deleted = 0
    used = select(Version.blob_sha256)
    for blob in session.scalars(select(Blob).where(Blob.sha256.not_in(used))):
        storage.delete(blob.sha256)
        session.delete(blob)
        deleted += 1
    session.commit()
    root = getattr(storage, "blobs", None)
    if root is None or not root.is_dir():
        return deleted
    known = set(session.scalars(select(Blob.sha256)))
    cutoff = time.time() - min_age_hours * 3600
    for path in root.glob("*/*/*"):
        try:
            if path.is_file() and path.name not in known and path.stat().st_mtime < cutoff:
                from app.services.storage import unlink

                unlink(path)
                deleted += 1
        except OSError:
            pass
    return deleted
