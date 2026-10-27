"""各业务模块共用的小工具：取项目内对象、新建 / 修改 / 停用 / 删除并写变更记录。"""

from typing import Any

from sqlalchemy.orm import Session

from app.models import ChangeAction
from app.services.errors import Invalid, NotFound
from app.services.history import Actor, record, snapshot


def clean(data: dict[str, Any], field: str, *, required: bool = False) -> None:
    """文本字段去首尾空格；空字符串当作 None；必填的不能为空。"""
    if field not in data:
        if required:
            raise Invalid("structure.field_required", f"{field} 不能为空", field=field)
        return
    value = data[field]
    if isinstance(value, str):
        value = value.strip() or None
    if required and value is None:
        raise Invalid("structure.field_required", f"{field} 不能为空", field=field)
    data[field] = value


def pick(data: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if k in fields}


def get_in_project(session: Session, model: type, obj_id: int, project_id: int, kind: str):
    """按 id 取对象，并确认它属于这个项目（别的项目的 id 当作不存在）。"""
    obj = session.get(model, obj_id)
    if obj is None or obj.project_id != project_id:
        raise NotFound(f"{kind}.not_found", f"{kind} {obj_id} 不存在", id=obj_id)
    return obj


def update(session: Session, obj: Any, changes: dict[str, Any], actor: Actor,
           reason: str | None, project_id: int, *,
           action: ChangeAction = ChangeAction.UPDATE) -> Any:
    before = snapshot(obj)
    for key, value in changes.items():
        setattr(obj, key, value)
    record(session, obj, action, before, snapshot(obj), actor, reason=reason,
           project_id=project_id)
    return obj


def set_active(session: Session, obj: Any, active: bool, actor: Actor,
               reason: str | None, project_id: int) -> Any:
    """停用（记为 delete）/ 启用（记为 restore）；状态没变时什么都不做。"""
    if obj.active == active:
        return obj
    action = ChangeAction.RESTORE if active else ChangeAction.DELETE
    before = snapshot(obj)
    obj.active = active
    record(session, obj, action, before, snapshot(obj), actor, reason=reason,
           project_id=project_id)
    return obj


def create(session: Session, obj: Any, actor: Actor, project_id: int,
           reason: str | None = None) -> Any:
    session.add(obj)
    session.flush()
    record(session, obj, ChangeAction.CREATE, None, snapshot(obj), actor,
           reason=reason, project_id=project_id)
    return obj


def delete(session: Session, obj: Any, actor: Actor, project_id: int,
           reason: str | None = None) -> None:
    """真删除；变更记录里留下删除前的完整快照。"""
    before = snapshot(obj)
    record(session, obj, ChangeAction.DELETE, before, None, actor, reason=reason,
           project_id=project_id)
    session.delete(obj)
    session.flush()
