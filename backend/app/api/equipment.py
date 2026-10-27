"""设备接口：设备种类、设备、单台出厂编号、历史与恢复、模块-设备种类对应、设备清单导入。"""

import json
import uuid
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Form, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.deps import ActorDep, SessionDep, save_and_regenerate
from app.api.projects import ReasonBody
from app.models import Job, JobStatus
from app.services import equipment as svc
from app.services import equipment_import as importer
from app.services import usage
from app.services.errors import Conflict, Invalid, NotFound
from app.services.projects import get_writable_project

router = APIRouter(tags=["设备"])

P = "/api/projects/{project_id}"
JOB_KIND = "equipment_import"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _examples(*items: dict[str, Any]) -> ConfigDict:
    return ConfigDict(json_schema_extra={"examples": list(items)})


# ---------------------------------------------------------------- 设备种类


class TypeCreate(BaseModel):
    model_config = _examples({"name": "种类A"})

    name: str = Field(min_length=1, max_length=200)


class TypeUpdate(BaseModel):
    model_config = _examples({"name": "种类A设备", "reason": "统一叫法"})

    name: str | None = Field(default=None, min_length=1, max_length=200)
    reason: str | None = None


class TypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    sort_order: int
    active: bool


@router.get(f"{P}/equipment-types", response_model=list[TypeOut], summary="设备种类列表")
def list_types(project_id: int, session: SessionDep, include_inactive: bool = False):
    return svc.list_equipment_types(session, project_id, include_inactive=include_inactive)


@router.post(f"{P}/equipment-types", response_model=TypeOut, status_code=201,
             summary="新建设备种类")
def create_type(project_id: int, body: TypeCreate, session: SessionDep, actor: ActorDep):
    item = svc.create_equipment_type(session, project_id, body.model_dump(), actor)
    session.commit()
    return item


@router.patch(f"{P}/equipment-types/{{type_id}}", response_model=TypeOut, summary="修改设备种类")
def update_type(project_id: int, type_id: int, body: TypeUpdate, session: SessionDep,
                actor: ActorDep):
    changes = body.model_dump(exclude_unset=True)
    reason = changes.pop("reason", None)
    item = svc.update_equipment_type(session, project_id, type_id, changes, actor, reason=reason)
    session.commit()
    return item


@router.post(f"{P}/equipment-types/{{type_id}}/deactivate", response_model=TypeOut,
             summary="停用设备种类")
def deactivate_type(project_id: int, type_id: int, session: SessionDep, actor: ActorDep,
                    body: ReasonBody | None = None):
    item = svc.set_equipment_type_active(session, project_id, type_id, False, actor,
                                         reason=body.reason if body else None)
    session.commit()
    return item


@router.get(f"{P}/equipment-types/{{type_id}}/usage", summary="设备种类能不能删除（下面没有设备才能删）")
def equipment_type_usage(project_id: int, type_id: int, session: SessionDep) -> dict[str, Any]:
    from app.models import EquipmentType
    from app.services.crud import get_in_project

    t = get_in_project(session, EquipmentType, type_id, project_id, "equipment_type")
    found = usage.equipment_type_usage(session, project_id, t)
    return {"deletable": not found, "usage": found}


@router.delete(f"{P}/equipment-types/{{type_id}}", status_code=204, summary="删除没有设备的设备种类")
def delete_equipment_type(project_id: int, type_id: int, session: SessionDep, actor: ActorDep,
                          reason: str | None = None):
    usage.delete_equipment_type(session, project_id, type_id, actor, reason=reason)
    save_and_regenerate(session, project_id, actor)


@router.post(f"{P}/equipment-types/{{type_id}}/activate", response_model=TypeOut,
             summary="启用设备种类")
def activate_type(project_id: int, type_id: int, session: SessionDep, actor: ActorDep,
                  body: ReasonBody | None = None):
    item = svc.set_equipment_type_active(session, project_id, type_id, True, actor,
                                         reason=body.reason if body else None)
    session.commit()
    return item


# ---------------------------------------------------------------- 模块-设备种类对应


class ModuleTypesBody(BaseModel):
    model_config = _examples({"equipment_type_ids": [1, 2]})

    equipment_type_ids: list[int] = Field(description="这个模块对应的设备种类；空列表表示不对应")
    reason: str | None = None


@router.get(f"{P}/modules/{{module_id}}/equipment-types", response_model=list[TypeOut],
            summary="模块对应的设备种类")
def module_types(project_id: int, module_id: int, session: SessionDep):
    return svc.list_module_equipment_types(session, project_id, module_id)


