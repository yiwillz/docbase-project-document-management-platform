"""变更记录查询、导出接口。"""

from datetime import datetime
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, ConfigDict

from app.api.deps import SessionDep
from app.models import ChangeAction
from app.services import change_log_export
from app.services.change_labels import labels, summary_kind
from app.services.history import query_change_logs

router = APIRouter(prefix="/api/change-logs", tags=["变更记录"])
# 项目内的导出放在项目路径下（按项目权限检查：要"导出"权限）
project_router = APIRouter(prefix="/api/projects/{project_id}/change-logs", tags=["变更记录"])
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ChangeLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int | None
    entity_type: str
    entity_id: str
    label: str | None = None  # 对象名称，如"1.3 Equipment datasheet / 72"；认不出的为空
    summary: str | None = None  # 汇总式记录的种类：recalculate / purge / transmittal_reply；普通记录为空
    action: ChangeAction
    changes: dict[str, Any]
    actor_id: int | None
    actor_name: str | None
    reason: str | None
    created_at: datetime


class ChangeLogPage(BaseModel):
    total: int
    items: list[ChangeLogOut]


@router.get("", response_model=ChangeLogPage, summary="查询变更记录（新的在前）")
def list_change_logs(
    session: SessionDep,
    project_id: int | None = None,
    entity_type: str | None = Query(default=None, description="对象类型，即表名，如 projects"),
    entity_id: str | None = None,
    actor: str | None = Query(default=None, description="操作人用户名"),
    since: datetime | None = Query(default=None, description="起始时间（含）"),
    until: datetime | None = Query(default=None, description="截止时间（不含）"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    total, items = query_change_logs(
        session,
        project_id=project_id,
        entity_type=entity_type,
        entity_id=entity_id,
        actor=actor,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    names = labels(session, items)
    return {"total": total, "items": [{**ChangeLogOut.model_validate(log).model_dump(), "label": names[log.id],
                                       "summary": summary_kind(log)} for log in items]}


@project_router.get("/export.xlsx", response_class=Response,
                    summary="导出项目的变更记录 Excel（筛选条件同查询；每个改动的字段一行，最多 5 万行）")
def export_change_logs(
    project_id: int,
    session: SessionDep,
    entity_type: str | None = Query(default=None, description="对象类型，即表名"),
    actor: str | None = Query(default=None, description="操作人用户名"),
    since: datetime | None = Query(default=None, description="起始时间（含）"),
    until: datetime | None = Query(default=None, description="截止时间（不含）"),
    lang: Literal["zh", "en"] = "zh",
):
    data = change_log_export.export(session, project_id, entity_type=entity_type, actor=actor,
                                    since=since, until=until, lang=lang)
    from app.services.projects import get_project

    name = f"{get_project(session, project_id).code}_ChangeLog_{datetime.now():%Y%m%d}.xlsx"
    return Response(content=data, media_type=XLSX, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})

