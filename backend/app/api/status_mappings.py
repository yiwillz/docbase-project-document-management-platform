"""状态映射规则接口。"""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import ActorDep, SessionDep
from app.models import DeliverableStatus, MatchType, Responsible
from app.services import status_map as svc

router = APIRouter(prefix="/api/projects/{project_id}/status-mappings", tags=["状态映射"])


class Rule(BaseModel):
    pattern: str = Field(min_length=1, max_length=200, description="要匹配的状态文字")
    match_type: MatchType = Field(default=MatchType.CONTAINS,
                                  description="equals 等于、contains 包含、regex 正则；均不区分大小写")
    status: DeliverableStatus
    responsible: Responsible | None = None
    is_definite: bool = Field(default=True, description="映射是否明确；不明确时不自动下发到每台设备")
    priority: int = Field(default=100, description="小的先匹配")


class ReplaceBody(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "rules": [
            {"pattern": "approved", "match_type": "equals", "status": "approved",
             "responsible": "none", "priority": 10},
            {"pattern": "resubmit", "match_type": "contains", "status": "revise_resubmit",
             "responsible": "ours", "priority": 20},
            {"pattern": "pending", "match_type": "contains", "status": "under_review",
             "responsible": "client", "priority": 30},
        ],
        "reason": "按客户 MDL 的写法调整",
    }]})

    rules: list[Rule]
    reason: str | None = None


class RuleOut(Rule):
    model_config = ConfigDict(from_attributes=True)

    id: int


class TestBody(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"text": "Pending client approval"}]})

    text: str


class TestResult(BaseModel):
    text: str
    status: DeliverableStatus | None
    responsible: Responsible | None
    definite: bool
    rule_id: int | None
    matched_rule_ids: list[int]


@router.get("", response_model=list[RuleOut], summary="项目的状态映射规则（按优先级）")
def list_rules(project_id: int, session: SessionDep):
    return svc.list_rules(session, project_id)


@router.put("", response_model=list[RuleOut],
            summary="整体替换项目的规则（旧规则停用、留历史）")
def replace_rules(project_id: int, body: ReplaceBody, session: SessionDep, actor: ActorDep):
    rules = [r.model_dump(mode="json") for r in body.rules]
    created = svc.replace_rules(session, project_id, rules, actor, reason=body.reason)
    session.commit()
    return created


@router.post("/test", response_model=TestResult | None,
             summary="试算：一段状态文字会映射成什么（文字为空返回 null）")
def test_rules(project_id: int, body: TestBody, session: SessionDep) -> Any:
    return svc.map_project_status(session, project_id, body.text)
