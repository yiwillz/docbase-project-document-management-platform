"""权限（PRD 第 2 节、F10）：所有"谁能做什么"都在这一个文件里判断。

两层：系统管理员（管用户、建项目、看全部项目，项目里也有全部权限）；项目里按成员的角色（角色是数据，
roles.permissions 是权限码列表）。不是成员就进不了这个项目。

每个接口要什么权限，按 方法 + 路径模板 在 RULES 里查（先匹配的算）；测试保证每个接口都查得到
（test_access.py），以后加接口忘了写规则会被拦下。

"谁能看哪个文件"集中在 can_see_file / visible_file_ids：第一版是项目成员能看本项目的全部文件；
以后按文件种类限制可见范围、供应商只看自己的文件，都改这里（第二版检索也要用它）。
"""

import re
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import File, ProjectMember, Role, User
from app.services.errors import Forbidden

# 全部权限码（内置角色在数据库里，见初始迁移和 6ea6266fee1a）
ALL_PERMISSIONS = (
    "file.read", "history.read", "file.write", "file.classify", "deliverable.update", "export",
    "project.config", "equipment.import", "mdl.import", "file.import", "milestone.close",
    "member.manage", "project.manage", "recycle.purge",
)

Kind = Literal["public", "login", "sysadmin", "project", "history"]


@dataclass(frozen=True)
class Rule:
    kind: Kind
    permission: str | None = None


# ---------------------------------------------------------------- 项目外的接口

GLOBAL: dict[tuple[str, str], Rule] = {
    ("GET", "/api/health"): Rule("public"),
    ("POST", "/api/auth/login"): Rule("public"),
    ("POST", "/api/auth/logout"): Rule("public"),
    ("GET", "/api/auth/me"): Rule("login"),
    ("POST", "/api/auth/password"): Rule("login"),
    ("GET", "/api/roles"): Rule("login"),
    ("GET", "/api/system/status"): Rule("login"),  # 备份细节只给系统管理员（接口里判断）
    ("GET", "/api/projects"): Rule("login"),  # 只列自己是成员的（系统管理员看全部）
    ("POST", "/api/projects"): Rule("sysadmin"),
    # 删除空项目：只给系统管理员（先于下面的项目内规则匹配）
    ("GET", "/api/projects/{project_id}/delete-check"): Rule("sysadmin"),
    ("POST", "/api/projects/{project_id}/delete"): Rule("sysadmin"),
    ("GET", "/api/structure-template"): Rule("login"),
    ("GET", "/api/equipment-template"): Rule("login"),
    ("GET", "/api/change-logs"): Rule("history"),  # 带 project_id 按项目查；不带只有系统管理员
    ("GET", "/api/users"): Rule("sysadmin"),
    ("POST", "/api/users"): Rule("sysadmin"),
    ("PATCH", "/api/users/{user_id}"): Rule("sysadmin"),
    ("POST", "/api/users/{user_id}/password"): Rule("sysadmin"),
}

# ---------------------------------------------------------------- 项目里的接口：/api/projects/{project_id} 后面的部分

W = ("POST", "PUT", "PATCH", "DELETE")
EXPORTS = (r"deliverables/outstanding\.xlsx|client-requirements\.xlsx|mdl/export|mdl-versions/\{rev_no\}/export"
           r"|milestones/\{milestone_id\}/closeout/export|transmittals/\{transmittal_id\}/export"
           r"|baselines/\{baseline_id\}/export\.xlsx|packages/\{job_id\}/download|change-logs/export\.xlsx")
CONFIG = (r"milestones|document-types|modules|equipment|equipment-types|review-codes|status-mappings"
          r"|copy-structure|reorder|deliverables/regenerate|mirror/sync")

