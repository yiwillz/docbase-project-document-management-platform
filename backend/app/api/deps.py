"""接口公共依赖：数据库会话、当前登录用户、权限检查（每个请求都过 check_access）。"""

from typing import Annotated
from urllib.parse import unquote

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.models import User
from app.services import access, auth
from app.services.deliverables import ensure_deliverables
from app.services.errors import Forbidden, Unauthorized
from app.services.history import Actor

SessionDep = Annotated[Session, Depends(get_session)]
CSRF_HEADER = "X-Docbase"  # 写操作必须带（浏览器跨站发不出自定义请求头）


def current_user(request: Request, session: Session) -> tuple[User | None, str]:
    """当前用户和认证方式：cookie（正常登录）；只在测试库上：X-User 请求头、或匿名当系统管理员。"""
    user = auth.resolve(session, request.cookies.get(auth.COOKIE))
    if user is not None:
        return user, "cookie"
    settings = get_settings()
    if (settings.auth_dev_header or settings.auth_dev_anonymous) and auth.dev_mode_allowed():
        name = unquote(request.headers.get("X-User") or "").strip()
        if name and settings.auth_dev_header:
            return auth.dev_user(session, name), "dev"
        if settings.auth_dev_anonymous:
            return auth.dev_user(session, "dev"), "dev"
    return None, "none"


def check_access(request: Request, session: SessionDep) -> None:
    """按 access.RULES 检查：登录了没有、有没有这个项目的这个权限；写操作要带防跨站请求头。"""
    route = request.scope.get("route")
    path = getattr(route, "path_format", request.url.path)
    rule = access.rule_for(request.method, path)
    user, how = current_user(request, session)
    request.state.user = user
    if rule is None:
        raise Forbidden("access.no_rule", f"接口没有权限规则：{request.method} {path}")
    if rule.kind == "public":
        return
    if user is None:
        raise Unauthorized("auth.required", "请先登录")
    if how == "cookie" and request.method not in ("GET", "HEAD") and request.headers.get(CSRF_HEADER) != "1":
        raise Forbidden("auth.csrf", f"写操作要带请求头 {CSRF_HEADER}")
    if rule.kind == "login":
        return
    if rule.kind == "sysadmin":
        if not user.is_sysadmin:
            raise Forbidden("access.sysadmin_only", "只有系统管理员能做")
        return
    if rule.kind == "history":
        pid = request.query_params.get("project_id")
        if pid and pid.isdigit():
            access.require(session, user, int(pid), "history.read")
        elif not user.is_sysadmin:
            raise Forbidden("access.sysadmin_only", "不按项目查变更记录只有系统管理员能看")
        return
    access.require(session, user, int(request.path_params["project_id"]), rule.permission or "")


def get_user(request: Request) -> User:
    user = getattr(request.state, "user", None)
    if user is None:
        raise Unauthorized("auth.required", "请先登录")
    return user


UserDep = Annotated[User, Depends(get_user)]


def get_actor(request: Request) -> Actor:
    """当前操作人（变更记录里记的）：登录的账号。"""
    user = get_user(request)
    return Actor(name=user.username, id=user.id)


ActorDep = Annotated[Actor, Depends(get_actor)]


def save_and_regenerate(session: Session, project_id: int, actor: Actor) -> None:
    """结构或设备改完后，按新数据重算应交项，再一起提交。"""
    ensure_deliverables(session, project_id, actor)
    session.commit()
