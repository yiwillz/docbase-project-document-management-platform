"""MDL 导出与导回接口。"""

import uuid
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.deps import ActorDep, SessionDep, save_and_regenerate
from app.models import Job, JobStatus
from app.services import mdl_export as svc
from app.services.errors import Conflict, Invalid, NotFound
from app.services.projects import get_project, get_writable_project

router = APIRouter(prefix="/api/projects/{project_id}", tags=["MDL 导出与导回"])

MEDIA = {"xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
         "pdf": "application/pdf"}
JOB_KIND = "mdl_return"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
Lang = Literal["en", "zh"]
Fmt = Literal["xlsx", "pdf"]


def _file(data: bytes, filename: str, fmt: str, headers: dict[str, str] | None = None) -> Response:
    return Response(content=data, media_type=MEDIA[fmt], headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        **(headers or {})})


class IssueBody(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "examples": [{"description": "合同签订后首次发出", "lang": "en", "format": "pdf"}]})

    description: str | None = Field(default=None, description="版本说明，如对应的发文号")
    lang: Lang = "en"
    format: Fmt = Field(default="xlsx", description="xlsx 或 pdf")


class ConfirmBody(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "examples": [{"token": "00000000-0000-0000-0000-000000000000"}]})

    token: uuid.UUID


@router.get("/mdl/export", response_class=Response,
            summary="内部预览：按当前结构导出 MDL（Excel 或 PDF，标草稿，不记版本）")
def export_draft(project_id: int, session: SessionDep, lang: Lang = "en", format: Fmt = "xlsx"):
    project = get_project(session, project_id)
    data = svc.export_current(session, project_id, lang=lang, fmt=format)
    return _file(data, f"{project.code}_MDL_draft.{format}", format)


@router.post("/mdl/issue", response_class=Response,
             summary="发给客户：记一个 MDL 版本并导出（结构没变时沿用上一版的版本号）")
def issue(project_id: int, session: SessionDep, actor: ActorDep, body: IssueBody | None = None):
    body = body or IssueBody()
    project = get_project(session, project_id)
    data, version, new = svc.issue(session, project_id, actor, lang=body.lang, fmt=body.format,
                                   description=body.description)
    session.commit()
    label = f"Rev.{version.rev_no}"
    return _file(data, f"{project.code}_MDL_{label}.{body.format}", body.format,
                 {"X-MDL-Revision": label, "X-MDL-New-Revision": "true" if new else "false"})


@router.get("/mdl-versions/{rev_no}/export", response_class=Response,
            summary="重新导出某个历史版本（结构用那一版，状态用当前的）")
def export_version(project_id: int, rev_no: int, session: SessionDep, lang: Lang = "en",
                   format: Fmt = "xlsx"):
    project = get_project(session, project_id)
    data = svc.export_version(session, project_id, rev_no, lang=lang, fmt=format)
    return _file(data, f"{project.code}_MDL_Rev.{rev_no}.{format}", format)


@router.post("/mdl/import/preview",
             summary="导回改过的 MDL：预览差异（只认平台导出的本项目文件；不写入）")
async def import_preview(project_id: int, file: UploadFile, session: SessionDep,
                         actor: ActorDep) -> dict[str, Any]:
    get_writable_project(session, project_id)
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise Invalid("mdl_import.too_large", "文件超过 10 MB")
    result = svc.parse_return(session, project_id, data)
    job = Job(kind=JOB_KIND, project_id=project_id, status=JobStatus.SUCCEEDED,
              payload={"filename": file.filename, "changes": result["changes"]},
              result={"stage": "previewed", "ok": result["ok"]},
              created_by=actor.id, finished_at=datetime.now(UTC))
    session.add(job)
    session.commit()
    return {"token": job.token, **result}


@router.post("/mdl/import/confirm", summary="确认导回（带预览返回的 token）")
def import_confirm(project_id: int, body: ConfirmBody, session: SessionDep, actor: ActorDep
                   ) -> dict[str, int]:
    job = session.scalar(select(Job).where(Job.token == body.token, Job.kind == JOB_KIND))
    if job is None or job.project_id != project_id:
        raise NotFound("mdl_import.not_found", "找不到这次预览，请重新上传")
    if not (job.result or {}).get("ok"):
        raise Conflict("mdl_import.has_errors", "预览里还有错误，不能导入")
    if (job.result or {}).get("stage") == "imported":
        raise Conflict("mdl_import.already_imported", "这次预览已经导入过了")
    counts = svc.apply_return(session, project_id, job.payload["changes"], actor,
                              source=job.payload.get("filename"))
    job.result = {**job.result, "stage": "imported", "imported": counts}
    save_and_regenerate(session, project_id, actor)
    return counts
