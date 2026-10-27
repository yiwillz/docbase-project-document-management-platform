"""存文件接口：方式一（指定位置）和方式二（智能投递）的上传预览 → 改预览 → 确认存入；
分块上传（断点续传）；待归类与归类；应交项的文件和版本；下载；命名模板；
浏览与检索（目录树、文件列表、详情、重新归类、回收区、在线预览）。"""

import io
import uuid
from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from app.api.deliverables import FiltersDep
from app.api.deps import ActorDep, SessionDep
from app.services import browse, naming
from app.services import files as svc
from app.services.errors import Invalid
from app.services.projects import get_project, get_writable_project
from app.services.storage import get_storage

router = APIRouter(prefix="/api/projects/{project_id}", tags=["存文件"])


class Overrides(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "source": "vendor", "title": "Cleanroom 1F", "file_date": "2026-02-26", "file_id": "new"}]})

    source: Literal["client", "vendor", "ours"] | None = Field(
        default=None, description="来源；升版时跟原文件一致，不能改")
    title: str | None = Field(default=None, max_length=300, description="文件标题；空着用原文件名得出的")
    file_date: date | None = Field(default=None, description="文件日期；空着按 文件名 → 修改时间 → 上传日")
    file_id: int | Literal["new"] | None = Field(
        default=None, description="new = 作为新文件；文件 id = 作为它的新版本；空着自动判断")
    deliverable_ids: list[int] | None = Field(
        default=None, description="换位置：应交项 id（几台设备给几个）；空列表 = 存进待归类")
    record_reply: bool | None = Field(
        default=None, description="客户退回的带章文件：同时按识别出的审核代码给传送单记客户回复")


def _modified(value: str | None) -> date | None:
    """浏览器给的文件最后修改时间：毫秒时间戳或 ISO 日期。"""
    if not value:
        return None
    try:
        if value.isdigit():
            return datetime.fromtimestamp(int(value) / 1000, UTC).date()
        return date.fromisoformat(value[:10])
    except (ValueError, OverflowError, OSError) as exc:
        raise Invalid("file.bad_modified", f"文件修改时间不对：{value}") from exc


def _ids(value: str) -> list[int]:
    try:
        return [int(x) for x in value.replace(" ", "").split(",") if x]
    except ValueError as exc:
        raise Invalid("file.bad_target", f"应交项编号不对：{value}") from exc


def _staged(session, project_id: int, file: UploadFile | None, upload_id: str | None
            ) -> tuple[str, str, int, str]:
    """一次传完的文件收进暂存区；分块传完的直接用 → (暂存编号, sha256, 字节数, 原文件名)。"""
    storage = get_storage()
    if upload_id:
        try:
            token = uuid.UUID(upload_id)
        except ValueError as exc:
            raise Invalid("upload.not_found", "上传编号不对") from exc
        return svc.finish_upload(session, project_id, storage, token)
    if file is None:
        raise Invalid("file.missing", "没有文件：请上传文件，或给出分块上传的编号")
    original = svc.clean_name(file.filename or "")
    staging_id, sha, size = storage.stage(file.file, max_bytes=svc.MAX_UPLOAD_BYTES)
    return staging_id, sha, size, original


@router.post("/files/preview", summary="方式一：上传到指定应交项并预览（文件名、新文件还是升版、是否重复）；不入库")
def upload_preview(
    project_id: int, session: SessionDep, actor: ActorDep, file: UploadFile | None = None,
    deliverable_ids: str = Form(description="应交项 id，几台设备用逗号分隔，如 12,13"),
    modified: str | None = Form(default=None, description="文件最后修改时间（毫秒时间戳或 YYYY-MM-DD）"),
    upload_id: str | None = Form(default=None, description="大文件先分块上传，这里给上传编号（不再带文件）"),
) -> dict[str, Any]:
    get_writable_project(session, project_id)
    ids = _ids(deliverable_ids)
    svc.load_target(session, project_id, ids)  # 位置不对先报错，不白传
    staging_id, sha, size, original = _staged(session, project_id, file, upload_id)
    result = svc.preview_staged(session, project_id, staging_id=staging_id, sha256=sha, size=size,
                                storage=get_storage(), original_name=original, deliverable_ids=ids,
                                modified=_modified(modified), actor=actor)
    session.commit()
    return result


