"""传送单接口：生成 / 补记、查看、修改、作废、客户回复、导出、客户填好后导回。"""

import uuid
from datetime import UTC, date, datetime
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.deps import ActorDep, SessionDep, save_and_regenerate
from app.models import Job, JobStatus, Transmittal, TransmittalItemVersion, TransmittalSource
from app.services import deliverable_query as query
from app.services import transmittal_export as exporter
from app.services import transmittals as svc
from app.services.errors import Conflict, Invalid, NotFound
from app.services.projects import get_project, get_writable_project

router = APIRouter(prefix="/api/projects/{project_id}/transmittals", tags=["传送单与客户回复"])

MEDIA = {"xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
         "pdf": "application/pdf"}
JOB_KIND = "transmittal_return"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class TransmittalCreate(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "deliverable_ids": [1, 2, 3], "subject": "Equipment datasheets for review",
        "submitted_date": "2026-10-24", "revisions": {"1": "Rev A"}}]})

    deliverable_ids: list[int] = Field(min_length=1, max_length=5000)
    subject: str | None = Field(default=None, max_length=500)
    submitted_date: date | None = Field(default=None, description="提交日期（真实日期）；平台生成时默认今天，补记时必填")
    method: str | None = Field(default=None, max_length=50, description="怎么交的：email / client_system / courier / other")
    external_ref: str | None = Field(default=None, max_length=200, description="客户系统单号")
    remarks: str | None = None
    revisions: dict[int, str] = Field(default_factory=dict, description="应交项 id → 客户看到的版次，如 Rev B")


class TransmittalUpdate(BaseModel):
    subject: str | None = Field(default=None, max_length=500)
    submitted_date: date | None = None
    method: str | None = Field(default=None, max_length=50)
    external_ref: str | None = Field(default=None, max_length=200)
    remarks: str | None = None
    reason: str | None = None


class VoidBody(BaseModel):
    reason: str = Field(min_length=1, description="作废原因（必填）")


class ReplyItem(BaseModel):
    item_id: int
    code: str | None = Field(default=None, description="这一份的审核代码；空字符串表示撤回回复")
    reply_date: date | None = None
    reply_comments: str | None = None
    deemed: bool | None = None
    deemed_basis: str | None = None


class ReplyBody(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "code": "1", "reply_date": "2026-11-05", "reply_ref": "CL-021",
        "items": [{"item_id": 2, "code": "3", "reply_comments": "Update nozzle table"}]}]})

    code: str | None = Field(default=None, description="整张单的审核代码（个别不同的写在 items 里）")
    reply_date: date | None = Field(default=None, description="客户回复的真实日期")
    reply_ref: str | None = Field(default=None, max_length=200, description="回函号 / 客户传送单号 / 系统单号")
    reply_comments: str | None = None
    deemed: bool = Field(default=False, description="视为批准（超期未回复，按合同条款）")
    deemed_basis: str | None = Field(default=None, description="视为批准的合同依据（条款号），视为批准时必填")
    items: list[ReplyItem] = Field(default_factory=list)
    reason: str | None = None


class ConfirmBody(BaseModel):
    token: uuid.UUID


def _out(session, project_id: int, t: Transmittal, *, with_items: bool = False) -> dict[str, Any]:
    data = {k: getattr(t, k) for k in ("id", "number", "subject", "submitted_date", "source",
                                       "method", "external_ref", "remarks", "created_by_name",
                                       "void", "void_reason", "created_at")}
    data["review_due"] = svc.review_due(get_project(session, project_id), t)
    if with_items:
        items = svc.items_of(session, t.id)
        rows = {it.id: it for it in query.load_items(
            session, project_id, query.Filters(ids=[i.deliverable_id for i in items]))}
        all_versions: dict[int, list[int]] = {}
        for item_id, vid in session.execute(
                select(TransmittalItemVersion.item_id, TransmittalItemVersion.version_id)
                .where(TransmittalItemVersion.item_id.in_([i.id for i in items]))
                .order_by(TransmittalItemVersion.version_id)):
            all_versions.setdefault(item_id, []).append(vid)
        data["items"] = [{
            "item_id": i.id, "deliverable_id": i.deliverable_id, "revision": i.revision,
            "version_id": i.version_id, "version_ids": all_versions.get(i.id, []),
            "review_code": i.review_code,
            "review_status": i.review_status, "review_final": i.review_final,
            "reply_date": i.reply_date, "reply_ref": i.reply_ref,
            "reply_comments": i.reply_comments, "deemed": i.deemed, "deemed_basis": i.deemed_basis,
            "reply_source": i.reply_source, "replied_by": i.replied_by_name,
            "deliverable": rows[i.deliverable_id].out() if i.deliverable_id in rows else None,
        } for i in items]
    return data


def _create(project_id: int, body: TransmittalCreate, session, actor, source) -> dict[str, Any]:
    t = svc.create_transmittal(session, project_id, body.deliverable_ids,
                               body.model_dump(exclude={"deliverable_ids", "revisions"}), actor,
                               source=source, revisions=body.revisions)
    session.commit()
    return _out(session, project_id, t, with_items=True)


