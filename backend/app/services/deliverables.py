"""应交项：按项目结构生成"应该有的文件"的空位（PRD 第 3 节）。

- 按设备的文件种类 → 每台关联的启用设备一个
- 按模块分区的（不按设备）→ 每个启用的分区一个
- 其余 → 整个文件种类一个

可以反复执行：已有的不重复建；不再需要的标作废（不删除），重新需要时恢复。
内部要求的文件种类也生成应交项（内部同样要追踪），只是不进客户 MDL。

初始状态只在新生成时设定，不覆盖已有状态：取 MDL 状态文字（分区或文件种类上的），
经状态映射得出，只采用"明确"的；否则为"未提交"。

客户要求被标为"不接受"的文件种类：应交项一律为"不适用"，依据写拒绝的依据；
改回接受 / 待确认时，这些应交项回到"未提交"。
"""

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Acceptance,
    ChangeAction,
    Deliverable,
    DeliverableStatus,
    DocumentType,
    DocumentTypeEquipment,
    DocumentTypeModule,
    Equipment,
    Milestone,
    Module,
    ModuleEquipmentType,
    Responsible,
)
from app.services.crud import get_in_project, update
from app.services.errors import Conflict, Invalid
from app.services.history import Actor, emit, record_summary
from app.services.projects import get_writable_project
from app.services.status_map import list_rules, map_status

STATUS_BASIS_FROM_MDL = "来自 MDL 状态文字"
DECLINED_BASIS = "客户要求不接受"

# 应交项的键：(文件种类, 设备, 模块)
Key = tuple[int, int | None, int | None]


@dataclass
class Result:
    created: int = 0
    obsoleted: int = 0
    restored: int = 0
    active: int = 0
    obsolete: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.created or self.obsoleted or self.restored)


def _desired(session: Session, project_id: int) -> dict[Key, dict[str, Any]]:
    """算出应有的全部应交项：键 → {文件种类, 用来定初始状态的 MDL 状态文字}。"""
    active_milestones = set(session.scalars(
        select(Milestone.id).where(Milestone.project_id == project_id, Milestone.active)))
    doc_types = [d for d in session.scalars(
        select(DocumentType).where(DocumentType.project_id == project_id, DocumentType.active)
    ) if d.milestone_id in active_milestones]
    doc_ids = [d.id for d in doc_types]
    active_modules = set(session.scalars(
        select(Module.id).where(Module.project_id == project_id, Module.active)))

    partitions: dict[int, dict[int, DocumentTypeModule]] = defaultdict(dict)
    links: dict[int, list[int]] = defaultdict(list)
    equipment_type: dict[int, int | None] = {}
    if doc_ids:
        for p in session.scalars(select(DocumentTypeModule).where(
                DocumentTypeModule.document_type_id.in_(doc_ids), DocumentTypeModule.active)):
            if p.module_id in active_modules:
                partitions[p.document_type_id][p.module_id] = p
        for doc_id, eq_id, eq_type in session.execute(
            select(DocumentTypeEquipment.document_type_id, Equipment.id,
                   Equipment.equipment_type_id)
            .join(Equipment, Equipment.id == DocumentTypeEquipment.equipment_id)
            .where(DocumentTypeEquipment.document_type_id.in_(doc_ids),
                   DocumentTypeEquipment.active, Equipment.active)
        ):
            links[doc_id].append(eq_id)
            equipment_type[eq_id] = eq_type

    # 设备种类 → 对应的模块（第 7 步配置；没配置时为空）
    type_modules: dict[int, list[int]] = defaultdict(list)
    for module_id, type_id in session.execute(
        select(ModuleEquipmentType.module_id, ModuleEquipmentType.equipment_type_id)
        .where(ModuleEquipmentType.active, ModuleEquipmentType.module_id.in_(active_modules))
    ):
        type_modules[type_id].append(module_id)

    desired: dict[Key, dict[str, Any]] = {}
    for d in doc_types:
        if d.per_equipment:
            for eq_id in links[d.id]:
                # 设备所在模块的分区状态文字优先，没有再用文件种类那一行的
                modules = type_modules.get(equipment_type[eq_id], [])
                text = next((partitions[d.id][m].mdl_status_text for m in modules
                             if m in partitions[d.id] and partitions[d.id][m].mdl_status_text),
                            d.mdl_status_text)
                desired[(d.id, eq_id, None)] = {"doc": d, "text": text}
        elif d.per_module and partitions[d.id]:
            for module_id, p in partitions[d.id].items():
                desired[(d.id, None, module_id)] = {"doc": d, "text": p.mdl_status_text}
        else:
            desired[(d.id, None, None)] = {"doc": d, "text": d.mdl_status_text}
    return desired


