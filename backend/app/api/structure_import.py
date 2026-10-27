"""项目结构标准模板：下载、上传预览、确认导入。

预览结果存进任务表（jobs），返回 token；确认时带 token，才真正写入。
"""

import uuid
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.api.deps import ActorDep, SessionDep
from app.models import Job, JobStatus
from app.services import structure_template as svc
from app.services.deliverables import ensure_deliverables
from app.services.errors import Conflict, Invalid, NotFound
from app.services.projects import get_writable_project

router = APIRouter(tags=["项目结构导入"])

JOB_KIND = "structure_import"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class IssueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    message: str
    sheet: str | None
    row: int | None
    column: str | None


class PreviewOut(BaseModel):
    token: uuid.UUID
    ok: bool
    summary: dict[str, int]
    milestones: list[dict[str, Any]]
    errors: list[IssueOut]
    warnings: list[IssueOut]


class ConfirmBody(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "examples": [{"token": "00000000-0000-0000-0000-000000000000"}]
    })

    token: uuid.UUID


class ConfirmOut(BaseModel):
    milestones: int
    modules: int
    document_types: int
    partitions: int


@router.get("/api/structure-template", summary="下载项目结构导入模板（空白 Excel）",
            response_class=Response)
def download_template(lang: Literal["zh", "en"] = "zh"):
    filename = "项目结构导入模板.xlsx" if lang == "zh" else "structure_template.xlsx"
    return Response(
        content=svc.build_template(lang),
        media_type=XLSX,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


def _tree(parsed: svc.ParsedStructure) -> list[dict[str, Any]]:
    """预览用的简要结构树。"""
    return [
        {
            "code": f"M{m['number']}",
            "name": m.get("name"),
            "document_types": [
                {"submittal_no": d["submittal_no"], "name": d.get("name"),
                 "per_equipment": bool(d.get("per_equipment")), "modules": d["modules"]}
                for d in parsed.document_types if d["milestone"] == m["number"]
            ],
        }
        for m in parsed.milestones
    ]


@router.post("/api/projects/{project_id}/structure-import/preview", response_model=PreviewOut,
             summary="上传填好的模板，预览导入结果（不写入）")
async def preview(project_id: int, file: UploadFile, session: SessionDep, actor: ActorDep):
    get_writable_project(session, project_id)
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise Invalid("structure_import.too_large", "文件超过 10 MB")
    parsed = svc.parse_template(data)
    job = Job(
        kind=JOB_KIND,
        project_id=project_id,
        status=JobStatus.SUCCEEDED,
        payload={"filename": file.filename, "parsed": parsed.to_json()},
        result={"stage": "previewed", "summary": parsed.summary()},
        created_by=actor.id,
        finished_at=datetime.now(UTC),
    )
    session.add(job)
    session.commit()
    return {
        "token": job.token,
        "ok": parsed.ok,
        "summary": parsed.summary(),
        "milestones": _tree(parsed),
        "errors": parsed.errors,
        "warnings": parsed.warnings,
    }


@router.post("/api/projects/{project_id}/structure-import/confirm", response_model=ConfirmOut,
             summary="确认导入（带预览返回的 token；有错误或已导入过则拒绝）")
def confirm(project_id: int, body: ConfirmBody, session: SessionDep, actor: ActorDep):
    job = session.scalar(select(Job).where(Job.token == body.token, Job.kind == JOB_KIND))
    if job is None or job.project_id != project_id:
        raise NotFound("structure_import.not_found", "找不到这次预览，请重新上传")
    if (job.result or {}).get("stage") == "imported":
        raise Conflict("structure_import.already_imported", "这次预览已经导入过了")
    counts = svc.import_structure(session, project_id, job.payload["parsed"], actor,
                                  source=job.payload.get("filename"))
    ensure_deliverables(session, project_id, actor, reason="标准模板导入后生成应交项")
    job.result = {**(job.result or {}), "stage": "imported", "imported": counts}
    session.commit()
    return counts