@router.post("", status_code=201, summary="生成传送单：勾选应交项，记下提交，应交项改为已提交")
def create(project_id: int, body: TransmittalCreate, session: SessionDep, actor: ActorDep):
    return _create(project_id, body, session, actor, TransmittalSource.PLATFORM)


@router.post("/record", status_code=201,
             summary="事后补记：在邮件或客户系统里已经交了的（提交日期必填）")
def record_submitted(project_id: int, body: TransmittalCreate, session: SessionDep, actor: ActorDep):
    return _create(project_id, body, session, actor, TransmittalSource.RECORDED)


@router.get("", summary="传送单列表（新的在前），带份数和已回复份数")
def list_all(project_id: int, session: SessionDep, include_void: bool = False):
    return [{**_out(session, project_id, row["transmittal"]), "items": row["items"],
             "replied": row["replied"]}
            for row in svc.list_transmittals(session, project_id, include_void=include_void)]


@router.get("/{transmittal_id}", summary="传送单详情（每份应交项和客户回复）")
def detail(project_id: int, transmittal_id: int, session: SessionDep):
    return _out(session, project_id, svc.get_transmittal(session, project_id, transmittal_id),
                with_items=True)


@router.patch("/{transmittal_id}", summary="改表头（改了提交日期会重新推出应交项状态）")
def update(project_id: int, transmittal_id: int, body: TransmittalUpdate, session: SessionDep,
           actor: ActorDep):
    t = svc.update_transmittal(session, project_id, transmittal_id,
                               body.model_dump(exclude_unset=True, exclude={"reason"}), actor,
                               reason=body.reason)
    session.commit()
    return _out(session, project_id, t)


@router.post("/{transmittal_id}/void", summary="作废（要写原因）；相关应交项按剩下的记录重新推出状态")
def void(project_id: int, transmittal_id: int, body: VoidBody, session: SessionDep, actor: ActorDep):
    t = svc.void_transmittal(session, project_id, transmittal_id, actor, reason=body.reason)
    session.commit()
    return _out(session, project_id, t)


@router.post("/{transmittal_id}/reply",
             summary="录入客户回复：一个结论用到整张单，个别不同的单独给；应交项状态随之更新")
def reply(project_id: int, transmittal_id: int, body: ReplyBody, session: SessionDep,
          actor: ActorDep) -> dict[str, int]:
    result = svc.set_reply(session, project_id, transmittal_id,
                           body.model_dump(exclude={"items", "reason"}), actor,
                           items=[i.model_dump(exclude_unset=True) for i in body.items],
                           reason=body.reason)
    session.commit()
    return result


@router.get("/{transmittal_id}/export", response_class=Response,
            summary="导出传送单（Excel 带客户回复空栏，可让客户填好退回；或 PDF）")
def export(project_id: int, transmittal_id: int, session: SessionDep,
           lang: Literal["en", "zh"] = "en", format: Literal["xlsx", "pdf"] = "xlsx"):
    t = svc.get_transmittal(session, project_id, transmittal_id)
    data = exporter.export(session, project_id, transmittal_id, lang=lang, fmt=format)
    return Response(content=data, media_type=MEDIA[format], headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(f'{t.number}.{format}')}"})


@router.post("/import/preview",
             summary="导回客户填好的传送单 Excel：预览回复（只认平台导出的本项目传送单；不写入）")
async def import_preview(project_id: int, file: UploadFile, session: SessionDep,
                         actor: ActorDep) -> dict[str, Any]:
    get_writable_project(session, project_id)
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise Invalid("transmittal_import.too_large", "文件超过 10 MB")
    result = exporter.parse_return(session, project_id, data, filename=file.filename)
    job = Job(kind=JOB_KIND, project_id=project_id, status=JobStatus.SUCCEEDED,
              payload={"filename": file.filename, "transmittal_id": result["transmittal_id"],
                       "reply_ref": result.get("reply_ref"), "changes": result["changes"]},
              result={"stage": "previewed", "ok": result["ok"]},
              created_by=actor.id, finished_at=datetime.now(UTC))
    session.add(job)
    session.commit()
    return {"token": job.token, **result}


@router.post("/import/confirm", summary="确认导回（带预览返回的 token）")
def import_confirm(project_id: int, body: ConfirmBody, session: SessionDep, actor: ActorDep
                   ) -> dict[str, int]:
    job = session.scalar(select(Job).where(Job.token == body.token, Job.kind == JOB_KIND))
    if job is None or job.project_id != project_id:
        raise NotFound("transmittal_import.not_found", "找不到这次预览，请重新上传")
    if not (job.result or {}).get("ok"):
        raise Conflict("transmittal_import.has_errors", "预览里还有错误，不能导入")
    if (job.result or {}).get("stage") == "imported":
        raise Conflict("transmittal_import.already_imported", "这次预览已经导入过了")
    counts = exporter.apply_return(session, project_id, job.payload["transmittal_id"],
                                   job.payload["changes"], job.payload["reply_ref"], actor)
    job.result = {**job.result, "stage": "imported", "imported": counts}
    save_and_regenerate(session, project_id, actor)
    return counts
