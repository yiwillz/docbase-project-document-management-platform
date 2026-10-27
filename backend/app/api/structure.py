"""项目结构接口：里程碑、模块、文件种类、模块分区、文件种类对应的设备。

编号全部由平台自动生成（里程碑 M1、M2…；文件种类 3.1、3.2…），接口里不能直接填编号。
"""

from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import ActorDep, SessionDep
from app.api.deps import save_and_regenerate as _save
from app.api.projects import ReasonBody
from app.models import Acceptance, DocumentTypeOrigin
from app.services import structure as svc
from app.services import usage

router = APIRouter(prefix="/api/projects/{project_id}", tags=["项目结构"])

POSITION_DESCRIPTION = "插到第几个（从 1 开始），原来的往后挪；不填则排在最后"


def _examples(*items: dict[str, Any]) -> ConfigDict:
    """接口文档里 Try it out 默认填的内容。"""
    return ConfigDict(json_schema_extra={"examples": list(items)})


# ---------------------------------------------------------------- 里程碑


class MilestoneFields(BaseModel):
    name: str | None = None
    description: str | None = None
    goal: str | None = None
    remarks: str | None = None
    due_date: date | None = None


class MilestoneCreate(MilestoneFields):
    model_config = _examples({"name": "Detailed Design", "due_date": "2026-06-07"})

    position: int | None = Field(default=None, description=POSITION_DESCRIPTION)


class MilestoneUpdate(MilestoneFields):
    model_config = _examples({"name": "Detailed Design & Interface", "reason": "客户改名"})

    reason: str | None = None


class MilestoneOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    code: str
    number: str
    name: str | None
    description: str | None
    goal: str | None
    remarks: str | None
    due_date: date | None
    sort_order: int
    active: bool
    created_at: datetime
    updated_at: datetime


@router.get("/milestones", response_model=list[MilestoneOut], summary="里程碑列表")
def list_milestones(project_id: int, session: SessionDep, include_inactive: bool = False):
    return svc.list_milestones(session, project_id, include_inactive=include_inactive)


@router.post("/milestones", response_model=MilestoneOut, status_code=201,
             summary="新建里程碑（编号自动生成）")
def create_milestone(project_id: int, body: MilestoneCreate, session: SessionDep,
                     actor: ActorDep):
    data = body.model_dump(exclude_unset=True)
    position = data.pop("position", None)
    milestone = svc.create_milestone(session, project_id, data, actor, position=position)
    _save(session, project_id, actor)
    return milestone


@router.patch("/milestones/{milestone_id}", response_model=MilestoneOut, summary="修改里程碑")
def update_milestone(project_id: int, milestone_id: int, body: MilestoneUpdate,
                     session: SessionDep, actor: ActorDep):
    changes = body.model_dump(exclude_unset=True)
    reason = changes.pop("reason", None)
    milestone = svc.update_milestone(session, project_id, milestone_id, changes, actor,
                                     reason=reason)
    _save(session, project_id, actor)
    return milestone


@router.delete("/milestones/{milestone_id}", status_code=204,
               summary="删除里程碑（下面须没有文件种类；后面的编号往前补）")
def delete_milestone(project_id: int, milestone_id: int, session: SessionDep, actor: ActorDep,
                     reason: str | None = None):
    svc.delete_milestone(session, project_id, milestone_id, actor, reason=reason)
    _save(session, project_id, actor)


@router.post("/milestones/{milestone_id}/deactivate", response_model=MilestoneOut,
             summary="停用里程碑（编号保留；下面还有启用的文件种类时拒绝）")
def deactivate_milestone(project_id: int, milestone_id: int, session: SessionDep,
                         actor: ActorDep, body: ReasonBody | None = None):
    milestone = svc.set_milestone_active(session, project_id, milestone_id, False, actor,
                                         reason=body.reason if body else None)
    _save(session, project_id, actor)
    return milestone


@router.post("/milestones/{milestone_id}/activate", response_model=MilestoneOut,
             summary="启用里程碑")
def activate_milestone(project_id: int, milestone_id: int, session: SessionDep,
                       actor: ActorDep, body: ReasonBody | None = None):
    milestone = svc.set_milestone_active(session, project_id, milestone_id, True, actor,
                                         reason=body.reason if body else None)
    _save(session, project_id, actor)
    return milestone