@router.put(f"{P}/modules/{{module_id}}/equipment-types", response_model=list[TypeOut],
            summary="设置模块对应的设备种类（一个模块可对应多个，也可不对应）")
def set_module_types(project_id: int, module_id: int, body: ModuleTypesBody,
                     session: SessionDep, actor: ActorDep):
    result = svc.set_module_equipment_types(session, project_id, module_id,
                                            body.equipment_type_ids, actor, reason=body.reason)
    save_and_regenerate(session, project_id, actor)
    return result


# ---------------------------------------------------------------- 设备


class EquipmentFields(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=500)
    equipment_type_id: int | None = None
    vendor: str | None = None
    quantity: int | None = Field(default=None, ge=1, description="台数；单台记录只在填出厂编号时才建")
    aliases: list[str] | None = None
    attrs: dict[str, Any] | None = Field(default=None, description="其他属性（设备清单里的其他列）")


class EquipmentCreate(EquipmentFields):
    model_config = _examples({"tag": "72", "name": "Mixer A", "vendor": "Vendor A",
                              "quantity": 2, "aliases": ["Mixer"]})

    tag: str | None = Field(default=None, max_length=50,
                            description="设备编号（选填）：有编号的项目内唯一、不复用；没编号的用名称区分")
    name: str = Field(min_length=1, max_length=500)


class EquipmentUpdate(EquipmentFields):
    model_config = _examples({"name": "Mixer A (rev)", "reason": "按供应商资料更正"})

    tag: str | None = Field(default=None, max_length=50,
                            description="设备编号；传空表示改成没编号；挂了文件后不能改")
    reason: str | None = None


class EquipmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tag: str | None
    name: str
    equipment_type_id: int | None
    vendor: str | None
    quantity: int | None
    aliases: list[str]
    attrs: dict[str, Any]
    sort_order: int
    active: bool
    created_at: datetime
    updated_at: datetime


class UnitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    seq: int
    serial_no: str | None
    active: bool


class SerialsBody(BaseModel):
    model_config = _examples({"serials": [{"seq": 1, "serial_no": "SN-001"},
                                          {"seq": 2, "serial_no": "SN-002"}]})

    class Serial(BaseModel):
        seq: int = Field(ge=1)
        serial_no: str | None = None

    serials: list[Serial]
    reason: str | None = None


class HistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    action: str
    changes: dict[str, Any]
    actor_name: str | None
    reason: str | None
    created_at: datetime


class RestoreBody(BaseModel):
    model_config = _examples({"change_log_id": 1, "reason": "改错了，恢复"})

    change_log_id: int = Field(description="要撤回的那条修改记录的 id（见历史）")
    reason: str | None = None


@router.get(f"{P}/equipment", response_model=list[EquipmentOut], summary="设备列表")
def list_equipment(project_id: int, session: SessionDep, include_inactive: bool = False,
                   equipment_type_id: int | None = None):
    return svc.list_equipment(session, project_id, include_inactive=include_inactive,
                              equipment_type_id=equipment_type_id)


@router.post(f"{P}/equipment", response_model=EquipmentOut, status_code=201, summary="新建设备")
def create_equipment(project_id: int, body: EquipmentCreate, session: SessionDep,
                     actor: ActorDep):
    item = svc.create_equipment(session, project_id, body.model_dump(exclude_unset=True), actor)
    save_and_regenerate(session, project_id, actor)
    return item


@router.get(f"{P}/equipment/{{equipment_id}}", response_model=EquipmentOut, summary="设备详情")
def get_equipment(project_id: int, equipment_id: int, session: SessionDep):
    return svc.get_equipment(session, project_id, equipment_id)


@router.patch(f"{P}/equipment/{{equipment_id}}", response_model=EquipmentOut, summary="修改设备")
def update_equipment(project_id: int, equipment_id: int, body: EquipmentUpdate,
                     session: SessionDep, actor: ActorDep):
    changes = body.model_dump(exclude_unset=True)
    reason = changes.pop("reason", None)
    item = svc.update_equipment(session, project_id, equipment_id, changes, actor, reason=reason)
    save_and_regenerate(session, project_id, actor)
    return item


@router.post(f"{P}/equipment/{{equipment_id}}/deactivate", response_model=EquipmentOut,
             summary="停用设备（编号仍占用；对应应交项作废）")
def deactivate_equipment(project_id: int, equipment_id: int, session: SessionDep,
                         actor: ActorDep, body: ReasonBody | None = None):
    item = svc.set_equipment_active(session, project_id, equipment_id, False, actor,
                                    reason=body.reason if body else None)
    save_and_regenerate(session, project_id, actor)
    return item


