"""传送单（提交记录）与客户回复（PRD F7.2、F7.6）。

应交项的状态跟着它**最近一次提交**走（作废的传送单不算）：
- 已提交、还没回复 → 已提交，责任方 客户，状态日期 = 提交日期
- 有回复 → 审核代码对应的状态，状态日期 = 回复日期，依据写回函号和传送单号
状态由 update_deliverable 写入（留痕、发事件）。人手改过的状态，下一次提交或回复时才会被覆盖。

"最近一次"按 提交日期（没有时按建立日期）→ 传送单建立顺序 排。
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    ChangeAction,
    Deliverable,
    DeliverableStatus,
    Project,
    ReplySource,
    Responsible,
    ReviewCode,
    Transmittal,
    TransmittalItem,
    TransmittalItemVersion,
    TransmittalSource,
)
from app.services import review_codes as codes_svc
from app.services.crud import get_in_project
from app.services.deliverables import update_deliverable
from app.services.errors import Conflict, Invalid, NotFound
from app.services.history import Actor, emit, record, record_summary, snapshot
from app.services.projects import get_project, get_writable_project

S = DeliverableStatus
DEFAULT_PREFIX = "TR"
HEADER_FIELDS = ("subject", "submitted_date", "method", "external_ref", "remarks")


# ---------------------------------------------------------------- 查询


def get_transmittal(session: Session, project_id: int, transmittal_id: int) -> Transmittal:
    get_project(session, project_id)
    return get_in_project(session, Transmittal, transmittal_id, project_id, "transmittal")


def items_of(session: Session, transmittal_id: int) -> list[TransmittalItem]:
    return list(session.scalars(select(TransmittalItem)
                                .where(TransmittalItem.transmittal_id == transmittal_id)
                                .order_by(TransmittalItem.id)))


def list_transmittals(session: Session, project_id: int, *, include_void: bool = False
                      ) -> list[dict[str, Any]]:
    """传送单列表（新的在前），带份数和已回复份数。"""
    get_project(session, project_id)
    stmt = select(Transmittal).where(Transmittal.project_id == project_id)
    if not include_void:
        stmt = stmt.where(Transmittal.void.is_(False))
    rows = list(session.scalars(stmt.order_by(Transmittal.seq.desc())))
    counts: dict[int, tuple[int, int]] = {}
    if rows:
        for tid, total, replied in session.execute(
                select(TransmittalItem.transmittal_id, func.count(),
                       func.count(TransmittalItem.review_code))
                .where(TransmittalItem.transmittal_id.in_([t.id for t in rows]))
                .group_by(TransmittalItem.transmittal_id)):
            counts[tid] = (total, replied)
    return [{"transmittal": t, "items": counts.get(t.id, (0, 0))[0],
             "replied": counts.get(t.id, (0, 0))[1]} for t in rows]


@dataclass
class Latest:
    item: TransmittalItem
    transmittal: Transmittal


def _order_key(t: Transmittal) -> tuple:
    return (t.submitted_date or (t.created_at.date() if t.created_at else date.max), t.seq)


def latest_items(session: Session, project_id: int,
                 deliverable_ids: list[int] | None = None) -> dict[int, Latest]:
    """每个应交项最近一次提交（不含作废的传送单）。"""
    stmt = (select(TransmittalItem, Transmittal)
            .join(Transmittal, Transmittal.id == TransmittalItem.transmittal_id)
            .where(Transmittal.project_id == project_id, Transmittal.void.is_(False)))
    if deliverable_ids is not None:
        if not deliverable_ids:
            return {}
        stmt = stmt.where(TransmittalItem.deliverable_id.in_(deliverable_ids))
    latest: dict[int, Latest] = {}
    for item, t in session.execute(stmt):
        current = latest.get(item.deliverable_id)
        if current is None or (_order_key(t), item.id) > (_order_key(current.transmittal),
                                                            current.item.id):
            latest[item.deliverable_id] = Latest(item, t)
    return latest


def review_due(project: Project, t: Transmittal) -> date | None:
    if project.review_days is None or t.submitted_date is None:
        return None
    return t.submitted_date + timedelta(days=project.review_days)


def review_info(session: Session, project_id: int, today: date) -> dict[int, dict[str, Any]]:
    """给应交项列表用：最近一张传送单、审核代码、回复日期、是否最终、审核截止日、是否客户超期。"""
    project = get_project(session, project_id)
    info = {}
    for deliverable_id, latest in latest_items(session, project_id).items():
        item, t = latest.item, latest.transmittal
        due = review_due(project, t)
        info[deliverable_id] = {
            "transmittal_id": t.id, "transmittal_no": t.number,
            "review_code": item.review_code,
            "review_status": item.review_status.value if item.review_status else None,
            "review_final": item.review_final, "reply_date": item.reply_date,
            "reply_ref": item.reply_ref, "deemed": item.deemed, "revision": item.revision,
            "review_due": due,
            "review_overdue": item.review_code is None and due is not None and due < today,
        }
    return info


# ---------------------------------------------------------------- 推出应交项状态


def _basis(item: TransmittalItem, t: Transmittal) -> str:
    if item.review_code is None:
        return f"传送单 {t.number}"
    parts = [f"审核代码 {item.review_code}"]
    if item.deemed:
        parts.append(f"视为批准：{item.deemed_basis}")
    if item.reply_ref:
        parts.append(item.reply_ref)
    return "，".join(parts) + f"（传送单 {t.number}）"


def sync_deliverables(session: Session, project_id: int, deliverable_ids: list[int],
                      actor: Actor, *, reason: str | None = None) -> None:
    """按最近一次提交 / 回复更新应交项的状态、责任方、日期、依据。"""
    latest = latest_items(session, project_id, deliverable_ids)
    for deliverable_id in deliverable_ids:
        d = session.get(Deliverable, deliverable_id)
        if d is None or d.obsolete:
            continue
        found = latest.get(deliverable_id)
        if found is None:
            # 提交记录全部作废：回到未提交
            changes: dict[str, Any] = {"status": S.NOT_SUBMITTED, "submitted_date": None,
                                       "status_basis": reason}
        elif found.item.review_status is None:
            t = found.transmittal
            changes = {"status": S.SUBMITTED, "responsible": Responsible.CLIENT,
                       "submitted_date": t.submitted_date, "status_date": t.submitted_date,
                       "status_basis": _basis(found.item, t)}
        else:
            item, t = found.item, found.transmittal
            changes = {"status": item.review_status, "submitted_date": t.submitted_date,
                       "status_date": item.reply_date, "status_basis": _basis(item, t)}
        update_deliverable(session, project_id, deliverable_id, changes, actor, reason=reason)


# ---------------------------------------------------------------- 新建、补记、修改、作废


def _next_number(session: Session, project: Project) -> tuple[int, str]:
    seq = (session.scalar(select(func.max(Transmittal.seq))
                          .where(Transmittal.project_id == project.id)) or 0) + 1
    prefix = (project.numbering_rules or {}).get("transmittal_prefix", DEFAULT_PREFIX)
    return seq, f"{project.code}-{prefix}-{seq:04d}"


def _check_deliverables(session: Session, project_id: int, ids: list[int]) -> list[int]:
    ids = list(dict.fromkeys(ids))
    if not ids:
        raise Invalid("transmittal.no_items", "没有选择应交项")
    for deliverable_id in ids:
        d = get_in_project(session, Deliverable, deliverable_id, project_id, "deliverable")
        if d.obsolete:
            raise Conflict("deliverable.obsolete", f"应交项 {deliverable_id} 已作废，不能提交",
                           id=deliverable_id)
    return ids


def create_transmittal(session: Session, project_id: int, deliverable_ids: list[int],
                       data: dict[str, Any], actor: Actor, *,
                       source: TransmittalSource = TransmittalSource.PLATFORM,
                       revisions: dict[int, str] | None = None,
                       versions: dict[int, list[int]] | None = None,
                       sync: bool = True) -> Transmittal:
    """新建传送单：在平台里生成，或事后补记（在邮件 / 客户系统里已经交了的）。

    平台生成的提交日期默认今天；补记的必须给提交日期。同时把这些应交项改成"已提交"。
    """
    project = get_writable_project(session, project_id)
    ids = _check_deliverables(session, project_id, deliverable_ids)
    data = {k: data.get(k) for k in HEADER_FIELDS}
    if source is TransmittalSource.PLATFORM and data["submitted_date"] is None:
        data["submitted_date"] = date.today()
    if source is TransmittalSource.RECORDED and data["submitted_date"] is None:
        raise Invalid("transmittal.date_required", "补记已提交的文件必须填提交日期")
    for f in ("subject", "method", "external_ref", "remarks"):
        data[f] = (data[f] or "").strip() or None
    seq, number = _next_number(session, project)
    t = Transmittal(project_id=project_id, seq=seq, number=number, source=source,
                    created_by_name=actor.name, **data)
    session.add(t)
    session.flush()
    # 平台生成的记下交的是哪几版文件（每份非客户文件的当前版本；交付包给的是包里的版本）；
    # 这些版本不进回收区。补记的不知道交的哪一版，不记
    from app.services.files import current_version_ids  # 避免循环引用

    if versions is None:
        versions = current_version_ids(session, ids) if source is TransmittalSource.PLATFORM else {}
    for deliverable_id in ids:
        vids = sorted(set(versions.get(deliverable_id) or []))
        item = TransmittalItem(transmittal_id=t.id, deliverable_id=deliverable_id,
                               version_id=vids[-1] if vids else None,
                               revision=(revisions or {}).get(deliverable_id))
        session.add(item)
        session.flush()
        for vid in vids:
            session.add(TransmittalItemVersion(item_id=item.id, version_id=vid))
    session.flush()
    record(session, t, ChangeAction.CREATE, None, {**snapshot(t), "deliverable_ids": ids}, actor,
           project_id=project_id)
    emit(session, "transmittal.created", t, {"number": t.number, "deliverables": len(ids),
                                             "source": source.value}, project_id=project_id)
    if sync:
        sync_deliverables(session, project_id, ids, actor, reason=f"传送单 {t.number}")
    return t


def update_transmittal(session: Session, project_id: int, transmittal_id: int,
                       changes: dict[str, Any], actor: Actor, *, reason: str | None = None
                       ) -> Transmittal:
    """改表头（标题、提交日期、方式、客户系统单号、备注）；改了提交日期会重新推出状态。"""
    get_writable_project(session, project_id)
    t = get_transmittal(session, project_id, transmittal_id)
    if t.void:
        raise Conflict("transmittal.void", f"传送单 {t.number} 已作废", id=t.id)
    changes = {k: v for k, v in changes.items() if k in HEADER_FIELDS}
    before = snapshot(t)
    for k, v in changes.items():
        setattr(t, k, v.strip() or None if isinstance(v, str) else v)
    log = record(session, t, ChangeAction.UPDATE, before, snapshot(t), actor, reason=reason,
                 project_id=project_id)
    if log is not None and "submitted_date" in log.changes:
        sync_deliverables(session, project_id, [i.deliverable_id for i in items_of(session, t.id)],
                          actor, reason=reason or f"传送单 {t.number} 改提交日期")
    return t


def void_transmittal(session: Session, project_id: int, transmittal_id: int, actor: Actor, *,
                     reason: str | None) -> Transmittal:
    """作废（要写原因）：相关应交项按剩下的记录重新推出状态。"""
    get_writable_project(session, project_id)
    t = get_transmittal(session, project_id, transmittal_id)
    if t.void:
        raise Conflict("transmittal.void", f"传送单 {t.number} 已作废", id=t.id)
    if not (reason or "").strip():
        raise Invalid("transmittal.void_reason_required", "作废传送单必须写原因")
    before = snapshot(t)
    t.void, t.void_reason = True, reason.strip()
    record(session, t, ChangeAction.UPDATE, before, snapshot(t), actor, reason=reason,
           project_id=project_id)
    emit(session, "transmittal.voided", t, {"number": t.number}, project_id=project_id)
    sync_deliverables(session, project_id, [i.deliverable_id for i in items_of(session, t.id)],
                      actor, reason=f"传送单 {t.number} 作废：{reason.strip()}")
    return t


# ---------------------------------------------------------------- 客户回复


def _apply_reply(item: TransmittalItem, code: ReviewCode | None, reply: dict[str, Any],
                 source: ReplySource, actor: Actor) -> None:
    if code is None:  # 撤回回复
        item.review_code = item.review_status = item.review_final = None
        item.reply_date = item.reply_ref = item.reply_comments = item.deemed_basis = None
        item.deemed, item.reply_source, item.replied_by_name = False, None, None
        return
    item.review_code, item.review_status, item.review_final = code.code, code.status, code.final
    item.reply_date = reply.get("reply_date")
    item.reply_ref = (reply.get("reply_ref") or "").strip() or None
    item.reply_comments = (reply.get("reply_comments") or "").strip() or None
    item.deemed = bool(reply.get("deemed"))
    item.deemed_basis = (reply.get("deemed_basis") or "").strip() or None if item.deemed else None
    item.reply_source, item.replied_by_name = source, actor.name


def set_reply(session: Session, project_id: int, transmittal_id: int, reply: dict[str, Any],
              actor: Actor, *, items: list[dict[str, Any]] | None = None,
              source: ReplySource = ReplySource.MANUAL, reason: str | None = None
              ) -> dict[str, int]:
    """录入客户回复：reply 里的代码、回复日期、函件号、意见、视为批准用到整张单上；
    items 里可以单独给某几份不同的结论（只给 items、不给 reply.code 时只改这几份）。
    代码传空字符串表示撤回回复。视为批准必须写合同依据。
    """
    get_writable_project(session, project_id)
    t = get_transmittal(session, project_id, transmittal_id)
    if t.void:
        raise Conflict("transmittal.void", f"传送单 {t.number} 已作废", id=t.id)
    codes = codes_svc.list_codes(session, project_id)
    all_items = {i.id: i for i in items_of(session, t.id)}

    def resolve(text: Any) -> ReviewCode | None | str:
        if text is None:
            return "skip"
        if str(text).strip() == "":
            return None
        code = codes_svc.find_code(codes, text)
        if code is None:
            raise Invalid("transmittal.bad_code", f"审核代码 {text} 不在项目配置里", code=str(text))
        return code

    plan: dict[int, tuple[ReviewCode | None, dict[str, Any]]] = {}
    default = resolve(reply.get("code"))
    if default != "skip":
        for item_id in all_items:
            plan[item_id] = (default, reply)
    for override in items or []:
        item_id = override.get("item_id")
        if item_id not in all_items:
            raise NotFound("transmittal_item.not_found", f"传送单上没有明细 {item_id}", id=item_id)
        merged = {**reply, **{k: v for k, v in override.items() if v is not None}}
        code = resolve(override.get("code", reply.get("code")))
        if code != "skip":
            plan[item_id] = (code, merged)
    if not plan:
        raise Invalid("transmittal.no_reply", "没有给出审核代码")
    for code, data in plan.values():
        if code is not None and data.get("deemed") and not (data.get("deemed_basis") or "").strip():
            raise Invalid("transmittal.deemed_needs_basis", "视为批准必须写合同依据（条款号）")

    changed = []
    for item_id, (code, data) in plan.items():
        item = all_items[item_id]
        before = snapshot(item)
        _apply_reply(item, code, data, source, actor)
        if record(session, item, ChangeAction.UPDATE, before, snapshot(item), actor,
                  reason=reason, project_id=project_id) is not None:
            changed.append(item)
    session.flush()
    if changed:
        emit(session, "transmittal.replied", t, {"number": t.number, "items": len(changed)},
             project_id=project_id)
        sync_deliverables(session, project_id, [i.deliverable_id for i in changed], actor,
                          reason=reason or f"客户回复（传送单 {t.number}）")
    return {"updated": len(changed), "unchanged": len(plan) - len(changed)}


# ---------------------------------------------------------------- MDL 导回时补记录


def record_mdl_return(session: Session, project_id: int, entries: list[dict[str, Any]],
                      actor: Actor, *, source_name: str | None) -> Transmittal | None:
    """MDL 导回改了状态后补上提交 / 回复记录（应交项状态已由导回写好，这里不再推出）。

    - 改成 已批准 / 需修改重交 / 不适用以外的客户结论：挂到最近一张还没回复的传送单上；
      没有就放进一张自动建立的"MDL 导回补记"传送单（提交日期取 MDL 上的，不知道为空）
    - 改成 已提交 / 审核中、且没有未回复的传送单：同样补一条提交记录（不带回复）
    entries: [{"deliverable_id", "status", "status_date", "submitted_date"}]
    """
    reply_statuses = (S.APPROVED, S.REVISE_RESUBMIT)
    pending_statuses = (S.SUBMITTED, S.UNDER_REVIEW)
    entries = [e for e in entries if e["status"] in reply_statuses + pending_statuses]
    if not entries:
        return None
    codes = codes_svc.list_codes(session, project_id)
    latest = latest_items(session, project_id, [e["deliverable_id"] for e in entries])
    ref = f"MDL 导回（{source_name}）" if source_name else "MDL 导回"
    orphans: list[dict[str, Any]] = []
    replied = 0
    for e in entries:
        found = latest.get(e["deliverable_id"])
        open_item = found.item if found and found.item.review_code is None else None
        if e["status"] in pending_statuses:
            if open_item is None:
                orphans.append(e)
            continue
        code = codes_svc.code_for_status(codes, e["status"])
        if code is None:
            continue
        if open_item is None:
            orphans.append({**e, "code": code})
            continue
        before = snapshot(open_item)
        _apply_reply(open_item, code, {"reply_date": e.get("status_date"), "reply_ref": ref},
                     ReplySource.MDL_RETURN, actor)
        record(session, open_item, ChangeAction.UPDATE, before, snapshot(open_item), actor,
               reason=ref, project_id=project_id)
        replied += 1

    t = None
    if orphans:
        dates = {e.get("submitted_date") for e in orphans} - {None}
        t = create_transmittal(
            session, project_id, [e["deliverable_id"] for e in orphans],
            {"subject": f"{ref}补记", "submitted_date": dates.pop() if len(dates) == 1 else None},
            actor, source=TransmittalSource.MDL_RETURN, sync=False)
        by_deliverable = {i.deliverable_id: i for i in items_of(session, t.id)}
        for e in orphans:
            if "code" in e:
                _apply_reply(by_deliverable[e["deliverable_id"]], e["code"],
                             {"reply_date": e.get("status_date"), "reply_ref": ref},
                             ReplySource.MDL_RETURN, actor)
        record_summary(session, project_id=project_id, entity_type="transmittal_items",
                       entity_id=f"transmittal:{t.id}", action=ChangeAction.UPDATE,
                       changes={"replied": [None, sum("code" in e for e in orphans)]},
                       actor=actor, reason=ref)
    if replied or orphans:
        emit(session, "transmittal.replied", t, {"source": "mdl_return", "items": replied + len(orphans)},
             project_id=project_id)
    return t


def deliverables_by_transmittal(session: Session, transmittal_ids: list[int]
                                ) -> dict[int, list[int]]:
    out: dict[int, list[int]] = defaultdict(list)
    for tid, did in session.execute(select(TransmittalItem.transmittal_id,
                                           TransmittalItem.deliverable_id)
                                    .where(TransmittalItem.transmittal_id.in_(transmittal_ids))):
        out[tid].append(did)
    return out