def _new_deliverable(project_id: int, key: Key, info: dict[str, Any], rules) -> Deliverable:
    doc: DocumentType = info["doc"]
    deliverable = Deliverable(project_id=project_id, document_type_id=key[0],
                              equipment_id=key[1], module_id=key[2])
    if doc.acceptance is Acceptance.DECLINED:
        deliverable.status = DeliverableStatus.NOT_APPLICABLE
        deliverable.status_basis = f"{DECLINED_BASIS}：{doc.acceptance_ref}"
        return deliverable
    result = map_status(rules, info["text"])
    if result is not None and result.definite:
        deliverable.status = result.status
        deliverable.responsible = result.responsible or Responsible.NONE
        deliverable.status_basis = f"{STATUS_BASIS_FROM_MDL}：{result.text}"
        # 状态真实发生的日期 MDL 里没有，留空；提交日期用 MDL 上的
        if result.status not in (DeliverableStatus.NOT_SUBMITTED, DeliverableStatus.NOT_APPLICABLE):
            deliverable.submitted_date = doc.mdl_submitted_date
    return deliverable


def ensure_deliverables(session: Session, project_id: int, actor: Actor, *,
                        reason: str | None = None) -> Result:
    """按当前结构生成 / 作废 / 恢复应交项，返回统计。有变化时写一条汇总变更记录并发事件。"""
    get_writable_project(session, project_id)
    desired = _desired(session, project_id)
    existing = {(x.document_type_id, x.equipment_id, x.module_id): x
                for x in session.scalars(
                    select(Deliverable).where(Deliverable.project_id == project_id))}

    result = Result()
    rules = None
    for key, info in desired.items():
        current = existing.get(key)
        if current is None:
            if rules is None:
                rules = list_rules(session, project_id)
            session.add(_new_deliverable(project_id, key, info, rules))
            result.created += 1
        elif current.obsolete:
            current.obsolete = False
            result.restored += 1
    for key, current in existing.items():
        if key not in desired and not current.obsolete:
            current.obsolete = True  # 不删除：可能挂过文件、被基线引用
            result.obsoleted += 1
    session.flush()
    _sync_acceptance(session, project_id, desired, existing, actor)

    counts = dict(session.execute(
        select(Deliverable.obsolete, func.count()).where(Deliverable.project_id == project_id)
        .group_by(Deliverable.obsolete)
    ).all())
    result.active, result.obsolete = counts.get(False, 0), counts.get(True, 0)

    if result.changed:
        changes = {k: [None, v] for k, v in asdict(result).items() if v}
        record_summary(session, project_id=project_id, entity_type="deliverables",
                       entity_id=f"project:{project_id}", action=ChangeAction.UPDATE,
                       changes=changes, actor=actor, reason=reason or "按项目结构重算应交项")
        emit(session, "deliverables.changed", payload=asdict(result), project_id=project_id)
    return result


def _sync_acceptance(session: Session, project_id: int, desired: dict[Key, dict[str, Any]],
                     existing: dict[Key, Deliverable], actor: Actor) -> None:
    """不接受的客户要求 → 应交项改为不适用；不再是"不接受"的 → 由此改成的回到未提交。"""
    for key, info in desired.items():
        d = existing.get(key)
        if d is None:
            continue  # 新生成的已在 _new_deliverable 里处理
        doc: DocumentType = info["doc"]
        declined = doc.acceptance is Acceptance.DECLINED
        basis = f"{DECLINED_BASIS}：{doc.acceptance_ref}"
        by_decline = (d.status is DeliverableStatus.NOT_APPLICABLE
                      and (d.status_basis or "").startswith(DECLINED_BASIS))
        if declined and (d.status is not DeliverableStatus.NOT_APPLICABLE or d.status_basis != basis):
            update_deliverable(session, project_id, d.id,
                               {"status": DeliverableStatus.NOT_APPLICABLE, "status_basis": basis},
                               actor, reason=basis)
        elif not declined and by_decline:
            update_deliverable(session, project_id, d.id,
                               {"status": DeliverableStatus.NOT_SUBMITTED, "status_basis": None},
                               actor, reason="客户要求改为接受 / 待确认")


def kind_of(deliverable: Deliverable) -> str:
    """应交项类型：按设备 / 按模块 / 整个种类。"""
    if deliverable.equipment_id is not None:
        return "per_equipment"
    if deliverable.module_id is not None:
        return "per_module"
    return "whole"


# ---------------------------------------------------------------- 截止日期、逾期（矩阵和 MDL 导出共用）


def effective_due(deliverable_due: date | None, type_due: date | None,
                  milestone_due: date | None) -> tuple[date | None, str | None]:
    """实际截止日期及其来源：应交项自己的 → 文件种类的 → 里程碑的。"""
    for value, source in ((deliverable_due, "deliverable"), (type_due, "document_type"),
                          (milestone_due, "milestone")):
        if value is not None:
            return value, source
    return None, None


def is_overdue(status: DeliverableStatus, due: date | None, today: date) -> bool:
    """逾期：截止日已过，且还没提交。"""
    return status is DeliverableStatus.NOT_SUBMITTED and due is not None and due < today


