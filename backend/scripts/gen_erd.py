"""从 app/models.py 自动生成数据库关系图 docs/database-erd.md（Mermaid）。

用法（在 backend 目录下）：python scripts/gen_erd.py
改了 models.py 后重新运行；测试 test_erd_up_to_date 会检查图是否过期。
"""

import sys
from pathlib import Path

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    Integer,
    String,
    Table,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

import app.models  # noqa: E402,F401  注册全部模型
from app.db import Base  # noqa: E402

OUTPUT = BACKEND_DIR.parent / "docs" / "database-erd.md"

# 分组展示；新增的表必须放进某个分组，否则生成时报错
GROUPS: dict[str, list[str]] = {
    "账号与权限": ["users", "roles", "project_members", "projects", "auth_sessions"],
    "项目配置": ["projects", "status_mappings", "review_codes", "mdl_versions"],
    "提交与客户审核": ["projects", "transmittals", "transmittal_items", "transmittal_item_versions", "deliverables",
                  "versions"],
    "MDL 结构与设备": [
        "projects",
        "milestones",
        "document_types",
        "document_type_modules",
        "modules",
        "module_equipment_types",
        "equipment_types",
        "equipment",
        "equipment_units",
        "document_type_equipment",
    ],
    "应交、文件与基线": [
        "document_types",
        "equipment",
        "modules",
        "deliverables",
        "file_deliverables",
        "files",
        "versions",
        "blobs",
        "baselines",
        "baseline_items",
    ],
    "系统（变更记录、事件、任务）": [
        "projects",
        "users",
        "change_logs",
        "events",
        "event_cursors",
        "jobs",
    ],
    "存量导入": ["projects", "jobs", "import_batches", "import_items", "files"],
}


def col_type(col) -> str:
    t = col.type
    if isinstance(t, Enum):
        return "enum"
    for cls, name in (
        (BigInteger, "bigint"),
        (Integer, "int"),
        (Boolean, "bool"),
        (DateTime, "datetime"),
        (Date, "date"),
        (JSONB, "json"),
        (Uuid, "uuid"),
        (Text, "text"),
        (String, "str"),
    ):
        if isinstance(t, cls):
            return name
    return type(t).__name__.lower()


def single_unique_cols(table: Table) -> set[str]:
    cols = set()
    for c in table.constraints:
        if c.__class__.__name__ == "UniqueConstraint" and len(c.columns) == 1:
            cols.update(col.name for col in c.columns)
    cols.update(c.name for c in table.columns if c.unique)
    return cols


def entity(table: Table) -> list[str]:
    uniques = single_unique_cols(table)
    lines = [f"    {table.name} {{"]
    for col in table.columns:
        keys = []
        if col.primary_key:
            keys.append("PK")
        if col.foreign_keys:
            keys.append("FK")
        if col.name in uniques:
            keys.append("UK")
        key = f" {', '.join(keys)}" if keys else ""
        note = ' "可空"' if col.nullable and not col.primary_key else ""
        lines.append(f"        {col_type(col)} {col.name}{key}{note}")
    lines.append("    }")
    return lines


def relations(tables: list[Table]) -> list[str]:
    names = {t.name for t in tables}
    lines = []
    for t in tables:
        for col in t.columns:
            for fk in col.foreign_keys:
                parent = fk.column.table.name
                if parent not in names:
                    continue
                left = "|o" if col.nullable else "||"
                lines.append(f'    {parent} {left}--o{{ {t.name} : "{col.name}"')
    return lines


def diagram(tables: list[Table]) -> list[str]:
    lines = ["```mermaid", "erDiagram"]
    for t in tables:
        lines += entity(t)
    lines += relations(tables)
    lines.append("```")
    return lines


def render() -> str:
    all_tables = dict(Base.metadata.tables)
    grouped = {name for names in GROUPS.values() for name in names}
    missing = set(all_tables) - grouped
    if missing:
        raise SystemExit(f"这些表没有放进 GROUPS：{sorted(missing)}")

    out = [
        "# 数据库关系图",
        "",
        "> 本文件由 `backend/scripts/gen_erd.py` 从 `app/models.py` 自动生成，**不要手改**。",
        "> 字段说明见 [database.md](database.md)。",
        ">",
        "> 查看方式：VS Code 里用 Markdown Preview Enhanced 打开预览（右键 → Markdown Preview Enhanced: Open Preview），"
        "或在 GitHub 上直接查看。",
        "",
        "图例：`PK` 主键 · `FK` 外键 · `UK` 唯一 · `||--o{` 一对多（必填） · `|o--o{` 一对多（可空）；"
        "连线上的文字是外键字段名。",
        "",
    ]
    for title, names in GROUPS.items():
        out += [f"## {title}", ""]
        out += diagram([all_tables[n] for n in names])
        out.append("")
    out += ["## 全部表（总图）", ""]
    out += diagram(sorted(all_tables.values(), key=lambda t: t.name))
    out.append("")
    return "\n".join(out)


def main() -> None:
    OUTPUT.write_text(render(), encoding="utf-8")
    print(f"已生成 {OUTPUT}")


if __name__ == "__main__":
    main()
