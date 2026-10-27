"""文件命名（PRD F5）：只做字符串计算，不碰数据库。

模板按项目配置（projects.naming_templates），没配的用默认值。可用的占位符：
  {no}         种类编号，如 3.5（已含里程碑号）
  {tags}       设备编号，多台用 & 连接，如 72&73
  {type}       种类名
  {equipment}  设备名（多台名称不同时用 & 连接，相同只写一次）
  {title}      文件标题（取自原文件名，去掉版本号和日期）
  {date}       文件日期（按 date_format，默认 YYYYMMDD）
  {version}    平台版本号 1、2、3…
文件名是由结构数据算出来的：设备或种类改名后，按当前名称重新算即可。
"""

import re
import string
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.services.errors import Invalid

DEFAULT_TEMPLATES: dict[str, Any] = {
    "equipment": "{no}.{tags}_{type}-{equipment}-{date}-V{version}",
    "equipment_untagged": "{no}_{type}-{equipment}-{date}-V{version}",
    # 一项下面有好几份文件（标题不同或格式不同）时，名字里加标题区分（用户 2026-10-25 确认）
    "equipment_titled": "{no}.{tags}_{type}-{equipment}-{title}-{date}-V{version}",
    "equipment_untagged_titled": "{no}_{type}-{equipment}-{title}-{date}-V{version}",
    "general": "{no}_{type}-{title}-{date}-V{version}",
    # 识别不出（待归类）时加在原文件名前的前缀；默认不加（用户 2026-10-25 确认），可配置成 "x"
    "unclassified_prefix": "",
    "date_format": "%Y%m%d",
    "tag_joiner": "&",
    # 文件名（不含扩展名）最长多少字符；导出时再加上文件夹不超过 260
    "max_length": 150,
}
TEMPLATE_KEYS = ("equipment", "equipment_untagged", "equipment_titled", "equipment_untagged_titled", "general")
FIELDS = frozenset({"no", "tags", "type", "equipment", "title", "date", "version"})
# 超长时可以截短的字段（按顺序先截前面的）
SHRINKABLE = ("title", "equipment", "type")

# Windows 不允许的字符和保留名
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}

# 版本号：V2、v3、Ver.5、Version 2、Rev B、Rev.3、R1（前后不能紧挨字母或数字）
VERSION_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"v(?:er(?:sion)?)?\.?\s?(?P<v>\d+(?:\.\d+)?)"
    r"|rev(?:ision)?[\s._-]?(?P<rev>\d+|[A-Za-z](?![A-Za-z]))"
    r"|r(?P<r>\d+)"
    r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)
# 日期：20260226、2026-02-26、2026.02.26、2026_02_26、26.02.2026、26-02-2026
DATE_PATTERNS = (
    (re.compile(r"(?<!\d)(?P<y>20\d{2})(?P<m>\d{2})(?P<d>\d{2})(?!\d)")),
    (re.compile(r"(?<!\d)(?P<y>20\d{2})[-._](?P<m>\d{1,2})[-._](?P<d>\d{1,2})(?!\d)")),
    (re.compile(r"(?<!\d)(?P<d>\d{1,2})[-._](?P<m>\d{1,2})[-._](?P<y>20\d{2})(?!\d)")),
)
_SEP = " _-."


def templates_for(project_templates: dict[str, Any] | None) -> dict[str, Any]:
    """项目配置 + 默认值。"""
    return {**DEFAULT_TEMPLATES, **{k: v for k, v in (project_templates or {}).items()
                                    if v not in (None, "")}}


def validate_templates(templates: dict[str, Any]) -> dict[str, Any]:
    """检查模板：只能用已知占位符，日期格式能用，长度上限合理。返回清理后的配置。"""
    clean: dict[str, Any] = {}
    for key, value in templates.items():
        if key not in DEFAULT_TEMPLATES:
            raise Invalid("naming.unknown_key", f"不认识的命名配置项：{key}", key=key)
        if value in (None, ""):
            continue  # 空着表示用默认
        if key in TEMPLATE_KEYS:
            try:
                names = {f for _, f, _, _ in string.Formatter().parse(str(value)) if f is not None}
            except ValueError as exc:
                raise Invalid("naming.bad_template", f"模板写法不对（{key}）：{exc}", key=key) from exc
            unknown = sorted(n for n in names if n not in FIELDS)
            if unknown:
                raise Invalid("naming.unknown_field", f"模板 {key} 里有不认识的占位符：{unknown}",
                              key=key, fields=unknown)
            if "version" not in names:
                raise Invalid("naming.no_version", f"模板 {key} 必须含 {{version}}", key=key)
        elif key == "date_format":
            try:
                date(2026, 2, 26).strftime(str(value))
            except ValueError as exc:
                raise Invalid("naming.bad_date_format", f"日期格式不对：{value}", key=key) from exc
        elif key == "max_length":
            if not isinstance(value, int) or not 40 <= value <= 200:
                raise Invalid("naming.bad_max_length", "文件名长度上限要在 40–200 之间", key=key)
        elif not isinstance(value, str) or _ILLEGAL.search(value):
            raise Invalid("naming.bad_value", f"{key} 不能含 Windows 不允许的字符", key=key)
        clean[key] = value
    return clean


