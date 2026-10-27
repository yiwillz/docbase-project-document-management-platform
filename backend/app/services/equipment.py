"""设备：设备种类、设备、单台（出厂编号）、模块-设备种类对应、字段级历史与恢复（PRD F3）。

- 一条设备 = 一个设备条目 + 台数，应交项按条目生成，不按台
- 设备编号（tag）选填：有编号的项目内唯一，停用的也占用，不复用；没挂文件时可以改，挂了文件就锁定。
  没编号的（数量多、没位号的）用名称区分：没编号的条目之间名称不能重复（停用的也占用）
- 单台（出厂编号）不再按台数自动建：只有填了出厂编号的台才有记录；台数减少时多出来的停用
- 只停用、不删除；所有写操作写变更记录
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ChangeAction,
    ChangeLog,
    Deliverable,
    Equipment,
    EquipmentType,
    EquipmentUnit,
    FileDeliverable,
    Module,
    ModuleEquipmentType,
)
from app.services.crud import clean, create, get_in_project, pick, set_active, update
from app.services.errors import Conflict, Invalid, NotFound
from app.services.history import Actor
from app.services.projects import get_project, get_writable_project

EQUIPMENT_FIELDS = ("tag", "name", "equipment_type_id", "vendor", "quantity", "aliases", "attrs")

# ---------------------------------------------------------------- 设备种类


def list_equipment_types(session: Session, project_id: int, *, include_inactive: bool = False
                         ) -> list[EquipmentType]:
    get_project(session, project_id)
    stmt = select(EquipmentType).where(EquipmentType.project_id == project_id)
    if not include_inactive:
        stmt = stmt.where(EquipmentType.active)
    return list(session.scalars(stmt.order_by(EquipmentType.sort_order, EquipmentType.id)))


def _type_by_name(session: Session, project_id: int, name: str) -> EquipmentType | None:
    return session.scalar(select(EquipmentType).where(EquipmentType.project_id == project_id,
                                                      EquipmentType.name == name))


def create_equipment_type(session: Session, project_id: int, data: dict[str, Any], actor: Actor,
                          *, reason: str | None = None) -> EquipmentType:
    get_writable_project(session, project_id)
    data = pick(data, ("name",))
    clean(data, "name", required=True)
    if _type_by_name(session, project_id, data["name"]) is not None:
        raise Conflict("equipment_type.name_taken", f"设备种类 {data['name']} 已存在",
                       name=data["name"])
    orders = session.scalars(select(EquipmentType.sort_order)
                             .where(EquipmentType.project_id == project_id))
    item = EquipmentType(project_id=project_id, name=data["name"],
                         sort_order=max(orders, default=0) + 1)
    return create(session, item, actor, project_id, reason)


def get_or_create_equipment_type(session: Session, project_id: int, name: str, actor: Actor,
                                 *, reason: str | None = None) -> EquipmentType:
    """按名字找设备种类，没有就新建，停用的重新启用（导入设备清单时用）。"""
    item = _type_by_name(session, project_id, name)
    if item is None:
        return create_equipment_type(session, project_id, {"name": name}, actor, reason=reason)
    set_active(session, item, True, actor, reason, project_id)
    return item


def update_equipment_type(session: Session, project_id: int, type_id: int,
                          changes: dict[str, Any], actor: Actor, *, reason: str | None = None
                          ) -> EquipmentType:
    get_writable_project(session, project_id)
    item = get_in_project(session, EquipmentType, type_id, project_id, "equipment_type")
    changes = pick(changes, ("name",))
    if "name" in changes:
        clean(changes, "name", required=True)
        other = _type_by_name(session, project_id, changes["name"])
        if other is not None and other.id != item.id:
            raise Conflict("equipment_type.name_taken", f"设备种类 {changes['name']} 已存在",
                           name=changes["name"])
    return update(session, item, changes, actor, reason, project_id)


def set_equipment_type_active(session: Session, project_id: int, type_id: int, active: bool,
                              actor: Actor, *, reason: str | None = None) -> EquipmentType:
    get_writable_project(session, project_id)
    item = get_in_project(session, EquipmentType, type_id, project_id, "equipment_type")
    return set_active(session, item, active, actor, reason, project_id)


# ---------------------------------------------------------------- 模块-设备种类对应


def list_module_equipment_types(session: Session, project_id: int, module_id: int
                                ) -> list[EquipmentType]:
    get_project(session, project_id)
    get_in_project(session, Module, module_id, project_id, "module")
    return list(session.scalars(
        select(EquipmentType)
        .join(ModuleEquipmentType, ModuleEquipmentType.equipment_type_id == EquipmentType.id)
        .where(ModuleEquipmentType.module_id == module_id, ModuleEquipmentType.active)
        .order_by(EquipmentType.sort_order, EquipmentType.id)
    ))


def set_module_equipment_types(session: Session, project_id: int, module_id: int,
                               type_ids: list[int], actor: Actor, *, reason: str | None = None
                               ) -> list[EquipmentType]:
    """把模块对应的设备种类设成正好是 type_ids（可以为空）；去掉的只停用。"""
    get_writable_project(session, project_id)
    get_in_project(session, Module, module_id, project_id, "module")
    wanted = list(dict.fromkeys(type_ids))
    for type_id in wanted:
        get_in_project(session, EquipmentType, type_id, project_id, "equipment_type")
    existing = {m.equipment_type_id: m for m in session.scalars(
        select(ModuleEquipmentType).where(ModuleEquipmentType.module_id == module_id))}
    for type_id in wanted:
        link = existing.get(type_id)
        if link is None:
            create(session, ModuleEquipmentType(module_id=module_id, equipment_type_id=type_id),
                   actor, project_id, reason)
        else:
            set_active(session, link, True, actor, reason, project_id)
    for type_id, link in existing.items():
        if type_id not in wanted:
            set_active(session, link, False, actor, reason, project_id)
    return list_module_equipment_types(session, project_id, module_id)


# ---------------------------------------------------------------- 设备


def list_equipment(session: Session, project_id: int, *, include_inactive: bool = False,
                   equipment_type_id: int | None = None) -> list[Equipment]:
    get_project(session, project_id)
    stmt = select(Equipment).where(Equipment.project_id == project_id)
    if not include_inactive:
        stmt = stmt.where(Equipment.active)
    if equipment_type_id is not None:
        stmt = stmt.where(Equipment.equipment_type_id == equipment_type_id)
    return list(session.scalars(stmt.order_by(Equipment.sort_order, Equipment.id)))


def get_equipment(session: Session, project_id: int, equipment_id: int) -> Equipment:
    get_project(session, project_id)
    return get_in_project(session, Equipment, equipment_id, project_id, "equipment")


def equipment_has_files(session: Session, equipment_id: int) -> bool:
    return session.scalar(
        select(FileDeliverable.id)
        .join(Deliverable, Deliverable.id == FileDeliverable.deliverable_id)
        .where(Deliverable.equipment_id == equipment_id).limit(1)
    ) is not None


def _tag_owner(session: Session, project_id: int, tag: str) -> Equipment | None:
    """占用这个编号的设备（含停用的：编号不复用）。"""
    return session.scalar(select(Equipment).where(Equipment.project_id == project_id,
                                                  Equipment.tag == tag))


def _untagged_name_owner(session: Session, project_id: int, name: str) -> Equipment | None:
    """没编号的设备里占用这个名称的（含停用的）。"""
    return session.scalar(select(Equipment).where(Equipment.project_id == project_id,
                                                  Equipment.tag.is_(None), Equipment.name == name))


def label(equipment: Equipment) -> str:
    """给人看的设备称呼：有编号用编号，没编号用名称。"""
    return equipment.tag or equipment.name


def _check_identity(session: Session, project_id: int, tag: str | None, name: str,
                    exclude_id: int | None = None) -> None:
    if tag is not None:
        owner = _tag_owner(session, project_id, tag)
        if owner is not None and owner.id != exclude_id:
            raise Conflict("equipment.tag_taken", f"设备编号 {tag} 已被使用（停用的设备也占用编号）",
                           tag=tag)
    else:
        owner = _untagged_name_owner(session, project_id, name)
        if owner is not None and owner.id != exclude_id:
            raise Conflict("equipment.name_taken",
                           f"已有没编号的设备叫\"{name}\"；没编号的设备靠名称区分，名称不能重复",
                           name=name)


def _prepare(session: Session, project_id: int, data: dict[str, Any], *, creating: bool
             ) -> dict[str, Any]:
    data = pick(data, EQUIPMENT_FIELDS)
    clean(data, "name", required=creating or "name" in data)
    clean(data, "tag")
    clean(data, "vendor")
    if "quantity" in data:
        quantity = data["quantity"]
        if quantity is not None and (not isinstance(quantity, int) or quantity < 1):
            raise Invalid("equipment.bad_quantity", f"台数应为正整数：{quantity}",
                          quantity=quantity)
    if "aliases" in data:
        data["aliases"] = list(dict.fromkeys(a.strip() for a in (data["aliases"] or [])
                                             if a and a.strip()))
    if "attrs" in data and data["attrs"] is None:
        data["attrs"] = {}
    if data.get("equipment_type_id") is not None:
        get_in_project(session, EquipmentType, data["equipment_type_id"], project_id,
                       "equipment_type")
    return data


def _trim_units(session: Session, project_id: int, equipment: Equipment, actor: Actor,
                reason: str | None = None) -> None:
    """台数减少时，超出台数的单台记录停用（出厂编号保留）；台数增加时之前停用的恢复。"""
    quantity = equipment.quantity or 1
    for unit in session.scalars(select(EquipmentUnit).where(EquipmentUnit.equipment_id == equipment.id)):
        set_active(session, unit, unit.seq <= quantity, actor, reason, project_id)


def create_equipment(session: Session, project_id: int, data: dict[str, Any], actor: Actor,
                     *, reason: str | None = None) -> Equipment:
    get_writable_project(session, project_id)
    data = _prepare(session, project_id, data, creating=True)
    _check_identity(session, project_id, data.get("tag"), data["name"])
    orders = session.scalars(select(Equipment.sort_order).where(Equipment.project_id == project_id))
    equipment = Equipment(project_id=project_id, sort_order=max(orders, default=0) + 1, **data)
    return create(session, equipment, actor, project_id, reason)


def update_equipment(session: Session, project_id: int, equipment_id: int,
                     changes: dict[str, Any], actor: Actor, *, reason: str | None = None,
                     action: ChangeAction = ChangeAction.UPDATE) -> Equipment:
    get_writable_project(session, project_id)
    equipment = get_in_project(session, Equipment, equipment_id, project_id, "equipment")
    changes = _prepare(session, project_id, changes, creating=False)
    changes = {k: v for k, v in changes.items() if getattr(equipment, k) != v}
    if "tag" in changes and equipment_has_files(session, equipment.id):
        raise Conflict("equipment.tag_locked",
                       f"设备 {label(equipment)} 已有文件，编号不能再改（编号会出现在文件名里）",
                       equipment_id=equipment.id)
    if "tag" in changes or "name" in changes:
        _check_identity(session, project_id, changes.get("tag", equipment.tag),
                        changes.get("name", equipment.name), exclude_id=equipment.id)
    if not changes:
        return equipment
    update(session, equipment, changes, actor, reason, project_id, action=action)
    if "quantity" in changes:
        _trim_units(session, project_id, equipment, actor, reason)
    return equipment


def set_equipment_active(session: Session, project_id: int, equipment_id: int, active: bool,
                         actor: Actor, *, reason: str | None = None) -> Equipment:
    get_writable_project(session, project_id)
    equipment = get_in_project(session, Equipment, equipment_id, project_id, "equipment")
    return set_active(session, equipment, active, actor, reason, project_id)


# ---------------------------------------------------------------- 单台


def list_units(session: Session, project_id: int, equipment_id: int, *,
               include_inactive: bool = False) -> list[EquipmentUnit]:
    get_equipment(session, project_id, equipment_id)
    stmt = select(EquipmentUnit).where(EquipmentUnit.equipment_id == equipment_id)
    if not include_inactive:
        stmt = stmt.where(EquipmentUnit.active)
    return list(session.scalars(stmt.order_by(EquipmentUnit.seq)))


def set_serial_numbers(session: Session, project_id: int, equipment_id: int,
                       serials: dict[int, str | None], actor: Actor, *,
                       reason: str | None = None) -> list[EquipmentUnit]:
    """按第几台填出厂编号：{1: "SN-001", 2: "SN-002"}。只有填了的台才建记录；
    第几台不能超过台数；传空表示清空。"""
    get_writable_project(session, project_id)
    equipment = get_equipment(session, project_id, equipment_id)
    quantity = equipment.quantity or 1
    units = {u.seq: u for u in list_units(session, project_id, equipment_id, include_inactive=True)}
    for seq, serial in serials.items():
        if not 1 <= seq <= quantity:
            raise NotFound("equipment_unit.not_found", f"没有第 {seq} 台（共 {quantity} 台）",
                           seq=seq, quantity=quantity)
        serial = (serial or "").strip() or None
        unit = units.get(seq)
        if unit is None:
            if serial is not None:
                create(session, EquipmentUnit(equipment_id=equipment.id, seq=seq, serial_no=serial),
                       actor, project_id, reason)
        elif unit.serial_no != serial:
            update(session, unit, {"serial_no": serial}, actor, reason, project_id)
    return list_units(session, project_id, equipment_id)


# ---------------------------------------------------------------- 历史与恢复


def history(session: Session, project_id: int, equipment_id: int) -> list[ChangeLog]:
    """设备本身的字段级修改记录（新的在前）。"""
    get_equipment(session, project_id, equipment_id)
    return list(session.scalars(
        select(ChangeLog).where(ChangeLog.entity_type == "equipment",
                                ChangeLog.entity_id == str(equipment_id))
        .order_by(ChangeLog.id.desc())
    ))


def restore_before(session: Session, project_id: int, equipment_id: int, change_log_id: int,
                   actor: Actor, *, reason: str | None = None) -> Equipment:
    """把某次修改改过的字段恢复成修改前的值，留一条"恢复"记录。"""
    get_writable_project(session, project_id)
    log = session.get(ChangeLog, change_log_id)
    if log is None or log.entity_type != "equipment" or log.entity_id != str(equipment_id):
        raise NotFound("change_log.not_found", f"设备 {equipment_id} 没有修改记录 {change_log_id}",
                       change_log_id=change_log_id)
    if log.action is not ChangeAction.UPDATE:
        raise Invalid("equipment.restore_not_update", "只能恢复\"修改\"类的记录（新建、停用请用对应操作）",
                      change_log_id=change_log_id)
    before = {f: values[0] for f, values in log.changes.items() if f in EQUIPMENT_FIELDS}
    if not before:
        raise Invalid("equipment.restore_nothing", "这条记录里没有可以恢复的字段",
                      change_log_id=change_log_id)
    return update_equipment(session, project_id, equipment_id, before, actor,
                            reason=reason or f"恢复到修改记录 #{change_log_id} 之前",
                            action=ChangeAction.RESTORE)
