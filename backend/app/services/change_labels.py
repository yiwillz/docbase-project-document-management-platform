"""变更记录的"对象名称"：把 表名 + 编号 换成人看得懂的称呼，如"1.3 Equipment datasheet / 72"。

一页记录按表分组，每张表一次查询，不逐条查。认不出的（汇总记录、已删除的对象）返回空。
"""

from collections import defaultdict
from collections.abc import Callable, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Baseline,
    ChangeLog,
    Deliverable,
    DocumentType,
    DocumentTypeEquipment,
    DocumentTypeModule,
    Equipment,
    EquipmentType,
    EquipmentUnit,
    File,
    ImportBatch,
    MdlVersion,
    Milestone,
    Module,
    ModuleEquipmentType,
    Project,
    ProjectMember,
    ReviewCode,
    StatusMapping,
    Transmittal,
    TransmittalItem,
    User,
    Version,
)


def _equipment(e: Equipment | None) -> str:
    return (e.tag or e.name) if e else "?"


def _doc(d: DocumentType | None) -> str:
    return f"{d.submittal_no} {d.name}" if d else "?"


def _get(session: Session, model, ids: Iterable[int]) -> dict[int, object]:
    ids = set(ids)
    return {o.id: o for o in session.scalars(select(model).where(model.id.in_(ids)))} if ids else {}


def _deliverables(session: Session, rows: list[Deliverable]) -> dict[int, str]:
    docs = _get(session, DocumentType, {r.document_type_id for r in rows})
    equipment = _get(session, Equipment, {r.equipment_id for r in rows if r.equipment_id})
    modules = _get(session, Module, {r.module_id for r in rows if r.module_id})
    out = {}
    for r in rows:
        where = _equipment(equipment.get(r.equipment_id)) if r.equipment_id else (
            modules[r.module_id].name if r.module_id in modules else None)
        out[r.id] = _doc(docs.get(r.document_type_id)) + (f" / {where}" if where else "")
    return out


# 表名 → (模型, 怎么从一批对象得出名称)
LABELERS: dict[str, tuple[type, Callable[[Session, list], dict[int, str]]]] = {
    "projects": (Project, lambda s, rows: {r.id: r.code for r in rows}),
    "milestones": (Milestone, lambda s, rows: {r.id: f"{r.code} {r.name or ''}".strip() for r in rows}),
    "modules": (Module, lambda s, rows: {r.id: r.name for r in rows}),
    "document_types": (DocumentType, lambda s, rows: {r.id: _doc(r) for r in rows}),
    "equipment": (Equipment, lambda s, rows: {r.id: _equipment(r) if r.tag is None else f"{r.tag} {r.name}"
                                              for r in rows}),
    "equipment_types": (EquipmentType, lambda s, rows: {r.id: r.name for r in rows}),
    "transmittals": (Transmittal, lambda s, rows: {r.id: r.number for r in rows}),
    "review_codes": (ReviewCode, lambda s, rows: {r.id: f"{r.code} {r.label_en}" for r in rows}),
    "status_mappings": (StatusMapping, lambda s, rows: {r.id: r.pattern for r in rows}),
    "mdl_versions": (MdlVersion, lambda s, rows: {r.id: f"Rev.{r.rev_no}" for r in rows}),
    "deliverables": (Deliverable, _deliverables),
    "document_type_modules": (DocumentTypeModule, lambda s, rows: {
        r.id: f"{_doc(d)} / {m.name if m else '?'}"
        for r in rows
        for d, m in [(s.get(DocumentType, r.document_type_id), s.get(Module, r.module_id))]}),
    "document_type_equipment": (DocumentTypeEquipment, lambda s, rows: {
        r.id: f"{_doc(s.get(DocumentType, r.document_type_id))} / {_equipment(s.get(Equipment, r.equipment_id))}"
        for r in rows}),
    "equipment_units": (EquipmentUnit, lambda s, rows: {
        r.id: f"{_equipment(s.get(Equipment, r.equipment_id))} #{r.seq}" for r in rows}),
    "transmittal_items": (TransmittalItem, lambda s, rows: {
        r.id: f"{t.number if (t := s.get(Transmittal, r.transmittal_id)) else '?'} / "
              f"{_deliverables(s, [d])[d.id] if (d := s.get(Deliverable, r.deliverable_id)) else '?'}"
        for r in rows}),
    "files": (File, lambda s, rows: {r.id: r.title or f"#{r.id}" for r in rows}),
    "baselines": (Baseline, lambda s, rows: {r.id: r.name for r in rows}),
    "import_batches": (ImportBatch, lambda s, rows: {r.id: f"#{r.id} {r.source_dir}" for r in rows}),
    "versions": (Version, lambda s, rows: {r.id: f"{r.filename}" for r in rows}),
    "module_equipment_types": (ModuleEquipmentType, lambda s, rows: {
        r.id: f"{m.name if (m := s.get(Module, r.module_id)) else '?'} / "
              f"{t.name if (t := s.get(EquipmentType, r.equipment_type_id)) else '?'}" for r in rows}),
    "project_members": (ProjectMember, lambda s, rows: {
        r.id: u.username if (u := s.get(User, r.user_id)) else f"#{r.user_id}" for r in rows}),
    "users": (User, lambda s, rows: {r.id: r.username for r in rows}),
}


def summary_kind(log: ChangeLog) -> str | None:
    """汇总式记录（不对应单个对象）是哪一种：重算应交项、清理回收区、整张传送单录回复；其他返回 None。"""
    if log.entity_type == "deliverables" and log.entity_id.startswith("project:"):
        return "recalculate"
    if log.entity_type == "versions" and log.entity_id == "purge":
        return "purge"
    if log.entity_type == "transmittal_items" and log.entity_id.startswith("transmittal:"):
        return "transmittal_reply"
    return None


def labels(session: Session, logs: Iterable[ChangeLog]) -> dict[int, str | None]:
    """每条变更记录的对象名称（记录 id → 名称）。"""
    logs = list(logs)
    wanted: dict[str, set[int]] = defaultdict(set)
    for log in logs:
        if log.entity_type in LABELERS and log.entity_id.isdigit():
            wanted[log.entity_type].add(int(log.entity_id))
    names: dict[tuple[str, int], str] = {}
    for table, ids in wanted.items():
        model, labeler = LABELERS[table]
        rows = list(session.scalars(select(model).where(model.id.in_(ids))))
        for obj_id, name in labeler(session, rows).items():
            names[(table, obj_id)] = name
    # 整张传送单录回复的汇总记录：名称是传送单号
    tr_ids = {int(log.entity_id.split(":", 1)[1]) for log in logs
              if log.entity_type == "transmittal_items" and log.entity_id.startswith("transmittal:")
              and log.entity_id.split(":", 1)[1].isdigit()}
    numbers = {t.id: t.number for t in session.scalars(select(Transmittal).where(Transmittal.id.in_(tr_ids)))} \
        if tr_ids else {}

    def name(log: ChangeLog) -> str | None:
        if log.entity_id.isdigit():
            return names.get((log.entity_type, int(log.entity_id)))
        if log.entity_id.startswith("transmittal:") and log.entity_id.split(":", 1)[1].isdigit():
            return numbers.get(int(log.entity_id.split(":", 1)[1]))
        return None

    return {log.id: name(log) for log in logs}
