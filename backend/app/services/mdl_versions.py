"""客户 MDL 的版本（PRD F2.7）。

平台的项目结构是主数据，可随时调整；客户 MDL 是从它导出、发给客户的某个版本。
MDL 本身也是一份要交给客户的文件，每次发给客户都带版本号：第 6 步导出并发给客户时
自动调用 freeze_version 记一版（Rev.0、Rev.1…，只增不改），不提供单独的"冻结"操作。
一个版本 = 当时"列入客户 MDL"的结构快照。
快照里里程碑、文件种类都按内部 id 记录，编号变了也能认出是同一个：
对比时显示为"编号 3.5 → 3.6"的修改，而不是一删一增。
版本只存结构，不存状态（状态天天在变，历史靠变更记录查）。
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ChangeAction,
    DocumentType,
    DocumentTypeModule,
    MdlVersion,
    Milestone,
    Module,
)
from app.services.errors import Conflict, Invalid, NotFound
from app.services.history import Actor, emit, record, to_plain
from app.services.projects import get_project, get_writable_project

MILESTONE_FIELDS = ("code", "number", "name", "goal", "description", "remarks", "due_date")
DOCUMENT_TYPE_FIELDS = (
    "submittal_no",
    "name",
    "category",
    "per_equipment",
    "per_module",
    "due_date",
    "content",
    "description",
    "purpose",
    "origin",
    "required_date",
    "basis",
    "changed_date",
    "change_ref",
)


# 已从结构里删掉的字段：旧版本快照里还有，比较时忽略（2026-10-24 删除）
_RETIRED_FIELDS = {"epc_reference", "doc_count"}


def label(rev_no: int) -> str:
    return f"Rev.{rev_no}"


# ---------------------------------------------------------------- 快照


def build_snapshot(session: Session, project_id: int) -> dict[str, Any]:
    """当前结构中"列入客户 MDL"的部分：启用的里程碑，以及其下启用且列入客户 MDL 的文件种类。"""
    milestones = list(session.scalars(
        select(Milestone).where(Milestone.project_id == project_id, Milestone.active)
    ))
    active_ids = {m.id for m in milestones}
    docs = [d for d in session.scalars(
        select(DocumentType).where(DocumentType.project_id == project_id, DocumentType.active,
                                   DocumentType.in_client_mdl)
    ) if d.milestone_id in active_ids]
    module_names = {m.id: m.name for m in session.scalars(
        select(Module).where(Module.project_id == project_id))}
    partitions: dict[int, list[DocumentTypeModule]] = {}
    if docs:
        for p in session.scalars(
            select(DocumentTypeModule)
            .where(DocumentTypeModule.document_type_id.in_([d.id for d in docs]),
                   DocumentTypeModule.active)
            .order_by(DocumentTypeModule.sort_order, DocumentTypeModule.id)
        ):
            partitions.setdefault(p.document_type_id, []).append(p)

    return {
        "milestones": {
            str(m.id): {f: to_plain(getattr(m, f)) for f in MILESTONE_FIELDS}
            for m in milestones
        },
        "document_types": {
            str(d.id): {
                **{f: to_plain(getattr(d, f)) for f in DOCUMENT_TYPE_FIELDS},
                "milestone_id": d.milestone_id,
                "modules": [module_names[p.module_id] for p in partitions.get(d.id, [])],
            }
            for d in docs
        },
    }


def snapshot_counts(snapshot: dict[str, Any]) -> dict[str, int]:
    return {"milestones": len(snapshot["milestones"]),
            "document_types": len(snapshot["document_types"])}


# ---------------------------------------------------------------- 对比


def _sort_key(item: dict[str, Any], number_field: str) -> tuple:
    """按编号排序（"3.10" 排在 "3.9" 后面）。"""
    number = item.get(number_field) or ""
    return tuple(int(p) if p.isdigit() else 0 for p in str(number).split("."))


def _diff_section(old: dict[str, dict], new: dict[str, dict], number_field: str,
                  label_fields: tuple[str, ...]) -> dict[str, list[dict[str, Any]]]:
    def head(item_id: str, item: dict[str, Any]) -> dict[str, Any]:
        return {"id": int(item_id), **{f: item.get(f) for f in label_fields}}

    added = [head(k, new[k]) for k in new.keys() - old.keys()]
    removed = [head(k, old[k]) for k in old.keys() - new.keys()]
    modified = []
    for k in old.keys() & new.keys():
        changes = {f: [old[k].get(f), new[k].get(f)]
                   for f in sorted((old[k].keys() | new[k].keys()) - _RETIRED_FIELDS)
                   if old[k].get(f) != new[k].get(f)}
        if changes:
            modified.append({**head(k, new[k]), "changes": changes})
    key = lambda item: _sort_key(item, number_field)  # noqa: E731
    return {"added": sorted(added, key=key), "removed": sorted(removed, key=key),
            "modified": sorted(modified, key=key)}


def diff_snapshots(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """两个快照的差异：里程碑、文件种类各自的新增 / 删除 / 修改（字段级前→后）。"""
    result = {
        "milestones": _diff_section(old["milestones"], new["milestones"], "number",
                                    ("code", "name")),
        "document_types": _diff_section(old["document_types"], new["document_types"],
                                        "submittal_no", ("submittal_no", "name")),
    }
    result["has_changes"] = any(v for section in result.values() for v in section.values())
    return result


# ---------------------------------------------------------------- 版本


def list_versions(session: Session, project_id: int) -> list[MdlVersion]:
    get_project(session, project_id)
    return list(session.scalars(
        select(MdlVersion).where(MdlVersion.project_id == project_id).order_by(MdlVersion.rev_no)
    ))


def get_version(session: Session, project_id: int, rev_no: int) -> MdlVersion:
    get_project(session, project_id)
    version = session.scalar(select(MdlVersion).where(MdlVersion.project_id == project_id,
                                                      MdlVersion.rev_no == rev_no))
    if version is None:
        raise NotFound("mdl_version.not_found", f"没有 {label(rev_no)}", rev_no=rev_no)
    return version


def freeze_version(session: Session, project_id: int, actor: Actor, *,
                   description: str | None = None) -> MdlVersion:
    """把当前结构记为新的 MDL 版本（第 6 步发 MDL 给客户时调用）。和上一版相比没有任何变化时拒绝。"""
    get_writable_project(session, project_id)
    snapshot = build_snapshot(session, project_id)
    versions = list_versions(session, project_id)
    if versions and not diff_snapshots(versions[-1].snapshot, snapshot)["has_changes"]:
        raise Conflict("mdl_version.no_changes",
                       f"和 {label(versions[-1].rev_no)} 相比没有变化，不需要新版本",
                       rev_no=versions[-1].rev_no)
    if not snapshot["document_types"]:
        raise Invalid("mdl_version.empty", "当前没有列入客户 MDL 的文件种类，不能冻结")

    version = MdlVersion(
        project_id=project_id,
        rev_no=versions[-1].rev_no + 1 if versions else 0,
        description=(description or "").strip() or None,
        snapshot=snapshot,
        frozen_by=actor.id,
        frozen_by_name=actor.name,
    )
    session.add(version)
    session.flush()
    # 快照可能很大，变更记录里只记版本号、说明和数量
    summary = {"rev_no": version.rev_no, "description": version.description,
               **snapshot_counts(snapshot)}
    record(session, version, ChangeAction.CREATE, None, summary, actor, project_id=project_id)
    emit(session, "mdl_version.frozen", version, {"rev_no": version.rev_no},
         project_id=project_id)
    return version


def compare(session: Session, project_id: int, from_rev: int, to_rev: int | None
            ) -> dict[str, Any]:
    """对比两个版本；to_rev 为 None 表示和当前结构比。"""
    old = get_version(session, project_id, from_rev).snapshot
    new = (build_snapshot(session, project_id) if to_rev is None
           else get_version(session, project_id, to_rev).snapshot)
    return {"from": label(from_rev), "to": label(to_rev) if to_rev is not None else "current",
            **diff_snapshots(old, new)}
