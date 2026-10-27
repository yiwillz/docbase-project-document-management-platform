"""状态映射：MDL 上的状态文字 → 平台状态 + 责任方（PRD 第 11 节 P2）。

规则全部是数据（status_mappings 表），代码里不写死任何状态文字。
匹配不区分大小写、空格统一；按优先级从小到大依次检查。
结果"明确"的条件：命中了规则、第一条命中的规则本身标为明确、并且命中的其他明确规则结论一致
（标为不明确的规则只做兜底，不参与比较）。
"""

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ChangeAction, DeliverableStatus, MatchType, Responsible, StatusMapping
from app.services.errors import Invalid
from app.services.history import Actor, record, snapshot
from app.services.projects import get_project, get_writable_project

RULE_FIELDS = ("pattern", "match_type", "status", "responsible", "is_definite", "priority")


@dataclass
class StatusResult:
    text: str
    status: DeliverableStatus | None = None
    responsible: Responsible | None = None
    definite: bool = False
    rule_id: int | None = None  # 起决定作用的规则
    matched_rule_ids: list[int] = field(default_factory=list)


def normalize(text: str) -> str:
    """去首尾空格、多个空白合成一个、不区分大小写。"""
    return " ".join(text.split()).casefold()


def _matches(rule: StatusMapping, text: str) -> bool:
    if rule.match_type is MatchType.REGEX:
        return re.search(rule.pattern, text, re.IGNORECASE) is not None
    pattern, norm = normalize(rule.pattern), normalize(text)
    if rule.match_type is MatchType.EQUALS:
        return norm == pattern
    return pattern in norm


def list_rules(session: Session, project_id: int | None) -> list[StatusMapping]:
    """项目的规则（project_id 为 None 时是产品默认规则），按优先级排序。"""
    if project_id is not None:
        get_project(session, project_id)
    stmt = select(StatusMapping).where(StatusMapping.active)
    stmt = stmt.where(StatusMapping.project_id.is_(None) if project_id is None
                      else StatusMapping.project_id == project_id)
    return list(session.scalars(stmt.order_by(StatusMapping.priority, StatusMapping.id)))


def map_status(rules: list[StatusMapping], text: str | None) -> StatusResult | None:
    """用给定规则映射一段状态文字；文字为空返回 None。"""
    if text is None or not text.strip():
        return None
    matched = [r for r in rules if _matches(r, text)]
    result = StatusResult(text=text.strip(), matched_rule_ids=[r.id for r in matched])
    if not matched:
        return result
    first = matched[0]
    # 只有标为明确的规则才参与"结论是否一致"的比较；不明确的规则是兜底用的，不推翻具体规则
    agree = all((r.status, r.responsible) == (first.status, first.responsible)
                for r in matched if r.is_definite)
    result.status, result.responsible, result.rule_id = first.status, first.responsible, first.id
    result.definite = first.is_definite and agree
    return result


def map_project_status(session: Session, project_id: int, text: str | None
                       ) -> StatusResult | None:
    return map_status(list_rules(session, project_id), text)


def _validate_rule(raw: dict[str, Any], index: int) -> dict[str, Any]:
    def fail(message: str) -> Invalid:
        return Invalid("status_mapping.invalid", f"第 {index} 条规则：{message}", row=index)

    data = {k: v for k, v in raw.items() if k in RULE_FIELDS}
    pattern = (data.get("pattern") or "").strip()
    if not pattern:
        raise fail("pattern 不能为空")
    data["pattern"] = pattern
    try:
        data["match_type"] = MatchType(data.get("match_type", "contains"))
        data["status"] = DeliverableStatus(data["status"])
        if data.get("responsible") is not None:
            data["responsible"] = Responsible(data["responsible"])
    except (KeyError, ValueError) as exc:
        raise fail(f"取值不合法（{exc}）") from exc
    if data["match_type"] is MatchType.REGEX:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise fail(f"正则写法有误（{exc}）") from exc
    data["is_definite"] = bool(data.get("is_definite", True))
    data["priority"] = int(data.get("priority", 100))
    return data


def replace_rules(session: Session, project_id: int, rules: list[dict[str, Any]], actor: Actor,
                  *, reason: str | None = None) -> list[StatusMapping]:
    """整体替换项目的规则：旧规则停用（留历史），新规则逐条新建。全部校验通过才写入。"""
    get_writable_project(session, project_id)
    prepared = [_validate_rule(r, i) for i, r in enumerate(rules, start=1)]
    for old in list_rules(session, project_id):
        before = snapshot(old)
        old.active = False
        record(session, old, ChangeAction.DELETE, before, snapshot(old), actor,
               reason=reason, project_id=project_id)
    created = []
    for data in prepared:
        rule = StatusMapping(project_id=project_id, **data)
        session.add(rule)
        session.flush()
        record(session, rule, ChangeAction.CREATE, None, snapshot(rule), actor,
               reason=reason, project_id=project_id)
        created.append(rule)
    return created


def export_rules(session: Session, project_id: int | None) -> list[dict[str, Any]]:
    """导出为 JSON 可用的列表（可再用 replace_rules 导入）。"""
    return [
        {
            "pattern": r.pattern,
            "match_type": r.match_type.value,
            "status": r.status.value,
            "responsible": r.responsible.value if r.responsible else None,
            "is_definite": r.is_definite,
            "priority": r.priority,
        }
        for r in list_rules(session, project_id)
    ]
