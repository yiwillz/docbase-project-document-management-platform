"""项目结构：里程碑、模块、文件种类、模块分区、文件种类对应的设备（PRD F2）。

编号规则：
- 编号全部由平台自动生成，按文本存：里程碑 M1、M2…（编号 1、2…），
  文件种类"里程碑编号.序号"（3.1 … 3.9、3.10）
- 新建默认排在最后；也可以插到某个位置，后面的依次往后挪
- 删除（真删，只有没挂文件时才行）后，后面的编号依次往前补
- 编号变动会牵连到的文件种类里只要有一个已经挂了文件，就不再自动变动：
  插入 / 调整顺序会被拒绝；删除照常进行，但留下空号
- 停用不改变编号（停用的仍占着自己的号）
- 里程碑可以并行，不强制先后顺序
"""

from collections.abc import Iterable
from datetime import date
from typing import Any, Literal

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.models import (
    Acceptance,
    BaselineItem,
    ChangeAction,
    Deliverable,
    DocumentType,
    DocumentTypeEquipment,
    DocumentTypeModule,
    DocumentTypeOrigin,
    Equipment,
    EquipmentType,
    FileDeliverable,
    MdlVersion,
    Milestone,
    Module,
    ModuleEquipmentType,
    Project,
)
from app.services.crud import clean as _clean
from app.services.crud import create as _create
from app.services.crud import delete as _delete
from app.services.crud import get_in_project as _get_in_project
from app.services.crud import pick as _pick
from app.services.crud import set_active as _set_active
from app.services.crud import update as _update
from app.services.errors import Conflict, Invalid, NotFound
from app.services.history import Actor, record, snapshot
from app.services.projects import get_project, get_writable_project

# 未配置时里程碑代码的前缀：M1、M2…
DEFAULT_MILESTONE_PREFIX = "M"

MILESTONE_FIELDS = ("name", "description", "goal", "remarks", "due_date")
MODULE_FIELDS = ("name",)
DOCUMENT_TYPE_FIELDS = (
    "name",
    "category",
    "per_equipment",
    "due_date",
    "content",
    "description",
    "purpose",
    "mdl_status_text",
    "mdl_remarks",
    "mdl_submitted_date",
    "origin",
    "required_date",
    "basis",
    "changed_date",
    "change_ref",
    "in_client_mdl",
    "acceptance",
    "acceptance_ref",
    "variation_ref",
    "keywords",
)
# 复制结构时带过去的文件种类字段：只复制定义，不复制进度和日程
_COPY_DOCUMENT_TYPE_FIELDS = (
    "submittal_no",
    "name",
    "category",
    "per_equipment",
    "per_module",
    "content",
    "description",
    "purpose",
    "in_client_mdl",
    "keywords",
    "sort_order",
)


# ---------------------------------------------------------------- 编号


def _milestone_prefix(project: Project) -> str:
    return (project.numbering_rules or {}).get("milestone_code_prefix", DEFAULT_MILESTONE_PREFIX)


def _seq(doc_type: DocumentType) -> int:
    return int(doc_type.submittal_no.rsplit(".", 1)[1])


def _doc_types_with_files(session: Session, document_type_ids: Iterable[int]) -> set[int]:
    """这些文件种类里，哪些已经挂了文件。"""
    ids = list(document_type_ids)
    if not ids:
        return set()
    return set(session.scalars(
        select(Deliverable.document_type_id).distinct()
        .join(FileDeliverable, FileDeliverable.deliverable_id == Deliverable.id)
        .where(Deliverable.document_type_id.in_(ids))
    ))


def has_files(session: Session, document_type_id: int) -> bool:
    return bool(_doc_types_with_files(session, [document_type_id]))


def _all_milestones(session: Session, project_id: int) -> list[Milestone]:
    """项目全部里程碑（含停用的），按编号排序。"""
    return sorted(session.scalars(select(Milestone).where(Milestone.project_id == project_id)),
                  key=lambda m: int(m.number))


def _all_document_types(session: Session, milestone_id: int) -> list[DocumentType]:
    """里程碑下全部文件种类（含停用的），按序号排序。"""
    return sorted(
        session.scalars(select(DocumentType).where(DocumentType.milestone_id == milestone_id)),
        key=_seq,
    )


def _renumber(
    session: Session,
    project: Project,
    milestones: dict[Milestone, int],
    document_types: dict[DocumentType, tuple[int, int]],
    actor: Actor,
    reason: str | None,
    *,
    strict: bool,
) -> bool:
    """改编号。milestones：里程碑 → 新编号；document_types：种类 → (里程碑编号, 新序号)。

    只处理真正有变化的。牵连到的种类里有挂了文件的：strict 时拒绝，否则不改、返回 False。
    分两轮写（先写临时值再写最终值），避免中途撞上"编号不能重复"的约束。
    """
    prefix = _milestone_prefix(project)
    ms_changes = [(m, str(n)) for m, n in milestones.items() if m.number != str(n)]
    dt_changes = [(d, f"{ms}.{seq}") for d, (ms, seq) in document_types.items()
                  if d.submittal_no != f"{ms}.{seq}"]
    if not ms_changes and not dt_changes:
        return True

    locked = _doc_types_with_files(session, [d.id for d, _ in dt_changes])
    if locked:
        if not strict:
            return False
        numbers = sorted(d.submittal_no for d, _ in dt_changes if d.id in locked)
        raise Conflict(
            "structure.numbers_locked",
            f"以下文件种类已有文件，编号不能再变动：{', '.join(numbers)}",
            submittal_nos=numbers,
        )

    befores = [(obj, snapshot(obj)) for obj, _ in ms_changes + dt_changes]
    for m, _ in ms_changes:
        m.number = m.code = f"~{m.id}"
    for d, _ in dt_changes:
        d.submittal_no = f"~{d.id}"
    session.flush()
    for m, number in ms_changes:
        m.number, m.code, m.sort_order = number, f"{prefix}{number}", int(number)
    for d, number in dt_changes:
        d.submittal_no = number
        d.sort_order = _seq(d)
    session.flush()
    for obj, before in befores:
        record(session, obj, ChangeAction.UPDATE, before, snapshot(obj), actor,
               reason=reason, project_id=project.id)
    return True


