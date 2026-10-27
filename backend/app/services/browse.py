"""文件浏览与检索（PRD F6）：三种视图的目录树、文件列表（筛选、关键字、分页、排序）、文件详情、
重新归类、回收区列表、在线预览的文件类型。

一个文件挂几台设备（72&73）时，两台设备下都能看到它；存储只有一份。
树按应交项统计文件数再往上汇总：先一次读出全部应交项和"文件 → 应交项"关联，不逐个文件查。
文件名按当前结构在内存里算（不逐个文件查数据库），和下载时的名字一致。
"""

import mimetypes
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Blob, ChangeAction, ChangeLog, File, FileDeliverable, Version
from app.services import files as files_svc
from app.services import naming
from app.services.change_labels import labels as change_labels
from app.services.deliverable_query import Filters, Item, load_items
from app.services.errors import Conflict, Invalid, NotFound
from app.services.history import Actor, emit, record
from app.services.projects import get_project, get_writable_project

View = Literal["milestone", "equipment", "module"]
UNCLASSIFIED, RECYCLE = "unclassified", "recycle"
# 能在浏览器里直接看的：PDF、图片、纯文本
PREVIEW_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp",
                 "image/svg+xml", "image/bmp", "text/plain"}


def media_type(filename: str) -> str:
    guessed, _ = mimetypes.guess_type(filename.replace("\\", "/").rsplit("/", 1)[-1])
    ext = naming.split_ext(filename)[1].lower()
    if ext in (".txt", ".log", ".csv", ".md"):
        return "text/plain"
    return guessed or "application/octet-stream"


def can_preview(filename: str) -> bool:
    return media_type(filename) in PREVIEW_TYPES


# ---------------------------------------------------------------- 一次读出的项目数据


@dataclass
class FileRow:
    file: File
    latest: Version | None
    size: int | None
    deliverable_ids: list[int]


class Snapshot:
    """浏览要用的项目数据：应交项（含作废的）、文件、最新版本、关联。"""

    def __init__(self, session: Session, project_id: int) -> None:
        self.project = get_project(session, project_id)
        self.items: dict[int, Item] = {it.id: it for it in load_items(session, project_id,
                                                                       include_obsolete=True)}
        files = {f.id: f for f in session.scalars(select(File).where(File.project_id == project_id))}
        links: dict[int, list[int]] = defaultdict(list)
        for fid, did in session.execute(select(FileDeliverable.file_id, FileDeliverable.deliverable_id)
                                        .join(File, File.id == FileDeliverable.file_id)
                                        .where(File.project_id == project_id)):
            links[fid].append(did)
        latest: dict[int, tuple[Version, int | None]] = {}
        for v, size in session.execute(
                select(Version, Blob.size).join(File, File.id == Version.file_id)
                .join(Blob, Blob.sha256 == Version.blob_sha256)
                .where(File.project_id == project_id, Version.version_no == File.current_version_no)):
            latest[v.file_id] = (v, size)
        self.files: dict[int, FileRow] = {}
        for fid, f in files.items():
            v, size = latest.get(fid, (None, None))
            self.files[fid] = FileRow(f, v, size, sorted(links.get(fid, [])))
        self.by_deliverable: dict[int, set[int]] = defaultdict(set)
        # 挂着同样几项的非客户文件有几份（按设备的有好几份时，文件名里加标题）
        self.same_set: dict[tuple[int, ...], int] = defaultdict(int)
        for fid, row in self.files.items():
            for did in row.deliverable_ids:
                self.by_deliverable[did].add(fid)
            if row.deliverable_ids and row.file.source.value != "client":
                self.same_set[tuple(row.deliverable_ids)] += 1

    # ------------------------------------------------------------ 文件名（和 files.current_name 一样的规则）
    def name(self, row: FileRow, version: Version | None = None) -> str:
        v = version or row.latest
        if v is None:
            return row.file.title or f"#{row.file.id}"
        items = [self.items[d] for d in row.deliverable_ids if d in self.items]
        if not items:
            return naming.unclassified_name(v.original_name, self.project.naming_templates)
        items.sort(key=lambda it: files_svc.naming_sort(it.equipment_tag))
        first = items[0]
        ctx = naming.NameContext(
            no=first.submittal_no, type_name=first.document_type_name,
            per_equipment=first.kind == "per_equipment", original_name=v.original_name,
            version=v.version_no, file_date=v.file_date or v.uploaded_at.date(),
            tags=[it.equipment_tag for it in items if it.kind == "per_equipment"],
            equipment_names=[it.equipment_name for it in items if it.equipment_name],
            title=row.file.title, keep_original=row.file.source.value == "client",
            with_title=first.kind == "per_equipment" and self.same_set[tuple(row.deliverable_ids)]
            - (row.file.source.value != "client") > 0)
        return naming.build_name(ctx, self.project.naming_templates)

    def where(self, it: Item) -> str:
        return " ".join(filter(None, [it.equipment_tag, it.equipment_name])) or it.module_name or ""

    def location(self, did: int) -> dict[str, Any]:
        it = self.items.get(did)
        if it is None:
            return {"deliverable_id": did}
        return {"deliverable_id": did, "milestone_code": it.milestone_code,
                "submittal_no": it.submittal_no, "document_type_name": it.document_type_name,
                "where": self.where(it), "status": it.status}


