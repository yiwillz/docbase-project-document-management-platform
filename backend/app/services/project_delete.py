"""删除空项目（2026-10-27 用户确认）：只给系统管理员，只能删"空"项目，要输入项目编号确认。

空项目 = 没有文件、没有传送单、没发过 MDL、没有阶段基线、没做过存量导入。有这些的就有了对外凭证和历史，
只能归档。删除时把项目下的结构、设备、应交项、成员、配置、变更记录、事件、任务一起删掉，
另写一条不挂项目的变更记录（删除前的项目信息和各类数量），所以"删过什么项目"仍然查得到。
"""

from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import (
    Baseline,
    ChangeAction,
    ChangeLog,
    Deliverable,
    DocumentType,
    DocumentTypeEquipment,
    DocumentTypeModule,
    Equipment,
    EquipmentType,
    EquipmentUnit,
    Event,
    File,
    ImportBatch,
    Job,
    MdlVersion,
    Milestone,
    Module,
    ModuleEquipmentType,
    ProjectMember,
    ReviewCode,
    StatusMapping,
    Transmittal,
)
from app.services.errors import Conflict, Invalid
from app.services.history import Actor, record_summary, snapshot
from app.services.projects import get_project

# 有这些就不是空项目（表名 → 说明，报错时告诉用户为什么不能删）
BLOCKERS = (
    (File, "files", "文件"),
    (Transmittal, "transmittals", "传送单"),
    (MdlVersion, "mdl_versions", "发给客户的 MDL 版本"),
    (Baseline, "baselines", "阶段基线"),
    (ImportBatch, "import_batches", "存量导入批次"),
)


def _count(session: Session, model, project_id: int) -> int:
    return session.scalar(select(func.count()).select_from(model).where(model.project_id == project_id)) or 0


def blockers(session: Session, project_id: int) -> dict[str, int]:
    """不能删的原因：{表名: 数量}，空的表示可以删。"""
    return {key: n for model, key, _ in BLOCKERS if (n := _count(session, model, project_id))}


def delete_project(session: Session, project_id: int, actor: Actor, *, confirm_code: str,
                   reason: str | None = None) -> dict[str, Any]:
    project = get_project(session, project_id)
    if (confirm_code or "").strip() != project.code:
        raise Invalid("project.delete_confirm", f"请输入项目编号 {project.code} 确认删除", code=project.code)
    found = blockers(session, project_id)
    if found:
        names = {key: label for _, key, label in BLOCKERS}
        what = "、".join(f"{names[k]} {n}" for k, n in found.items())
        raise Conflict("project.not_empty", f"项目 {project.code} 已有{what}，不能删除，只能归档",
                       code=project.code, **found)

    pid = project.id
    doc_ids = select(DocumentType.id).where(DocumentType.project_id == pid)
    module_ids = select(Module.id).where(Module.project_id == pid)
    equipment_ids = select(Equipment.id).where(Equipment.project_id == pid)
    counts = {
        "milestones": _count(session, Milestone, pid), "document_types": _count(session, DocumentType, pid),
        "equipment": _count(session, Equipment, pid), "deliverables": _count(session, Deliverable, pid),
        "members": _count(session, ProjectMember, pid),
    }
    before = snapshot(project)
    # 按依赖关系从下往上删
    for stmt in (
        delete(Deliverable).where(Deliverable.project_id == pid),
        delete(DocumentTypeEquipment).where(DocumentTypeEquipment.document_type_id.in_(doc_ids)),
        delete(DocumentTypeModule).where(DocumentTypeModule.document_type_id.in_(doc_ids)),
        delete(ModuleEquipmentType).where(ModuleEquipmentType.module_id.in_(module_ids)),
        delete(EquipmentUnit).where(EquipmentUnit.equipment_id.in_(equipment_ids)),
        delete(Equipment).where(Equipment.project_id == pid),
        delete(EquipmentType).where(EquipmentType.project_id == pid),
        delete(DocumentType).where(DocumentType.project_id == pid),
        delete(Milestone).where(Milestone.project_id == pid),
        delete(Module).where(Module.project_id == pid),
        delete(ReviewCode).where(ReviewCode.project_id == pid),
        delete(StatusMapping).where(StatusMapping.project_id == pid),
        delete(ProjectMember).where(ProjectMember.project_id == pid),
        delete(Job).where(Job.project_id == pid),
        delete(Event).where(Event.project_id == pid),
        delete(ChangeLog).where(ChangeLog.project_id == pid),
    ):
        session.execute(stmt)
    session.delete(project)
    session.flush()
    # 不挂项目的一条记录：删了什么项目、删之前有多少东西
    record_summary(session, project_id=None, entity_type="projects", entity_id=str(pid),
                   action=ChangeAction.DELETE,
                   changes={**{k: [v, None] for k, v in before.items()},
                            **{f"count.{k}": [v, 0] for k, v in counts.items()}},
                   actor=actor, reason=reason or "删除空项目")
    return {"deleted": pid, "code": before.get("code"), "counts": counts}