def _shift_milestones(session: Session, project: Project, from_number: int, delta: int,
                      actor: Actor, reason: str | None, *, strict: bool) -> bool:
    """编号 ≥ from_number 的里程碑整体移动 delta，下面的文件种类前缀跟着变。"""
    ms_map: dict[Milestone, int] = {}
    dt_map: dict[DocumentType, tuple[int, int]] = {}
    for m in _all_milestones(session, project.id):
        if int(m.number) >= from_number:
            ms_map[m] = int(m.number) + delta
            for d in _all_document_types(session, m.id):
                dt_map[d] = (int(m.number) + delta, _seq(d))
    return _renumber(session, project, ms_map, dt_map, actor, reason, strict=strict)


def _shift_document_types(session: Session, project: Project, milestone: Milestone,
                          from_seq: int, delta: int, actor: Actor, reason: str | None,
                          *, strict: bool) -> bool:
    """里程碑下序号 ≥ from_seq 的文件种类整体移动 delta。"""
    dt_map = {d: (int(milestone.number), _seq(d) + delta)
              for d in _all_document_types(session, milestone.id) if _seq(d) >= from_seq}
    return _renumber(session, project, {}, dt_map, actor, reason, strict=strict)


def _insert_position(position: int | None, last: int) -> int:
    """要插到的编号：不给或超出末尾 → 排在最后。"""
    if position is None or position > last:
        return last + 1
    if position < 1:
        raise Invalid("structure.bad_position", "位置从 1 开始", position=position)
    return position


# ---------------------------------------------------------------- 里程碑


def list_milestones(session: Session, project_id: int, *, include_inactive: bool = False
                    ) -> list[Milestone]:
    get_project(session, project_id)
    return [m for m in _all_milestones(session, project_id) if include_inactive or m.active]


def create_milestone(session: Session, project_id: int, data: dict[str, Any], actor: Actor,
                     *, position: int | None = None) -> Milestone:
    """新建里程碑，编号自动生成（默认排在最后；给 position 则插到该位置，后面的往后挪）。"""
    project = get_writable_project(session, project_id)
    data = _pick(data, MILESTONE_FIELDS)
    for f in ("name", "description", "goal", "remarks"):
        _clean(data, f)
    existing = _all_milestones(session, project_id)
    number = _insert_position(position, int(existing[-1].number) if existing else 0)
    if existing and number <= int(existing[-1].number):
        _shift_milestones(session, project, number, 1, actor, None, strict=True)
    milestone = Milestone(project_id=project_id, number=str(number),
                          code=f"{_milestone_prefix(project)}{number}", sort_order=number, **data)
    return _create(session, milestone, actor, project_id)


def update_milestone(session: Session, project_id: int, milestone_id: int,
                     changes: dict[str, Any], actor: Actor, *, reason: str | None = None
                     ) -> Milestone:
    """修改名称、说明、目标、备注、截止日期（编号由平台管理，不能直接改）。"""
    get_writable_project(session, project_id)
    milestone = _get_in_project(session, Milestone, milestone_id, project_id, "milestone")
    changes = _pick(changes, MILESTONE_FIELDS)
    for f in ("name", "description", "goal", "remarks"):
        _clean(changes, f)
    return _update(session, milestone, changes, actor, reason, project_id)


def set_milestone_active(session: Session, project_id: int, milestone_id: int, active: bool,
                         actor: Actor, *, reason: str | None = None) -> Milestone:
    get_writable_project(session, project_id)
    milestone = _get_in_project(session, Milestone, milestone_id, project_id, "milestone")
    if not active and any(d.active for d in _all_document_types(session, milestone.id)):
        raise Conflict("milestone.has_document_types",
                       f"里程碑 {milestone.code} 下还有启用的文件种类，请先停用它们",
                       milestone_id=milestone.id)
    return _set_active(session, milestone, active, actor, reason, project_id)


def delete_milestone(session: Session, project_id: int, milestone_id: int, actor: Actor,
                     *, reason: str | None = None) -> None:
    """删除里程碑（下面必须没有任何文件种类）；后面的里程碑编号往前补。"""
    project = get_writable_project(session, project_id)
    milestone = _get_in_project(session, Milestone, milestone_id, project_id, "milestone")
    if _all_document_types(session, milestone.id):
        raise Conflict("milestone.not_empty",
                       f"里程碑 {milestone.code} 下还有文件种类，请先删除它们",
                       milestone_id=milestone.id)
    number = int(milestone.number)
    _delete(session, milestone, actor, project_id, reason)
    _shift_milestones(session, project, number + 1, -1, actor, reason, strict=False)