@router.post("/drop/preview", summary="方式二（智能投递）：上传一个文件，按描述 / 文件夹 / 文件名识别位置并预览；不入库")
def drop_preview(
    project_id: int, session: SessionDep, actor: ActorDep, file: UploadFile | None = None,
    description: str | None = Form(default=None, description="一句共用描述，如 \"Vendor A 的设备数据表\""),
    folder: str | None = Form(default=None, description="拖入文件夹时的相对路径（不含文件名）"),
    vendor: str | None = Form(default=None, description="先选的供应商，只在这家的设备里找"),
    modified: str | None = Form(default=None, description="文件最后修改时间（毫秒时间戳或 YYYY-MM-DD）"),
    upload_id: str | None = Form(default=None, description="大文件先分块上传，这里给上传编号"),
) -> dict[str, Any]:
    get_writable_project(session, project_id)
    staging_id, sha, size, original = _staged(session, project_id, file, upload_id)
    result = svc.drop_preview(session, project_id, get_storage(), staging_id=staging_id, sha256=sha,
                              size=size, original_name=original, modified=_modified(modified),
                              description=description, folder=folder, vendor=vendor, actor=actor)
    session.commit()
    return result


# ---------------------------------------------------------------- 分块上传（断点续传）


class UploadBegin(BaseModel):
    filename: str = Field(min_length=1, max_length=500)
    size: int = Field(gt=0, description="文件总字节数")


@router.post("/uploads", summary="开始一次分块上传（大文件；断了可以按已收到的字节数接着传）")
def upload_begin(project_id: int, body: UploadBegin, session: SessionDep, actor: ActorDep
                 ) -> dict[str, Any]:
    result = svc.begin_upload(session, project_id, get_storage(), filename=body.filename,
                              size=body.size, actor=actor)
    session.commit()
    return result


@router.get("/uploads/{upload_id}", summary="分块上传已收到多少字节（断了以后从这里接着传）")
def upload_get(project_id: int, upload_id: uuid.UUID, session: SessionDep) -> dict[str, Any]:
    return svc.upload_status(session, project_id, get_storage(), upload_id)


@router.put("/uploads/{upload_id}", summary="传一块：请求体是原始字节，offset 必须等于已收到的字节数")
async def upload_put(project_id: int, upload_id: uuid.UUID, request: Request, session: SessionDep,
                     actor: ActorDep, offset: int = Query(ge=0)) -> dict[str, Any]:
    data = await request.body()

    def write() -> dict[str, Any]:
        result = svc.upload_chunk(session, project_id, get_storage(), upload_id, offset, io.BytesIO(data))
        session.commit()
        return result

    return await run_in_threadpool(write)


# ---------------------------------------------------------------- 待归类


class ClassifyBody(BaseModel):
    deliverable_ids: list[int] = Field(min_length=1, description="挂到哪个应交项（几台设备给几个）")
    reason: str | None = None


@router.get("/files/unclassified", summary="待归类的文件（没有挂到任何应交项上的）")
def unclassified(project_id: int, session: SessionDep) -> list[dict[str, Any]]:
    return svc.list_unclassified(session, project_id)


@router.post("/files/{file_id}/classify", summary="归类：把待归类的文件挂到应交项上")
def classify(project_id: int, file_id: int, body: ClassifyBody, session: SessionDep,
             actor: ActorDep) -> dict[str, Any]:
    result = svc.classify(session, project_id, file_id, body.deliverable_ids, actor,
                          reason=body.reason)
    session.commit()
    return result


@router.post("/files/uploads/{token}/plan", summary="改来源 / 标题 / 日期 / 升版对象后重新预览")
def upload_replan(project_id: int, token: uuid.UUID, body: Overrides, session: SessionDep
                  ) -> dict[str, Any]:
    return svc.replan(session, project_id, token, body.model_dump(exclude_none=True))


@router.post("/files/uploads/{token}/confirm", summary="确认存入（带预览返回的 token 和改过的内容）")
def upload_confirm(project_id: int, token: uuid.UUID, body: Overrides, session: SessionDep,
                   actor: ActorDep) -> dict[str, Any]:
    result = svc.confirm(session, project_id, get_storage(), token,
                         body.model_dump(exclude_none=True), actor)
    session.commit()
    return result


@router.get("/deliverables/{deliverable_id}/files", summary="应交项的文件，每个带全部版本（新的在前）")
def deliverable_files(project_id: int, deliverable_id: int, session: SessionDep
                      ) -> list[dict[str, Any]]:
    return svc.list_files(session, project_id, deliverable_id)


@router.get("/versions/{version_id}/download", response_class=FileResponse,
            summary="下载某个版本（文件名按当前的结构名称算）")
def download(project_id: int, version_id: int, session: SessionDep):
    version, file = svc.get_version(session, project_id, version_id)
    name = svc.current_name(session, version, file)
    storage = get_storage()
    path = storage.path(version.blob_sha256)
    if path is None or not path.is_file():
        storage.open(version.blob_sha256).close()  # 报"存储不可用"或"文件实体不存在"
    return FileResponse(path, filename=name)


