"""阶段基线（PRD F8.3）：冻结某一时刻每个应交项的状态、责任方和挂着的每份文件的当前版本（缺件也记）；
查看、两个基线对比（或和现在比）、导出清单 Excel、按基线导出交付包（delivery.start_package）。
基线只增不改；被基线引用的版本不进回收区、不清理（files.protected_version_ids）。
"""

import io
from collections import defaultdict
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Baseline, BaselineItem, ChangeAction, DeliverableStatus, Responsible, Version
from app.services.browse import Snapshot
from app.services.errors import Invalid, NotFound
from app.services.history import Actor, emit, record, snapshot
from app.services.projects import get_project, get_writable_project


def get_baseline(session: Session, project_id: int, baseline_id: int) -> Baseline:
    b = session.get(Baseline, baseline_id)
    if b is None or b.project_id != project_id:
        raise NotFound("baseline.not_found", f"基线 {baseline_id} 不存在", id=baseline_id)
    return b


def _current(snap: Snapshot) -> dict[int, dict[str, Any]]:
    """现在每个（未作废的）应交项：状态、责任方、挂着的每份文件的当前版本。"""
    out = {}
    for did, it in snap.items.items():
        if it.obsolete:
            continue
        versions = {}
        for fid in snap.by_deliverable.get(did, ()):
            row = snap.files[fid]
            if row.latest is not None:
                versions[row.latest.id] = fid
        out[did] = {"status": it.status, "responsible": it.responsible, "versions": versions}
    return out


def freeze(session: Session, project_id: int, name: str, actor: Actor, *, description: str | None = None
           ) -> Baseline:
    get_writable_project(session, project_id)
    name = (name or "").strip()
    if not name:
        raise Invalid("baseline.name_required", "基线要有名称")
    snap = Snapshot(session, project_id)
    current = _current(snap)
    b = Baseline(project_id=project_id, name=name, description=(description or "").strip() or None,
                 frozen_by=actor.id, frozen_by_name=actor.name)
    session.add(b)
    session.flush()
    by_status: dict[str, int] = defaultdict(int)
    with_files = 0
    for did, cur in current.items():
        status, responsible = DeliverableStatus(cur["status"]), Responsible(cur["responsible"])
        by_status[cur["status"]] += 1
        if cur["versions"]:
            with_files += 1
            for vid in sorted(cur["versions"]):
                session.add(BaselineItem(baseline_id=b.id, deliverable_id=did, version_id=vid,
                                         status=status, responsible=responsible))
        else:
            session.add(BaselineItem(baseline_id=b.id, deliverable_id=did, version_id=None,
                                     status=status, responsible=responsible))
    b.counts = {"deliverables": len(current), "with_files": with_files, "missing": len(current) - with_files,
                "files": sum(len(c["versions"]) for c in current.values()), "by_status": dict(by_status)}
    session.flush()
    record(session, b, ChangeAction.CREATE, None, snapshot(b), actor)
    emit(session, "baseline.frozen", b, {"name": b.name, **b.counts})
    return b


def baseline_out(b: Baseline) -> dict[str, Any]:
    return {"id": b.id, "name": b.name, "description": b.description, "frozen_at": b.frozen_at,
            "frozen_by": b.frozen_by_name, "counts": b.counts or {}}


def list_baselines(session: Session, project_id: int) -> list[dict[str, Any]]:
    get_project(session, project_id)
    return [baseline_out(b) for b in session.scalars(
        select(Baseline).where(Baseline.project_id == project_id).order_by(Baseline.id.desc()))]


def _side(session: Session, baseline_id: int | None, snap: Snapshot) -> dict[int, dict[str, Any]]:
    """一边的数据：基线（按基线编号）或现在（None）。versions：版本 id → 文件 id。"""
    if baseline_id is None:
        return _current(snap)
    rows = session.execute(select(BaselineItem, Version.file_id)
                           .outerjoin(Version, Version.id == BaselineItem.version_id)
                           .where(BaselineItem.baseline_id == baseline_id)).all()
    out: dict[int, dict[str, Any]] = {}
    for item, fid in rows:
        side = out.setdefault(item.deliverable_id, {"status": item.status.value,
                                                     "responsible": item.responsible.value, "versions": {}})
        if item.version_id:
            side["versions"][item.version_id] = fid
    return out


def _label(snap: Snapshot, did: int) -> dict[str, Any]:
    it = snap.items.get(did)
    if it is None:
        return {"deliverable_id": did}
    return {"deliverable_id": did, "milestone_code": it.milestone_code, "submittal_no": it.submittal_no,
            "document_type_name": it.document_type_name, "where": snap.where(it)}


