"""登录、退出、当前用户、改密码；用户管理（系统管理员）；角色；项目成员（项目管理员）。"""

from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from app.api.deps import ActorDep, SessionDep, UserDep
from app.config import get_settings
from app.services import access, auth
from app.services import users as svc

router = APIRouter(tags=["登录与权限"])


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class PasswordIn(BaseModel):
    old_password: str
    new_password: str


class UserCreate(BaseModel):
    username: str
    password: str
    display_name: str | None = None
    email: str | None = None
    is_sysadmin: bool = False


class UserUpdate(BaseModel):
    display_name: str | None = None
    email: str | None = None
    is_sysadmin: bool | None = None
    active: bool | None = None


class ResetIn(BaseModel):
    password: str


class MemberIn(BaseModel):
    role: str = Field(description="角色代码：admin / member / readonly")


def me_out(session, user) -> dict[str, Any]:
    return {**svc.user_out(user), "projects": access.all_project_permissions(session, user),
            "all_permissions": list(access.ALL_PERMISSIONS)}


@router.post("/api/auth/login", summary="登录：成功后在 cookie 里放会话令牌")
def login(body: LoginIn, request: Request, response: Response, session: SessionDep) -> dict[str, Any]:
    token, user = auth.login(session, body.username, body.password,
                             ip=request.client.host if request.client else None,
                             user_agent=request.headers.get("user-agent"))
    response.set_cookie(auth.COOKIE, token, httponly=True, samesite="lax", secure=request.url.scheme == "https",
                        max_age=get_settings().session_hours * 3600, path="/")
    return me_out(session, user)


@router.post("/api/auth/logout", summary="退出登录")
def logout(request: Request, response: Response, session: SessionDep) -> dict[str, bool]:
    auth.logout(session, request.cookies.get(auth.COOKIE))
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@router.get("/api/auth/me", summary="当前登录的用户，以及在各项目里的权限")
def me(user: UserDep, session: SessionDep) -> dict[str, Any]:
    return me_out(session, user)


@router.post("/api/auth/password", summary="改自己的密码（别处登录的会话作废）")
def change_password(body: PasswordIn, request: Request, user: UserDep, session: SessionDep) -> dict[str, bool]:
    auth.change_password(session, user, body.old_password, body.new_password,
                         keep_token=request.cookies.get(auth.COOKIE))
    session.commit()
    return {"ok": True}


# ---------------------------------------------------------------- 用户管理（系统管理员）


@router.get("/api/users", summary="用户列表")
def list_users(session: SessionDep) -> list[dict[str, Any]]:
    return [svc.user_out(u) for u in svc.list_users(session)]


@router.post("/api/users", status_code=201, summary="新建用户（用户不能自己注册）")
def create_user(body: UserCreate, session: SessionDep, actor: ActorDep) -> dict[str, Any]:
    u = svc.create_user(session, body.model_dump(), actor)
    session.commit()
    return svc.user_out(u)


@router.patch("/api/users/{user_id}", summary="改用户：名字、邮箱、系统管理员、停用 / 启用")
def update_user(user_id: int, body: UserUpdate, session: SessionDep, actor: ActorDep) -> dict[str, Any]:
    u = svc.update_user(session, user_id, body.model_dump(exclude_unset=True), actor)
    session.commit()
    return svc.user_out(u)


@router.post("/api/users/{user_id}/password", summary="重置密码（这个人的会话全部作废，解除锁定）")
def reset_password(user_id: int, body: ResetIn, session: SessionDep, actor: ActorDep) -> dict[str, Any]:
    u = svc.reset_password(session, user_id, body.password, actor)
    session.commit()
    return svc.user_out(u)


@router.get("/api/roles", summary="角色（角色是数据：代码、名称、权限码）")
def list_roles(session: SessionDep) -> list[dict[str, Any]]:
    return [{"code": r.code, "name": r.name, "permissions": r.permissions, "builtin": r.builtin}
            for r in svc.roles(session)]


# ---------------------------------------------------------------- 项目成员


@router.get("/api/projects/{project_id}/members", summary="项目成员")
def members(project_id: int, session: SessionDep) -> list[dict[str, Any]]:
    return svc.members(session, project_id)


@router.get("/api/projects/{project_id}/members/candidates", summary="可加为成员的用户")
def member_candidates(project_id: int, session: SessionDep) -> list[dict[str, Any]]:
    """项目管理员加成员时选人用：启用中、还不是这个项目成员的用户（只给用户名和姓名）。"""
    return svc.member_candidates(session, project_id)


@router.put("/api/projects/{project_id}/members/{user_id}", summary="加成员或改角色")
def set_member(project_id: int, user_id: int, body: MemberIn, session: SessionDep, actor: ActorDep
               ) -> list[dict[str, Any]]:
    svc.set_member(session, project_id, user_id, body.role, actor)
    session.commit()
    return svc.members(session, project_id)


@router.delete("/api/projects/{project_id}/members/{user_id}", summary="移出项目")
def remove_member(project_id: int, user_id: int, session: SessionDep, actor: ActorDep) -> list[dict[str, Any]]:
    svc.remove_member(session, project_id, user_id, actor)
    session.commit()
    return svc.members(session, project_id)
