"""第 11 步：文件命名（PRD F5）。只做字符串计算，不连数据库。"""

from datetime import date

import pytest

from app.services import naming
from app.services.errors import Invalid
from app.services.naming import NameContext, build_name

D = date(2026, 2, 26)


def ctx(**kw) -> NameContext:
    base = {"no": "3.5", "type_name": "Cut sheet", "per_equipment": True, "original_name": "a.pdf",
            "version": 1, "file_date": D, "tags": ["72"], "equipment_names": ["Mixer A"]}
    return NameContext(**{**base, **kw})


# ---------------------------------------------------------------- 默认模板


def test_per_equipment_name_matches_example():
    assert build_name(ctx()) == "3.5.72_Cut sheet-Mixer A-20260226-V1.pdf"


def test_multiple_equipment_joined_with_ampersand():
    name = build_name(ctx(tags=["72", "73"], equipment_names=["Mixer", "Mixer"]))
    assert name == "3.5.72&73_Cut sheet-Mixer-20260226-V1.pdf"


def test_multiple_equipment_with_different_names():
    name = build_name(ctx(tags=["72", "73"], equipment_names=["Mixer", "Dryer"]))
    assert name == "3.5.72&73_Cut sheet-Mixer&Dryer-20260226-V1.pdf"


def test_equipment_without_tag_has_no_number():
    name = build_name(ctx(tags=[None], equipment_names=["Beta part"], original_name="x.PDF"))
    assert name == "3.5_Cut sheet-Beta part-20260226-V1.PDF"  # 扩展名大小写保持原样


def test_general_uses_title_from_original_name():
    name = build_name(ctx(no="3.12", type_name="Layout drawing", per_equipment=False, tags=[],
                          equipment_names=[], original_name="Cleanroom 1F_20260226_V3.dwg", version=2))
    assert name == "3.12_Layout drawing-Cleanroom 1F-20260226-V2.dwg"


def test_module_name_never_in_filename():
    # 按模块的应交项和"不对应设备"一样，用文件标题；模块名不进文件名
    name = build_name(ctx(no="3.3", type_name="GA drawings", per_equipment=False, tags=[],
                          equipment_names=[], title="Alpha bench area"))
    assert name == "3.3_GA drawings-Alpha bench area-20260226-V1.pdf"


def test_client_file_keeps_original_name():
    name = build_name(ctx(keep_original=True, original_name="Client Spec rev 2.pdf"))
    assert name == "Client Spec rev 2.pdf"


def test_unclassified_keeps_original_name_prefix_configurable():
    # 用户 2026-10-25 确认：待归类默认不加 x；需要时在命名模板里配置前缀
    assert naming.unclassified_name("unknown.pdf") == "unknown.pdf"
    assert naming.unclassified_name("unknown.pdf", {"unclassified_prefix": "x"}) == "xunknown.pdf"


def test_names_kept_as_configured_no_case_change():
    name = build_name(ctx(type_name="CUT sheet", equipment_names=["MIXER a"]))
    assert name == "3.5.72_CUT sheet-MIXER a-20260226-V1.pdf"


def test_empty_title_leaves_no_double_dash():
    name = build_name(ctx(no="1.1", type_name="Spec", per_equipment=False, tags=[],
                          equipment_names=[], original_name="V2.pdf"))
    assert name == "1.1_Spec-20260226-V1.pdf"


# ---------------------------------------------------------------- 非法字符与长度


def test_illegal_windows_characters_removed():
    name = build_name(ctx(type_name='Cut: sheet?', equipment_names=['Mixer "A"/B']))
    assert name == "3.5.72_Cut sheet-Mixer AB-20260226-V1.pdf"


def test_reserved_windows_name_prefixed():
    assert naming.sanitize("CON.pdf") == "_CON.pdf"
    assert naming.sanitize("name. ") == "name"


def test_long_title_is_shortened_to_limit():
    long_title = "Very long title " * 20
    name = build_name(ctx(no="3.12", type_name="Layout", per_equipment=False, tags=[],
                          equipment_names=[], title=long_title))
    stem = name.removesuffix(".pdf")
    assert len(stem) <= naming.DEFAULT_TEMPLATES["max_length"]
    assert stem.startswith("3.12_Layout-Very long title") and stem.endswith("-20260226-V1")


# ---------------------------------------------------------------- 从原文件名识别


@pytest.mark.parametrize(("filename", "version"), [
    ("Layout V2.pdf", "V2"), ("layout_v3.pdf", "v3"), ("Spec Ver.5.docx", "Ver.5"),
    ("Spec Version 2.docx", "Version 2"), ("drawing Rev B.dwg", "Rev B"), ("drawing_rev_C.dwg", "rev_C"),
    ("report R1.pdf", "R1"), ("Review notes.pdf", None), ("Reverse osmosis.pdf", None),
    ("Level 2 plan.pdf", None),
])
def test_parse_version(filename, version):
    assert naming.parse_version(filename) == version


