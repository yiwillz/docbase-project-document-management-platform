"""设备清单导入（PRD F3.1）：自动认表头 → 预览（可手动指定列）→ 比对（有编号按编号，没编号按名称）→ 确认写入。

设备清单基本是"一行一台设备"的平表，所以按表头认列是可靠的；认不全时不报错，
返回全部列和猜测结果，由用户指定"哪一列是什么"后重新预览。
认不出的列原样存进设备的"其他属性"（attrs），一列都不丢。
清单里没有了的设备只提示，不删除（确认时可选择一起停用）。
"""

import io
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Equipment, EquipmentType
from app.services import equipment as svc
from app.services.history import Actor
from app.services.projects import get_writable_project

# 标准字段 → 认得的表头写法（比较时去掉空格和标点、不区分大小写）
FIELDS: dict[str, tuple[str, ...]] = {
    "tag": ("设备编号", "编号", "位号", "设备位号", "no", "tag", "tagno", "equipmentno", "itemno"),
    "name": ("设备名", "设备名称", "名称", "name", "equipmentname", "equipment"),
    "equipment_type": ("设备种类", "种类", "类别", "设备类别", "category", "type", "equipmenttype"),
    "vendor": ("供应商", "厂家", "制造商", "vendor", "supplier", "manufacturer"),
    "quantity": ("台数", "数量", "qty", "quantity"),
    "aliases": ("别名", "alias", "aliases"),
}
FIELD_LABELS = {"tag": "设备编号", "name": "设备名", "equipment_type": "设备种类", "vendor": "供应商",
                "quantity": "台数", "aliases": "别名"}
REQUIRED = ("name",)  # 设备编号选填：没编号的按名称区分
ATTR, IGNORE = "attr", "ignore"  # 手动指定时：存进其他属性 / 忽略
HEADER_SCAN_ROWS = 20
ERROR_VALUES = {"#REF!", "#N/A", "#VALUE!", "#DIV/0!", "#NAME?", "#NULL!", "#NUM!"}
_LIST_SPLIT = re.compile(r"[,，;；/\n]+")


def _norm(text: Any) -> str:
    return re.sub(r"[\s\.\#:：_\-()（）]+", "", str(text or "")).casefold()


_LOOKUP = {_norm(alias): name for name, aliases in FIELDS.items() for alias in aliases}


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, (datetime, date)):
        value = (value.date() if isinstance(value, datetime) else value).isoformat()
    text = " ".join(str(value).replace("\xa0", " ").split())
    return None if not text or text in ERROR_VALUES else text


def _attr_value(value: Any) -> Any:
    """存进 attrs 的值：数字保留数字，日期转文字，其余转文字。"""
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return int(value) if isinstance(value, float) and value.is_integer() else value
    return _text(value)


# ---------------------------------------------------------------- 标准模板


def build_template(lang: str = "zh") -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "设备清单" if lang == "zh" else "Equipment"
    headers = ([FIELD_LABELS[f] for f in FIELDS] if lang == "zh"
               else ["Tag", "Name", "Category", "Vendor", "Qty", "Alias"])
    for col, header in enumerate(headers, start=1):
        cell = ws.cell(1, col, header)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="C00000" if col <= 2 else "305496")
        cell.alignment = Alignment(horizontal="center")
        ws.column_dimensions[get_column_letter(col)].width = 18 if col != 2 else 36
    ws.freeze_panes = "A2"
    guide = wb.create_sheet("说明" if lang == "zh" else "Guide")
    guide.column_dimensions["A"].width = 90
    for line in ("设备清单导入模板",
                 "1. 一行一个设备条目；红色表头（设备名）必填。",
                 "2. 设备编号选填：有编号的项目内唯一，停用的设备也占用编号，不能复用；",
                 "   没编号的（数量多、没位号的）靠名称区分，没编号的设备名称不能重复。",
                 "3. 台数不填按 1；别名多个用逗号分隔；设备种类不存在会自动新建。",
                 "4. 可以在后面加任意列（如功率、尺寸、电压），导入后原样保存为设备属性。"):
        guide.append([line])
    guide["A1"].font = Font(bold=True, size=14)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------- 解析


