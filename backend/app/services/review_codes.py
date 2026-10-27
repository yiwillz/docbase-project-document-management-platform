"""审核代码配置（PRD F7.6）：客户审完给的结论标记 → 平台状态 + 是否最终批准。

每个项目一套（新建项目时从产品默认复制）；整体替换，旧的停用留历史。
传送单明细里存的是当时的代码、状态、是否最终，改配置不影响历史记录。
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ChangeAction, DeliverableStatus, ReviewCode
from app.services.errors import Invalid
from app.services.history import Actor, record, snapshot
from app.services.projects import get_project, get_writable_project
from app.services.status_map import normalize

# 审核代码能对应的平台状态：客户的结论只会是这几种
REVIEW_STATUSES = (DeliverableStatus.APPROVED, DeliverableStatus.REVISE_RESUBMIT,
                   DeliverableStatus.UNDER_REVIEW, DeliverableStatus.NOT_APPLICABLE)
FIELDS = ("code", "label_en", "label_zh", "status", "final", "aliases")


def list_codes(session: Session, project_id: int) -> list[ReviewCode]:
    get_project(session, project_id)
    return list(session.scalars(
        select(ReviewCode).where(ReviewCode.project_id == project_id, ReviewCode.active)
        .order_by(ReviewCode.sort_order, ReviewCode.id)))


def _validate(raw: dict[str, Any], index: int, seen: set[str]) -> dict[str, Any]:
    def fail(message: str, code: str = "review_code.invalid") -> Invalid:
        return Invalid(code, f"第 {index} 个审核代码：{message}", row=index)

    data = {k: raw.get(k) for k in FIELDS}
    data["code"] = str(data["code"] or "").strip()
    data["label_en"] = str(data["label_en"] or "").strip()
    if not data["code"] or not data["label_en"]:
        raise fail("代码和英文名称必填")
    if data["code"].casefold() in seen:
        raise fail(f"代码 {data['code']} 重复", "review_code.duplicate")
    seen.add(data["code"].casefold())
    try:
        data["status"] = DeliverableStatus(data["status"])
    except ValueError as exc:
        raise fail(f"状态不合法：{data['status']}") from exc
    if data["status"] not in REVIEW_STATUSES:
        raise fail("审核结论只能对应 已批准 / 需修改重交 / 客户审核中 / 不适用")
    data["label_zh"] = (data["label_zh"] or "").strip() or None
    data["final"] = bool(True if data["final"] is None else data["final"])
    data["aliases"] = [a.strip() for a in (data["aliases"] or []) if a and a.strip()]
    data["sort_order"] = index
    return data


def replace_codes(session: Session, project_id: int, codes: list[dict[str, Any]], actor: Actor,
                  *, reason: str | None = None) -> list[ReviewCode]:
    """整体替换项目的审核代码：旧的停用（留历史），新的逐个新建。全部校验通过才写入。"""
    get_writable_project(session, project_id)
    if not codes:
        raise Invalid("review_code.empty", "至少要有一个审核代码")
    seen: set[str] = set()
    prepared = [_validate(c, i, seen) for i, c in enumerate(codes, start=1)]
    for old in list_codes(session, project_id):
        before = snapshot(old)
        old.active = False
        record(session, old, ChangeAction.DELETE, before, snapshot(old), actor, reason=reason,
               project_id=project_id)
    created = []
    for data in prepared:
        code = ReviewCode(project_id=project_id, **data)
        session.add(code)
        session.flush()
        record(session, code, ChangeAction.CREATE, None, snapshot(code), actor, reason=reason,
               project_id=project_id)
        created.append(code)
    return created


def find_code(codes: list[ReviewCode], text: str | None) -> ReviewCode | None:
    """按代码、英文 / 中文名称、别名找审核代码（不区分大小写）；"1 - Approved" 这种写法也认。"""
    if text is None:
        return None
    wanted = normalize(str(text))
    if not wanted:
        return None
    head = normalize(str(text).split("-")[0]) if "-" in str(text) else None
    for c in codes:
        names = {normalize(c.code), normalize(c.label_en), *(normalize(a) for a in c.aliases)}
        if c.label_zh:
            names.add(normalize(c.label_zh))
        if wanted in names or (head and head == normalize(c.code)):
            return c
    return None


def code_for_status(codes: list[ReviewCode], status: DeliverableStatus) -> ReviewCode | None:
    """某个平台状态默认用哪个审核代码（MDL 导回时用）：同状态里优先取"最终"的、排在前面的。"""
    same = [c for c in codes if c.status is status]
    return next((c for c in same if c.final), same[0] if same else None)