def reorder_milestones(session: Session, project_id: int, ids: list[int], actor: Actor) -> None:
    """按新顺序重排全部里程碑，编号随之变为 1、2、3…（下面的文件种类前缀跟着变）。"""
    project = get_writable_project(session, project_id)
    current = _all_milestones(session, project_id)
    if sorted(ids) != sorted(m.id for m in current) or len(set(ids)) != len(ids):
        raise Invalid("structure.reorder_incomplete", "调整顺序时要给出全部里程碑（含停用的）")
    by_id = {m.id: m for m in current}
    ms_map = {by_id[i]: n for n, i in enumerate(ids, start=1)}
    dt_map = {d: (n, _seq(d)) for m, n in ms_map.items()
              for d in _all_document_types(session, m.id)}
    _renumber(session, project, ms_map, dt_map, actor, None, strict=True)


# ---------------------------------------------------------------- 模块


def list_modules(session: Session, project_id: int, *, include_inactive: bool = False
                 ) -> list[Module]:
    get_project(session, project_id)
    stmt = select(Module).where(Module.project_id == project_id)
    if not include_inactive:
        stmt = stmt.where(Module.active)
    return list(session.scalars(stmt.order_by(Module.sort_order, Module.id)))


def _module_by_name(session: Session, project_id: int, name: str) -> Module | None:
    return session.scalar(select(Module).where(Module.project_id == project_id,
                                               Module.name == name))


def _next_module_order(session: Session, project_id: int) -> int:
    orders = session.scalars(select(Module.sort_order).where(Module.project_id == project_id))
    return max(orders, default=0) + 1


def create_module(session: Session, project_id: int, data: dict[str, Any], actor: Actor
                  ) -> Module:
    get_writable_project(session, project_id)
    data = _pick(data, MODULE_FIELDS)
    _clean(data, "name", required=True)
    if _module_by_name(session, project_id, data["name"]) is not None:
        raise Conflict("module.name_taken", f"模块 {data['name']} 已存在", name=data["name"])
    module = Module(project_id=project_id,
                    sort_order=_next_module_order(session, project_id), **data)
    return _create(session, module, actor, project_id)


def update_module(session: Session, project_id: int, module_id: int, changes: dict[str, Any],
                  actor: Actor, *, reason: str | None = None) -> Module:
    get_writable_project(session, project_id)
    module = _get_in_project(session, Module, module_id, project_id, "module")
    changes = _pick(changes, MODULE_FIELDS)
    if "name" in changes:
        _clean(changes, "name", required=True)
        other = _module_by_name(session, project_id, changes["name"])
        if other is not None and other.id != module.id:
            raise Conflict("module.name_taken", f"模块 {changes['name']} 已存在",
                           name=changes["name"])
    return _update(session, module, changes, actor, reason, project_id)


def set_module_active(session: Session, project_id: int, module_id: int, active: bool,
                      actor: Actor, *, reason: str | None = None) -> Module:
    """停用模块时，它的分区保留不动；对应的应交项在第 5 步标作废。"""
    get_writable_project(session, project_id)
    module = _get_in_project(session, Module, module_id, project_id, "module")
    return _set_active(session, module, active, actor, reason, project_id)


def reorder_modules(session: Session, project_id: int, ids: list[int], actor: Actor) -> None:
    get_writable_project(session, project_id)
    for order, module_id in enumerate(dict.fromkeys(ids), start=1):
        module = _get_in_project(session, Module, module_id, project_id, "module")
        if module.sort_order != order:
            _update(session, module, {"sort_order": order}, actor, None, project_id)


def _resolve_modules(session: Session, project_id: int, module_ids: list[int],
                     module_names: list[str], actor: Actor) -> list[int]:
    """模块 id + 模块名 → 模块 id 列表。名字不存在就新建；同名的停用模块重新启用。"""
    result = []
    for module_id in module_ids:
        module = _get_in_project(session, Module, module_id, project_id, "module")
        if not module.active:
            raise Conflict("module.inactive", f"模块 {module.name} 已停用", module_id=module_id)
        result.append(module.id)
    for raw in module_names:
        name = (raw or "").strip()
        if not name:
            continue
        module = _module_by_name(session, project_id, name)
        if module is None:
            module = create_module(session, project_id, {"name": name}, actor)
        elif not module.active:
            _set_active(session, module, True, actor, None, project_id)
        result.append(module.id)
    return list(dict.fromkeys(result))


# ---------------------------------------------------------------- 文件种类


def list_document_types(session: Session, project_id: int, *, milestone_id: int | None = None,
                        include_inactive: bool = False) -> list[DocumentType]:
    get_project(session, project_id)
    stmt = select(DocumentType).where(DocumentType.project_id == project_id)
    if milestone_id is not None:
        stmt = stmt.where(DocumentType.milestone_id == milestone_id)
    if not include_inactive:
        stmt = stmt.where(DocumentType.active)
    milestone_order = {m.id: int(m.number) for m in _all_milestones(session, project_id)}
    return sorted(session.scalars(stmt), key=lambda d: (milestone_order[d.milestone_id], _seq(d)))


