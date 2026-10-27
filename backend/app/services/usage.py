"""配置类数据"用过没有"：没用过的可以真删除（编号 / 名称释放），用过的只能停用（2026-10-27 用户确认，第 17F 步）。

应交项"用过" = 挂了文件、进过传送单（包括补记的）、进过阶段基线。
- 设备：它的应交项有用过的；或者最近一次发 MDL 给客户时它已经存在（客户见过这个编号）
- 模块：按模块的应交项有用过的；或者最近一次发 MDL 时它已经存在
- 设备种类：还有设备属于它（包括停用的），要先删设备
文件种类、里程碑的删除规则在 structure.py（文件种类也按这里的"应交项用过"判断）。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import (
    BaselineItem,
    ChangeLog,
    Deliverable,
    DocumentTypeEquipment,
    DocumentTypeModule,
    Equipment,
    EquipmentType,
    EquipmentUnit,
    FileDeliverable,
    Module,
    ModuleEquipmentType,
    TransmittalItem,
)
from app.services.crud import delete as _delete
from app.services.crud import get_in_project
from app.services.errors import Conflict
from app.services.history import Actor
from app.services.projects import get_writable_project


def deliverable_usage(session: Session, deliverable_ids: list[int]) -> dict[str, int]:
    """这些应交项用过没有：{files: 文件数, transmittals: 传送单数, baselines: 基线数}，没用过的项不出现。"""
    if not deliverable_ids:
        return {}
    files = session.scalar(select(func.count(func.distinct(FileDeliverable.file_id)))
                           .where(FileDeliverable.deliverable_id.in_(deliverable_ids))) or 0
    transmittals = session.scalar(select(func.count(func.distinct(TransmittalItem.transmittal_id)))
                                  .where(TransmittalItem.deliverable_id.in_(deliverable_ids))) or 0
    baselines = session.scalar(select(func.count(func.distinct(BaselineItem.baseline_id)))
                               .where(BaselineItem.deliverable_id.in_(deliverable_ids))) or 0
    return {k: v for k, v in (("files", files), ("transmittals", transmittals), ("baselines", baselines)) if v}


def last_mdl_issue(session: Session, project_id: int) -> datetime | None:
    """最近一次发 MDL 给客户的时间（新版本、或结构没变沿用上一版再发，都记在变更记录里）。"""
    return session.scalar(select(func.max(ChangeLog.created_at))
                          .where(ChangeLog.project_id == project_id, ChangeLog.entity_type == "mdl_versions"))


def _seen_by_client(session: Session, project_id: int, created_at: datetime | None) -> bool:
    issued = last_mdl_issue(session, project_id)
    return issued is not None and created_at is not None and created_at <= issued


def equipment_usage(session: Session, project_id: int, equipment: Equipment) -> dict[str, int]:
    ids = list(session.scalars(select(Deliverable.id).where(Deliverable.equipment_id == equipment.id)))
    out = deliverable_usage(session, ids)
    if _seen_by_client(session, project_id, equipment.created_at):
        out["mdl_issued"] = 1
    return out


def module_usage(session: Session, project_id: int, module: Module) -> dict[str, int]:
    ids = list(session.scalars(select(Deliverable.id).where(Deliverable.module_id == module.id)))
    out = deliverable_usage(session, ids)
    if _seen_by_client(session, project_id, module.created_at):
        out["mdl_issued"] = 1
    return out


def equipment_type_usage(session: Session, project_id: int, etype: EquipmentType) -> dict[str, int]:
    n = session.scalar(select(func.count()).select_from(Equipment).where(Equipment.equipment_type_id == etype.id))
    return {"equipment": n} if n else {}


def _refuse(kind: str, name: str, usage: dict[str, int]) -> None:
    raise Conflict(f"{kind}.in_use", f"{name} 已经用过，只能停用", name=name, **usage)


# ---------------------------------------------------------------- 删除（调用方提交并重算应交项）


def delete_equipment(session: Session, project_id: int, equipment_id: int, actor: Actor, *,
                     reason: str | None = None) -> None:
    """删除没用过的设备：连同出厂编号、种类对应设备的勾选、它的应交项；编号释放。"""
    get_writable_project(session, project_id)
    e = get_in_project(session, Equipment, equipment_id, project_id, "equipment")
    usage = equipment_usage(session, project_id, e)
    if usage:
        _refuse("equipment", e.tag or e.name, usage)
    session.execute(delete(Deliverable).where(Deliverable.equipment_id == e.id))
    session.execute(delete(DocumentTypeEquipment).where(DocumentTypeEquipment.equipment_id == e.id))
    session.execute(delete(EquipmentUnit).where(EquipmentUnit.equipment_id == e.id))
    _delete(session, e, actor, project_id, reason)


def delete_module(session: Session, project_id: int, module_id: int, actor: Actor, *,
                  reason: str | None = None) -> None:
    """删除没用过的模块：连同模块分区、模块对应的设备种类、按模块的应交项。"""
    get_writable_project(session, project_id)
    m = get_in_project(session, Module, module_id, project_id, "module")
    usage = module_usage(session, project_id, m)
    if usage:
        _refuse("module", m.name, usage)
    session.execute(delete(Deliverable).where(Deliverable.module_id == m.id))
    session.execute(delete(DocumentTypeModule).where(DocumentTypeModule.module_id == m.id))
    session.execute(delete(ModuleEquipmentType).where(ModuleEquipmentType.module_id == m.id))
    _delete(session, m, actor, project_id, reason)


def delete_equipment_type(session: Session, project_id: int, type_id: int, actor: Actor, *,
                          reason: str | None = None) -> None:
    """删除没有设备的设备种类：连同模块对应关系。"""
    get_writable_project(session, project_id)
    t = get_in_project(session, EquipmentType, type_id, project_id, "equipment_type")
    usage = equipment_type_usage(session, project_id, t)
    if usage:
        raise Conflict("equipment_type.in_use", f"设备种类 {t.name} 下还有设备，先删除或改掉这些设备的种类",
                       name=t.name, **usage)
    session.execute(delete(ModuleEquipmentType).where(ModuleEquipmentType.equipment_type_id == t.id))
    _delete(session, t, actor, project_id, reason)


def bulk_delete_equipment(session: Session, project_id: int, ids: list[int], actor: Actor, *,
                          reason: str | None = None) -> dict[str, Any]:
    """批量删除：没用过的删掉，用过的跳过并给出原因（可以再批量停用）。"""
    deleted, skipped = [], []
    for eid in ids:
        e = get_in_project(session, Equipment, eid, project_id, "equipment")
        usage = equipment_usage(session, project_id, e)
        if usage:
            skipped.append({"id": e.id, "tag": e.tag, "name": e.name, "usage": usage})
            continue
        delete_equipment(session, project_id, eid, actor, reason=reason)
        deleted.append(eid)
    return {"deleted": deleted, "skipped": skipped}