@dataclass
class Issue:
    code: str
    message: str
    row: int | None = None
    column: str | None = None


@dataclass
class ParsedEquipment:
    sheet: str | None = None
    sheets: list[str] = field(default_factory=list)
    header_row: int | None = None
    columns: list[dict[str, Any]] = field(default_factory=list)  # 每列：字母、表头、对应、样例
    rows: list[dict[str, Any]] = field(default_factory=list)
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_json(self) -> dict[str, Any]:
        return {**{k: v for k, v in asdict(self).items() if k not in ("errors", "warnings")},
                "errors": [asdict(i) for i in self.errors],
                "warnings": [asdict(i) for i in self.warnings]}


def _guess_header_row(ws) -> int | None:
    """在前 20 行里找认得的表头最多的一行。"""
    best, best_score = None, 0
    for r in range(1, min(ws.max_row, HEADER_SCAN_ROWS) + 1):
        found = {_LOOKUP.get(_norm(ws.cell(r, c).value)) for c in range(1, ws.max_column + 1)}
        found.discard(None)
        if len(found) > best_score:
            best, best_score = r, len(found)
    return best


def _add_row(result: ParsedEquipment, values: dict[str, Any], attrs: dict[str, Any], r: int,
             seen: dict[tuple[str, str], int], quantity_column: str | None = None) -> None:
    """校验一行（设备名必填、同一设备不能出现两次、台数是正整数），通过的放进 result.rows。"""
    row: dict[str, Any] = {"row": r}
    row["tag"], row["name"] = _text(values.get("tag")), _text(values.get("name"))
    if row["name"] is None:
        result.errors.append(Issue("equipment_import.required", "设备名不能为空", row=r))
        return
    key = identity(row["tag"], row["name"])
    if key in seen:
        what = f"设备编号 {row['tag']}" if row["tag"] else f"没编号的设备\"{row['name']}\""
        result.errors.append(Issue("equipment_import.duplicate_tag",
                                   f"{what}和第 {seen[key]} 行重复", row=r))
        return
    seen[key] = r
    row["equipment_type"] = _text(values.get("equipment_type"))
    row["vendor"] = _text(values.get("vendor"))
    quantity = _text(values.get("quantity"))
    if quantity is None:
        row["quantity"] = None
    elif re.fullmatch(r"[1-9]\d*", quantity):
        row["quantity"] = int(quantity)
    else:
        result.errors.append(Issue("equipment_import.bad_quantity", f"台数应为正整数：{quantity}",
                                   row=r, column=quantity_column))
        return
    aliases = _text(values.get("aliases"))
    row["aliases"] = [a.strip() for a in _LIST_SPLIT.split(aliases)] if aliases else []
    row["attrs"] = attrs
    result.rows.append(row)


def parse_rows(rows: list[dict[str, Any]]) -> ParsedEquipment:
    """页面上粘贴的多行（每行：tag / name / equipment_type / vendor / quantity / aliases），
    和 Excel 导入同样校验；行号从 1 开始。"""
    result = ParsedEquipment()
    seen: dict[tuple[str, str], int] = {}
    for r, values in enumerate(rows, start=1):
        if all(_text(values.get(f)) is None for f in FIELDS):
            continue
        _add_row(result, values, {}, r, seen)
    if not result.rows and result.ok:
        result.warnings.append(Issue("equipment_import.empty", "没有设备数据"))
    return result