def _active_milestone(session: Session, project_id: int, milestone_id: int | None) -> Milestone:
    if milestone_id is None:
        raise Invalid("structure.field_required", "milestone_id 不能为空", field="milestone_id")
    milestone = _get_in_project(session, Milestone, milestone_id, project_id, "milestone")
    if not milestone.active:
        raise Conflict("milestone.inactive", f"里程碑 {milestone.code} 已停用",
                       milestone_id=milestone_id)
    return milestone


def _prepare_document_type(data: dict[str, Any], *, require_name: bool) -> dict[str, Any]:
    data = _pick(data, DOCUMENT_TYPE_FIELDS)
    _clean(data, "name", required=require_name or "name" in data)
    for f in ("category", "content", "description", "purpose", "mdl_status_text", "mdl_remarks",
              "basis", "change_ref", "acceptance_ref", "variation_ref"):
        _clean(data, f)
    for f in ("per_equipment", "in_client_mdl", "origin", "acceptance", "keywords"):
        if data.get(f) is None:
            data.pop(f, None)
    if "keywords" in data:
        # 去空白、去重（不分大小写），保持原写法
        seen, keywords = set(), []
        for k in data["keywords"]:
            k = str(k).strip()
            if k and k.casefold() not in seen:
                seen.add(k.casefold())
                keywords.append(k)
        data["keywords"] = keywords
    if "origin" in data:
        try:
            data["origin"] = DocumentTypeOrigin(data["origin"])
        except ValueError as exc:
            raise Invalid("document_type.bad_origin", f"要求类别不合法：{data['origin']}",
                          origin=data["origin"]) from exc
    if "acceptance" in data:
        try:
            data["acceptance"] = Acceptance(data["acceptance"])
        except ValueError as exc:
            raise Invalid("document_type.bad_acceptance", f"接受情况不合法：{data['acceptance']}",
                          acceptance=data["acceptance"]) from exc
    return data


def _apply_acceptance(data: dict[str, Any], origin: DocumentTypeOrigin, *,
                      current: Acceptance | None = None, current_ref: str | None = None) -> None:
    """接受情况只对"客户要求"有效：客户要求默认待确认；改成其他类别时清空。
    不接受必须写依据（拒绝的函件号等），应交项随之自动改为不适用（见 deliverables）。"""
    if origin is not DocumentTypeOrigin.CLIENT:
        if data.get("acceptance") is not None:
            raise Invalid("document_type.acceptance_not_client",
                          "只有要求类别为客户要求的文件种类才有接受情况")
        if current is not None:
            data["acceptance"] = None
        return
    acceptance = data.get("acceptance", current) or Acceptance.PENDING
    data["acceptance"] = acceptance
    ref = data.get("acceptance_ref", current_ref)
    if acceptance is Acceptance.DECLINED and not ref:
        raise Invalid("document_type.decline_needs_ref", "不接受客户要求时必须写依据（如回复的函件号）")


def _apply_client_visibility(data: dict[str, Any], origin: DocumentTypeOrigin, *,
                             current: bool | None = None,
                             previous_origin: DocumentTypeOrigin | None = None) -> None:
    """内部要求一律不列入客户 MDL；合同规定、客户要求默认列入（新建时、从内部要求改过来时），
    可以手动隐藏。current / previous_origin 为修改前的值，新建时为空。"""
    if origin is DocumentTypeOrigin.INTERNAL:
        if data.get("in_client_mdl") is True:
            raise Invalid("document_type.internal_in_mdl",
                          "内部要求不能列入客户 MDL；要给客户看，请把要求类别改为合同规定或客户要求")
        if current is not False or "in_client_mdl" in data:
            data["in_client_mdl"] = False
    elif "in_client_mdl" not in data and (
            current is None or previous_origin is DocumentTypeOrigin.INTERNAL):
        data["in_client_mdl"] = True


def default_origin(session: Session, project_id: int) -> DocumentTypeOrigin:
    """新建文件种类的默认要求类别：还没发过 MDL 版本时是"合同规定"，发过之后是"客户要求"。"""
    issued = session.scalar(select(exists().where(MdlVersion.project_id == project_id)))
    return DocumentTypeOrigin.CLIENT if issued else DocumentTypeOrigin.CONTRACT


def _last_seq(session: Session, milestone: Milestone) -> int:
    docs = _all_document_types(session, milestone.id)
    return _seq(docs[-1]) if docs else 0


def suggest_submittal_no(session: Session, project_id: int, milestone_id: int) -> str:
    """排在最后时会得到的编号。"""
    get_project(session, project_id)
    milestone = _get_in_project(session, Milestone, milestone_id, project_id, "milestone")
    return f"{milestone.number}.{_last_seq(session, milestone) + 1}"


def create_document_type(session: Session, project_id: int, data: dict[str, Any],
                         actor: Actor, *, position: int | None = None) -> DocumentType:
    return bulk_create_document_types(session, project_id, data.get("milestone_id"), [data],
                                      actor, position=position)[0]


