"""闭环检查、里程碑关闭 / 重新打开、关闭清单导出、合同外新增要求清单。"""

from datetime import date, datetime
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import ActorDep, SessionDep
from app.services import closeout as svc
from app.services import requirements_report
from app.services.projects import get_project

router = APIRouter(prefix="/api/projects/{project_id}", tags=["闭环与关闭"])

MEDIA = {"xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
         "pdf": "application/pdf"}


class CloseBody(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "closed_date": "2026-11-30", "basis": "付款申请 PA-03"}]})

    closed_date: date | None = Field(default=None, description="真实关闭日期，默认今天")
    basis: str | None = Field(default=None, description="关闭依据，如付款申请号、客户确认函")
    force: bool = Field(default=False, description="还有未闭环项时强制关闭（要写原因）")
    reason: str | None = None


class ReasonBody(BaseModel):
    reason: str = Field(min_length=1)


def _file(data: bytes, name: str, fmt: str) -> Response:
    return Response(content=data, media_type=MEDIA[fmt], headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


@router.get("/milestones/{milestone_id}/closeout",
            summary="闭环检查：每个应交项卡在哪、能不能关闭")
def check(project_id: int, milestone_id: int, session: SessionDep) -> dict[str, Any]:
    return svc.check(session, project_id, milestone_id)


@router.get("/milestones/{milestone_id}/closeout/export", response_class=Response,
            summary="里程碑关闭清单（Excel / PDF）：每项的批准代码、日期、回函号、传送单")
def export(project_id: int, milestone_id: int, session: SessionDep,
           lang: Literal["en", "zh"] = "en", format: Literal["xlsx", "pdf"] = "xlsx"):
    project = get_project(session, project_id)
    data = svc.export(session, project_id, milestone_id, lang=lang, fmt=format)
    code = svc.check(session, project_id, milestone_id)["milestone"]["code"]
    return _file(data, f"{project.code}_{code}_Closeout.{format}", format)


@router.post("/milestones/{milestone_id}/close", summary="记录里程碑关闭（不锁定，可重新打开）")
def close(project_id: int, milestone_id: int, body: CloseBody, session: SessionDep,
          actor: ActorDep) -> dict[str, Any]:
    svc.close(session, project_id, milestone_id, body.model_dump(), actor)
    session.commit()
    return svc.check(session, project_id, milestone_id)["milestone"]


@router.post("/milestones/{milestone_id}/reopen", summary="重新打开已关闭的里程碑（要写原因）")
def reopen(project_id: int, milestone_id: int, body: ReasonBody, session: SessionDep,
           actor: ActorDep) -> dict[str, Any]:
    svc.reopen(session, project_id, milestone_id, actor, reason=body.reason)
    session.commit()
    return svc.check(session, project_id, milestone_id)["milestone"]


@router.get("/client-requirements.xlsx", response_class=Response,
            summary="合同外新增要求清单：提出日期、要求依据、接受情况、变更单号、完成情况")
def client_requirements(project_id: int, session: SessionDep, lang: Literal["en", "zh"] = "en"):
    project = get_project(session, project_id)
    data = requirements_report.export(session, project_id, lang=lang)
    return _file(data, f"{project.code}_Client_Requests_{datetime.now():%Y%m%d}.xlsx", "xlsx")