def parse_equipment(data: bytes, *, sheet: str | None = None, header_row: int | None = None,
                    mapping: dict[str, str] | None = None) -> ParsedEquipment:
    """mapping：手动指定的列对应，键为列字母（如 "C"），值为标准字段 / "attr" / "ignore"。"""
    result = ParsedEquipment()
    try:
        wb = load_workbook(io.BytesIO(data), data_only=True)
    except Exception as exc:
        result.errors.append(Issue("equipment_import.unreadable", f"无法读取 Excel：{exc}"))
        return result
    result.sheets = wb.sheetnames
    if sheet is not None and sheet not in wb.sheetnames:
        result.errors.append(Issue("equipment_import.no_sheet",
                                   f"没有工作表 {sheet}（有：{', '.join(wb.sheetnames)}）"))
        return result
    ws = wb[sheet] if sheet else wb.worksheets[0]
    result.sheet = ws.title

    result.header_row = header_row or _guess_header_row(ws)
    if result.header_row is None:
        # 一个表头都认不出：先把第一行有内容的当表头，列出全部列，让用户指定对应
        result.header_row = next((r for r in range(1, ws.max_row + 1)
                                  if any(_text(c.value) for c in ws[r])), None)
        if result.header_row is None:
            result.errors.append(Issue("equipment_import.empty", "表是空的"))
            return result
        result.warnings.append(Issue(
            "equipment_import.header_guessed",
            f"前 {HEADER_SCAN_ROWS} 行里没有认得的表头，暂按第 {result.header_row} 行当表头；"
            "如果不对，请指定表头在第几行", row=result.header_row))

    mapping = {k.upper(): v for k, v in (mapping or {}).items()}
    valid_targets = set(FIELDS) | {ATTR, IGNORE}
    for letter, target in mapping.items():
        if target not in valid_targets:
            result.errors.append(Issue("equipment_import.bad_mapping",
                                       f"列 {letter} 指定的 {target} 不认识", column=letter))

    used: dict[str, str] = {}
    for c in range(1, ws.max_column + 1):
        header = _text(ws.cell(result.header_row, c).value)
        letter = get_column_letter(c)
        if header is None and letter not in mapping:
            continue
        target = mapping.get(letter) or _LOOKUP.get(_norm(header)) or ATTR
        if target in FIELDS:
            if target in used:  # 两列都像同一个字段：后面那列放进其他属性
                result.warnings.append(Issue(
                    "equipment_import.duplicate_field",
                    f"列 {letter}（{header}）和列 {used[target]} 都像\"{FIELD_LABELS[target]}\"，"
                    f"已把列 {letter} 当作其他属性", column=letter))
                target = ATTR
            else:
                used[target] = letter
        samples = [_text(ws.cell(r, c).value) for r in range(result.header_row + 1,
                                                              min(ws.max_row, result.header_row + 3) + 1)]
        result.columns.append({"column": letter, "index": c, "header": header or letter,
                               "field": target, "samples": samples,
                               "detected": letter not in mapping})
    for f in REQUIRED:
        if f not in used:
            result.errors.append(Issue(
                "equipment_import.missing_field",
                f"没找到\"{FIELD_LABELS[f]}\"列，请在列对应里指定是哪一列（表里的列："
                f"{', '.join(c['column'] + '=' + c['header'] for c in result.columns)}）"))
    if not result.ok:
        return result

    seen: dict[tuple[str, str], int] = {}
    for r in range(result.header_row + 1, ws.max_row + 1):
        values = {c["field"] if c["field"] in FIELDS else c["header"]: ws.cell(r, c["index"]).value
                  for c in result.columns if c["field"] != IGNORE}
        if all(_text(v) is None for v in values.values()):
            continue
        attrs = {c["header"]: _attr_value(ws.cell(r, c["index"]).value)
                 for c in result.columns
                 if c["field"] == ATTR and _attr_value(ws.cell(r, c["index"]).value) is not None}
        _add_row(result, values, attrs, r, seen, used.get("quantity"))
    if not result.rows and result.ok:
        result.warnings.append(Issue("equipment_import.empty", "表里没有设备数据"))
    return result


# ---------------------------------------------------------------- 比对与导入


def identity(tag: str | None, name: str) -> tuple[str, str]:
    """清单里的一行对应哪条设备：有编号按编号，没编号按名称。"""
    return ("tag", tag) if tag else ("name", name)


def _existing(session: Session, project_id: int) -> dict[tuple[str, str], Equipment]:
    return {identity(e.tag, e.name): e for e in session.scalars(
        select(Equipment).where(Equipment.project_id == project_id))}


