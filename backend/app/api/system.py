"""系统状态接口：页面顶部的提醒（文件存储断开、备份异常）。"""

from typing import Any

from fastapi import APIRouter

from app.api.deps import SessionDep, UserDep
from app.services import system

router = APIRouter(tags=["系统"])


@router.get("/api/system/status", summary="文件存储、备份状态和版本（备份的细节只给系统管理员）")
def status(user: UserDep, session: SessionDep) -> dict[str, Any]:
    data = system.status(session)
    storage = data["storage"]
    if not user.is_sysadmin:
        # 普通用户只需要知道"存储能不能用"，不给路径、错误原文和备份情况
        return {"version": data["version"], "storage": {"ok": storage["ok"], "error": None}, "backup": None}
    return data