# ---------------------------------------------------------------- 模块


class ModuleCreate(BaseModel):
    model_config = _examples({"name": "Automation"})

    name: str = Field(min_length=1, max_length=200)


class ModuleUpdate(BaseModel):
    model_config = _examples({"name": "Automation & Robotics", "reason": "改名"})

    name: str | None = Field(default=None, min_length=1, max_length=200)
    reason: str | None = None


class ModuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    name: str
    sort_order: int
    active: bool
    created_at: datetime
    updated_at: datetime


@router.get("/modules", response_model=list[ModuleOut], summary="模块列表")
def list_modules(project_id: int, session: SessionDep, include_inactive: bool = False):
    return svc.list_modules(session, project_id, include_inactive=include_inactive)


@router.post("/modules", response_model=ModuleOut, status_code=201, summary="新建模块")
def create_module(project_id: int, body: ModuleCreate, session: SessionDep, actor: ActorDep):
    module = svc.create_module(session, project_id, body.model_dump(), actor)
    _save(session, project_id, actor)
    return module


@router.patch("/modules/{module_id}", response_model=ModuleOut, summary="修改模块")
def update_module(project_id: int, module_id: int, body: ModuleUpdate, session: SessionDep,
                  actor: ActorDep):
    changes = body.model_dump(exclude_unset=True)
    reason = changes.pop("reason", None)
    module = svc.update_module(session, project_id, module_id, changes, actor, reason=reason)
    _save(session, project_id, actor)
    return module


@router.post("/modules/{module_id}/deactivate", response_model=ModuleOut, summary="停用模块")
def deactivate_module(project_id: int, module_id: int, session: SessionDep, actor: ActorDep,
                      body: ReasonBody | None = None):
    module = svc.set_module_active(session, project_id, module_id, False, actor,
                                   reason=body.reason if body else None)
    _save(session, project_id, actor)
    return module


@router.get("/modules/{module_id}/usage", summary="模块用过没有（没用过的能删除；用过的只能停用）")
def module_usage(project_id: int, module_id: int, session: SessionDep) -> dict[str, Any]:
    from app.models import Module
    from app.services.crud import get_in_project

    m = get_in_project(session, Module, module_id, project_id, "module")
    found = usage.module_usage(session, project_id, m)
    return {"deletable": not found, "usage": found}


@router.delete("/modules/{module_id}", status_code=204, summary="删除没用过的模块（连同分区和按模块的应交项）")
def delete_module(project_id: int, module_id: int, session: SessionDep, actor: ActorDep,
                  reason: str | None = None):
    usage.delete_module(session, project_id, module_id, actor, reason=reason)
    _save(session, project_id, actor)


@router.post("/modules/{module_id}/activate", response_model=ModuleOut, summary="启用模块")
def activate_module(project_id: int, module_id: int, session: SessionDep, actor: ActorDep,
                    body: ReasonBody | None = None):
    module = svc.set_module_active(session, project_id, module_id, True, actor,
                                   reason=body.reason if body else None)
    _save(session, project_id, actor)
    return module


# ---------------------------------------------------------------- 文件种类


class DocumentTypeFields(BaseModel):
    name: str = Field(min_length=1, max_length=500)
    category: str | None = None
    per_equipment: bool | None = Field(default=None, description="是否按设备")
    due_date: date | None = None
    content: str | None = Field(default=None, description="应含内容")
    description: str | None = None
    purpose: str | None = None
    mdl_status_text: str | None = None
    mdl_remarks: str | None = None
    mdl_submitted_date: date | None = None
    origin: DocumentTypeOrigin | None = Field(
        default=None, description="要求类别：contract 合同规定 / client 客户要求 / internal 内部要求；"
                                  "不填时，还没发过 MDL 版本为 contract，发过之后为 client")
    required_date: date | None = Field(
        default=None, description="要求日期：合同签订日 / 客户提出日 / 内部决定日（真实日期，不知道就空着）")
    basis: str | None = Field(default=None, description="要求依据：合同规定填合同条款；客户要求填客户提出要求的函件、邮件、会议纪要编号")
    changed_date: date | None = Field(default=None, description="要求内容变更的日期（真实日期）")
    change_ref: str | None = Field(default=None, description="变更依据，如客户函件号、邮件")
    in_client_mdl: bool | None = Field(
        default=None, description="是否列入客户 MDL：内部要求一律否；合同规定、客户要求默认是，可设为否以隐藏")
    acceptance: Acceptance | None = Field(
        default=None, description="客户要求是否接受：pending 待确认（默认）/ accepted 接受 / declined 不接受；只对客户要求有效")
    acceptance_ref: str | None = Field(default=None, description="接受 / 不接受的依据（不接受时必填）")
    variation_ref: str | None = Field(default=None, description="变更单号（VO / CO）")
    keywords: list[str] | None = Field(default=None, description="识别关键词：文件里常见的别的叫法（智能投递用）")