def bulk_create_document_types(session: Session, project_id: int, milestone_id: int | None,
                               rows: list[dict[str, Any]], actor: Actor,
                               *, position: int | None = None) -> list[DocumentType]:
    """在一个里程碑下一次新建多个文件种类（前端从 Excel 粘贴多行时用），编号自动生成。

    默认排在最后；给 position 则从该位置插入，原来的往后挪。
    全部校验通过才写入；任何一行有问题整批不写，错误里带行号（从 1 开始）。
    """
    project = get_writable_project(session, project_id)
    milestone = _active_milestone(session, project_id, milestone_id)
    if not rows:
        raise Invalid("document_type.no_rows", "没有要新建的行")
    prepared = []
    for i, row in enumerate(rows, start=1):
        try:
            prepared.append(_prepare_document_type(row, require_name=True))
        except Invalid as exc:
            exc.params["row"] = i
            exc.message = f"第 {i} 行：{exc.message}"
            raise

    origin = default_origin(session, project_id)
    for i, data in enumerate(prepared, start=1):
        data.setdefault("origin", origin)
        try:
            _apply_client_visibility(data, data["origin"])
            _apply_acceptance(data, data["origin"])
        except Invalid as exc:
            exc.params["row"] = i
            exc.message = f"第 {i} 行：{exc.message}"
            raise

    start = _insert_position(position, _last_seq(session, milestone))
    if start <= _last_seq(session, milestone):
        _shift_document_types(session, project, milestone, start, len(prepared), actor, None,
                              strict=True)
    created = []
    for offset, data in enumerate(prepared):
        seq = start + offset
        doc_type = DocumentType(project_id=project_id, milestone_id=milestone.id,
                                submittal_no=f"{milestone.number}.{seq}", sort_order=seq, **data)
        created.append(_create(session, doc_type, actor, project_id))
    return created


def update_document_type(session: Session, project_id: int, document_type_id: int,
                         changes: dict[str, Any], actor: Actor, *, reason: str | None = None
                         ) -> DocumentType:
    """修改文件种类（编号由平台管理）。给 milestone_id 表示移到另一个里程碑的最后，
    原里程碑后面的编号往前补；挂了文件的不能移。"""
    project = get_writable_project(session, project_id)
    doc_type = _get_in_project(session, DocumentType, document_type_id, project_id,
                               "document_type")
    target_id = changes.get("milestone_id")
    changes = _prepare_document_type(changes, require_name=False)
    _apply_client_visibility(changes, changes.get("origin", doc_type.origin),
                             current=doc_type.in_client_mdl, previous_origin=doc_type.origin)
    _apply_acceptance(changes, changes.get("origin", doc_type.origin),
                      current=doc_type.acceptance, current_ref=doc_type.acceptance_ref)
    _update(session, doc_type, changes, actor, reason, project_id)
    if changes.get("per_equipment") is False:
        _apply_equipment(session, project_id, doc_type, [], actor, reason)

    if target_id is not None and target_id != doc_type.milestone_id:
        if has_files(session, doc_type.id):
            raise Conflict("document_type.number_locked",
                           f"文件种类 {doc_type.submittal_no} 已有文件，不能换里程碑",
                           document_type_id=doc_type.id)
        target = _active_milestone(session, project_id, target_id)
        source = session.get(Milestone, doc_type.milestone_id)
        old_seq = _seq(doc_type)
        _update(session, doc_type, {
            "milestone_id": target.id,
            "submittal_no": f"{target.number}.{_last_seq(session, target) + 1}",
            "sort_order": _last_seq(session, target) + 1,
        }, actor, reason, project_id)
        _shift_document_types(session, project, source, old_seq + 1, -1, actor, reason,
                              strict=False)
    return doc_type


def set_document_type_active(session: Session, project_id: int, document_type_id: int,
                             active: bool, actor: Actor, *, reason: str | None = None
                             ) -> DocumentType:
    get_writable_project(session, project_id)
    doc_type = _get_in_project(session, DocumentType, document_type_id, project_id,
                               "document_type")
    if active:
        _active_milestone(session, project_id, doc_type.milestone_id)
    return _set_active(session, doc_type, active, actor, reason, project_id)


def delete_document_type(session: Session, project_id: int, document_type_id: int,
                         actor: Actor, *, reason: str | None = None) -> None:
    """删除文件种类（只有没挂文件、没被基线引用时才行），连同它的分区、设备关联、应交项；
    后面的编号往前补（牵连的种类有文件时留空号）。挂了文件的只能停用。"""
    project = get_writable_project(session, project_id)
    doc_type = _get_in_project(session, DocumentType, document_type_id, project_id,
                               "document_type")
    if has_files(session, doc_type.id):
        raise Conflict("document_type.has_files",
                       f"文件种类 {doc_type.submittal_no} 已有文件，只能停用",
                       document_type_id=doc_type.id)
    deliverable_ids = list(session.scalars(
        select(Deliverable.id).where(Deliverable.document_type_id == doc_type.id)
    ))
    if deliverable_ids and session.scalar(select(exists().where(
            BaselineItem.deliverable_id.in_(deliverable_ids)))):
        raise Conflict("document_type.in_baseline",
                       f"文件种类 {doc_type.submittal_no} 已被阶段基线引用，只能停用",
                       document_type_id=doc_type.id)
    from app.models import TransmittalItem

    if deliverable_ids and session.scalar(select(exists().where(
            TransmittalItem.deliverable_id.in_(deliverable_ids)))):
        raise Conflict("document_type.in_transmittal",
                       f"文件种类 {doc_type.submittal_no} 已进过传送单，只能停用",
                       document_type_id=doc_type.id)

    for model in (DocumentTypeModule, DocumentTypeEquipment, Deliverable):
        for obj in session.scalars(select(model).where(model.document_type_id == doc_type.id)):
            session.delete(obj)
    session.flush()
    milestone = session.get(Milestone, doc_type.milestone_id)
    seq = _seq(doc_type)
    _delete(session, doc_type, actor, project_id, reason)
    _shift_document_types(session, project, milestone, seq + 1, -1, actor, reason, strict=False)


