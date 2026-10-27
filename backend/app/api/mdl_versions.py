"""客户 MDL 版本接口：列表、查看、对比。

版本在第 6 步导出并发给客户时自动记录，这里不提供单独的"冻结"接口。
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.api.deps import SessionDep
from app.services import mdl_versions as svc
from app.services.errors import Invalid

router = APIRouter(prefix="/api/projects/{project_id}/mdl-versions", tags=["MDL 版本"])


class VersionOut(BaseModel):
    rev_no: int
    label: str
    description: str | None
    frozen_by_name: str | None
    frozen_at: datetime
    milestones: int
    document_types: int


class VersionDetail(VersionOut):
    snapshot: dict[str, Any]


def _out(version, *, detail: bool = False) -> dict[str, Any]:
    data = {
        "rev_no": version.rev_no,
        "label": svc.label(version.rev_no),
        "description": version.description,
        "frozen_by_name": version.frozen_by_name,
        "frozen_at": version.frozen_at,
        **svc.snapshot_counts(version.snapshot),
    }
    if detail:
        data["snapshot"] = version.snapshot
    return data


@router.get("", response_model=list[VersionOut], summary="MDL 版本列表")
def list_versions(project_id: int, session: SessionDep):
    return [_out(v) for v in svc.list_versions(session, project_id)]


@router.get("/compare",
            summary="对比两个版本（to 填 current 表示和当前结构比）：新增 / 删除 / 修改")
def compare(project_id: int, session: SessionDep,
            from_rev: int = Query(alias="from", ge=0, description="旧版本号，如 0"),
            to: str = Query(default="current", description="新版本号，或 current")
            ) -> dict[str, Any]:
    if to != "current" and not to.isdigit():
        raise Invalid("mdl_version.bad_to", "to 应为版本号或 current", to=to)
    return svc.compare(session, project_id, from_rev, None if to == "current" else int(to))


@router.get("/{rev_no}", response_model=VersionDetail, summary="查看某个版本（含结构快照）")
def get_version(project_id: int, rev_no: int, session: SessionDep):
    return _out(svc.get_version(session, project_id, rev_no), detail=True)
