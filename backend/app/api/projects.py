"""项目接口。"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import ActorDep, SessionDep, UserDep
from app.services import access, deliverable_query, project_delete
from app.services import projects as svc
from app.services.files import unclassified_counts

router = APIRouter(prefix="/api/projects", tags=["项目"])


class ProjectCreate(BaseModel):
    # 接口文档里 Try it out 默认填的内容；不写的话会带上一堆无效的示例值
    model_config = ConfigDict(
        json_schema_extra={"examples": [{"code": "P-001", "name": "演示项目"}]}
    )

    code: str = Field(min_length=1, max_length=100, description="项目编号，唯一")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    naming_templates: dict[str, Any] = Field(default_factory=dict)
    numbering_rules: dict[str, Any] = Field(default_factory=dict)
    client_name: str | None = Field(default=None, max_length=200, description="客户名称（MDL 表头）")
    contractor_name: str | None = Field(default=None, max_length=200, description="总包名称（MDL 表头）")
    mdl_doc_no: str | None = Field(default=None, max_length=100, description="MDL 文件编号")
    mdl_prepared_by: str | None = Field(default=None, max_length=200, description="MDL 编制人")
    mdl_checked_by: str | None = Field(default=None, max_length=200, description="MDL 审核人")
    mdl_approved_by: str | None = Field(default=None, max_length=200, description="MDL 批准人")
    review_days: int | None = Field(default=None, ge=0, description="客户审核期限（天），空着表示不限")


class ProjectUpdate(BaseModel):
    """只传要改的字段。"""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"name": "演示项目（改名）", "reason": "客户要求改名"}]}
    )

    code: str | None = Field(default=None, min_length=1, max_length=100)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    naming_templates: dict[str, Any] | None = None
    numbering_rules: dict[str, Any] | None = None
    client_name: str | None = Field(default=None, max_length=200, description="客户名称（MDL 表头）")
    contractor_name: str | None = Field(default=None, max_length=200, description="总包名称（MDL 表头）")
    mdl_doc_no: str | None = Field(default=None, max_length=100, description="MDL 文件编号")
    mdl_prepared_by: str | None = Field(default=None, max_length=200, description="MDL 编制人")
    mdl_checked_by: str | None = Field(default=None, max_length=200, description="MDL 审核人")
    mdl_approved_by: str | None = Field(default=None, max_length=200, description="MDL 批准人")
    review_days: int | None = Field(default=None, ge=0, description="客户审核期限（天），空着表示不限")
    reason: str | None = Field(default=None, description="修改原因，写进变更记录")


class ReasonBody(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"reason": "项目完工"}]})

    reason: str | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    description: str | None
    archived_at: datetime | None
    naming_templates: dict[str, Any]
    numbering_rules: dict[str, Any]
    client_name: str | None
    contractor_name: str | None
    mdl_doc_no: str | None
    mdl_prepared_by: str | None
    mdl_checked_by: str | None
    mdl_approved_by: str | None
    review_days: int | None
    created_at: datetime
    updated_at: datetime


class ProjectListItem(ProjectOut):
    unclassified_count: int = Field(default=0, description="待归类的文件数")
    completion: float | None = Field(default=None, description="完成率：已批准 ÷ 应交（不含不适用）；没有应交项时为空")
    outstanding_count: int = Field(default=0, description="缺件数：未提交、需修改重交的应交项")
    overdue_count: int = Field(default=0, description="逾期数：未提交且过了截止日的应交项")


@router.get("", response_model=list[ProjectListItem], summary="项目列表（带完成率、缺件、逾期、待归类数）")
def list_projects(session: SessionDep, user: UserDep, include_archived: bool = False):
    counts = unclassified_counts(session)
    stats = deliverable_query.project_stats(session)
    visible = access.visible_project_ids(session, user)  # 只列自己是成员的（系统管理员看全部）
    out = []
    for p in svc.list_projects(session, include_archived=include_archived):
        if visible is not None and p.id not in visible:
            continue
        st = stats.get(p.id, {})
        out.append(ProjectListItem.model_validate(p).model_copy(update={
            "unclassified_count": counts.get(p.id, 0), "completion": st.get("completion"),
            "outstanding_count": st.get("outstanding", 0), "overdue_count": st.get("overdue", 0)}))
    return out


@router.post("", response_model=ProjectOut, status_code=201, summary="新建项目")
def create_project(body: ProjectCreate, session: SessionDep, actor: ActorDep):
    project = svc.create_project(session, body.model_dump(), actor)
    session.commit()
    return project


@router.get("/{project_id}", response_model=ProjectOut, summary="项目详情")
def get_project(project_id: int, session: SessionDep):
    return svc.get_project(session, project_id)


@router.patch("/{project_id}", response_model=ProjectOut, summary="修改项目")
def update_project(project_id: int, body: ProjectUpdate, session: SessionDep, actor: ActorDep):
    changes = body.model_dump(exclude_unset=True)
    reason = changes.pop("reason", None)
    project = svc.update_project(session, project_id, changes, actor, reason=reason)
    session.commit()
    return project


@router.post("/{project_id}/archive", response_model=ProjectOut, summary="归档项目（只读）")
def archive_project(
    project_id: int, session: SessionDep, actor: ActorDep, body: ReasonBody | None = None
):
    project = svc.archive_project(session, project_id, actor, reason=body.reason if body else None)
    session.commit()
    return project


class DeleteBody(BaseModel):
    confirm_code: str = Field(description="再输一遍项目编号确认")
    reason: str | None = None


@router.get("/{project_id}/delete-check", summary="能不能删除（只有空项目能删；系统管理员）")
def delete_check(project_id: int, session: SessionDep) -> dict[str, Any]:
    svc.get_project(session, project_id)
    found = project_delete.blockers(session, project_id)
    return {"deletable": not found, "blockers": found}


@router.post("/{project_id}/delete", summary="删除空项目（系统管理员；有文件、传送单、MDL 版本、基线、导入的只能归档）")
def delete_project(project_id: int, body: DeleteBody, session: SessionDep, actor: ActorDep) -> dict[str, Any]:
    result = project_delete.delete_project(session, project_id, actor, confirm_code=body.confirm_code,
                                           reason=body.reason)
    session.commit()
    return result


@router.post("/{project_id}/unarchive", response_model=ProjectOut, summary="取消归档")
def unarchive_project(
    project_id: int, session: SessionDep, actor: ActorDep, body: ReasonBody | None = None
):
    project = svc.unarchive_project(
        session, project_id, actor, reason=body.reason if body else None
    )
    session.commit()
    return project