def detail(session: Session, project_id: int, baseline_id: int) -> dict[str, Any]:
    """基线里每个应交项：冻结时的状态、责任方、每份文件的版本（文件名按现在的结构算）。"""
    b = get_baseline(session, project_id, baseline_id)
    snap = Snapshot(session, project_id)
    side = _side(session, b.id, snap)
    versions = {v.id: v for v in session.scalars(select(Version).where(
        Version.id.in_({vid for s in side.values() for vid in s["versions"]})))}
    rows = []
    for did, s in side.items():
        files = []
        for vid, fid in s["versions"].items():
            v, row = versions.get(vid), snap.files.get(fid)
            files.append({"version_id": vid, "file_id": fid, "version_no": v.version_no if v else None,
                          "filename": snap.name(row, v) if (row and v) else None,
                          "original_name": v.original_name if v else None})
        rows.append({**_label(snap, did), "status": s["status"], "responsible": s["responsible"],
                     "files": sorted(files, key=lambda f: f["filename"] or "")})
    rows.sort(key=lambda r: (r.get("milestone_code") or "", _num(r.get("submittal_no")), r.get("where") or ""))
    return {**baseline_out(b), "items": rows}


def _num(no: str | None) -> tuple:
    return tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in (no or "").split("."))


def compare(session: Session, project_id: int, a_id: int, b_id: int | None) -> dict[str, Any]:
    """基线 a 和基线 b（或现在）比：每个应交项有哪些变化。"""
    a = get_baseline(session, project_id, a_id)
    b = get_baseline(session, project_id, b_id) if b_id is not None else None
    snap = Snapshot(session, project_id)
    left, right = _side(session, a.id, snap), _side(session, b.id if b else None, snap)
    versions = {v.id: v for v in session.scalars(select(Version).where(Version.id.in_(
        {vid for s in (*left.values(), *right.values()) for vid in s["versions"]})))}

    def ver(vid: int) -> str:
        v = versions.get(vid)
        return f"V{v.version_no} {v.original_name}" if v else str(vid)

    rows = []
    for did in sorted(set(left) | set(right)):
        l_side, r_side = left.get(did), right.get(did)
        changes = []
        if l_side is None:
            changes.append({"kind": "added"})
        elif r_side is None:
            changes.append({"kind": "removed"})
        else:
            if l_side["status"] != r_side["status"]:
                changes.append({"kind": "status", "from": l_side["status"], "to": r_side["status"]})
            l_files = {fid: vid for vid, fid in l_side["versions"].items()}
            r_files = {fid: vid for vid, fid in r_side["versions"].items()}
            for fid in sorted(set(l_files) | set(r_files)):
                lv, rv = l_files.get(fid), r_files.get(fid)
                if lv and rv and lv != rv:
                    changes.append({"kind": "new_version", "from": ver(lv), "to": ver(rv)})
                elif rv and not lv:
                    changes.append({"kind": "new_file", "to": ver(rv)})
                elif lv and not rv:
                    changes.append({"kind": "file_removed", "from": ver(lv)})
            if l_files and not r_files:
                changes.append({"kind": "now_missing"})
            elif r_files and not l_files:
                changes.append({"kind": "now_delivered"})
        if changes:
            rows.append({**_label(snap, did), "changes": changes})
    rows.sort(key=lambda r: (r.get("milestone_code") or "", _num(r.get("submittal_no")), r.get("where") or ""))
    return {"a": baseline_out(a), "b": baseline_out(b) if b else None, "rows": rows,
            "counts": {k: sum(1 for r in rows for c in r["changes"] if c["kind"] == k)
                       for k in ("status", "new_version", "new_file", "file_removed", "now_missing",
                                 "now_delivered", "added", "removed")}}


HEAD = {"zh": ("里程碑", "编号", "文件种类", "设备 / 模块", "状态", "责任方", "版本", "文件名", "原文件名"),
        "en": ("Milestone", "No.", "Document type", "Equipment / module", "Status", "Responsible", "Version",
               "File name", "Original name")}


def export_xlsx(session: Session, project_id: int, baseline_id: int, lang: str = "zh") -> bytes:
    d = detail(session, project_id, baseline_id)
    wb = Workbook()
    ws = wb.active
    ws.title = "baseline"
    ws.append([d["name"], str(d["frozen_at"])[:16], d["frozen_by"] or ""])
    ws.append([])
    ws.append(HEAD.get(lang, HEAD["zh"]))
    for cell in ws[3]:
        cell.font = Font(bold=True)
    for r in d["items"]:
        base = [r.get("milestone_code"), r.get("submittal_no"), r.get("document_type_name"), r.get("where"),
                r["status"], r["responsible"]]
        if not r["files"]:
            ws.append([*base, "", "", ""])
        for f in r["files"]:
            ws.append([*base, f"V{f['version_no']}" if f["version_no"] else "", f["filename"], f["original_name"]])
    for col, width in zip("ABCDEFGHI", (10, 8, 30, 24, 14, 10, 8, 50, 40), strict=True):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A4"
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