# ---------------------------------------------------------------- 目录树


def _node(key: str, label: str, **extra) -> dict[str, Any]:
    return {"key": key, "label": label, "files": set(), "children": {}, **extra}


def tree(session: Session, project_id: int, view: View) -> dict[str, Any]:
    """一种视图的目录树：每个节点带文件数（不重复计）；叶子节点带应交项 id（可以"上传到此处"）。"""
    snap = Snapshot(session, project_id)
    roots: dict[str, dict[str, Any]] = {}

    def child(parent: dict[str, dict[str, Any]], key: str, label: str, **extra) -> dict[str, Any]:
        if key not in parent:
            parent[key] = _node(key, label, **extra)
        return parent[key]

    for it in snap.items.values():
        fids = snap.by_deliverable.get(it.id, set())
        if it.obsolete and not fids:
            continue  # 作废的应交项没挂文件就不列
        doc_label = f"{it.submittal_no} {it.document_type_name}"
        where = snap.where(it)
        if view == "milestone":
            ms = child(roots, f"m:{it.milestone_id}", f"{it.milestone_code} {it.milestone_name or ''}".strip())
            if it.kind == "whole":
                path = [ms, child(ms["children"], f"x:{it.id}", doc_label, deliverable_id=it.id)]
            else:
                doc = child(ms["children"], f"d:{it.document_type_id}", doc_label)
                path = [ms, doc, child(doc["children"], f"x:{it.id}", where, deliverable_id=it.id)]
        elif view == "equipment":
            if it.equipment_id is None:
                continue  # 不对应设备的文件不在这个视图里
            et = child(roots, f"t:{it.equipment_type_id or 0}", it.equipment_type_name or "", untyped=it.equipment_type_id is None)
            eq = child(et["children"], f"e:{it.equipment_id}", where)
            path = [et, eq, child(eq["children"], f"x:{it.id}", doc_label, deliverable_id=it.id)]
        else:  # module
            mod = child(roots, f"mod:{it.module_id or 0}", it.module_name or "", unassigned=it.module_id is None)
            if it.kind in ("whole", "per_module"):
                path = [mod, child(mod["children"], f"x:{it.id}", doc_label, deliverable_id=it.id)]
            else:
                doc = child(mod["children"], f"d:{it.module_id or 0}:{it.document_type_id}", doc_label)
                path = [mod, doc, child(doc["children"], f"x:{it.id}", where, deliverable_id=it.id)]
        for n in path:
            n["files"] |= fids

    def out(n: dict[str, Any]) -> dict[str, Any]:
        children = [out(c) for c in n["children"].values()]
        return {k: v for k, v in n.items() if k not in ("files", "children")} | {
            "count": len(n["files"]), "children": children}

    unclassified = sum(1 for r in snap.files.values() if not r.deliverable_ids)
    recycle_count = len(recycled_versions(session, project_id, snapshot=snap))
    in_view = set().union(*(n["files"] for n in roots.values())) if roots else set()
    return {"view": view, "nodes": [out(n) for n in roots.values()],
            "unclassified": unclassified, "recycle": recycle_count,
            "total": len(snap.files), "view_total": len(in_view) + unclassified}