def reorder_document_types(session: Session, project_id: int, milestone_id: int | None,
                           ids: list[int], actor: Actor) -> None:
    """按新顺序重排里程碑下全部文件种类，编号随之变为 x.1、x.2…"""
    project = get_writable_project(session, project_id)
    if milestone_id is None:
        raise Invalid("structure.field_required", "调整文件种类顺序时 milestone_id 不能为空",
                      field="milestone_id")
    milestone = _get_in_project(session, Milestone, milestone_id, project_id, "milestone")
    current = _all_document_types(session, milestone.id)
    if sorted(ids) != sorted(d.id for d in current) or len(set(ids)) != len(ids):
        raise Invalid("structure.reorder_incomplete",
                      "调整顺序时要给出这个里程碑下的全部文件种类（含停用的）")
    by_id = {d.id: d for d in current}
    dt_map = {by_id[i]: (int(milestone.number), n) for n, i in enumerate(ids, start=1)}
    _renumber(session, project, {}, dt_map, actor, None, strict=True)


# ---------------------------------------------------------------- 模块分区


def _partitions(session: Session, document_type_id: int) -> dict[int, DocumentTypeModule]:
    rows = session.scalars(
        select(DocumentTypeModule).where(DocumentTypeModule.document_type_id == document_type_id)
    )
    return {p.module_id: p for p in rows}


def _active_partition_modules(session: Session, document_type_id: int) -> list[int]:
    parts = sorted(_partitions(session, document_type_id).values(), key=lambda p: p.sort_order)
    return [p.module_id for p in parts if p.active]


def _apply_modules(session: Session, project_id: int, doc_type: DocumentType,
                   module_ids: list[int], actor: Actor, reason: str | None) -> None:
    """把文件种类的分区设成正好是 module_ids；去掉的只停用，不删除。"""
    wanted = list(dict.fromkeys(module_ids))
    existing = _partitions(session, doc_type.id)
    for order, module_id in enumerate(wanted, start=1):
        partition = existing.get(module_id)
        if partition is None:
            _create(session, DocumentTypeModule(document_type_id=doc_type.id,
                                                module_id=module_id, sort_order=order),
                    actor, project_id, reason)
        else:
            if partition.sort_order != order:
                _update(session, partition, {"sort_order": order}, actor, reason, project_id)
            _set_active(session, partition, True, actor, reason, project_id)
    for module_id, partition in existing.items():
        if module_id not in wanted:
            _set_active(session, partition, False, actor, reason, project_id)
    if doc_type.per_module != bool(wanted):
        _update(session, doc_type, {"per_module": bool(wanted)}, actor, reason, project_id)


def set_document_type_modules(session: Session, project_id: int, document_type_id: int,
                              actor: Actor, *, module_ids: list[int] | None = None,
                              module_names: list[str] | None = None,
                              reason: str | None = None) -> DocumentType:
    """设置文件种类按哪些模块分区。可以直接给新模块名，系统顺手建好（模块是项目级共用的）。"""
    get_writable_project(session, project_id)
    doc_type = _get_in_project(session, DocumentType, document_type_id, project_id,
                               "document_type")
    wanted = _resolve_modules(session, project_id, module_ids or [], module_names or [], actor)
    _apply_modules(session, project_id, doc_type, wanted, actor, reason)
    return doc_type


PARTITION_FIELDS = ("mdl_status_text", "mdl_remarks")


def update_partition(session: Session, project_id: int, document_type_id: int, module_id: int,
                     changes: dict[str, Any], actor: Actor, *, reason: str | None = None
                     ) -> DocumentTypeModule:
    """修改某个分区在 MDL 上的状态文字和备注。"""
    get_writable_project(session, project_id)
    _get_in_project(session, DocumentType, document_type_id, project_id, "document_type")
    partition = _partitions(session, document_type_id).get(module_id)
    if partition is None or not partition.active:
        raise NotFound("partition.not_found", f"文件种类 {document_type_id} 没有模块 {module_id} 的分区",
                       document_type_id=document_type_id, module_id=module_id)
    changes = _pick(changes, PARTITION_FIELDS)
    for f in PARTITION_FIELDS:
        _clean(changes, f)
    return _update(session, partition, changes, actor, reason, project_id)


