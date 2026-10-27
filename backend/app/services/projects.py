"""项目：新建、修改、归档、查询（PRD F1）。"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ChangeAction, Project, ReviewCode, StatusMapping
from app.services.errors import Conflict, Invalid, NotFound
from app.services.history import Actor, emit, record, snapshot

# 允许通过"修改项目"改的字段
EDITABLE_FIELDS = (
    "code",
    "name",
    "description",
    "naming_templates",
    "numbering_rules",
    "client_name",
    "contractor_name",
    "mdl_doc_no",
    "mdl_prepared_by",
    "mdl_checked_by",
    "mdl_approved_by",
    "review_days",
)

# 复制默认状态规则时要带上的字段
_MAPPING_FIELDS = ("pattern", "match_type", "status", "responsible", "is_definite", "priority")
# 复制默认审核代码时要带上的字段
_REVIEW_CODE_FIELDS = ("code", "label_en", "label_zh", "status", "final", "aliases", "sort_order")


def list_projects(session: Session, *, include_archived: bool = False) -> list[Project]:
    stmt = select(Project).where(Project.active).order_by(Project.code)
    if not include_archived:
        stmt = stmt.where(Project.archived_at.is_(None))
    return list(session.scalars(stmt))


def get_project(session: Session, project_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None or not project.active:
        raise NotFound("project.not_found", f"项目 {project_id} 不存在", project_id=project_id)
    return project


def _clean_text(data: dict[str, Any], field: str) -> None:
    """去掉首尾空格；必填字段不能为空。"""
    if field not in data:
        return
    value = (data[field] or "").strip()
    if not value:
        raise Invalid(f"project.{field}_required", f"{field} 不能为空", field=field)
    data[field] = value


def _check_code_free(session: Session, code: str, exclude_id: int | None = None) -> None:
    stmt = select(Project.id).where(Project.code == code)
    if exclude_id is not None:
        stmt = stmt.where(Project.id != exclude_id)
    if session.scalar(stmt) is not None:
        raise Conflict("project.code_taken", f"项目编号 {code} 已被使用", code=code)


def _check_review_days(data: dict[str, Any]) -> None:
    value = data.get("review_days")
    if value is not None and (not isinstance(value, int) or value < 0):
        raise Invalid("project.bad_review_days", "客户审核期限必须是不小于 0 的整数（天）",
                      review_days=value)


def _ensure_not_archived(project: Project) -> None:
    if project.archived_at is not None:
        raise Conflict(
            "project.archived", f"项目 {project.code} 已归档，只读", project_id=project.id
        )


def get_writable_project(session: Session, project_id: int) -> Project:
    """取项目用于写操作：不存在 → 404，已归档 → 409。"""
    project = get_project(session, project_id)
    _ensure_not_archived(project)
    return project


def _copy_default_status_mappings(session: Session, project: Project) -> None:
    """把产品默认状态规则（project_id 为空）复制一份到新项目下。"""
    defaults = session.scalars(
        select(StatusMapping).where(StatusMapping.project_id.is_(None), StatusMapping.active)
    )
    for m in defaults:
        session.add(
            StatusMapping(project_id=project.id, **{f: getattr(m, f) for f in _MAPPING_FIELDS})
        )


def _copy_default_review_codes(session: Session, project: Project) -> None:
    """把产品默认审核代码（project_id 为空）复制一份到新项目下。"""
    defaults = session.scalars(
        select(ReviewCode).where(ReviewCode.project_id.is_(None), ReviewCode.active)
        .order_by(ReviewCode.sort_order, ReviewCode.id)
    )
    for c in defaults:
        session.add(ReviewCode(project_id=project.id, **{f: getattr(c, f) for f in _REVIEW_CODE_FIELDS}))


def create_project(session: Session, data: dict[str, Any], actor: Actor) -> Project:
    data = {k: v for k, v in data.items() if k in EDITABLE_FIELDS}
    _clean_text(data, "code")
    _clean_text(data, "name")
    if "code" not in data or "name" not in data:
        raise Invalid("project.code_name_required", "项目编号和名称必填")
    _check_code_free(session, data["code"])
    _check_review_days(data)

    project = Project(**data)
    session.add(project)
    session.flush()
    _copy_default_status_mappings(session, project)
    _copy_default_review_codes(session, project)

    record(session, project, ChangeAction.CREATE, None, snapshot(project), actor,
           project_id=project.id)
    emit(session, "project.created", project, {"code": project.code}, project_id=project.id)
    return project


def update_project(
    session: Session,
    project_id: int,
    changes: dict[str, Any],
    actor: Actor,
    *,
    reason: str | None = None,
) -> Project:
    project = get_project(session, project_id)
    _ensure_not_archived(project)

    changes = {k: v for k, v in changes.items() if k in EDITABLE_FIELDS}
    for key in ("naming_templates", "numbering_rules"):
        if key in changes and changes[key] is None:
            changes[key] = {}
    if changes.get("naming_templates"):
        from app.services.naming import validate_templates

        changes["naming_templates"] = validate_templates(changes["naming_templates"])
    _clean_text(changes, "code")
    _clean_text(changes, "name")
    if "code" in changes and changes["code"] != project.code:
        _check_code_free(session, changes["code"], exclude_id=project.id)
    _check_review_days(changes)

    before = snapshot(project)
    for key, value in changes.items():
        setattr(project, key, value)
    log = record(session, project, ChangeAction.UPDATE, before, snapshot(project), actor,
                 reason=reason, project_id=project.id)
    if log is not None:
        emit(session, "project.updated", project, {"fields": list(log.changes)},
             project_id=project.id)
    return project


def archive_project(
    session: Session, project_id: int, actor: Actor, *, reason: str | None = None
) -> Project:
    project = get_project(session, project_id)
    _ensure_not_archived(project)
    before = snapshot(project)
    project.archived_at = datetime.now(UTC)
    record(session, project, ChangeAction.UPDATE, before, snapshot(project), actor,
           reason=reason, project_id=project.id)
    emit(session, "project.archived", project, project_id=project.id)
    return project


def unarchive_project(
    session: Session, project_id: int, actor: Actor, *, reason: str | None = None
) -> Project:
    project = get_project(session, project_id)
    if project.archived_at is None:
        raise Conflict(
            "project.not_archived", f"项目 {project.code} 未归档", project_id=project.id
        )
    before = snapshot(project)
    project.archived_at = None
    record(session, project, ChangeAction.UPDATE, before, snapshot(project), actor,
           reason=reason, project_id=project.id)
    emit(session, "project.unarchived", project, project_id=project.id)
    return project
