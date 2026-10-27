"""存量导入接口：新建批次（后台扫描）→ 预演报告 → 改报告导回 → 执行 → 暂停 / 继续 → 整批撤销。"""

import uuid
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import ActorDep, SessionDep
from app.config import import_roots
from app.models import Job, JobStatus
from app.services import importer as svc
from app.services.errors import Conflict, Invalid, NotFound
from app.services.projects import get_writable_project

router = APIRouter(prefix="/api/projects/{project_id}/imports", tags=["存量导入"])
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_REPORT_BYTES = 50 * 1024 * 1024


class BatchCreate(BaseModel):
    source_dir: str = Field(min_length=1, description="要导入的目录（服务器上的路径，须在 DOCBASE_IMPORT_ROOTS 之下）")
    description: str | None = Field(default=None, description="一句共用描述，识别时用")


class TokenBody(BaseModel):
    token: uuid.UUID


@router.get("/roots", summary="允许导入的目录（DOCBASE_IMPORT_ROOTS）")
def roots(project_id: int) -> list[str]:
    return [str(r) for r in import_roots()]


@router.post("", status_code=201, summary="新建导入批次：后台扫描目录（算哈希、不复制），完成后出预演报告")
def create(project_id: int, body: BatchCreate, session: SessionDep, actor: ActorDep) -> dict[str, Any]:
    batch = svc.create_batch(session, project_id, body.source_dir, actor, description=body.description)
    session.commit()
    return svc.batch_out(session, batch)


@router.get("", summary="导入批次列表（新的在前）")
def list_batches(project_id: int, session: SessionDep) -> list[dict[str, Any]]:
    return svc.list_batches(session, project_id)


@router.get("/{batch_id}", summary="导入批次：状态、数量、后台任务进度")
def get_batch(project_id: int, batch_id: int, session: SessionDep) -> dict[str, Any]:
    return svc.batch_out(session, svc.get_batch(session, project_id, batch_id))


@router.get("/{batch_id}/items", summary="导入明细（可按状态、结果筛选，分页）")
def items(project_id: int, batch_id: int, session: SessionDep,
          state: Literal["new", "duplicate", "existing", "old_version", "unreadable"] | None = None,
          result: Literal["pending", "done", "failed", "skipped", "undone", "kept"] | None = None,
          unclassified: bool | None = None, suspected: bool | None = None,
          offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=1000)
          ) -> dict[str, Any]:
    return svc.list_items(session, project_id, batch_id, state=state, result=result,
                          unclassified=unclassified, suspected=suspected, offset=offset, limit=limit)


@router.get("/{batch_id}/report.xlsx", response_class=Response,
            summary="预演报告 Excel（黄色的列可以改：处理、种类编号、设备编号、模块、来源、标题）")
def report(project_id: int, batch_id: int, session: SessionDep, lang: Literal["zh", "en"] = "zh"):
    data = svc.export_report(session, project_id, batch_id, lang)
    name = f"import_{batch_id}_report.xlsx"
    return Response(content=data, media_type=XLSX,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


@router.post("/{batch_id}/report/preview", summary="导回改过的预演报告：列出每行改了什么（不写入）")
async def report_preview(project_id: int, batch_id: int, file: UploadFile, session: SessionDep,
                         actor: ActorDep) -> dict[str, Any]:
    get_writable_project(session, project_id)
    data = await file.read(MAX_REPORT_BYTES + 1)
    if len(data) > MAX_REPORT_BYTES:
        raise Invalid("import_report.too_large", "文件超过 50 MB")
    result = svc.parse_report(session, project_id, batch_id, data)
    job = Job(kind=svc.REPORT_KIND, project_id=project_id, status=JobStatus.SUCCEEDED,
              payload={"batch_id": batch_id, "changes": result["changes"]},
              result={"stage": "previewed", "ok": result["ok"]}, created_by=actor.id,
              finished_at=datetime.now(UTC))
    session.add(job)
    session.commit()
    return {"token": job.token, **result}


@router.post("/{batch_id}/report/confirm", summary="确认导回（带预览返回的 token）")
def report_confirm(project_id: int, batch_id: int, body: TokenBody, session: SessionDep,
                   actor: ActorDep) -> dict[str, Any]:
    job = session.scalar(select(Job).where(Job.token == body.token, Job.kind == svc.REPORT_KIND))
    if job is None or job.project_id != project_id or job.payload.get("batch_id") != batch_id:
        raise NotFound("import_report.not_found", "找不到这次预览，请重新上传")
    if not (job.result or {}).get("ok"):
        raise Conflict("import_report.has_errors", "预览里还有错误，不能导入")
    if (job.result or {}).get("stage") == "applied":
        raise Conflict("import_report.already_applied", "这次预览已经导入过了")
    result = svc.apply_report(session, project_id, batch_id, job.payload["changes"], actor)
    job.result = {**job.result, "stage": "applied"}
    session.commit()
    return {**result, "batch": svc.batch_out(session, svc.get_batch(session, project_id, batch_id))}


def _act(fn, project_id: int, batch_id: int, session, *args) -> dict[str, Any]:
    batch = fn(session, project_id, batch_id, *args)
    session.commit()
    return svc.batch_out(session, batch)


@router.post("/{batch_id}/execute", summary="执行：后台把要导入的文件复制进存储（原目录不动）")
def execute(project_id: int, batch_id: int, session: SessionDep, actor: ActorDep) -> dict[str, Any]:
    return _act(svc.start_execute, project_id, batch_id, session, actor)


@router.post("/{batch_id}/pause", summary="暂停当前的后台任务（扫描 / 执行 / 撤销）")
def pause(project_id: int, batch_id: int, session: SessionDep, actor: ActorDep) -> dict[str, Any]:
    return _act(svc.set_paused, project_id, batch_id, session, True)


@router.post("/{batch_id}/resume", summary="继续（暂停或失败后从断点接着做）")
def resume(project_id: int, batch_id: int, session: SessionDep, actor: ActorDep) -> dict[str, Any]:
    return _act(svc.set_paused, project_id, batch_id, session, False)


@router.post("/{batch_id}/undo", summary="整批撤销：删除这一批建的文件版本（已提交给客户的、又被升过版的不撤）")
def undo(project_id: int, batch_id: int, session: SessionDep, actor: ActorDep) -> dict[str, Any]:
    return _act(svc.start_undo, project_id, batch_id, session, actor)