@router.get(f"{P}/equipment/{{equipment_id}}/usage",
            summary="设备用过没有（没用过的能删除；用过的只能停用）")
def equipment_usage(project_id: int, equipment_id: int, session: SessionDep) -> dict[str, Any]:
    e = svc.get_equipment(session, project_id, equipment_id)
    found = usage.equipment_usage(session, project_id, e)
    return {"deletable": not found, "usage": found}


@router.delete(f"{P}/equipment/{{equipment_id}}", status_code=204,
               summary="删除没用过的设备（编号释放；用过的只能停用）")
def delete_equipment(project_id: int, equipment_id: int, session: SessionDep, actor: ActorDep,
                     reason: str | None = None):
    usage.delete_equipment(session, project_id, equipment_id, actor, reason=reason)
    save_and_regenerate(session, project_id, actor)


class BulkIdsBody(BaseModel):
    ids: list[int] = Field(min_length=1)
    reason: str | None = None


@router.post(f"{P}/equipment/bulk-delete", summary="批量删除设备：没用过的删掉，用过的跳过并说明原因")
def bulk_delete_equipment(project_id: int, body: BulkIdsBody, session: SessionDep, actor: ActorDep
                          ) -> dict[str, Any]:
    result = usage.bulk_delete_equipment(session, project_id, body.ids, actor, reason=body.reason)
    save_and_regenerate(session, project_id, actor)
    return result


@router.post(f"{P}/equipment/bulk-deactivate", summary="批量停用设备（编号仍占用；对应应交项作废）")
def bulk_deactivate_equipment(project_id: int, body: BulkIdsBody, session: SessionDep, actor: ActorDep
                              ) -> dict[str, Any]:
    for eid in body.ids:
        svc.set_equipment_active(session, project_id, eid, False, actor, reason=body.reason)
    save_and_regenerate(session, project_id, actor)
    return {"deactivated": body.ids}


@router.post(f"{P}/equipment/{{equipment_id}}/activate", response_model=EquipmentOut,
             summary="启用设备")
def activate_equipment(project_id: int, equipment_id: int, session: SessionDep,
                       actor: ActorDep, body: ReasonBody | None = None):
    item = svc.set_equipment_active(session, project_id, equipment_id, True, actor,
                                    reason=body.reason if body else None)
    save_and_regenerate(session, project_id, actor)
    return item


@router.get(f"{P}/equipment/{{equipment_id}}/history", response_model=list[HistoryOut],
            summary="设备的字段级修改历史（新的在前）")
def equipment_history(project_id: int, equipment_id: int, session: SessionDep):
    return svc.history(session, project_id, equipment_id)


@router.post(f"{P}/equipment/{{equipment_id}}/restore", response_model=EquipmentOut,
             summary="恢复到某次修改之前（把那次改过的字段恢复成修改前的值）")
def restore_equipment(project_id: int, equipment_id: int, body: RestoreBody,
                      session: SessionDep, actor: ActorDep):
    item = svc.restore_before(session, project_id, equipment_id, body.change_log_id, actor,
                              reason=body.reason)
    save_and_regenerate(session, project_id, actor)
    return item


@router.get(f"{P}/equipment/{{equipment_id}}/units", response_model=list[UnitOut],
            summary="单台列表（出厂编号）")
def list_units(project_id: int, equipment_id: int, session: SessionDep,
               include_inactive: bool = False):
    return svc.list_units(session, project_id, equipment_id, include_inactive=include_inactive)


@router.put(f"{P}/equipment/{{equipment_id}}/units", response_model=list[UnitOut],
            summary="填写出厂编号（按第几台）")
def set_serials(project_id: int, equipment_id: int, body: SerialsBody, session: SessionDep,
                actor: ActorDep):
    units = svc.set_serial_numbers(session, project_id, equipment_id,
                                   {s.seq: s.serial_no for s in body.serials}, actor,
                                   reason=body.reason)
    session.commit()
    return units


# ---------------------------------------------------------------- 设备清单导入


class ConfirmBody(BaseModel):
    model_config = _examples({"token": "00000000-0000-0000-0000-000000000000",
                              "deactivate_missing": False})

    token: uuid.UUID
    deactivate_missing: bool = Field(default=False,
                                     description="把清单里没有了的设备一起停用（默认只提示）")


@router.get("/api/equipment-template", response_class=Response,
            summary="下载设备清单标准模板（空白 Excel）")