def bulk_set_modules(session: Session, project_id: int, document_type_ids: list[int],
                     mode: Literal["add", "remove", "replace"], actor: Actor, *,
                     module_ids: list[int] | None = None, module_names: list[str] | None = None,
                     reason: str | None = None) -> list[DocumentType]:
    """批量设置分区：add 加上、remove 去掉、replace 替换。"""
    get_writable_project(session, project_id)
    chosen = _resolve_modules(session, project_id, module_ids or [], module_names or [], actor)
    result = []
    for doc_type_id in dict.fromkeys(document_type_ids):
        doc_type = _get_in_project(session, DocumentType, doc_type_id, project_id,
                                   "document_type")
        current = _active_partition_modules(session, doc_type.id)
        if mode == "add":
            wanted = current + [m for m in chosen if m not in current]
        elif mode == "remove":
            wanted = [m for m in current if m not in chosen]
        else:
            wanted = chosen
        _apply_modules(session, project_id, doc_type, wanted, actor, reason)
        result.append(doc_type)
    return result


# ---------------------------------------------------------------- 文件种类对应的设备


def _links(session: Session, document_type_id: int) -> dict[int, DocumentTypeEquipment]:
    rows = session.scalars(select(DocumentTypeEquipment).where(
        DocumentTypeEquipment.document_type_id == document_type_id))
    return {link.equipment_id: link for link in rows}


def _select_equipment(session: Session, project_id: int, *, equipment_ids: list[int],
                      equipment_type_ids: list[int], module_ids: list[int],
                      all_equipment: bool) -> list[int]:
    """按"全部 / 设备种类 / 模块（经模块-设备种类对应）/ 指定设备"选出项目里启用的设备。"""
    base = select(Equipment).where(Equipment.project_id == project_id, Equipment.active)
    chosen: list[Equipment] = []
    if all_equipment:
        chosen += session.scalars(base)
    if equipment_type_ids:
        chosen += session.scalars(base.where(Equipment.equipment_type_id.in_(equipment_type_ids)))
    if module_ids:
        type_ids = select(ModuleEquipmentType.equipment_type_id).where(
            ModuleEquipmentType.module_id.in_(module_ids), ModuleEquipmentType.active)
        chosen += session.scalars(base.where(Equipment.equipment_type_id.in_(type_ids)))
    for equipment_id in equipment_ids:
        equipment = _get_in_project(session, Equipment, equipment_id, project_id, "equipment")
        if not equipment.active:
            raise Conflict("equipment.inactive", f"设备 {equipment.tag or equipment.name} 已停用",
                           equipment_id=equipment_id)
        chosen.append(equipment)
    return list(dict.fromkeys(e.id for e in sorted(chosen, key=lambda e: (e.sort_order, e.id))))


def _apply_equipment(session: Session, project_id: int, doc_type: DocumentType,
                     equipment_ids: list[int], actor: Actor, reason: str | None) -> None:
    """把文件种类对应的设备设成正好是 equipment_ids；去掉的只停用。
    关联了设备的种类自动标为"按设备"。"""
    existing = _links(session, doc_type.id)
    new_links = []
    for equipment_id in equipment_ids:
        link = existing.get(equipment_id)
        if link is None:
            new_links.append(DocumentTypeEquipment(document_type_id=doc_type.id,
                                                   equipment_id=equipment_id))
        else:
            _set_active(session, link, True, actor, reason, project_id)
    if new_links:
        # 一次写入再逐条留痕：一个种类勾几百台设备时，逐条写库太慢
        session.add_all(new_links)
        session.flush()
        for link in new_links:
            record(session, link, ChangeAction.CREATE, None, snapshot(link), actor,
                   reason=reason, project_id=project_id)
    for equipment_id, link in existing.items():
        if equipment_id not in equipment_ids:
            _set_active(session, link, False, actor, reason, project_id)
    if equipment_ids and not doc_type.per_equipment:
        _update(session, doc_type, {"per_equipment": True}, actor, reason, project_id)


def bulk_set_equipment(session: Session, project_id: int, document_type_ids: list[int],
                       mode: Literal["add", "remove", "replace"], actor: Actor, *,
                       equipment_ids: list[int] | None = None,
                       equipment_type_ids: list[int] | None = None,
                       module_ids: list[int] | None = None, all_equipment: bool = False,
                       reason: str | None = None) -> list[DocumentType]:
    """给一个或多个文件种类批量勾选对应的设备：add 加上、remove 去掉、replace 替换。"""
    get_writable_project(session, project_id)
    chosen = _select_equipment(session, project_id, equipment_ids=equipment_ids or [],
                               equipment_type_ids=equipment_type_ids or [],
                               module_ids=module_ids or [], all_equipment=all_equipment)
    result = []
    for doc_type_id in dict.fromkeys(document_type_ids):
        doc_type = _get_in_project(session, DocumentType, doc_type_id, project_id,
                                   "document_type")
        current = [e for e, link in _links(session, doc_type.id).items() if link.active]
        if mode == "add":
            wanted = current + [e for e in chosen if e not in current]
        elif mode == "remove":
            wanted = [e for e in current if e not in chosen]
        else:
            wanted = chosen
        _apply_equipment(session, project_id, doc_type, wanted, actor, reason)
        result.append(doc_type)
    return result


# ---------------------------------------------------------------- 复制结构