@pytest.mark.parametrize(("filename", "found"), [
    ("Layout 20260226.pdf", date(2026, 2, 26)), ("Layout 2026-02-26.pdf", date(2026, 2, 26)),
    ("Layout 2026.2.6.pdf", date(2026, 2, 6)), ("Layout_26.02.2026.pdf", date(2026, 2, 26)),
    ("Layout 20261399.pdf", None), ("PO 12345678.pdf", None), ("Layout.pdf", None),
])
def test_parse_date(filename, found):
    assert naming.parse_date(filename) == found


@pytest.mark.parametrize(("filename", "title"), [
    ("Cleanroom 1F_20260226_V3.dwg", "Cleanroom 1F"),
    ("report R1 (26.02.2026).pdf", "report"),
    ("Layout - Area A Rev B.pdf", "Layout - Area A"),
    ("3.12_Layout drawing-Cleanroom 1F-20260226-V1.pdf", "Cleanroom 1F"),  # 平台命名的再传回来
    ("no extension", "no extension"),
])
def test_extract_title(filename, title):
    assert naming.extract_title(filename, no="3.12", type_name="Layout drawing") == title


def test_title_key_ignores_case_and_punctuation():
    assert naming.title_key("Cleanroom  1F") == naming.title_key("cleanroom_1f")
    assert naming.title_key("Area A") != naming.title_key("Area B")


def test_split_ext_ignores_folders_and_colons():
    assert naming.split_ext("dir\\sub/Plan V3.final.pdf") == ("Plan V3.final", ".pdf")
    assert naming.split_ext("a:b.pdf") == ("a:b", ".pdf")
    assert naming.split_ext(".hidden") == (".hidden", "")


# ---------------------------------------------------------------- 自定义模板


def test_custom_template_and_date_format():
    templates = {"equipment": "{tags}-{no}-{type}-V{version} ({date})", "date_format": "%Y-%m-%d",
                 "tag_joiner": "+"}
    name = build_name(ctx(tags=["72", "73"]), templates)
    assert name == "72+73-3.5-Cut sheet-V1 (2026-02-26).pdf"


def test_validate_templates_rejects_unknown_fields_and_missing_version():
    with pytest.raises(Invalid) as e:
        naming.validate_templates({"general": "{no}_{project}-V{version}"})
    assert e.value.code == "naming.unknown_field"
    with pytest.raises(Invalid) as e:
        naming.validate_templates({"general": "{no}_{type}"})
    assert e.value.code == "naming.no_version"
    with pytest.raises(Invalid) as e:
        naming.validate_templates({"oops": "x"})
    assert e.value.code == "naming.unknown_key"
    with pytest.raises(Invalid):
        naming.validate_templates({"unclassified_prefix": "x?"})
    assert naming.validate_templates({"general": "", "max_length": 120}) == {"max_length": 120}


def test_samples_cover_every_case():
    assert set(naming.samples()) == {"equipment", "multi_equipment", "equipment_untagged", "equipment_titled",
                                     "general", "client", "unclassified"}


def test_equipment_with_several_files_gets_title():
    name = build_name(ctx(no="3.8", type_name="P&IDs", tags=["13"], equipment_names=["Tool A"],
                          original_name="3.8.13_Tool A Wiring diagram_2026.3.02_V2.png", with_title=True))
    assert name == "3.8.13_P&IDs-Tool A-Wiring diagram-20260226-V1.png"


@pytest.mark.parametrize(("stem", "tags", "rest"), [
    ("3.8.13_Wiring diagram", ["13"], "Wiring diagram"),
    ("3.8.1.Preliminary P&IDs-Tool B", ["1"], "Preliminary P&IDs-Tool B"),
    ("3.8.28-P&ID-Furnace", ["28"], "P&ID-Furnace"),
    ("3.8.24&26&29 Front&rear P&ID", ["24", "26", "29"], "Front&rear P&ID"),
    ("3.8_Overview", [], "Overview"),
])
def test_platform_prefix_separators(stem, tags, rest):
    assert naming.platform_prefix(stem, "3.8") == (tags, rest)


def test_platform_prefix_rejects_lookalikes():
    assert naming.platform_prefix("3.81_x", "3.8") is None
    assert naming.platform_prefix("3.12.Layout plan", "3.12") == ([], "Layout plan")  # Layout 不是设备编号


def test_title_strips_platform_prefix_type_and_equipment():
    t = naming.extract_title("3.8.13_P&IDs-Tool A-Wiring diagram-20260302-V2.png", no="3.8",
                             type_name="P&IDs", equipment_names=["Tool A"])
    assert t == "Wiring diagram"
    assert naming.extract_title("3.8.13_P&IDs-Tool A-20260302-V2.pdf", no="3.8", type_name="P&IDs",
                                equipment_names=["Tool A"]) == ""