# ---------------------------------------------------------------- 从原文件名识别


def split_ext(filename: str) -> tuple[str, str]:
    """('Layout V2', '.pdf')；扩展名保持原样大小写。"""
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    dot = name.rfind(".")
    if dot <= 0 or len(name) - dot > 10 or " " in name[dot:]:
        return name, ""
    return name[:dot], name[dot:]


def parse_version(filename: str) -> str | None:
    """原文件名里的版本写法，如 'V2'、'Rev B'；没有返回 None。取最后一个。"""
    stem, _ = split_ext(filename)
    found = list(VERSION_RE.finditer(stem))
    return found[-1].group(0).strip(_SEP) if found else None


def _dates(stem: str):
    for pattern in DATE_PATTERNS:
        for m in pattern.finditer(stem):
            try:
                yield m, date(int(m["y"]), int(m["m"]), int(m["d"]))
            except ValueError:
                continue


def parse_date(filename: str) -> date | None:
    """原文件名里的日期；有多个取第一个能用的。"""
    stem, _ = split_ext(filename)
    found = sorted(_dates(stem), key=lambda x: x[0].start())
    return found[0][1] if found else None


def _tidy(text: str) -> str:
    """连续的分隔符合成一个，去掉首尾分隔符和多余空格。"""
    text = re.sub(r"[(\[{]\s*[)\]}]", " ", text)  # 去掉版本号、日期后留下的空括号
    text = re.sub(r"\s*([-_.])(?:\s*[-_.])+\s*", r"\1", text)
    text = re.sub(r"\s+([-_.])(?=\S)", r"\1", text)  # "Plan .final" → "Plan.final"
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip(_SEP)


def platform_prefix(stem: str, no: str) -> tuple[list[str], str] | None:
    """文件名开头是不是"种类编号[.设备编号]"：3.8.13_…、3.8.1.…、3.8.28-…、3.8.24&26&29 …、3.12_…。
    编号后面可以是 _ . - 或空格；设备编号要带数字（避免把 "3.12.Layout" 的 Layout 当成编号）。
    返回 (设备编号列表, 剩下的部分)；不是就返回 None。"""
    m = re.match(rf"^\s*{re.escape(no)}(?:\.(?P<tags>[^\s_.\-&]+(?:&[^\s_.\-&]+)*))?(?P<sep>[_\-.\s]+|$)", stem)
    if not m:
        return None
    tags = [t for t in (m["tags"] or "").split("&") if t]
    if tags and not all(re.search(r"\d", t) for t in tags):
        m = re.match(rf"^\s*{re.escape(no)}(?P<sep>[_\-.\s]+|$)", stem)
        if not m:
            return None
        tags = []
    return tags, stem[m.end():]


def _strip_lead(stem: str, words: list[str]) -> str:
    """去掉开头的种类名 / 设备名（平台命名的文件再传回来时）。"""
    changed = True
    while changed:
        changed = False
        for w in sorted((w for w in words if w), key=len, reverse=True):
            if stem.casefold().startswith(w.casefold()):
                rest = stem[len(w):]
                if not rest or rest[0] in _SEP or not rest[0].isalnum():
                    stem, changed = rest.lstrip(_SEP), True
    return stem


def extract_title(filename: str, *, no: str | None = None, type_name: str | None = None,
                  equipment_names: list[str] | None = None) -> str:
    """文件标题：原文件名去掉扩展名、版本号、日期；
    如果原文件名本身是平台命名的（"3.12_Layout drawing-Cleanroom 1F-20260226-V1"、
    "3.8.13_P&IDs-Tool A-Flowchart-20260302-V2"），去掉前面的编号、种类名、设备名。"""
    stem, _ = split_ext(filename)
    if no:
        found = platform_prefix(stem, no)
        if found:
            stem = _strip_lead(found[1], [type_name or "", *(equipment_names or [])])
    spans = [m.span() for m in VERSION_RE.finditer(stem)] + [m.span() for m, _ in _dates(stem)]
    for start, end in sorted(spans, reverse=True):
        stem = stem[:start] + " " + stem[end:]
    return _tidy(stem)