class DocumentTypeCreate(DocumentTypeFields):
    model_config = _examples({"milestone_id": 1, "name": "Cut sheet", "per_equipment": True})

    milestone_id: int
    position: int | None = Field(default=None, description=POSITION_DESCRIPTION)


class DocumentTypeBulk(BaseModel):
    model_config = _examples({
        "milestone_id": 1,
        "rows": [{"name": "Technical Specification", "per_equipment": True},
                 {"name": "General Arrangement Drawings"},
                 {"name": "Utility Consumption List"}],
    })

    milestone_id: int
    rows: list[DocumentTypeFields] = Field(min_length=1, max_length=500)
    position: int | None = Field(default=None, description=POSITION_DESCRIPTION)


class DocumentTypeUpdate(BaseModel):
    model_config = _examples({"name": "Cut sheet (rev)", "reason": "按客户意见改名"})

    milestone_id: int | None = Field(
        default=None, description="移到另一个里程碑（排在最后，编号自动改；挂了文件的不能移）"
    )
    name: str | None = Field(default=None, min_length=1, max_length=500)
    category: str | None = None
    per_equipment: bool | None = Field(default=None,
                                       description="改为 false 时，对应的设备关联一并停用")
    due_date: date | None = None
    content: str | None = None
    description: str | None = None
    purpose: str | None = None
    mdl_status_text: str | None = None
    mdl_remarks: str | None = None
    mdl_submitted_date: date | None = None
    origin: DocumentTypeOrigin | None = Field(
        default=None, description="要求类别：contract 合同规定 / client 客户要求 / internal 内部要求；"
                                  "不填时，还没发过 MDL 版本为 contract，发过之后为 client")
    required_date: date | None = Field(
        default=None, description="要求日期：合同签订日 / 客户提出日 / 内部决定日（真实日期，不知道就空着）")
    basis: str | None = Field(default=None, description="要求依据：合同规定填合同条款；客户要求填客户提出要求的函件、邮件、会议纪要编号")
    changed_date: date | None = Field(default=None, description="要求内容变更的日期（真实日期）")
    change_ref: str | None = Field(default=None, description="变更依据，如客户函件号、邮件")
    in_client_mdl: bool | None = Field(
        default=None, description="是否列入客户 MDL：内部要求一律否；合同规定、客户要求默认是，可设为否以隐藏")
    acceptance: Acceptance | None = Field(
        default=None, description="客户要求是否接受：pending 待确认（默认）/ accepted 接受 / declined 不接受；只对客户要求有效")
    acceptance_ref: str | None = Field(default=None, description="接受 / 不接受的依据（不接受时必填）")
    variation_ref: str | None = Field(default=None, description="变更单号（VO / CO）")
    keywords: list[str] | None = Field(default=None, description="识别关键词：文件里常见的别的叫法（智能投递用）")
    reason: str | None = None


class DocumentTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    milestone_id: int
    submittal_no: str
    name: str
    category: str | None
    per_equipment: bool
    per_module: bool
    due_date: date | None
    content: str | None
    description: str | None
    purpose: str | None
    mdl_status_text: str | None
    mdl_remarks: str | None
    mdl_submitted_date: date | None
    origin: DocumentTypeOrigin
    required_date: date | None
    basis: str | None
    changed_date: date | None
    change_ref: str | None
    in_client_mdl: bool
    acceptance: Acceptance | None
    acceptance_ref: str | None
    variation_ref: str | None
    keywords: list[str]
    sort_order: int
    active: bool
    created_at: datetime
    updated_at: datetime


class NextNumberOut(BaseModel):
    submittal_no: str