# 已完成（不再待办）的状态；完成率的分母不含不适用
DONE_STATUSES = (DeliverableStatus.APPROVED, DeliverableStatus.NOT_APPLICABLE)


def is_outstanding(status: DeliverableStatus, received: bool) -> bool:
    """缺件：还要我方或供应商交东西的（2026-10-27 用户确认）。

    不算不适用的；还没收到文件且没批准的，或客户要求修改重交的（要交新文件）。
    收到了文件但还没提交给客户的不算缺件（文件已经在手上）。
    """
    if status is DeliverableStatus.NOT_APPLICABLE:
        return False
    return status is DeliverableStatus.REVISE_RESUBMIT or (not received and status is not DeliverableStatus.APPROVED)


# ---------------------------------------------------------------- 修改单个应交项

DELIVERABLE_FIELDS = ("status", "responsible", "due_date", "submitted_date", "status_date",
                      "status_basis", "remarks")

# 改状态时没指定责任方，按新状态带出（未提交、已提交时保持原样）
DEFAULT_RESPONSIBLE = {
    DeliverableStatus.UNDER_REVIEW: Responsible.CLIENT,
    DeliverableStatus.REVISE_RESUBMIT: Responsible.OURS,
    DeliverableStatus.APPROVED: Responsible.NONE,
    DeliverableStatus.NOT_APPLICABLE: Responsible.NONE,
}


def update_deliverable(session: Session, project_id: int, deliverable_id: int,
                       changes: dict[str, Any], actor: Actor, *, reason: str | None = None
                       ) -> Deliverable:
    """修改一个应交项（状态、责任方、截止日、提交日期、状态日期、状态依据、内部备注）。

    状态变了时：没指定责任方就按新状态带出；没指定状态日期就清空（旧日期属于旧状态，
    不知道真实日期就空着）；没指定状态依据就用 reason。写变更记录，并发事件
    deliverable.status_changed。
    """
    get_writable_project(session, project_id)
    deliverable = get_in_project(session, Deliverable, deliverable_id, project_id, "deliverable")
    if deliverable.obsolete:
        raise Conflict("deliverable.obsolete", f"应交项 {deliverable_id} 已作废，不能修改",
                       id=deliverable_id)
    changes = {k: v for k, v in changes.items() if k in DELIVERABLE_FIELDS}
    try:
        if changes.get("status") is not None:
            changes["status"] = DeliverableStatus(changes["status"])
        if changes.get("responsible") is not None:
            changes["responsible"] = Responsible(changes["responsible"])
    except ValueError as exc:
        raise Invalid("deliverable.bad_value", f"取值不合法：{exc}") from exc
    if "status" in changes and changes["status"] is None:
        changes.pop("status")
    if "responsible" in changes and changes["responsible"] is None:
        changes.pop("responsible")

    old_status = deliverable.status
    status_changed = "status" in changes and changes["status"] != old_status
    if status_changed:
        if "responsible" not in changes and changes["status"] in DEFAULT_RESPONSIBLE:
            changes["responsible"] = DEFAULT_RESPONSIBLE[changes["status"]]
        changes.setdefault("status_date", None)
        changes.setdefault("status_basis", reason)
    changes = {k: v for k, v in changes.items() if getattr(deliverable, k) != v}
    if not changes:
        return deliverable
    update(session, deliverable, changes, actor, reason, project_id)
    if status_changed:
        emit(session, "deliverable.status_changed", deliverable,
             {"from": old_status.value, "to": deliverable.status.value,
              "status_date": deliverable.status_date.isoformat() if deliverable.status_date else None},
             project_id=project_id)
    return deliverable


def bulk_update(session: Session, project_id: int, deliverable_ids: list[int],
                changes: dict[str, Any], actor: Actor, *, reason: str | None = None
                ) -> dict[str, int]:
    """批量修改：同一组改动用到每个应交项上，每个单独留痕。

    先检查全部应交项（存在、属于本项目、未作废），再在保存点里逐个修改；
    任何一个出错都整体撤回，保证要么全改、要么全不改。
    """
    get_writable_project(session, project_id)
    ids = list(dict.fromkeys(deliverable_ids))
    if not ids:
        raise Invalid("deliverable.bulk_empty", "没有选择应交项")
    for deliverable_id in ids:
        d = get_in_project(session, Deliverable, deliverable_id, project_id, "deliverable")
        if d.obsolete:
            raise Conflict("deliverable.obsolete", f"应交项 {deliverable_id} 已作废，不能修改",
                           id=deliverable_id)
    updated = 0
    with session.begin_nested():
        for deliverable_id in ids:
            d = session.get(Deliverable, deliverable_id)
            before = {k: getattr(d, k) for k in DELIVERABLE_FIELDS}
            update_deliverable(session, project_id, deliverable_id, dict(changes), actor,
                               reason=reason)
            updated += before != {k: getattr(d, k) for k in DELIVERABLE_FIELDS}
    return {"updated": updated, "unchanged": len(ids) - updated}
