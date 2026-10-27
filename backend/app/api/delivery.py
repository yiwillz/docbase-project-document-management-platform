"""交付与基线接口：交付包（估算、生成、列表、下载）、阶段基线（冻结、列表、查看、对比、导出）、共享盘镜像状态。"""

from datetime import date
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from app.api.deps import ActorDep, SessionDep
from app.services import access
from app.services import baselines as base_svc
from app.services import delivery as svc
from app.services import mirror as mirror_svc
from app.services.projects import get_project

router = APIRouter(prefix="/api/projects/{project_id}", tags=["交付与基线"])
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ScopeIn(BaseModel):
    milestone_ids: list[int] = Field(default_factory=list)
    document_type_ids: list[int] = Field(default_factory=list)
    equipment_ids: list[int] = Field(default_factory=list, description="只放这几台设备（按设备的种类）")


class SubmitIn(BaseModel):
    subject: str | None = Field(default=None, max_length=500)
    submitted_date: date | None = None
    external_ref: str | None = Field(default=None, max_length=200)
    remarks: str | None = None


class PackageIn(BaseModel):
    scope: ScopeIn = Field(default_factory=ScopeIn)
    layout: Literal["equipment", "milestone"] = "equipment"
    lang: Literal["zh", "en"] = "en"
    include_client: bool = Field(default=False, description="放不放客户文件（交给客户的包一般不放）")
    baseline_id: int | None = Field(default=None, description="按这个基线里的版本导出")
    submit: SubmitIn | None = Field(default=None, description="作为提交发给客户：同时生成传送单，记下包里的版本")


def _kw(body: PackageIn) -> dict[str, Any]:
    return {"layout": body.layout, "lang": body.lang, "include_client": body.include_client,
            "scope": svc.Scope(**body.scope.model_dump())}


@router.post("/packages/estimate", summary="生成交付包前先估算：多少个文件、多大")
def estimate(project_id: int, body: PackageIn, session: SessionDep) -> dict[str, Any]:
    versions = svc.baseline_versions(session, project_id, body.baseline_id) if body.baseline_id else None
    return svc.estimate(session, project_id, versions=versions, **_kw(body))


@router.post("/packages", status_code=201, summary="生成交付包（后台做）；可同时作为提交发给客户")
def create_package(project_id: int, body: PackageIn, request: Request, session: SessionDep, actor: ActorDep
                   ) -> dict[str, Any]:
    if body.submit:  # 作为提交：还要能改应交项状态
        access.require(session, request.state.user, project_id, "deliverable.update")
    job = svc.start_package(session, project_id, actor, baseline_id=body.baseline_id,
                            submit=body.submit.model_dump() if body.submit else None, **_kw(body))
    session.commit()
    return svc.package_out(job)


@router.get("/packages", summary="交付包列表（新的在前）")
def packages(project_id: int, session: SessionDep) -> list[dict[str, Any]]:
    return svc.list_packages(session, project_id)


@router.get("/packages/{job_id}/download", response_class=FileResponse, summary="下载交付包")
def download_package(project_id: int, job_id: int, session: SessionDep):
    path, name = svc.package_file(session, project_id, job_id)
    return FileResponse(path, media_type="application/zip", filename=name)


# ---------------------------------------------------------------- 阶段基线


class BaselineIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None


@router.post("/baselines", status_code=201, summary="冻结一个阶段基线（每个应交项的状态和每份文件的当前版本）")
def freeze(project_id: int, body: BaselineIn, session: SessionDep, actor: ActorDep) -> dict[str, Any]:
    b = base_svc.freeze(session, project_id, body.name, actor, description=body.description)
    session.commit()
    return base_svc.baseline_out(b)


@router.get("/baselines", summary="阶段基线列表")
def list_baselines(project_id: int, session: SessionDep) -> list[dict[str, Any]]:
    return base_svc.list_baselines(session, project_id)


@router.get("/baselines/{baseline_id}", summary="基线详情：每个应交项冻结时的状态和文件版本")
def baseline_detail(project_id: int, baseline_id: int, session: SessionDep) -> dict[str, Any]:
    return base_svc.detail(session, project_id, baseline_id)


@router.get("/baselines/{baseline_id}/compare", summary="和另一个基线比（不给 other 就是和现在比）")
def compare(project_id: int, baseline_id: int, session: SessionDep, other: int | None = None) -> dict[str, Any]:
    return base_svc.compare(session, project_id, baseline_id, other)


@router.get("/baselines/{baseline_id}/export.xlsx", response_class=Response, summary="基线清单 Excel")
def export_baseline(project_id: int, baseline_id: int, session: SessionDep,
                    lang: Literal["zh", "en"] = "zh"):
    data = base_svc.export_xlsx(session, project_id, baseline_id, lang)
    project = get_project(session, project_id)
    name = f"{project.code}_baseline_{baseline_id}.xlsx"
    return Response(content=data, media_type=XLSX,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


# ---------------------------------------------------------------- 共享盘镜像


@router.get("/mirror", summary="共享盘镜像状态（镜像目录、方式、上次同步、待同步的变化）")
def mirror_status(project_id: int, session: SessionDep) -> dict[str, Any]:
    get_project(session, project_id)
    return mirror_svc.status(session)


@router.post("/mirror/sync", summary="马上把全部项目的镜像对一遍（后台做）")
def mirror_sync(project_id: int, session: SessionDep, actor: ActorDep) -> dict[str, Any]:
    get_project(session, project_id)
    mirror_svc.request_full_sync(session)
    session.commit()
    return mirror_svc.status(session)