class ModulesBody(BaseModel):
    model_config = _examples({"module_names": ["Automation", "Alpha"]})

    module_ids: list[int] = Field(default_factory=list, description="已有模块的 id")
    module_names: list[str] = Field(default_factory=list,
                                    description="模块名；不存在的会自动新建（项目内共用）")
    reason: str | None = None


class BulkModulesBody(ModulesBody):
    model_config = _examples({"document_type_ids": [1, 2], "module_names": ["Automation"],
                              "mode": "add"})

    document_type_ids: list[int] = Field(min_length=1)
    mode: Literal["add", "remove", "replace"] = Field(
        description="add 加上、remove 去掉、replace 替换"
    )


class BulkEquipmentBody(BaseModel):
    model_config = _examples({"document_type_ids": [1], "all_equipment": True, "mode": "add"})

    document_type_ids: list[int] = Field(min_length=1)
    mode: Literal["add", "remove", "replace"] = Field(
        description="add 加上、remove 去掉、replace 替换"
    )
    all_equipment: bool = Field(default=False, description="项目里全部启用的设备")
    equipment_type_ids: list[int] = Field(default_factory=list, description="这些设备种类的设备")
    module_ids: list[int] = Field(default_factory=list,
                                  description="这些模块对应的设备种类下的设备")
    equipment_ids: list[int] = Field(default_factory=list, description="指定的设备")
    reason: str | None = None


@router.get("/document-types", response_model=list[DocumentTypeOut], summary="文件种类列表")
def list_document_types(project_id: int, session: SessionDep, milestone_id: int | None = None,
                        include_inactive: bool = False):
    return svc.list_document_types(session, project_id, milestone_id=milestone_id,
                                   include_inactive=include_inactive)


@router.get("/document-types/next-number", response_model=NextNumberOut,
            summary="排在最后时会得到的编号（3.9 之后是 3.10）")
def next_number(project_id: int, milestone_id: int, session: SessionDep):
    return {"submittal_no": svc.suggest_submittal_no(session, project_id, milestone_id)}


@router.post("/document-types", response_model=DocumentTypeOut, status_code=201,
             summary="新建文件种类（编号自动生成）")
def create_document_type(project_id: int, body: DocumentTypeCreate, session: SessionDep,
                         actor: ActorDep):
    data = body.model_dump(exclude_unset=True)
    position = data.pop("position", None)
    doc_type = svc.create_document_type(session, project_id, data, actor, position=position)
    _save(session, project_id, actor)
    return doc_type


@router.post("/document-types/bulk", response_model=list[DocumentTypeOut], status_code=201,
             summary="批量新建文件种类（从 Excel 粘贴多行用；有一行出错整批不写）")
def bulk_create_document_types(project_id: int, body: DocumentTypeBulk, session: SessionDep,
                               actor: ActorDep):
    rows = [r.model_dump(exclude_unset=True) for r in body.rows]
    created = svc.bulk_create_document_types(session, project_id, body.milestone_id, rows, actor,
                                             position=body.position)
    _save(session, project_id, actor)
    return created


@router.post("/document-types/bulk-modules", response_model=list[DocumentTypeOut],
             summary="批量设置模块分区")
def bulk_set_modules(project_id: int, body: BulkModulesBody, session: SessionDep,
                     actor: ActorDep):
    result = svc.bulk_set_modules(session, project_id, body.document_type_ids, body.mode, actor,
                                  module_ids=body.module_ids, module_names=body.module_names,
                                  reason=body.reason)
    _save(session, project_id, actor)
    return result


@router.post("/document-types/bulk-equipment", response_model=list[DocumentTypeOut],
             summary="批量勾选文件种类对应的设备（全部 / 按设备种类 / 按模块 / 指定）")
def bulk_set_equipment(project_id: int, body: BulkEquipmentBody, session: SessionDep,
                       actor: ActorDep):
    result = svc.bulk_set_equipment(
        session, project_id, body.document_type_ids, body.mode, actor,
        equipment_ids=body.equipment_ids, equipment_type_ids=body.equipment_type_ids,
        module_ids=body.module_ids, all_equipment=body.all_equipment, reason=body.reason,
    )
    _save(session, project_id, actor)
    return result