def node_deliverables(snap: Snapshot, view: View, node: str) -> set[int] | None:
    """节点下的应交项；None 表示不限（根）。"""
    if not node:
        return None
    kind, _, rest = node.partition(":")
    out = set()
    for it in snap.items.values():
        if kind == "x":
            hit = str(it.id) == rest
        elif kind == "m":
            hit = str(it.milestone_id) == rest
        elif kind == "d" and view == "milestone":
            hit = str(it.document_type_id) == rest
        elif kind == "t":
            hit = it.equipment_id is not None and str(it.equipment_type_id or 0) == rest
        elif kind == "e":
            hit = str(it.equipment_id) == rest
        elif kind == "mod":
            hit = str(it.module_id or 0) == rest
        elif kind == "d":  # 模块视图："d:模块:种类"
            mod, _, doc = rest.partition(":")
            hit = str(it.module_id or 0) == mod and str(it.document_type_id) == doc
        else:
            raise Invalid("browse.bad_node", f"节点不对：{node}", node=node)
        if hit:
            out.add(it.id)
    return out


# ---------------------------------------------------------------- 文件列表


def list_files(session: Session, project_id: int, *, view: View = "milestone", node: str = "",
               filters: Filters | None = None, sources: list[str] | None = None,
               q: str | None = None, sort: str = "uploaded_at", order: str = "desc",
               offset: int = 0, limit: int = 100) -> dict[str, Any]:
    snap = Snapshot(session, project_id)
    if node == RECYCLE:
        rows = recycled_versions(session, project_id, snapshot=snap)
        return {"total": len(rows), "offset": 0, "limit": len(rows), "items": [], "recycle": rows}
    if node == UNCLASSIFIED:
        candidates = [r for r in snap.files.values() if not r.deliverable_ids]
    else:
        scope = node_deliverables(snap, view, node)
        if view == "equipment" and scope is None:
            scope = {i for i, it in snap.items.items() if it.equipment_id is not None}
        f = filters or Filters()
        has_filter = any(v not in (None, [], "") for k, v in f.__dict__.items() if k != "q")
        allowed = {i for i, it in snap.items.items()
                   if (scope is None or i in scope) and f.match(it)}
        candidates = [r for r in snap.files.values()
                      if (any(d in allowed for d in r.deliverable_ids)
                          or (not r.deliverable_ids and scope is None and not has_filter))]
    if sources:
        candidates = [r for r in candidates if r.file.source.value in sources]
    if sort not in ("name", "size", "uploaded_at"):
        raise Invalid("browse.bad_sort", f"不能按 {sort} 排序", sort=sort)
    # 文件名要按当前结构算，比较费时：只有搜关键字、按名称排序时才给全部文件算，否则只算这一页
    names: dict[int, str] = {}
    if (q and q.strip()) or sort == "name":
        names = {r.file.id: snap.name(r) for r in candidates}
    if q and q.strip():
        needle = q.strip().casefold()
        candidates = [r for r in candidates if needle in _search_text(snap, r, names[r.file.id])]
    key = {"name": lambda r: names[r.file.id].casefold(),
           "size": lambda r: r.size or 0,
           "uploaded_at": lambda r: r.latest.uploaded_at.isoformat() if r.latest else ""}[sort]
    candidates.sort(key=key, reverse=order == "desc")
    page = candidates[offset:offset + limit]
    return {"total": len(candidates), "offset": offset, "limit": limit,
            "items": [_row_out(snap, r, names.get(r.file.id)) for r in page]}


