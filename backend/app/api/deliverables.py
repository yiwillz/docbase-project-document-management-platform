"""应交项接口：重算、列表、分组列表、矩阵、汇总、修改（单个 / 批量）、缺件清单导出。"""

from datetime import date, datetime
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import ActorDep, SessionDep
from app.models import DeliverableStatus, DocumentTypeOrigin, Responsible
from app.services import deliverable_query as query
from app.services import deliverables as svc
from app.services import outstanding_export
from app.services.projects import get_project

router = APIRouter(prefix="/api/projects/{project_id}/deliverables", tags=["应交项"])

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
Kind = Literal["whole", "per_module", "per_equipment"]


class RegenerateOut(BaseModel):
    created: int
    obsoleted: int
    restored: int
    active: int
    obsolete: int


@router.post("/regenerate", response_model=RegenerateOut,
             summary="按当前结构重算应交项（结构修改后会自动执行，一般不用手动调）")
def regenerate(project_id: int, session: SessionDep, actor: ActorDep):
    result = svc.ensure_deliverables(session, project_id, actor, reason="手动重算应交项")
    session.commit()
    return result.__dict__


# ---------------------------------------------------------------- 筛选条件


def get_filters(
    milestone_id: Annotated[list[int] | None, Query(description="里程碑（可多选）")] = None,
    document_type_id: Annotated[list[int] | None, Query(description="文件种类")] = None,
    module_id: Annotated[list[int] | None, Query(description="模块（按设备的按设备种类对应的模块）")] = None,
    equipment_type_id: Annotated[list[int] | None, Query(description="设备种类")] = None,
    equipment_id: Annotated[list[int] | None, Query(description="设备")] = None,
    vendor: Annotated[list[str] | None, Query(description="供应商；空字符串 = 未指定供应商")] = None,
    status: Annotated[list[DeliverableStatus] | None, Query(description="状态")] = None,
    responsible: Annotated[list[Responsible] | None, Query(description="责任方")] = None,
    origin: Annotated[list[DocumentTypeOrigin] | None, Query(description="要求类别")] = None,
    kind: Annotated[list[Kind] | None, Query(description="类型：整个种类 / 按模块 / 按设备")] = None,
    overdue: Annotated[bool | None, Query(description="是否逾期")] = None,
    in_client_mdl: Annotated[bool | None, Query(description="是否列入客户 MDL")] = None,
    review_overdue: Annotated[bool | None, Query(description="是否客户超期未审")] = None,
    no_evidence: Annotated[bool | None, Query(description="是否无凭证（已批准却没有批准的客户回复，或不适用没写依据）")] = None,
    received: Annotated[bool | None, Query(description="收集情况：是否已经收到文件")] = None,
    outstanding: Annotated[bool | None, Query(description="是否缺件（没收到文件且没批准，或需修改重交）")] = None,
    q: Annotated[str | None, Query(description="关键字：编号、种类名、设备编号、设备名称、模块、供应商")] = None,
) -> query.Filters:
    def values(items):
        return [getattr(i, "value", i) for i in items] if items else None

    return query.Filters(
        milestone_ids=milestone_id, document_type_ids=document_type_id, module_ids=module_id,
        equipment_type_ids=equipment_type_id, equipment_ids=equipment_id, vendors=vendor,
        statuses=values(status), responsibles=values(responsible), origins=values(origin),
        kinds=kind, overdue=overdue, in_client_mdl=in_client_mdl, review_overdue=review_overdue,
        no_evidence=no_evidence, received=received, outstanding=outstanding, q=q or None)


FiltersDep = Annotated[query.Filters, Depends(get_filters)]


# ---------------------------------------------------------------- 查询


class ItemOut(BaseModel):
    id: int
    kind: str
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
    due_source: str | None
    overdue: bool
    submitted_date: date | None
    status_date: date | None
    status_basis: str | None
    remarks: str | None
    acceptance: str | None
    transmittal_id: int | None
    transmittal_no: str | None
    review_code: str | None
    review_final: bool | None
    reply_date: date | None
    reply_ref: str | None
    review_due: date | None
    review_overdue: bool
    no_evidence: bool
    current_version: int | None
    file_count: int = 0
    received: bool = False
    outstanding: bool = False