# ---------------------------------------------------------------- 命名模板


class NamingPreview(BaseModel):
    templates: dict[str, Any] = Field(default_factory=dict, description="要试算的模板（不保存）")


@router.get("/naming", summary="命名模板：默认值、项目当前配置、示例文件名")
def naming_config(project_id: int, session: SessionDep) -> dict[str, Any]:
    project = get_project(session, project_id)
    return {"defaults": naming.DEFAULT_TEMPLATES, "templates": project.naming_templates or {},
            "fields": sorted(naming.FIELDS), "samples": naming.samples(project.naming_templates)}


@router.post("/naming/preview", summary="试算模板：返回示例文件名（不保存；保存用修改项目接口）")
def naming_preview(project_id: int, body: NamingPreview, session: SessionDep) -> dict[str, str]:
    get_project(session, project_id)
    return naming.samples(naming.validate_templates(body.templates))


# ---------------------------------------------------------------- 浏览与检索


class MoveBody(BaseModel):
    deliverable_ids: list[int] = Field(description="移到哪个应交项（几台设备给几个）；空列表 = 移回待归类")
    reason: str | None = None


@router.get("/files/tree", summary="文件浏览的目录树（三种视图），每个节点带文件数")
def files_tree(project_id: int, session: SessionDep,
               view: Literal["milestone", "equipment", "module"] = "milestone") -> dict[str, Any]:
    return browse.tree(session, project_id, view)


@router.get("/files/recycle", summary="回收区：进了回收区、还没清理的版本，带还剩几天清理")
def files_recycle(project_id: int, session: SessionDep) -> list[dict[str, Any]]:
    return browse.recycled_versions(session, project_id)


@router.get("/files", summary="文件列表：按目录树节点、筛选条件、关键字（文件名 / 原文件名 / 标题 / 编号 / 设备）查，分页")
def files_list(
    project_id: int, session: SessionDep, filters: FiltersDep,
    view: Literal["milestone", "equipment", "module"] = "milestone",
    node: str = Query(default="", description="目录树节点；unclassified = 待归类，recycle = 回收区"),
    source: list[Literal["client", "vendor", "ours"]] | None = Query(default=None, description="来源"),
    sort: Literal["name", "uploaded_at", "size"] = "uploaded_at",
    order: Literal["asc", "desc"] = "desc",
    offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    q, filters.q = filters.q, None  # 这里的关键字查文件，不查应交项
    return browse.list_files(session, project_id, view=view, node=node, filters=filters,
                             sources=source, q=q, sort=sort, order=order, offset=offset, limit=limit)


@router.get("/files/{file_id}", summary="文件详情：所在位置、全部版本、操作记录")
def file_detail(project_id: int, file_id: int, session: SessionDep) -> dict[str, Any]:
    return browse.detail(session, project_id, file_id)


@router.post("/files/{file_id}/move", summary="重新归类：移到别的应交项，或移回待归类（已交给客户的不能移）")
def file_move(project_id: int, file_id: int, body: MoveBody, session: SessionDep,
              actor: ActorDep) -> dict[str, Any]:
    result = browse.move(session, project_id, file_id, body.deliverable_ids, actor, reason=body.reason)
    session.commit()
    return result


@router.get("/versions/{version_id}/preview", response_class=FileResponse,
            summary="在线预览（PDF、图片、纯文本）：浏览器里直接打开，不下载")
def preview_version(project_id: int, version_id: int, session: SessionDep):
    version, file = svc.get_version(session, project_id, version_id)
    kind = browse.media_type(version.original_name)
    if kind not in browse.PREVIEW_TYPES:
        raise Invalid("version.no_preview", "这种文件不能在线预览，请下载", media_type=kind)
    storage = get_storage()
    path = storage.path(version.blob_sha256)
    if path is None or not path.is_file():
        storage.open(version.blob_sha256).close()
    media = f"{kind}; charset=utf-8" if kind == "text/plain" else kind
    # 文件内容是用户上传的：图片、SVG、文本禁止脚本（SVG 里可能带脚本），只当文件看；
    # PDF 要用浏览器自带的阅读器，加 sandbox 会打不开，只禁止浏览器猜类型
    headers = {"X-Content-Type-Options": "nosniff"}
    if kind != "application/pdf":
        headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox"
    return FileResponse(path, media_type=media, filename=svc.current_name(session, version, file),
                        content_disposition_type="inline", headers=headers)
