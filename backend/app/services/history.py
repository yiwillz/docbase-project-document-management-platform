"""变更记录与事件（PRD F10.2、第 6 节）。

用法：改之前 snapshot，改之后 snapshot，再 record；需要通知后台时 emit。
record / emit 只往 Session 里加记录，不提交——和业务数据在同一个事务里提交。
"""

import enum
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from app.models import ChangeAction, ChangeLog, Event

# 每次写都会变的字段，记进去只会产生噪音
SKIP_FIELDS = frozenset({"created_at", "updated_at"})


@dataclass(frozen=True)
class Actor:
    """操作人。第 16 步之前来自请求头 X-User，用户表里没有时 id 为空。"""

    name: str
    id: int | None = None


def to_plain(value: Any) -> Any:
    """转成能存进 JSON 的值。"""
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (uuid.UUID, Decimal)):
        return str(value)
    return value


def snapshot(obj: Any) -> dict[str, Any]:
    """把一条记录的全部字段转成字典（不含 created_at / updated_at）。"""
    mapper = inspect(obj).mapper
    return {
        attr.key: to_plain(getattr(obj, attr.key))
        for attr in mapper.column_attrs
        if attr.key not in SKIP_FIELDS
    }


def diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, list[Any]]:
    """字段级差异：{字段: [前, 后]}，只含变了的字段。"""
    changes = {}
    for key in sorted(before.keys() | after.keys()):
        old, new = before.get(key), after.get(key)
        if old != new:
            changes[key] = [old, new]
    return changes


def entity_ref(obj: Any) -> tuple[str, str]:
    """对象类型（表名）和对象 ID（多列主键用 : 连接）。"""
    state = inspect(obj)
    return state.mapper.local_table.name, ":".join(str(v) for v in state.identity)


def record(
    session: Session,
    obj: Any,
    action: ChangeAction,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    actor: Actor,
    *,
    reason: str | None = None,
    project_id: int | None = None,
) -> ChangeLog | None:
    """写一条变更记录；没有变化时不写，返回 None。

    project_id 不传时取 obj.project_id（对象本身是项目时请显式传入）。
    """
    changes = diff(before or {}, after or {})
    if not changes:
        return None
    if inspect(obj).identity is None:
        session.flush()  # 新对象先拿到 id
    entity_type, entity_id = entity_ref(obj)
    log = ChangeLog(
        project_id=project_id if project_id is not None else getattr(obj, "project_id", None),
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        changes=changes,
        actor_id=actor.id,
        actor_name=actor.name,
        reason=reason,
    )
    session.add(log)
    return log


def record_summary(
    session: Session,
    *,
    project_id: int | None,
    entity_type: str,
    entity_id: str,
    action: ChangeAction,
    changes: dict[str, list[Any]],
    actor: Actor,
    reason: str | None = None,
) -> ChangeLog:
    """写一条汇总式变更记录（不对应单个对象，如"重算应交项：新增 30、作废 2"）。"""
    log = ChangeLog(
        project_id=project_id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        changes=changes,
        actor_id=actor.id,
        actor_name=actor.name,
        reason=reason,
    )
    session.add(log)
    return log


def emit(
    session: Session,
    event_type: str,
    obj: Any = None,
    payload: dict[str, Any] | None = None,
    *,
    project_id: int | None = None,
) -> Event:
    """写一条事件（Outbox）。"""
    entity_type = entity_id = None
    if obj is not None:
        if inspect(obj).identity is None:
            session.flush()
        entity_type, entity_id = entity_ref(obj)
        if project_id is None:
            project_id = getattr(obj, "project_id", None)
    event = Event(
        project_id=project_id,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload or {},
    )
    session.add(event)
    return event


def query_change_logs(
    session: Session,
    *,
    project_id: int | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    actor: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[int, list[ChangeLog]]:
    """按项目 / 对象 / 操作人 / 时间查变更记录，新的在前。返回 (总数, 本页)。"""
    conditions = []
    if project_id is not None:
        conditions.append(ChangeLog.project_id == project_id)
    if entity_type is not None:
        conditions.append(ChangeLog.entity_type == entity_type)
    if entity_id is not None:
        conditions.append(ChangeLog.entity_id == entity_id)
    if actor is not None:
        conditions.append(ChangeLog.actor_name == actor)
    if since is not None:
        conditions.append(ChangeLog.created_at >= since)
    if until is not None:
        conditions.append(ChangeLog.created_at < until)

    total = session.scalar(select(func.count()).select_from(ChangeLog).where(*conditions))
    items = session.scalars(
        select(ChangeLog).where(*conditions).order_by(ChangeLog.id.desc()).limit(limit).offset(offset)
    ).all()
    return total or 0, list(items)