PROJECT: list[tuple[tuple[str, ...], str, str]] = [
    # 存量导入：只有管理员（查看也是，页面只给管理员）
    (("GET", *W), r"imports(/.*)?", "file.import"),
    # 加成员时选人：能看到全部用户名，只给能管成员的人
    (("GET",), r"members/candidates", "member.manage"),
    # 导出：只读用户不能导出（只能下载、预览单个文件）
    (("GET",), EXPORTS, "export"),
    (("POST",), r"mdl/issue|packages|packages/estimate", "export"),
    # 其余查看
    (("GET",), r".*", "file.read"),
    # 只算不存：状态映射试算、命名模板预览
    (("POST",), r"status-mappings/test|naming/preview", "file.read"),
    # 里程碑关闭 / 重新打开：只有管理员
    (("POST",), r"milestones/\{milestone_id\}/(close|reopen)", "milestone.close"),
    # 导入：MDL 导回、标准模板、设备清单
    (("POST",), r"mdl/import/.*|structure-import/.*", "mdl.import"),
    (("POST",), r"equipment-import/.*", "equipment.import"),
    # 存文件
    (("POST", "PUT"), r"files/preview|files/uploads/.*|uploads(/.*)?|drop/preview", "file.write"),
    (("POST",), r"files/\{file_id\}/(classify|move)", "file.classify"),
    # 应交项状态、传送单、客户回复、基线
    (W, r"deliverables/.*|transmittals(/.*)?|baselines", "deliverable.update"),
    # 项目成员
    (W, r"members(/.*)?", "member.manage"),
    # 归档 / 取消归档
    (("POST",), r"archive|unarchive", "project.manage"),
    # 项目结构、设备、设置、命名模板、镜像同步
    (("PATCH",), r"", "project.config"),
    (W, rf"({CONFIG})(/.*)?", "project.config"),
]
PREFIX = "/api/projects/{project_id}"


def rule_for(method: str, path: str) -> Rule | None:
    """方法 + 路径模板 → 规则；没有规则的返回 None（当作拒绝）。"""
    method = "GET" if method == "HEAD" else method
    if (method, path) in GLOBAL:
        return GLOBAL[(method, path)]
    if path == PREFIX or path.startswith(PREFIX + "/"):
        rest = path[len(PREFIX) + 1:]
        for methods, pattern, perm in PROJECT:
            if method in methods and re.fullmatch(pattern, rest):
                return Rule("project", perm)
    return None


# ---------------------------------------------------------------- 谁有什么权限


def project_permissions(session: Session, user: User, project_id: int) -> set[str]:
    """这个人在这个项目里有哪些权限；不是成员返回空集合。"""
    if user.is_sysadmin:
        return set(ALL_PERMISSIONS)
    row = session.execute(select(Role.permissions).join(ProjectMember, ProjectMember.role_id == Role.id)
                          .where(ProjectMember.project_id == project_id, ProjectMember.user_id == user.id,
                                 ProjectMember.active, Role.active)).first()
    return set(row[0]) if row else set()


def all_project_permissions(session: Session, user: User) -> dict[int, list[str]]:
    """这个人在各项目里的权限（登录后给前端用；系统管理员不列，前端按"全部"处理）。"""
    rows = session.execute(select(ProjectMember.project_id, Role.permissions)
                           .join(Role, Role.id == ProjectMember.role_id)
                           .where(ProjectMember.user_id == user.id, ProjectMember.active, Role.active))
    return {pid: sorted(perms) for pid, perms in rows}


def visible_project_ids(session: Session, user: User) -> set[int] | None:
    """能看到的项目；None 表示全部（系统管理员）。"""
    if user.is_sysadmin:
        return None
    return set(session.scalars(select(ProjectMember.project_id).where(
        ProjectMember.user_id == user.id, ProjectMember.active)))


def require(session: Session, user: User | None, project_id: int, permission: str) -> None:
    if user is None:
        from app.services.errors import Unauthorized

        raise Unauthorized("auth.required", "请先登录")
    perms = project_permissions(session, user, project_id)
    if not perms:
        raise Forbidden("access.not_member", "你不是这个项目的成员", project_id=project_id)
    if permission not in perms:
        raise Forbidden("access.denied", f"没有权限：{permission}", permission=permission)


def can_see_file(session: Session, user: User, file: File) -> bool:
    """谁能看哪个文件（第一版：项目成员能看本项目的全部文件）。"""
    return "file.read" in project_permissions(session, user, file.project_id)


def visible_file_ids(session: Session, user: User, project_id: int) -> set[int] | None:
    """项目里这个人能看到的文件；None 表示全部（第一版总是全部；以后按种类 / 供应商限制时返回集合）。"""
    return None if "file.read" in project_permissions(session, user, project_id) else set()