def title_key(title: str | None) -> str:
    """比较标题是否相同用：不分大小写，只看字母数字。"""
    return " ".join(re.findall(r"[^\W_]+", (title or "").casefold()))


def sanitize(name: str) -> str:
    """去掉 Windows 不允许的字符；结尾不能是点或空格；避开保留名。"""
    name = _ILLEGAL.sub("", name)
    name = re.sub(r"\s{2,}", " ", name).strip().rstrip(". ")
    if name.split(".")[0].upper() in _RESERVED:
        name = "_" + name
    return name


# ---------------------------------------------------------------- 生成文件名


@dataclass
class NameContext:
    no: str
    type_name: str
    per_equipment: bool
    original_name: str
    version: int
    file_date: date
    tags: list[str | None] = field(default_factory=list)  # 每台设备的编号（没有为 None）
    equipment_names: list[str] = field(default_factory=list)
    title: str | None = None
    keep_original: bool = False  # 客户文件保持原名
    with_title: bool = False  # 按设备的一项下有好几份文件：名字里加标题


def _unique(values) -> list[str]:
    seen, out = set(), []
    for v in values:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def build_name(ctx: NameContext, templates: dict[str, Any] | None = None) -> str:
    """按模板生成文件名（含扩展名）。"""
    t = templates_for(templates)
    stem, ext = split_ext(ctx.original_name)
    if ctx.keep_original:
        return sanitize(stem) + ext
    joiner = t["tag_joiner"]
    tags = [tag for tag in ctx.tags if tag]
    values = {
        "no": ctx.no,
        "type": ctx.type_name,
        "tags": joiner.join(tags),
        "equipment": joiner.join(_unique(ctx.equipment_names)),
        "title": ctx.title if ctx.title is not None else extract_title(
            ctx.original_name, no=ctx.no, type_name=ctx.type_name, equipment_names=ctx.equipment_names),
        "date": ctx.file_date.strftime(t["date_format"]),
        "version": str(ctx.version),
    }
    if ctx.per_equipment:
        suffix = "_titled" if ctx.with_title else ""
        template = t["equipment" + suffix] if tags else t["equipment_untagged" + suffix]
    else:
        template = t["general"]
    values = {k: sanitize(v) for k, v in values.items()}

    def render(vals: dict[str, str]) -> str:
        return _tidy(template.format(**vals))

    name = render(values)
    limit = int(t["max_length"])
    for key in SHRINKABLE:  # 超长时依次截短标题、设备名、种类名
        over = len(name) - limit
        if over <= 0:
            break
        if values[key]:
            values[key] = values[key][: max(len(values[key]) - over, 1)].rstrip(_SEP)
            name = render(values)
    return sanitize(name) + ext


def unclassified_name(original_name: str, templates: dict[str, Any] | None = None) -> str:
    """识别不出的文件（待归类）：前缀 + 原文件名；默认没有前缀。"""
    return sanitize(templates_for(templates)["unclassified_prefix"] + original_name)


def samples(templates: dict[str, Any] | None = None) -> dict[str, str]:
    """设置页试算用的示例文件名。"""
    d = date(2026, 2, 26)
    base = {"version": 1, "file_date": d}
    cases = {
        "equipment": NameContext(no="3.5", type_name="Cut sheet", per_equipment=True, tags=["72"],
                                 equipment_names=["Mixer A"],
                                 original_name="Mixer cut sheet Rev B.pdf", **base),
        "multi_equipment": NameContext(no="3.5", type_name="Cut sheet", per_equipment=True,
                                       tags=["72", "73"],
                                       equipment_names=["Mixer A", "Mixer A"],
                                       original_name="cut sheet.pdf", **base),
        "equipment_untagged": NameContext(no="3.5", type_name="Cut sheet", per_equipment=True,
                                          tags=[None], equipment_names=["Beta part"],
                                          original_name="part.pdf", **base),
        "equipment_titled": NameContext(no="3.8", type_name="P&IDs", per_equipment=True, tags=["13"],
                                        equipment_names=["Tool A"], with_title=True,
                                        original_name="Wiring diagram_2026.3.02_V2.png", **base),
        "general": NameContext(no="3.12", type_name="Layout drawing", per_equipment=False,
                               original_name="Cleanroom 1F_20260226_V3.dwg", **base),
        "client": NameContext(no="1.1", type_name="Spec", per_equipment=False, keep_original=True,
                              original_name="Client spec rev 2.pdf", **base),
    }
    out = {k: build_name(c, templates) for k, c in cases.items()}
    out["unclassified"] = unclassified_name("unknown file.pdf", templates)
    return out