def download_template(lang: Literal["zh", "en"] = "zh"):
    filename = "设备清单导入模板.xlsx" if lang == "zh" else "equipment_template.xlsx"
    return Response(content=importer.build_template(lang), media_type=XLSX,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


@router.post(f"{P}/equipment-import/preview",
             summary="上传设备清单预览：自动认表头；认不全时用 mapping 指定列后再预览（不写入）")
async def preview(project_id: int, file: UploadFile, session: SessionDep, actor: ActorDep,
                  sheet: str | None = Form(default=None, description="工作表名，不填用第一张"),
                  header_row: int | None = Form(default=None, description="表头在第几行，不填自动找"),
                  mapping: str | None = Form(
                      default=None,
                      description='手动指定列对应（JSON），如 {"B": "tag", "D": "name", "F": "ignore"}；'
                                  "可用 tag / name / equipment_type / vendor / quantity / aliases / "
                                  "attr（存进其他属性）/ ignore（忽略）")) -> dict[str, Any]:
    get_writable_project(session, project_id)
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise Invalid("equipment_import.too_large", "文件超过 10 MB")
    try:
        mapping_dict = json.loads(mapping) if mapping else None
    except json.JSONDecodeError as exc:
        raise Invalid("equipment_import.bad_mapping_json", f"mapping 不是合法的 JSON：{exc}") from exc
    parsed = importer.parse_equipment(data, sheet=sheet, header_row=header_row,
                                      mapping=mapping_dict)
    diff = importer.compare(session, project_id, parsed.rows) if parsed.ok else None
    job = Job(kind=JOB_KIND, project_id=project_id, status=JobStatus.SUCCEEDED,
              payload={"filename": file.filename, "rows": parsed.rows},
              result={"stage": "previewed", "ok": parsed.ok,
                      "summary": diff["summary"] if diff else None},
              created_by=actor.id, finished_at=datetime.now(UTC))
    session.add(job)
    session.commit()
    return {"token": job.token, "ok": parsed.ok, **parsed.to_json(),
            "rows": len(parsed.rows), "diff": diff}


class PastedRow(BaseModel):
    tag: str | None = None
    name: str | None = None
    equipment_type: str | None = None
    vendor: str | None = None
    quantity: str | int | None = None
    aliases: str | None = None


class PastedRows(BaseModel):
    model_config = _examples({"rows": [{"tag": "72", "name": "Mixer A", "equipment_type": "Alpha",
                                        "quantity": 2},
                                       {"name": "Beta part", "equipment_type": "Part", "quantity": 3000}]})

    rows: list[PastedRow] = Field(min_length=1, max_length=5000)


@router.post(f"{P}/equipment-import/preview-rows",
             summary="页面上粘贴的多行设备预览（和 Excel 导入同样比对、同样用 confirm 确认）")
def preview_rows(project_id: int, body: PastedRows, session: SessionDep, actor: ActorDep
                 ) -> dict[str, Any]:
    get_writable_project(session, project_id)
    parsed = importer.parse_rows([r.model_dump() for r in body.rows])
    diff = importer.compare(session, project_id, parsed.rows) if parsed.ok else None
    job = Job(kind=JOB_KIND, project_id=project_id, status=JobStatus.SUCCEEDED,
              payload={"filename": "粘贴", "rows": parsed.rows},
              result={"stage": "previewed", "ok": parsed.ok,
                      "summary": diff["summary"] if diff else None},
              created_by=actor.id, finished_at=datetime.now(UTC))
    session.add(job)
    session.commit()
    return {"token": job.token, "ok": parsed.ok, **parsed.to_json(), "rows": len(parsed.rows),
            "diff": diff}


@router.post(f"{P}/equipment-import/confirm",
             summary="确认导入（带预览返回的 token）；导入后自动重算应交项")
def confirm(project_id: int, body: ConfirmBody, session: SessionDep, actor: ActorDep
            ) -> dict[str, int]:
    job = session.scalar(select(Job).where(Job.token == body.token, Job.kind == JOB_KIND))
    if job is None or job.project_id != project_id:
        raise NotFound("equipment_import.not_found", "找不到这次预览，请重新上传")
    if not (job.result or {}).get("ok"):
        raise Conflict("equipment_import.has_errors", "预览里还有错误，不能导入")
    if (job.result or {}).get("stage") == "imported":
        raise Conflict("equipment_import.already_imported", "这次预览已经导入过了")
    counts = importer.import_equipment(session, project_id, job.payload["rows"], actor,
                                       deactivate_missing=body.deactivate_missing,
                                       source=job.payload.get("filename"))
    job.result = {**job.result, "stage": "imported", "imported": counts}
    save_and_regenerate(session, project_id, actor)
    return counts