@router.patch("/document-types/{document_type_id}", response_model=DocumentTypeOut,
              summary="修改文件种类")
def update_document_type(project_id: int, document_type_id: int, body: DocumentTypeUpdate,
                         session: SessionDep, actor: ActorDep):
    changes = body.model_dump(exclude_unset=True)
    reason = changes.pop("reason", None)
    doc_type = svc.update_document_type(session, project_id, document_type_id, changes, actor,
                                        reason=reason)
    _save(session, project_id, actor)
    return doc_type


@router.delete("/document-types/{document_type_id}", status_code=204,
               summary="删除文件种类（挂了文件的只能停用；后面的编号往前补）")
def delete_document_type(project_id: int, document_type_id: int, session: SessionDep,
                         actor: ActorDep, reason: str | None = None):
    svc.delete_document_type(session, project_id, document_type_id, actor, reason=reason)
    _save(session, project_id, actor)


@router.put("/document-types/{document_type_id}/modules", response_model=DocumentTypeOut,
            summary="设置这个文件种类按哪些模块分区（可直接写新模块名）")
def set_document_type_modules(project_id: int, document_type_id: int, body: ModulesBody,
                              session: SessionDep, actor: ActorDep):
    doc_type = svc.set_document_type_modules(session, project_id, document_type_id, actor,
                                             module_ids=body.module_ids,
                                             module_names=body.module_names, reason=body.reason)
    _save(session, project_id, actor)
    return doc_type


@router.post("/document-types/{document_type_id}/deactivate", response_model=DocumentTypeOut,
             summary="停用文件种类（编号保留）")
def deactivate_document_type(project_id: int, document_type_id: int, session: SessionDep,
                             actor: ActorDep, body: ReasonBody | None = None):
    doc_type = svc.set_document_type_active(session, project_id, document_type_id, False, actor,
                                            reason=body.reason if body else None)
    _save(session, project_id, actor)
    return doc_type


@router.post("/document-types/{document_type_id}/activate", response_model=DocumentTypeOut,
             summary="启用文件种类")
def activate_document_type(project_id: int, document_type_id: int, session: SessionDep,
                           actor: ActorDep, body: ReasonBody | None = None):
    doc_type = svc.set_document_type_active(session, project_id, document_type_id, True, actor,
                                            reason=body.reason if body else None)
    _save(session, project_id, actor)
    return doc_type


# ---------------------------------------------------------------- 顺序、复制、结构树


class ReorderBody(BaseModel):
    model_config = _examples({"entity": "document_types", "milestone_id": 1, "ids": [3, 1, 2]})

    entity: Literal["milestones", "modules", "document_types"]
    ids: list[int] = Field(min_length=1, description="按新顺序排列的 id")
    milestone_id: int | None = Field(default=None, description="调整文件种类顺序时必填")


class CopyBody(BaseModel):
    model_config = _examples({"source_project_id": 1})

    source_project_id: int


class CopyResult(BaseModel):
    milestones: int
    modules: int
    document_types: int
    partitions: int
    equipment_types: int = 0
    module_equipment_types: int = 0


@router.post("/reorder", status_code=204,
             summary="调整顺序（里程碑、文件种类的编号随之重排；模块只改顺序）")
def reorder(project_id: int, body: ReorderBody, session: SessionDep, actor: ActorDep):
    if body.entity == "milestones":
        svc.reorder_milestones(session, project_id, body.ids, actor)
    elif body.entity == "modules":
        svc.reorder_modules(session, project_id, body.ids, actor)
    else:
        svc.reorder_document_types(session, project_id, body.milestone_id, body.ids, actor)
    _save(session, project_id, actor)


@router.post("/copy-structure", response_model=CopyResult,
             summary="从另一个项目复制结构（只复制定义；目标项目须为空）")
def copy_structure(project_id: int, body: CopyBody, session: SessionDep, actor: ActorDep):
    counts = svc.copy_structure(session, project_id, body.source_project_id, actor)
    _save(session, project_id, actor)
    return counts


@router.get("/structure",
            summary="整棵结构树：模块 + 里程碑 → 文件种类 → 分区 / 对应设备")
def get_structure(project_id: int, session: SessionDep, include_inactive: bool = False
                  ) -> dict[str, Any]:
    return svc.get_structure(session, project_id, include_inactive=include_inactive)