def _desired_values(row: dict[str, Any], type_ids: dict[str, int]) -> dict[str, Any]:
    return {"name": row["name"], "vendor": row["vendor"], "quantity": row["quantity"],
            "aliases": row["aliases"], "attrs": row["attrs"],
            "equipment_type_id": type_ids.get(row["equipment_type"]) if row["equipment_type"] else None}


def compare(session: Session, project_id: int, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """和数据库比对（有编号按编号，没编号按名称）：新增 / 修改（字段级前→后）/ 重新启用 / 清单里没有了。"""
    existing = _existing(session, project_id)
    types = {t.id: t.name for t in session.scalars(
        select(EquipmentType).where(EquipmentType.project_id == project_id))}
    type_ids = {name: tid for tid, name in types.items()}

    added, modified, reactivated = [], [], []
    for row in rows:
        current = existing.get(identity(row["tag"], row["name"]))
        if current is None:
            added.append({"tag": row["tag"], "name": row["name"], "row": row["row"],
                          "new_type": bool(row["equipment_type"])
                          and row["equipment_type"] not in type_ids})
            continue
        changes = {}
        desired = _desired_values(row, type_ids)
        for f, new in desired.items():
            old = getattr(current, f)
            if f == "equipment_type_id":
                old_name, new_name = types.get(old), row["equipment_type"]
                if old_name != new_name:
                    changes["equipment_type"] = [old_name, new_name]
            elif old != new:
                changes[f] = [old, new]
        if not current.active:
            reactivated.append({"tag": row["tag"], "name": row["name"], "row": row["row"]})
        if changes:
            modified.append({"tag": row["tag"], "name": row["name"], "row": row["row"],
                             "changes": changes})
    sheet_keys = {identity(r["tag"], r["name"]) for r in rows}
    missing = [{"tag": e.tag, "name": e.name} for key, e in existing.items()
               if e.active and key not in sheet_keys]
    new_types = sorted({r["equipment_type"] for r in rows
                        if r["equipment_type"] and r["equipment_type"] not in type_ids})
    return {"added": added, "modified": modified, "reactivated": reactivated, "missing": missing,
            "new_types": new_types,
            "summary": {"added": len(added), "modified": len(modified),
                        "reactivated": len(reactivated), "missing": len(missing),
                        "unchanged": len(rows) - len(added) - len(modified)}}


def import_equipment(session: Session, project_id: int, rows: list[dict[str, Any]], actor: Actor,
                     *, deactivate_missing: bool = False, source: str | None = None
                     ) -> dict[str, int]:
    """按比对结果写入：新建、修改、重新启用；可选把清单里没有了的设备停用。"""
    get_writable_project(session, project_id)
    reason = f"导入设备清单（{source}）" if source else "导入设备清单"
    type_ids: dict[str, int] = {}
    for name in {r["equipment_type"] for r in rows if r["equipment_type"]}:
        type_ids[name] = svc.get_or_create_equipment_type(session, project_id, name, actor,
                                                          reason=reason).id
    existing = _existing(session, project_id)
    counts = {"added": 0, "modified": 0, "reactivated": 0, "deactivated": 0}
    for row in rows:
        values = _desired_values(row, type_ids)
        current = existing.get(identity(row["tag"], row["name"]))
        if current is None:
            svc.create_equipment(session, project_id, {"tag": row["tag"], **values}, actor,
                                 reason=reason)
            counts["added"] += 1
            continue
        if not current.active:
            svc.set_equipment_active(session, project_id, current.id, True, actor, reason=reason)
            counts["reactivated"] += 1
        before = {f: getattr(current, f) for f in values}
        svc.update_equipment(session, project_id, current.id, values, actor, reason=reason)
        if any(getattr(current, f) != before[f] for f in values):
            counts["modified"] += 1
    if deactivate_missing:
        sheet_keys = {identity(r["tag"], r["name"]) for r in rows}
        for key, equipment in existing.items():
            if equipment.active and key not in sheet_keys:
                svc.set_equipment_active(session, project_id, equipment.id, False, actor,
                                         reason=f"{reason}：清单里没有了")
                counts["deactivated"] += 1
    return counts
