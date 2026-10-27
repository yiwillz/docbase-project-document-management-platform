"""用户和项目成员：系统管理员建用户、改资料、重置密码、停用；项目管理员管本项目成员和角色。
用户不能自己注册。停用、重置密码时这个人的会话全部作废。所有改动写变更记录。"""

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ChangeAction, ProjectMember, Role, User
from app.services import auth
from app.services.errors import Conflict, Invalid, NotFound
from app.services.history import Actor, record, snapshot
from app.services.projects import get_project, get_writable_project

USER_FIELDS = ("display_name", "email", "is_sysadmin", "active", "locale")
_USERNAME = re.compile(r"^[A-Za-z0-9._@-]{2,100}$")


def user_out(u: User) -> dict[str, Any]:
    return {"id": u.id, "username": u.username, "display_name": u.display_name, "email": u.email,
            "is_sysadmin": u.is_sysadmin, "active": u.active, "auth_provider": u.auth_provider,
            "last_login_at": u.last_login_at, "locked": bool(u.locked_until and u.locked_until > auth._now()),
            "created_at": u.created_at}


def _safe(u: User) -> dict[str, Any]:
    """写变更记录用：不记密码哈希。"""
    data = snapshot(u)
    data.pop("password_hash", None)
    return data


def list_users(session: Session) -> list[User]:
    return list(session.scalars(select(User).order_by(User.username)))


def get_user(session: Session, user_id: int) -> User:
    u = session.get(User, user_id)
    if u is None:
        raise NotFound("user.not_found", f"用户 {user_id} 不存在", id=user_id)
    return u


def create_user(session: Session, data: dict[str, Any], actor: Actor) -> User:
    username = (data.get("username") or "").strip()
    if not _USERNAME.fullmatch(username):
        raise Invalid("user.bad_username", "用户名只能用字母、数字和 . _ @ -，2–100 位")
    if session.scalar(select(User.id).where(User.username == username)):
        raise Conflict("user.username_taken", f"用户名 {username} 已经有了", username=username)
    auth.check_password_rules(data.get("password") or "")
    u = User(username=username, display_name=(data.get("display_name") or "").strip() or None,
             email=(data.get("email") or "").strip() or None, is_sysadmin=bool(data.get("is_sysadmin")),
             auth_provider="local", password_hash=auth.hash_password(data["password"]),
             password_changed_at=auth._now())
    session.add(u)
    session.flush()
    record(session, u, ChangeAction.CREATE, None, _safe(u), actor, project_id=None)
    return u


def update_user(session: Session, user_id: int, changes: dict[str, Any], actor: Actor) -> User:
    u = get_user(session, user_id)
    changes = {k: v for k, v in changes.items() if k in USER_FIELDS}
    if u.id == actor.id and (changes.get("is_sysadmin") is False or changes.get("active") is False):
        raise Conflict("user.self_lockout", "不能取消自己的系统管理员、也不能停用自己")
    before = _safe(u)
    for k, v in changes.items():
        setattr(u, k, (v.strip() or None) if isinstance(v, str) else v)
    if changes.get("active") is False:
        auth.revoke_all(session, u.id)
    record(session, u, ChangeAction.UPDATE, before, _safe(u), actor, project_id=None)
    return u


def reset_password(session: Session, user_id: int, password: str, actor: Actor) -> User:
    u = get_user(session, user_id)
    auth.check_password_rules(password)
    u.password_hash, u.password_changed_at = auth.hash_password(password), auth._now()
    u.failed_logins, u.locked_until = 0, None
    auth.revoke_all(session, u.id)
    record(session, u, ChangeAction.UPDATE, {"password": "***"}, {"password": "(reset)"}, actor,
           project_id=None, reason="重置密码")
    return u


# ---------------------------------------------------------------- 项目成员


def roles(session: Session) -> list[Role]:
    return list(session.scalars(select(Role).where(Role.active).order_by(Role.id)))


def members(session: Session, project_id: int) -> list[dict[str, Any]]:
    get_project(session, project_id)
    rows = session.execute(select(ProjectMember, User, Role).join(User, User.id == ProjectMember.user_id)
                           .join(Role, Role.id == ProjectMember.role_id)
                           .where(ProjectMember.project_id == project_id, ProjectMember.active)
                           .order_by(User.username))
    return [{"user_id": u.id, "username": u.username, "display_name": u.display_name, "active": u.active,
             "role": r.code, "role_name": r.name} for _m, u, r in rows]


def member_candidates(session: Session, project_id: int) -> list[dict[str, Any]]:
    """启用中、还不是这个项目成员的用户。"""
    get_project(session, project_id)
    taken = select(ProjectMember.user_id).where(ProjectMember.project_id == project_id, ProjectMember.active)
    rows = session.scalars(select(User).where(User.active, User.id.not_in(taken)).order_by(User.username))
    return [{"id": u.id, "username": u.username, "display_name": u.display_name} for u in rows]


def set_member(session: Session, project_id: int, user_id: int, role_code: str, actor: Actor) -> ProjectMember:
    get_writable_project(session, project_id)
    user = get_user(session, user_id)
    role = session.scalar(select(Role).where(Role.code == role_code, Role.active))
    if role is None:
        raise Invalid("member.bad_role", f"没有角色 {role_code}", role=role_code)
    m = session.scalar(select(ProjectMember).where(ProjectMember.project_id == project_id,
                                                  ProjectMember.user_id == user.id))
    if m is None:
        m = ProjectMember(project_id=project_id, user_id=user.id, role_id=role.id, active=True)
        session.add(m)
        session.flush()
        record(session, m, ChangeAction.CREATE, None, {**snapshot(m), "username": user.username}, actor,
               project_id=project_id)
    else:
        before = snapshot(m)
        m.role_id, m.active = role.id, True
        record(session, m, ChangeAction.UPDATE, before, snapshot(m), actor, project_id=project_id)
    return m


def remove_member(session: Session, project_id: int, user_id: int, actor: Actor) -> None:
    get_writable_project(session, project_id)
    m = session.scalar(select(ProjectMember).where(ProjectMember.project_id == project_id,
                                                  ProjectMember.user_id == user_id, ProjectMember.active))
    if m is None:
        raise NotFound("member.not_found", "这个人不是项目成员")
    if user_id == actor.id:
        raise Conflict("member.self_remove", "不能把自己移出项目")
    before = snapshot(m)
    m.active = False
    record(session, m, ChangeAction.UPDATE, before, snapshot(m), actor, project_id=project_id)