def copy_structure(session: Session, target_project_id: int, source_project_id: int,
                   actor: Actor) -> dict[str, int]:
    """把源项目启用的里程碑、模块、文件种类、分区、设备种类、模块对应的设备种类复制到目标项目（编号照搬）。

    只复制定义；不复制 MDL 状态、备注、提交日期、截止日期，也不复制设备和设备关联
    （设备属于各自项目）。目标项目必须还没有里程碑和模块；目标项目已有同名的设备种类
    （如先导入了设备清单）就沿用，不重复建。
    """
    target = get_writable_project(session, target_project_id)
    source = get_project(session, source_project_id)
    if source.id == target.id:
        raise Invalid("structure.copy_same_project", "不能从项目自己复制")
    if session.scalar(select(exists().where(Milestone.project_id == target.id))) or \
            session.scalar(select(exists().where(Module.project_id == target.id))):
        raise Conflict("structure.target_not_empty",
                       f"项目 {target.code} 已有结构，不能复制", project_id=target.id)

    reason = f"从项目 {source.code} 复制"
    module_map: dict[int, int] = {}
    for m in list_modules(session, source.id):
        new = _create(session, Module(project_id=target.id, name=m.name,
                                      sort_order=m.sort_order), actor, target.id, reason)
        module_map[m.id] = new.id

    # 设备种类（同名的沿用）和模块对应的设备种类
    existing = {t.name: t.id for t in session.scalars(
        select(EquipmentType).where(EquipmentType.project_id == target.id))}
    type_map: dict[int, int] = {}
    new_types = 0
    for t in session.scalars(select(EquipmentType).where(EquipmentType.project_id == source.id, EquipmentType.active)
                             .order_by(EquipmentType.sort_order, EquipmentType.id)):
        if t.name in existing:
            type_map[t.id] = existing[t.name]
        else:
            type_map[t.id] = _create(session, EquipmentType(project_id=target.id, name=t.name,
                                                            sort_order=t.sort_order), actor, target.id, reason).id
            new_types += 1
    links = 0
    for link in session.scalars(select(ModuleEquipmentType).where(
            ModuleEquipmentType.module_id.in_(list(module_map)), ModuleEquipmentType.active)):
        if link.equipment_type_id in type_map:
            _create(session, ModuleEquipmentType(module_id=module_map[link.module_id],
                                                 equipment_type_id=type_map[link.equipment_type_id]),
                    actor, target.id, reason)
            links += 1

    counts = {"milestones": 0, "modules": len(module_map), "document_types": 0,
              "partitions": 0, "equipment_types": new_types, "module_equipment_types": links}
    for ms in list_milestones(session, source.id):
        new_ms = _create(session, Milestone(
            project_id=target.id, code=ms.code, number=ms.number, name=ms.name,
            description=ms.description, goal=ms.goal, sort_order=ms.sort_order,
        ), actor, target.id, reason)
        counts["milestones"] += 1
        for dt in list_document_types(session, source.id, milestone_id=ms.id):
            new_dt = _create(session, DocumentType(
                project_id=target.id, milestone_id=new_ms.id,
                **{f: getattr(dt, f) for f in _COPY_DOCUMENT_TYPE_FIELDS},
            ), actor, target.id, reason)
            counts["document_types"] += 1
            for module_id in _active_partition_modules(session, dt.id):
                if module_id in module_map:
                    _create(session, DocumentTypeModule(
                        document_type_id=new_dt.id, module_id=module_map[module_id],
                        sort_order=_partitions(session, dt.id)[module_id].sort_order,
                    ), actor, target.id, reason)
                    counts["partitions"] += 1
    return counts


# ---------------------------------------------------------------- 结构树


def get_structure(session: Session, project_id: int, *, include_inactive: bool = False
                  ) -> dict[str, Any]:
    """整棵结构树：模块列表 + 里程碑 → 文件种类 → 分区 / 对应设备。前端项目结构页用。"""
    get_project(session, project_id)
    module_names = {m.id: m.name for m in list_modules(session, project_id,
                                                       include_inactive=True)}
    equipment = {e.id: e for e in session.scalars(
        select(Equipment).where(Equipment.project_id == project_id))}
    doc_types = list_document_types(session, project_id, include_inactive=include_inactive)

    by_milestone: dict[int, list[dict[str, Any]]] = {}
    for d in doc_types:
        parts = sorted(_partitions(session, d.id).values(), key=lambda p: p.sort_order)
        links = sorted(_links(session, d.id).values(),
                       key=lambda link: (equipment[link.equipment_id].sort_order,
                                         link.equipment_id))
        by_milestone.setdefault(d.milestone_id, []).append({
            **_row(d),
            "partitions": [{**_row(p), "module_name": module_names.get(p.module_id)}
                           for p in parts if include_inactive or p.active],
            "equipment": [{**_row(link), "tag": equipment[link.equipment_id].tag,
                           "equipment_name": equipment[link.equipment_id].name}
                          for link in links if include_inactive or link.active],
        })
    return {
        "modules": [_row(m) for m in list_modules(session, project_id,
                                                  include_inactive=include_inactive)],
        "milestones": [
            {**_row(m), "document_types": by_milestone.get(m.id, [])}
            for m in list_milestones(session, project_id, include_inactive=include_inactive)
        ],
    }


def _row(obj: Any) -> dict[str, Any]:
    """结构树里的一个节点：对象全部字段，日期转成文字。"""
    data = {}
    for attr in obj.__mapper__.column_attrs:
        value = getattr(obj, attr.key)
        data[attr.key] = value.isoformat() if isinstance(value, date) else value
    return data