class ItemPage(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[ItemOut]


@router.get("", response_model=ItemPage, summary="应交项列表（每个应交项一行，可筛选、分页）")
def list_items(project_id: int, session: SessionDep, filters: FiltersDep,
               offset: Annotated[int, Query(ge=0)] = 0,
               limit: Annotated[int, Query(ge=1, le=5000)] = 500):
    items = query.load_items(session, project_id, filters)
    return {"total": len(items), "offset": offset, "limit": limit,
            "items": [it.out() for it in items[offset:offset + limit]]}


@router.get("/tree", summary="分组列表：里程碑 → 文件种类 → （按设备的再分模块）→ 应交项，每层带统计")
def tree(project_id: int, session: SessionDep, filters: FiltersDep) -> dict[str, Any]:
    return query.tree(session, project_id, filters)


@router.get("/matrix", summary="矩阵：行 = 文件种类，列 = 设备或模块，格子 = 状态（建议按里程碑筛选）")
def matrix(project_id: int, session: SessionDep, filters: FiltersDep) -> dict[str, Any]:
    return query.matrix(session, project_id, filters)


@router.get("/summary", summary="汇总：完成率、各方待办、逾期，按里程碑和模块")
def summary(project_id: int, session: SessionDep, filters: FiltersDep) -> dict[str, Any]:
    return query.summary(session, project_id, filters)


@router.get("/outstanding.xlsx", summary="缺件清单 Excel（没收到文件的 + 需修改重交的；每个供应商一张表）",
            response_class=Response)
def outstanding(project_id: int, session: SessionDep, filters: FiltersDep,
                lang: Literal["en", "zh"] = "en"):
    data = outstanding_export.export(session, project_id, filters, lang=lang)
    code = get_project(session, project_id).code
    name = f"{code}_Outstanding_{datetime.now():%Y%m%d}.xlsx"
    return Response(content=data, media_type=XLSX, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


# ---------------------------------------------------------------- 修改


class DeliverableChanges(BaseModel):
    """只传要改的字段；日期传 null 表示清空。改了状态但没给状态日期时，状态日期清空。"""

    status: DeliverableStatus | None = None
    responsible: Responsible | None = None
    due_date: date | None = Field(default=None, description="应交项自己的截止日；null = 用种类 / 里程碑的")
    submitted_date: date | None = None
    status_date: date | None = Field(default=None, description="当前状态真实发生的日期，如客户批准那天")
    status_basis: str | None = Field(default=None, description="依据，如客户函件号")
    remarks: str | None = Field(default=None, description="内部备注（不导出给客户）")


class DeliverableUpdate(DeliverableChanges):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "status": "approved", "status_date": "2026-10-20", "status_basis": "客户函 CL-012",
        "reason": "客户批准"}]})

    reason: str | None = None


class BulkUpdate(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "ids": [1, 2, 3], "changes": {"status": "under_review", "status_date": "2026-10-22"},
        "reason": "传送单 TR-005 已提交"}]})

    ids: list[int] = Field(min_length=1, max_length=5000)
    changes: DeliverableChanges
    reason: str | None = None


class BulkOut(BaseModel):
    updated: int
    unchanged: int


def _changes(body: DeliverableChanges) -> dict[str, Any]:
    return body.model_dump(exclude_unset=True, exclude={"reason"}, mode="python")


@router.patch("/{deliverable_id}", response_model=ItemOut, summary="修改一个应交项")
def update_one(project_id: int, deliverable_id: int, body: DeliverableUpdate,
               session: SessionDep, actor: ActorDep):
    svc.update_deliverable(session, project_id, deliverable_id, _changes(body), actor,
                           reason=body.reason)
    session.commit()
    [item] = query.load_items(session, project_id, query.Filters(ids=[deliverable_id]))
    return item.out()


@router.post("/bulk-update", response_model=BulkOut,
             summary="批量修改：同一组改动用到选中的应交项上，要么全改、要么全不改")
def update_many(project_id: int, body: BulkUpdate, session: SessionDep, actor: ActorDep):
    result = svc.bulk_update(session, project_id, body.ids, _changes(body.changes), actor,
                             reason=body.reason)
    session.commit()
    return result