def _search_text(snap: Snapshot, r: FileRow, name: str) -> str:
    v = r.latest
    parts = [name, v.filename if v else None, v.original_name if v else None, r.file.title]
    for did in r.deliverable_ids:
        it = snap.items.get(did)
        if it:
            parts += [it.submittal_no, it.document_type_name, snap.where(it)]
    return " ".join(filter(None, parts)).casefold()


def _row_out(snap: Snapshot, r: FileRow, name: str | None = None) -> dict[str, Any]:
    v = r.latest
    name = name or snap.name(r)
    locations = [snap.location(d) for d in r.deliverable_ids]
    return {"id": r.file.id, "filename": name, "title": r.file.title, "source": r.file.source.value,
            "current_version": r.file.current_version_no,
            "version_id": v.id if v else None, "original_name": v.original_name if v else None,
            "file_date": v.file_date if v else None, "size": r.size,
            "uploaded_by": v.uploaded_by_name if v else None,
            "uploaded_at": v.uploaded_at.isoformat() if v else None,
            "preview": bool(v and can_preview(v.original_name)),
            "deliverable_ids": r.deliverable_ids, "locations": locations}


# ---------------------------------------------------------------- 详情、重新归类、回收区


def detail(session: Session, project_id: int, file_id: int) -> dict[str, Any]:
    """文件详情：所在位置、全部版本（新的在前）、这个文件和各版本的操作记录。"""
    snap = Snapshot(session, project_id)
    row = snap.files.get(file_id)
    if row is None:
        raise NotFound("file.not_found", f"文件 {file_id} 不存在", id=file_id)
    versions = session.scalars(select(Version).where(Version.file_id == file_id)
                               .order_by(Version.version_no.desc())).all()
    protected = files_svc.protected_version_ids(session, [v.id for v in versions])
    sizes = dict(session.execute(select(Blob.sha256, Blob.size).where(
        Blob.sha256.in_({v.blob_sha256 for v in versions}))).all())
    version_rows = [{
        "id": v.id, "version_no": v.version_no, "filename": snap.name(row, v),
        "stored_name": v.filename, "original_name": v.original_name, "file_date": v.file_date,
        "date_source": v.date_source, "size": sizes.get(v.blob_sha256),
        "uploaded_by": v.uploaded_by_name, "uploaded_at": v.uploaded_at,
        "recycled_at": v.recycled_at, "purged_at": v.purged_at, "protected": v.id in protected,
        "preview": can_preview(v.original_name) and v.purged_at is None} for v in versions]
    logs = session.scalars(select(ChangeLog).where(
        ChangeLog.project_id == project_id,
        ((ChangeLog.entity_type == "files") & (ChangeLog.entity_id == str(file_id)))
        | ((ChangeLog.entity_type == "versions") & ChangeLog.entity_id.in_([str(v.id) for v in versions])),
    ).order_by(ChangeLog.id.desc()).limit(200)).all()
    names = change_labels(session, logs)
    return {"id": file_id, "title": row.file.title, "source": row.file.source.value,
            "current_version": row.file.current_version_no, "filename": snap.name(row),
            "deliverable_ids": row.deliverable_ids,
            "locations": [snap.location(d) for d in row.deliverable_ids],
            "submitted": bool(protected), "versions": version_rows,
            "logs": [{"id": log.id, "created_at": log.created_at, "actor": log.actor_name,
                      "action": log.action.value, "entity_type": log.entity_type,
                      "label": names.get(log.id), "changes": log.changes, "reason": log.reason}
                     for log in logs]}


