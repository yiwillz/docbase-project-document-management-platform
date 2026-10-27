"""应交项查询：列表与筛选、分组列表、汇总统计、矩阵（PRD F7.1、F7.3、F7.4）。

一次把项目的有效应交项连同种类、里程碑、模块、设备读出来，在内存里算实际截止日、
是否逾期、所属模块，再筛选和统计（一个项目几千条，内存里算足够快，也保证各接口口径一致）。

每个应交项还带上最近一次提交和客户回复（第 8B 步）：传送单号、审核代码、回复日期、
是否最终批准、审核截止日、是否客户超期未审、是否无凭证。

无凭证：状态是已批准，但最近一次提交没有"批准"的客户回复；或状态是不适用，但没写依据。

按设备的应交项本身不挂模块；它的模块按"设备种类 ↔ 模块"的对应得出：
优先取该文件种类分区里的模块，种类没有分区时取设备种类对应的第一个模块；都没有为空（"其他"）。
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from typing import Any

from sqlalchemy import case, exists, func, select
from sqlalchemy.orm import Session

from app.models import (
    Deliverable,
    DeliverableStatus,
    DocumentType,
    DocumentTypeModule,
    Equipment,
    EquipmentType,
    Milestone,
    Module,
    ModuleEquipmentType,
    Responsible,
)
from app.services.deliverables import DONE_STATUSES, effective_due, is_outstanding, is_overdue, kind_of
from app.services.projects import get_project

S = DeliverableStatus


@dataclass
class Item:
    """一个应交项，带上显示和筛选要用的信息。"""

    id: int
    kind: str  # whole / per_module / per_equipment
    milestone_id: int
    milestone_code: str
    milestone_name: str | None
    document_type_id: int
    submittal_no: str
    document_type_name: str
    category: str | None
    origin: str
    in_client_mdl: bool
    module_id: int | None
    module_name: str | None
    equipment_id: int | None
    equipment_tag: str | None
    equipment_name: str | None
    equipment_type_id: int | None
    equipment_type_name: str | None
    vendor: str | None
    status: str
    responsible: str
    due_date: date | None
    due_source: str | None  # deliverable / document_type / milestone
    overdue: bool
    submitted_date: date | None
    status_date: date | None
    status_basis: str | None
    remarks: str | None
    acceptance: str | None = None  # 客户要求的接受情况
    transmittal_id: int | None = None
    transmittal_no: str | None = None
    review_code: str | None = None
    review_final: bool | None = None
    reply_date: date | None = None
    reply_ref: str | None = None
    review_due: date | None = None
    review_overdue: bool = False
    no_evidence: bool = False
    current_version: int | None = None  # 挂着的文件里最新的版本号；没有文件为空
    file_count: int = 0
    received: bool = False  # 收集情况：平台里有没有这一项的文件（按有没有文件自动判断）
    outstanding: bool = False  # 缺件：见 deliverables.is_outstanding
    obsolete: bool = False  # 作废的（只有文件浏览会读出来）
    # 排序用，不返回
    _sort: tuple = field(default=(), repr=False)

    def out(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("_sort")
        return data


@dataclass
class Filters:
    milestone_ids: list[int] | None = None
    document_type_ids: list[int] | None = None
    module_ids: list[int] | None = None
    equipment_type_ids: list[int] | None = None
    equipment_ids: list[int] | None = None
    vendors: list[str] | None = None
    statuses: list[str] | None = None
    responsibles: list[str] | None = None
    origins: list[str] | None = None
    kinds: list[str] | None = None
    overdue: bool | None = None
    in_client_mdl: bool | None = None
    review_overdue: bool | None = None
    no_evidence: bool | None = None
    received: bool | None = None
    outstanding: bool | None = None
    q: str | None = None
    ids: list[int] | None = None

    def match(self, it: Item) -> bool:
        checks = (
            (self.ids, it.id), (self.milestone_ids, it.milestone_id),
            (self.document_type_ids, it.document_type_id), (self.module_ids, it.module_id),
            (self.equipment_type_ids, it.equipment_type_id), (self.equipment_ids, it.equipment_id),
            (self.statuses, it.status), (self.responsibles, it.responsible),
            (self.origins, it.origin), (self.kinds, it.kind),
        )
        if any(allowed and value not in allowed for allowed, value in checks):
            return False
        if self.vendors and (it.vendor or "") not in self.vendors:
            return False
        if self.overdue is not None and it.overdue != self.overdue:
            return False
        if self.in_client_mdl is not None and it.in_client_mdl != self.in_client_mdl:
            return False
        if self.review_overdue is not None and it.review_overdue != self.review_overdue:
            return False
        if self.no_evidence is not None and it.no_evidence != self.no_evidence:
            return False
        if self.received is not None and it.received != self.received:
            return False
        if self.outstanding is not None and it.outstanding != self.outstanding:
            return False
        if self.q:
            needle = self.q.strip().casefold()
            hay = " ".join(filter(None, (it.submittal_no, it.document_type_name, it.equipment_tag,
                                         it.equipment_name, it.module_name, it.vendor)))
            if needle not in hay.casefold():
                return False
        return True


def _number(text: str) -> tuple:
    """"4.10" 排在 "4.9" 后面：按点分开逐段比数字，不是数字的按文字。"""
    return tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in str(text).split("."))


def load_items(session: Session, project_id: int, filters: Filters | None = None, *,
               today: date | None = None, include_obsolete: bool = False) -> list[Item]:
    """项目的有效应交项（默认不含作废的），排好序：里程碑 → 种类 → 模块 → 设备。
    include_obsolete：文件浏览用，作废的应交项上可能还挂着文件。"""
    get_project(session, project_id)
    today = today or date.today()
    rows = session.execute(
        select(Deliverable, DocumentType, Milestone)
        .join(DocumentType, DocumentType.id == Deliverable.document_type_id)
        .join(Milestone, Milestone.id == DocumentType.milestone_id)
        .where(Deliverable.project_id == project_id,
               True if include_obsolete else Deliverable.obsolete.is_(False))
    ).all()
    modules = {m.id: m for m in session.scalars(select(Module).where(Module.project_id == project_id))}
    equipment = {e.id: e for e in session.scalars(
        select(Equipment).where(Equipment.project_id == project_id))}
    types = {t.id: t for t in session.scalars(
        select(EquipmentType).where(EquipmentType.project_id == project_id))}
    type_modules: dict[int, list[int]] = defaultdict(list)
    for module_id, type_id in session.execute(
            select(ModuleEquipmentType.module_id, ModuleEquipmentType.equipment_type_id)
            .join(Module, Module.id == ModuleEquipmentType.module_id)
            .where(ModuleEquipmentType.active, Module.project_id == project_id)):
        type_modules[type_id].append(module_id)
    for ids in type_modules.values():
        ids.sort(key=lambda i: (modules[i].sort_order, i))
    doc_modules: dict[int, set[int]] = defaultdict(set)
    for doc_id, module_id in session.execute(
            select(DocumentTypeModule.document_type_id, DocumentTypeModule.module_id)
            .join(DocumentType, DocumentType.id == DocumentTypeModule.document_type_id)
            .where(DocumentTypeModule.active, DocumentType.project_id == project_id)):
        doc_modules[doc_id].add(module_id)

    from app.services.transmittals import review_info  # 避免循环引用

    reviews = review_info(session, project_id, today)
    from app.services.files import version_counts

    counts = version_counts(session, project_id)
    items = []
    for d, doc, ms in rows:
        module_id = d.module_id
        e = equipment.get(d.equipment_id) if d.equipment_id else None
        if e is not None and module_id is None:
            candidates = type_modules.get(e.equipment_type_id, [])
            in_doc = [m for m in candidates if m in doc_modules.get(doc.id, ())]
            pool = in_doc if doc_modules.get(doc.id) else candidates
            module_id = pool[0] if pool else None
        module = modules.get(module_id) if module_id else None
        etype = types.get(e.equipment_type_id) if e and e.equipment_type_id else None
        due, source = effective_due(d.due_date, doc.due_date, ms.due_date)
        items.append(Item(
            id=d.id, kind=kind_of(d), milestone_id=ms.id, milestone_code=ms.code,
            milestone_name=ms.name, document_type_id=doc.id, submittal_no=doc.submittal_no,
            document_type_name=doc.name, category=doc.category, origin=doc.origin.value,
            in_client_mdl=doc.in_client_mdl, module_id=module.id if module else None,
            module_name=module.name if module else None,
            equipment_id=e.id if e else None, equipment_tag=e.tag if e else None,
            equipment_name=e.name if e else None,
            equipment_type_id=etype.id if etype else None,
            equipment_type_name=etype.name if etype else None, vendor=e.vendor if e else None,
            status=d.status.value, responsible=d.responsible.value, due_date=due,
            due_source=source, overdue=is_overdue(d.status, due, today),
            submitted_date=d.submitted_date, status_date=d.status_date,
            status_basis=d.status_basis, remarks=d.remarks,
            acceptance=doc.acceptance.value if doc.acceptance else None,
            **_review_fields(d, reviews.get(d.id)),
            file_count=counts.get(d.id, (0, None))[0], current_version=counts.get(d.id, (0, None))[1],
            received=d.id in counts, outstanding=is_outstanding(d.status, d.id in counts),
            obsolete=d.obsolete,
            _sort=(_number(ms.number), _number(doc.submittal_no),
                   (module.sort_order, module.id) if module else (1 << 30, 0),
                   (e.sort_order, e.tag or "", e.id) if e else (0, "", 0)),
        ))
    items.sort(key=lambda it: it._sort)
    return [it for it in items if filters is None or filters.match(it)]


def _review_fields(d: Deliverable, review: dict[str, Any] | None) -> dict[str, Any]:
    if d.status is S.APPROVED:
        no_evidence = review is None or review["review_status"] != S.APPROVED.value
    elif d.status is S.NOT_APPLICABLE:
        no_evidence = not (d.status_basis or "").strip()
    else:
        no_evidence = False
    if review is None:
        return {"no_evidence": no_evidence}
    return {k: review[k] for k in ("transmittal_id", "transmittal_no", "review_code",
                                   "review_final", "reply_date", "reply_ref", "review_due",
                                   "review_overdue")} | {"no_evidence": no_evidence}


# ---------------------------------------------------------------- 统计


def stats(items: Iterable[Item]) -> dict[str, Any]:
    """完成率（已批准 ÷ 应交，不含不适用）、各方待办（未完成的按责任方）、逾期、按状态。"""
    by_status: dict[str, int] = defaultdict(int)
    pending = {"ours": 0, "vendor": 0, "client": 0, "unassigned": 0}
    total = overdue = review_overdue = no_evidence = received = outstanding = 0
    for it in items:
        total += 1
        by_status[it.status] += 1
        overdue += it.overdue
        review_overdue += it.review_overdue
        no_evidence += it.no_evidence
        outstanding += it.outstanding
        received += it.received and it.status != S.NOT_APPLICABLE.value
        if S(it.status) not in DONE_STATUSES:
            key = "unassigned" if it.responsible == Responsible.NONE.value else it.responsible
            pending[key] += 1
    approved = by_status.get(S.APPROVED.value, 0)
    applicable = total - by_status.get(S.NOT_APPLICABLE.value, 0)
    return {
        "total": total, "applicable": applicable, "approved": approved,
        "completion": round(approved / applicable, 4) if applicable else None,
        "overdue": overdue, "review_overdue": review_overdue, "no_evidence": no_evidence,
        # 收集情况：应交的（不含不适用）里已经收到文件的；缺件
        "received": received, "received_rate": round(received / applicable, 4) if applicable else None,
        "outstanding": outstanding,
        "pending": pending, "by_status": dict(by_status),
    }


def project_stats(session: Session, today: date | None = None) -> dict[int, dict[str, Any]]:
    """项目列表用：每个项目的完成率、缺件数、逾期数（一条分组查询，口径和应交矩阵的汇总一样）。

    完成率 = 已批准 ÷ 应交（不含不适用）；缺件 = 还没收到文件的（不含已批准）和需修改重交的；
    逾期 = 未提交且实际截止日（应交项 → 文件种类 → 里程碑）已过。没有应交项的项目不在结果里。
    缺件的口径见 deliverables.is_outstanding（这里用同样的条件写成 SQL）。
    """
    from app.models import File, FileDeliverable

    today = today or date.today()
    due = func.coalesce(Deliverable.due_date, DocumentType.due_date, Milestone.due_date)
    count = lambda cond: func.sum(case((cond, 1), else_=0))  # noqa: E731
    has_file = exists().where(FileDeliverable.deliverable_id == Deliverable.id,
                              FileDeliverable.file_id == File.id)
    outstanding = (Deliverable.status != S.NOT_APPLICABLE) & (
        (Deliverable.status == S.REVISE_RESUBMIT) | (~has_file & (Deliverable.status != S.APPROVED)))
    rows = session.execute(
        select(Deliverable.project_id,
               count(Deliverable.status != S.NOT_APPLICABLE),
               count(Deliverable.status == S.APPROVED),
               count(outstanding),
               count((Deliverable.status == S.NOT_SUBMITTED) & (due < today)))
        .join(DocumentType, DocumentType.id == Deliverable.document_type_id)
        .join(Milestone, Milestone.id == DocumentType.milestone_id)
        .where(Deliverable.obsolete.is_(False))
        .group_by(Deliverable.project_id))
    return {pid: {"completion": round(approved / applicable, 4) if applicable else None,
                  "outstanding": outstanding, "overdue": overdue}
            for pid, applicable, approved, outstanding, overdue in rows}


# 点卡片 / 按钮加上的筛选：汇总数字不跟它们走
CARD_FILTERS = ("statuses", "responsibles", "overdue", "review_overdue", "no_evidence", "received", "outstanding")


def scope_only(filters: Filters | None) -> Filters | None:
    """去掉点卡片加上的筛选，只留范围筛选。"""
    if filters is None:
        return None
    return replace(filters, **{f: None for f in CARD_FILTERS})


def summary(session: Session, project_id: int, filters: Filters | None = None, *,
            today: date | None = None) -> dict[str, Any]:
    """汇总：全项目、按里程碑（含里程碑下按模块）、按模块。

    兼容第 5 步的字段：active、obsolete、by_kind、by_status。
    顶部的数字只跟"范围"筛选走（里程碑、模块、种类、设备、供应商、关键字……），
    不跟点卡片加上的筛选走（状态、责任方、逾期、收集情况等），否则点了卡片数字就变了。
    """
    items = load_items(session, project_id, scope_only(filters), today=today)
    obsolete = len(session.scalars(select(Deliverable.id).where(
        Deliverable.project_id == project_id, Deliverable.obsolete.is_(True))).all())
    by_kind: dict[str, int] = defaultdict(int)
    for it in items:
        by_kind[it.kind] += 1

    milestones: dict[int, dict[str, Any]] = {}
    by_ms: dict[int, list[Item]] = defaultdict(list)
    by_module: dict[int | None, list[Item]] = defaultdict(list)
    by_ms_module: dict[tuple[int, int | None], list[Item]] = defaultdict(list)
    module_names: dict[int | None, str | None] = {}
    for it in items:
        milestones.setdefault(it.milestone_id, {"milestone_id": it.milestone_id,
                                                "code": it.milestone_code, "name": it.milestone_name})
        by_ms[it.milestone_id].append(it)
        by_module[it.module_id].append(it)
        by_ms_module[(it.milestone_id, it.module_id)].append(it)
        module_names[it.module_id] = it.module_name

    def module_rows(groups: dict[int | None, list[Item]]) -> list[dict[str, Any]]:
        # 没有模块的（整个种类，或设备没对应模块）排最后
        keys = sorted(groups, key=lambda m: (m is None, groups[m][0]._sort[2]))
        return [{"module_id": m, "module_name": module_names[m], **stats(groups[m])} for m in keys]

    by_origin: dict[str, list[Item]] = defaultdict(list)
    for it in items:
        by_origin[it.origin].append(it)
    whole = stats(items)
    return {
        "active": len(items), "obsolete": obsolete, "by_kind": dict(by_kind),
        "by_status": whole["by_status"], "overall": whole,
        "milestones": [
            {**info, **stats(by_ms[ms_id]),
             "modules": module_rows({m: v for (ms, m), v in by_ms_module.items() if ms == ms_id})}
            for ms_id, info in milestones.items()],
        "modules": module_rows(by_module),
        # 按要求类别分开看：合同内的完成情况不被新增的冲淡
        "by_origin": {o: stats(v) for o, v in sorted(by_origin.items())},
    }


# ---------------------------------------------------------------- 分组列表


def tree(session: Session, project_id: int, filters: Filters | None = None, *,
         today: date | None = None) -> dict[str, Any]:
    """里程碑 → 文件种类 → 应交项；按设备的种类再按模块分一层（界面上可折叠）。"""
    items = load_items(session, project_id, filters, today=today)
    milestones: list[dict[str, Any]] = []
    for it in items:
        if not milestones or milestones[-1]["milestone_id"] != it.milestone_id:
            milestones.append({"milestone_id": it.milestone_id, "code": it.milestone_code,
                               "name": it.milestone_name, "_items": [], "document_types": []})
        ms = milestones[-1]
        ms["_items"].append(it)
        docs = ms["document_types"]
        if not docs or docs[-1]["document_type_id"] != it.document_type_id:
            docs.append({"document_type_id": it.document_type_id, "submittal_no": it.submittal_no,
                         "name": it.document_type_name, "kind": it.kind, "origin": it.origin,
                         "in_client_mdl": it.in_client_mdl, "_items": []})
        docs[-1]["_items"].append(it)

    for ms in milestones:
        for doc in ms["document_types"]:
            doc_items = doc.pop("_items")
            doc["stats"] = stats(doc_items)
            if doc["kind"] == "per_equipment":
                groups: dict[int | None, list[Item]] = {}
                for it in doc_items:
                    groups.setdefault(it.module_id, []).append(it)
                order = sorted(groups, key=lambda m: m is None)
                doc["modules"] = [{"module_id": m, "module_name": groups[m][0].module_name,
                                   "stats": stats(groups[m]),
                                   "items": [it.out() for it in groups[m]]} for m in order]
                doc["items"] = []
            else:
                doc["modules"] = []
                doc["items"] = [it.out() for it in doc_items]
        ms["stats"] = stats(ms.pop("_items"))
    return {"stats": stats(items), "milestones": milestones}


# ---------------------------------------------------------------- 矩阵


def matrix(session: Session, project_id: int, filters: Filters | None = None, *,
           today: date | None = None) -> dict[str, Any]:
    """矩阵：行 = 文件种类，列 = 设备（按设备种类分组）或模块；整个种类只有一格。

    格子的键：设备 "e:<id>"，模块 "m:<id>"，整个种类 "whole"；没有的格子表示不需要交。
    """
    items = load_items(session, project_id, filters, today=today)
    rows: list[dict[str, Any]] = []
    module_cols: dict[int, tuple] = {}
    equipment_cols: dict[int, dict[str, Any]] = {}
    for it in items:
        if not rows or rows[-1]["document_type_id"] != it.document_type_id:
            rows.append({"document_type_id": it.document_type_id, "submittal_no": it.submittal_no,
                         "name": it.document_type_name, "milestone_id": it.milestone_id,
                         "milestone_code": it.milestone_code, "kind": it.kind,
                         "origin": it.origin, "_items": [], "cells": {}})
        row = rows[-1]
        row["_items"].append(it)
        if it.kind == "per_equipment":
            key = f"e:{it.equipment_id}"
            equipment_cols[it.equipment_id] = {"id": it.equipment_id, "tag": it.equipment_tag,
                                               "name": it.equipment_name, "vendor": it.vendor,
                                               "type_id": it.equipment_type_id,
                                               "type_name": it.equipment_type_name,
                                               "_sort": it._sort[3]}
        elif it.kind == "per_module":
            key = f"m:{it.module_id}"
            module_cols[it.module_id] = (it._sort[2], it.module_name)
        else:
            key = "whole"
        row["cells"][key] = {"deliverable_id": it.id, "status": it.status,
                             "responsible": it.responsible, "overdue": it.overdue,
                             "due_date": it.due_date, "current_version": it.current_version,
                             "received": it.received}
    for row in rows:
        row["stats"] = stats(row.pop("_items"))

    groups: dict[int | None, dict[str, Any]] = {}
    for col in sorted(equipment_cols.values(), key=lambda c: c["_sort"]):
        col.pop("_sort")
        group = groups.setdefault(col["type_id"], {"equipment_type_id": col["type_id"],
                                                   "name": col["type_name"], "equipment": []})
        group["equipment"].append({k: col[k] for k in ("id", "tag", "name", "vendor")})
    return {
        "modules": [{"id": m, "name": name} for m, (_, name) in
                    sorted(module_cols.items(), key=lambda kv: kv[1][0])],
        "equipment_groups": sorted(groups.values(), key=lambda g: g["equipment_type_id"] is None),
        "rows": rows,
    }
