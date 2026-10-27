"""审核代码配置接口。"""

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import ActorDep, SessionDep
from app.models import DeliverableStatus
from app.services import review_codes as svc

router = APIRouter(prefix="/api/projects/{project_id}/review-codes", tags=["审核代码"])


class Code(BaseModel):
    code: str = Field(min_length=1, max_length=20, description="客户用的代码，如 1、A")
    label_en: str = Field(min_length=1, max_length=200)
    label_zh: str | None = Field(default=None, max_length=200)
    status: DeliverableStatus = Field(description="对应的平台状态：approved / revise_resubmit / under_review / not_applicable")
    final: bool = Field(default=True, description="是否最终批准；带意见批准为 false，闭环时要求拿到最终批准")
    aliases: list[str] = Field(default_factory=list, description="识别用的别名，如 approved as noted")


class ReplaceBody(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "codes": [
            {"code": "A", "label_en": "Approved", "label_zh": "批准", "status": "approved"},
            {"code": "B", "label_en": "Approved as noted", "label_zh": "带意见批准",
             "status": "approved", "final": False},
            {"code": "C", "label_en": "Revise & Resubmit", "label_zh": "修改重交",
             "status": "revise_resubmit", "final": False},
        ],
        "reason": "按客户的文件控制程序改为 A / B / C",
    }]})

    codes: list[Code] = Field(min_length=1)
    reason: str | None = None


class CodeOut(Code):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sort_order: int


@router.get("", response_model=list[CodeOut], summary="项目的审核代码")
def list_codes(project_id: int, session: SessionDep):
    return svc.list_codes(session, project_id)


@router.put("", response_model=list[CodeOut], summary="整体替换项目的审核代码（旧的停用、留历史）")
def replace_codes(project_id: int, body: ReplaceBody, session: SessionDep, actor: ActorDep):
    created = svc.replace_codes(session, project_id, [c.model_dump(mode="json") for c in body.codes],
                                actor, reason=body.reason)
    session.commit()
    return created