def move(session: Session, project_id: int, file_id: int, deliverable_ids: list[int], actor: Actor,
         *, reason: str | None = None) -> dict[str, Any]:
    """重新归类：移到别的应交项，或移回待归类（空列表）。

    已经交给客户的（有版本被未作废的传送单或阶段基线引用）不能移；目标已有同样的文件时拒绝。
    """
    get_writable_project(session, project_id)
    file = session.get(File, file_id)
    if file is None or file.project_id != project_id:
        raise NotFound("file.not_found", f"文件 {file_id} 不存在", id=file_id)
    current = sorted(session.scalars(select(FileDeliverable.deliverable_id)
                                     .where(FileDeliverable.file_id == file_id)))
    if not current:
        if not deliverable_ids:
            raise Conflict("file.already_unclassified", "这个文件已经在待归类里")
        return files_svc.classify(session, project_id, file_id, deliverable_ids, actor, reason=reason)
    version_ids = list(session.scalars(select(Version.id).where(Version.file_id == file_id)))
    if files_svc.protected_version_ids(session, version_ids):
        raise Conflict("file.submitted",
                       "这个文件已提交给客户（在传送单或阶段基线里），不能移；请先作废传送单，或在新位置重新上传")
    target_ids: list[int] = []
    if deliverable_ids:
        target = files_svc.load_target(session, project_id, deliverable_ids)
        target_ids = target.ids
        if sorted(target_ids) == current:
            raise Conflict("file.same_place", "文件已经在这个位置")
        existing = files_svc.files_of(session, target_ids)
        existing.pop(file_id, None)
        others = session.scalars(select(File).where(File.id.in_(existing))).all() if existing else []
        exts = files_svc.file_exts(session, [*existing, file_id])
        clash = [f for f in others if f.source == file.source and files_svc.same_document(
            f, existing[f.id], exts.get(f.id, ""), target_ids=target_ids, title=file.title,
            new_ext=exts.get(file_id, ""), per_equipment=target.doc.per_equipment)]
        if clash:
            raise Conflict("file.target_has_file", "目标位置已有这个文件，请改用\"上传为新版本\"",
                           file_id=clash[0].id)
    for link in session.scalars(select(FileDeliverable).where(FileDeliverable.file_id == file_id)):
        session.delete(link)
    session.flush()
    for did in target_ids:
        session.add(FileDeliverable(file_id=file_id, deliverable_id=did))
    session.flush()
    record(session, file, ChangeAction.UPDATE, {"deliverable_ids": current},
           {"deliverable_ids": target_ids}, actor, reason=reason)
    emit(session, "file.moved", file, {"from": current, "to": target_ids})
    latest = session.scalar(select(Version).where(Version.file_id == file_id)
                            .order_by(Version.version_no.desc()).limit(1))
    return {"file_id": file_id, "deliverable_ids": target_ids,
            "filename": files_svc.current_name(session, latest, file) if latest else None}


def recycled_versions(session: Session, project_id: int, *, snapshot: Snapshot | None = None
                      ) -> list[dict[str, Any]]:
    """回收区：进了回收区、还没清理的版本（新进的在前），带还剩几天清理。"""
    snap = snapshot or Snapshot(session, project_id)
    days = get_settings().recycle_days
    rows = session.execute(
        select(Version, Blob.size).join(File, File.id == Version.file_id)
        .join(Blob, Blob.sha256 == Version.blob_sha256)
        .where(File.project_id == project_id, Version.recycled_at.is_not(None),
               Version.purged_at.is_(None))
        .order_by(Version.recycled_at.desc())).all()
    today = date.today()
    out = []
    for v, size in rows:
        row = snap.files.get(v.file_id)
        purge_on = v.recycled_at.date() + timedelta(days=days)
        out.append({"id": v.id, "file_id": v.file_id, "version_no": v.version_no,
                    "filename": snap.name(row, v) if row else v.filename,
                    "original_name": v.original_name, "size": size,
                    "recycled_at": v.recycled_at, "purge_on": purge_on,
                    "days_left": max((purge_on - today).days, 0),
                    "preview": can_preview(v.original_name),
                    "locations": [snap.location(d) for d in (row.deliverable_ids if row else [])]})
    return out
